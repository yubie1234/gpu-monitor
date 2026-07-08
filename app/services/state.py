"""최신 스냅샷 저장소 + 백그라운드 async Refresher.

수집기는 동기(blocking urllib)라 asyncio.to_thread 로 이벤트 루프 밖에서 돌린다.
요청 핸들러는 최신 스냅샷을 즉시 돌려줄 뿐, 절대 수집을 기다리지 않는다.
"""

import asyncio
import time

from app.services.snapshot import build_snapshot


class SnapshotStore:
    def __init__(self):
        self._snap = None
        # 마지막 성공 수집 시각(epoch). 실패 사이클에선 갱신하지 않는다 —
        # 나이가 계속 늘어야 staleness 알럿이 정체를 탐지한다.
        self.last_success_epoch = None

    def get(self):
        return self._snap

    def set(self, snap):
        self._snap = snap


class Refresher:
    def __init__(self, settings, store, interval):
        self.settings = settings
        self.store = store
        self.interval = max(float(interval or 15.0), 1.0)
        self.refreshes = 0   # 매 시도 +1 (heartbeat — 루프 정지 탐지)
        self.failures = 0    # 예상외 예외만 +1 (통상 수집 실패는 snap.errors 가 담당)
        self._task = None
        self._stop = None

    async def _refresh_once(self):
        self.refreshes += 1
        try:
            snap = await asyncio.to_thread(build_snapshot, self.settings)
            self.store.set(snap)
            self.store.last_success_epoch = time.time()
        except Exception:  # noqa: BLE001  (루프는 죽지 않는다)
            self.failures += 1

    async def _loop(self):
        while not self._stop.is_set():
            await self._refresh_once()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
            except asyncio.TimeoutError:
                pass

    async def start(self):
        self._stop = asyncio.Event()
        await self._refresh_once()          # 첫 화면이 비지 않게 즉시 1회
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        if self._stop:
            self._stop.set()
        if self._task:
            try:
                await self._task
            except Exception:  # noqa: BLE001
                pass


def compute_freshness(last_success_epoch, interval, now, stale_factor=3):
    """마지막 성공 수집 시각으로 데이터 신선도 계산 (순수 함수 — FastAPI 없이 테스트).

    age 는 서버 now 기준(초), 음수로 안 내려가게 clamp. 첫 수집 전(last=None)이면
    age=None·stale=False. stale 은 age 가 interval*stale_factor(기본 3배 = 2회 이상
    사이클 누락)을 넘을 때 True — 일시 지연 오탐을 피하면서 진짜 정지는 잡는다.
    """
    interval = max(float(interval or 1.0), 1.0)
    if last_success_epoch is None:
        return {"age_seconds": None, "stale": False, "interval_seconds": interval}
    age = max(now - last_success_epoch, 0.0)
    return {"age_seconds": round(age, 1),
            "stale": age > interval * stale_factor,
            "interval_seconds": interval}


def build_meta(store, refresher):
    """/metrics 용 관측성 meta.

    up 은 반드시 스토어 원본(store.get() is None 여부)으로 판정한다 —
    라우트의 _snap 폴백은 빈 스토어를 합성 dict 로 가려 snap 만으로는 구분 불가.
    """
    return {
        "up": store.get() is not None,
        "last_success_epoch": getattr(store, "last_success_epoch", None),
        "refreshes": getattr(refresher, "refreshes", None),
        "failures": getattr(refresher, "failures", None),
    }
