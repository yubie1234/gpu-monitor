# CLAUDE.md

Claude Code(claude.ai/code) 가 이 저장소에서 작업할 때 참고하는 가이드.

## What this is

`gpu-monitor` 는 **노드별 GPU 할당(allocation) 현황** 대시보드다(FastAPI). "각 노드가 어떤
GPU를 몇 개 가졌고, 그중 몇 개가 어떤 워크로드에 할당됐으며, 몇 개가 비었나"에 답한다.
웹 대시보드(`/`), JSON API(`/api/snapshot`), Prometheus(`/metrics`)를 낸다.

**핵심 구분 — 할당(allocation)이지 실사용률(utilization)이 아니다.** Pod 의
`resources.limits/requests` 의 `nvidia.com/gpu` 수만 본다. DCGM(%, VRAM, 온도)은 다루지
않는다(필요하면 dcgm-exporter + Grafana). 이 경계를 흐리지 말 것.

`model-monitor` 의 형제 프로젝트지만 **축이 다르다**: model-monitor 는 *LiteLLM model →
api_base → backend*, 이건 *Node → GPU → Workload*. 코드 골격(K8sClient, GPU 원시함수,
Refresher, FastAPI 셸, Prometheus, 에어갭 빌드)은 model-monitor 에서 가져왔다.

**의존성 정책:** 수집 계층(`app/core`, `app/services`)은 **표준 라이브러리만**
(`urllib`, `ssl`, `json`, `asyncio`). 웹 계층만 FastAPI. 배포 타깃은 에어갭이라 새 외부
패키지가 필요하면 **먼저 사용자에게 확인**하고 이미지에 벤더링한다. PyYAML 은 선택
(없으면 JSON 설정만).

## Layout

```
app/
  __init__.py          # __version__ — 단일 진실원
  main.py              # create_app(), lifespan -> Refresher 시작/정지, app.state 배선
  __main__.py          # `python -m app`
  config.py            # Settings(pydantic-settings) + 파일 병합 -> collector dict
  core/k8s.py          # K8sClient (in-cluster, urllib+ssl)
  services/
    gpu.py             # GPU 원시함수: pod_gpu / node_gpu / pod_ready / short_gpu_product
    workload.py        # classify_workload (owner ref + 라벨로 워크로드 타입 추정)
    collect.py         # collect_gpu_nodes / collect_allocations (노드별 Pod 점유)
    snapshot.py        # build_snapshot / summarize
    state.py           # SnapshotStore + async Refresher
    prometheus.py      # render_prometheus_metrics
    demo.py            # demo_snapshot
  schemas/snapshot.py  # 응답 모델 (전 Optional, extra=allow)
  api/routes.py        # /api/snapshot, /snapshot.json, /metrics, /healthz, /readyz
  web/routes.py        # / (대시보드)
  web/templates/dashboard.html
test_gpu_monitor.py    # stdlib unittest — app.services 만 import (FastAPI 불필요)
```

## Commands

```bash
python3 -m unittest -v                                    # 테스트
python3 -m pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8089
MONITOR_DEMO=true uvicorn app.main:app --port 8089        # 라이브 없이 미리보기
./ci.sh                                                   # 이미지 빌드
```

## 데이터 흐름

`build_snapshot(settings)` → 단일 `snap` dict (API/대시보드/JSON/Prometheus 공용):

1. `collect_gpu_nodes` — `GET /api/v1/nodes`(옵션 labelSelector). `nvidia.com/gpu`
   capacity>0 인 노드만 남기고, 장치명은 노드 라벨 `nvidia.com/gpu.product` 에서.
2. `collect_allocations(node)` — `GET /api/v1/pods?fieldSelector=spec.nodeName=<node>`
   로 그 노드의 Pod 을 받아, GPU 점유(`pod_gpu`>0) Pod 만 allocation 으로. Succeeded/Failed
   Pod 은 GPU 를 놓은 것으로 보고 제외. 워크로드 타입은 `classify_workload`(라벨/owner ref).
3. `summarize` — 클러스터 총/할당/유휴, 장치별·워크로드 타입별 분포. free =
   `allocatable(없으면 capacity) - allocated`.

### 백그라운드 수집
수집기는 동기(blocking urllib). lifespan 이 async `Refresher`(`services/state.py`)를 띄워
`asyncio.to_thread` 로 `interval` 마다 스냅샷을 다시 만든다. 요청 핸들러는 `SnapshotStore`
의 최신 스냅샷을 즉시 반환 — 요청·스크레이프 경로에서 절대 수집하지 않는다.

## Branch strategy

`main` 을 두 개의 장수(long-lived) 라인으로 나누고, 기능 작업은 별도 브랜치에서 한다
(model-monitor 와 동일):

- **`develop`** — 진행 중 개발 통합 브랜치.
- **`product`** — 제품/릴리스 라인 추적 브랜치.
- **`feature/<name>`** — 기능당 한 브랜치(`develop` 에서 분기). 기능 개발은 여기서 하고
  `develop`/`product`/`main` 에 직접 하지 않는다.

### 머지 시 태깅 (자동)
`.github/workflows/tag-on-merge.yml` 이 `develop`/`product` 에 머지(push)될 때
`app/__init__.py` 의 `__version__` 으로 태그를 만든다:
- **`product`** → `v<version>` — 불변 릴리스 태그. **이미 있으면 워크플로 실패** →
  product 릴리스 전 `__version__` bump 강제.
- **`develop`** → `v<version>-develop` — 플로팅 pre-release 태그. develop 머지마다 최신
  커밋으로 **force-이동**.

즉 product 릴리스엔 `__version__` bump, develop 머지는 `-develop` 태그만 재지정.
태그 push 는 브랜치 워크플로를 다시 켜지 않는다(루프 없음).

### 브랜치별 이미지 태그
`ci.sh`/`push.sh` 에 `BRANCH=` 를 넘기면 브랜치별 floating 이미지 태그가 갈린다:
- 미지정/`product` → `<version>` + `:latest`
- 그 외(`develop` 등) → `<version>-<BRANCH>` + `:<BRANCH>` (예: `:develop`)

이렇게 해서 develop 빌드가 product 의 `:latest` 를 덮지 않는다.

## Conventions

- **Versioning:** `app/__init__.py` 의 `__version__` 이 단일 진실원 — 이미지 태그
  (`ci.sh`/`push.sh` grep), FastAPI `version`, `/api/snapshot` 의 `version`,
  `gpu_monitor_build_info` 를 모두 구동. 여기서 bump 하고 README 헤더도 맞춘다.
- **Schemas vs dicts:** 스냅샷은 dict 로 만들고 테스트. `schemas/snapshot.py` 는 API 경계
  문서/검증용 (전 Optional, `extra="allow"` — 아무것도 안 떨어뜨림). Pydantic 을 수집기에
  밀어넣지 말 것.
- **할당만, 사용률 아님** — 새 기능 넣을 때 이 경계를 지킨다.
- 한 노드 수집 실패가 전체 스냅샷을 막지 않게 per-node try/except 로 감싸고 `error` 기록.
- 주석·사용자 문자열은 한국어 — 주변 언어에 맞춘다.
- 파싱/집계/워크로드 분류 로직을 건드리면 회귀 테스트를 추가한다. `FakeClient` 패턴
  (path substring 라우팅) 사용. 테스트는 `app.services` 만 import.
```
