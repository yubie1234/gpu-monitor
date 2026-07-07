"""JSON API / Prometheus / health.

핸들러는 SnapshotStore 의 최신 스냅샷을 즉시 반환한다(스크레이프·요청 경로에서 수집 X).
"""

import json

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from app.schemas.snapshot import Snapshot
from app.services.prometheus import render_prometheus_metrics
from app.services.state import build_meta

router = APIRouter()


def _snap(request: Request):
    snap = request.app.state.store.get()
    return snap or {"version": None, "ts": None, "nodes": [], "summary": {},
                    "k8s_enabled": False, "errors": ["아직 첫 수집 전"]}


@router.get("/api/snapshot", response_model=Snapshot,
            summary="노드별 GPU 할당 스냅샷")
def api_snapshot(request: Request):
    return _snap(request)


@router.get("/snapshot.json", include_in_schema=False)
def snapshot_json(request: Request):
    data = json.dumps(_snap(request), ensure_ascii=False, indent=2)
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
