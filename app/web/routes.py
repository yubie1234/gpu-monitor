"""대시보드 (/). 템플릿 placeholder(__INTERVAL_MS__/__BASE_PATH__)를 주입해 반환."""

import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter()
_TEMPLATE = os.path.join(os.path.dirname(__file__), "templates", "dashboard.html")


def load_dashboard_html(interval_ms, base_path):
    with open(_TEMPLATE, "r", encoding="utf-8") as f:
        html = f.read()
    return (html
            .replace("__INTERVAL_MS__", str(int(interval_ms)))
            .replace("__BASE_PATH__", base_path or ""))


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard(request: Request):
    st = request.app.state
    return load_dashboard_html(st.interval_ms, st.base_path)
