"""create_app() + lifespan(백그라운드 Refresher 시작/정지). app.state 배선."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api.routes import router as api_router
from app.config import build_collector_settings, get_settings, normalize_root_path
from app.services.state import Refresher, SnapshotStore
from app.web.routes import router as web_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await app.state.refresher.start()
    try:
        yield
    finally:
        await app.state.refresher.stop()


def create_app() -> FastAPI:
    settings = get_settings()
    collector = build_collector_settings(settings)
    root_path = normalize_root_path(settings.root_path)

    app = FastAPI(
        title="gpu-monitor",
        version=__version__,
        description="노드별 GPU 할당(allocation) 현황 · 실사용률(DCGM)이 아님",
        root_path=root_path,
        lifespan=lifespan,
    )
    store = SnapshotStore()
    app.state.store = store
    app.state.settings = collector
    app.state.interval_ms = int(max(settings.interval, 1.0) * 1000)
    app.state.base_path = root_path
    app.state.grafana_url = settings.grafana_url.strip()
    app.state.refresher = Refresher(collector, store, settings.interval)

    app.include_router(api_router)
    app.include_router(web_router)
    return app


app = create_app()
