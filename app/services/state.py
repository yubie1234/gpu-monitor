"""최신 스냅샷 저장소 + 백그라운드 async Refresher.

수집기는 동기(blocking urllib)라 asyncio.to_thread 로 이벤트 루프 밖에서 돌린다.
요청 핸들러는 최신 스냅샷을 즉시 돌려줄 뿐, 절대 수집을 기다리지 않는다.
"""

import asyncio

from app.services.snapshot import build_snapshot


class SnapshotStore:
    def __init__(self):
        self._snap = None

    def get(self):
        return self._snap

    def set(self, snap):
        self._snap = snap


class Refresher:
    def __init__(self, settings, store, interval):
        self.settings = settings
        self.store = store
        self.interval = max(float(interval or 15.0), 1.0)
        self._task = None
        self._stop = None

    async def _refresh_once(self):
        try:
            snap = await asyncio.to_thread(build_snapshot, self.settings)
            self.store.set(snap)
        except Exception:  # noqa: BLE001  (루프는 죽지 않는다)
            pass

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
