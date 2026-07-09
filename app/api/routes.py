"""JSON API / Prometheus / health.

핸들러는 SnapshotStore 의 최신 스냅샷을 즉시 반환한다(스크레이프·요청 경로에서 수집 X).
"""

import json
import time

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from app.schemas.snapshot import Snapshot
from app.services.prometheus import render_prometheus_metrics
from app.services.state import build_meta, compute_freshness

router = APIRouter()


def _snap(request: Request):
    snap = request.app.state.store.get()
    return snap or {"version": None, "ts": None, "nodes": [], "summary": {},
                    "k8s_enabled": False, "errors": ["아직 첫 수집 전"]}


def _freshness(request: Request):
    """요청 시점 기준 데이터 신선도 meta.

    나이(age)는 서버 시계로 계산한다 — 브라우저·서버 시계 차(skew)에 흔들리지 않게
    클라이언트가 아니라 여기서 잰다. 계산은 state.compute_freshness(순수 함수)에 위임.
    """
    store = request.app.state.store
    interval = getattr(request.app.state, "interval_ms", 15000) / 1000.0
    return compute_freshness(
        getattr(store, "last_success_epoch", None), interval, time.time())


@router.get("/api/snapshot", response_model=Snapshot,
            summary="노드별 GPU 할당 스냅샷")
def api_snapshot(request: Request):
    # 스토어 원본을 변형하지 않게 얕은 복사에 meta 를 얹는다.
    return dict(_snap(request), meta=_freshness(request))


@router.get("/snapshot.json", include_in_schema=False)
def snapshot_json(request: Request):
    data = json.dumps(dict(_snap(request), meta=_freshness(request)),
                      ensure_ascii=False, indent=2)
    return Response(
        content=data, media_type="application/json",
        headers={"Content-Disposition":
                 "attachment; filename=gpu-monitor-snapshot.json"})


@router.get("/metrics", include_in_schema=False)
def metrics(request: Request):
    if not request.app.state.settings.get("metrics", True):
        return PlainTextResponse("metrics disabled\n", status_code=404)
    meta = build_meta(request.app.state.store, request.app.state.refresher)
    return PlainTextResponse(
        render_prometheus_metrics(_snap(request), meta),
        media_type="text/plain; version=0.0.4; charset=utf-8")


@router.get("/healthz", include_in_schema=False)
def healthz():
    return {"status": "ok"}


@router.get("/readyz", include_in_schema=False)
def readyz(request: Request):
    ready = request.app.state.store.get() is not None
    return JSONResponse({"status": "ready" if ready else "starting"},
                        status_code=200 if ready else 503)
