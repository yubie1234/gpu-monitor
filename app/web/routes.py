"""대시보드 (/). 템플릿 placeholder 를 문맥별로 이스케이프해 주입 후 반환.

base_path/grafana_url 은 운영자 설정(MONITOR_ROOT_PATH/MONITOR_GRAFANA_URL)이라 문자
정제가 없다. HTML 속성(_ATTR)과 JS 문자열(_JS) 문맥에 각각 다른 이스케이프를 써서,
값에 따옴표·백슬래시·`</script>` 등이 들어가도 문맥을 탈출하지 못하게 한다.
"""

import html as _html
import json
import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter()
_TEMPLATE = os.path.join(os.path.dirname(__file__), "templates", "dashboard.html")

# JS 문자열 안에서 문맥 탈출을 유발하는 문자 -> 유니코드 escape.
# < > & 는 `</script>` 조기 종료 방지, U+2028/2029 는 JS 에서 개행 취급이라 문자열 깨짐 방지.
_JS_UNSAFE = {"<": "\\u003c", ">": "\\u003e", "&": "\\u0026",
              chr(0x2028): "\\u2028", chr(0x2029): "\\u2029"}


def _attr(value):
    """이중따옴표 HTML 속성값용 이스케이프 (&, <, >, ", ')."""
    return _html.escape(str(value or ""), quote=True)


def _js(value):
    """JS 문자열 리터럴(따옴표 포함)로 안전 인코딩.

    json.dumps 가 따옴표·백슬래시·개행을 처리하고, _JS_UNSAFE 치환이 `</script>`
    탈출과 HTML 파서 조기 종료를 막는다.
    """
    s = json.dumps(str(value or ""))
    for ch, esc in _JS_UNSAFE.items():
        s = s.replace(ch, esc)
    return s


def load_dashboard_html(interval_ms, base_path, grafana_url=""):
    with open(_TEMPLATE, "r", encoding="utf-8") as f:
        doc = f.read()
    return (doc
            .replace("__INTERVAL_MS__", str(int(interval_ms)))
            .replace("__BASE_PATH_ATTR__", _attr(base_path))
            .replace("__BASE_PATH_JS__", _js(base_path))
            .replace("__GRAFANA_URL_ATTR__", _attr(grafana_url))
            .replace("__GRAFANA_URL_JS__", _js(grafana_url)))


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard(request: Request):
    st = request.app.state
    return load_dashboard_html(st.interval_ms, st.base_path, st.grafana_url)
