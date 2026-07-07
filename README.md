# gpu-monitor `v0.1.0`

노드별 **GPU 할당(allocation) 현황** 대시보드. 클러스터의 각 노드가 어떤 GPU를 몇 개
가졌고(capacity), 그중 몇 개가 어떤 워크로드에 **할당**됐는지(allocated), 몇 개가
비어 있는지(free)를 한눈에 보여준다.

> **할당 ≠ 실사용률.** 이 도구는 Pod 이 요청/점유한 `nvidia.com/gpu` 수를 본다.
> GPU가 실제 몇 % 바쁜지(사용률·VRAM·온도)는 다루지 않는다 — 그건 DCGM + Grafana 영역.

`model-monitor`(LiteLLM 서빙 모니터)의 형제 프로젝트다. 다만 축이 다르다:
`model-monitor` 는 *LiteLLM model → backend*, `gpu-monitor` 는 *Node → GPU → Workload*.

## 무엇을 보여주나

- **노드별**: GPU 장치명(H100/B200…) · 할당/총량 · 유휴
- **노드 내 할당 목록**: 각 GPU 점유 Pod 을 **워크로드 타입**과 함께
  (KServe · Job · Notebook · Deployment · StatefulSet · …, Pod 라벨/owner 로 추정)
- **클러스터 집계**: 총/할당/유휴 GPU, 장치별·워크로드 타입별 분포
- Prometheus `/metrics`

## 실행

```bash
python3 -m pip install -r requirements.txt

# 라이브 (in-cluster; ServiceAccount 토큰 자동 사용)
uvicorn app.main:app --host 0.0.0.0 --port 8089
python3 -m app

# 라이브 엔드포인트 없이 미리보기
MONITOR_DEMO=true uvicorn app.main:app --port 8089
```

## 엔드포인트

- `/` — 대시보드(HTML)
- `/api/snapshot` — 노드별 GPU 할당 JSON
- `/snapshot.json` — 다운로드
- `/metrics` — Prometheus
- `/healthz`, `/readyz`

## 메트릭

`/metrics` (Prometheus text 0.0.4). **전부 할당·수집 헬스 지표다 — 사용률(DCGM) 아님.**

| 계열 | 타입 | 라벨 | 의미 |
|---|---|---|---|
| `gpu_monitor_build_info` | gauge (상수 1) | `version` | 빌드 정보 |
| `gpu_monitor_cluster_gpu_capacity` / `_allocated` / `_free` | gauge | – | 클러스터 총/할당/유휴 GPU |
| `gpu_monitor_nodes` | gauge | – | GPU 노드 수 |
| `gpu_monitor_node_gpu` | gauge | `node`, `product`, `state` | 노드별 GPU — state=capacity/allocatable/allocated/free (값 `None` 이면 라인 생략) |
| `gpu_monitor_node_ready` | gauge (0/1) | `node` | 노드 Ready — free>0 이어도 0 이면 스케줄 불가 |
| `gpu_monitor_node_info` | gauge (상수 1) | `node`, `product`, `product_raw` | 축약 제품명 ↔ GFD 원문 라벨 매핑 |
| `gpu_monitor_node_collect_error` | gauge (0/1) | `node`, `product` | 노드 Pod 조회 실패 — 1 이면 그 노드 allocation 이 과소집계 중 |
| `gpu_monitor_gpu_allocated_by_type` | gauge | `type` | 워크로드 타입별 할당 |
| `gpu_monitor_gpu_allocated_by_namespace` | gauge | `namespace` | 네임스페이스별 할당 |
| `gpu_monitor_gpu_allocated_by_ready` | gauge | `ready` (`true`/`false`) | Pod ready 별 할당 — false = 점유만 하고 아직 안 뜬 GPU |
| `gpu_monitor_collect_errors` | gauge | – | 스냅샷 수준 수집 오류 수 — RBAC 403 등. `readyz` 는 이때도 200 이므로 이 메트릭이 유일한 신호 |
| `gpu_monitor_k8s_enabled` / `gpu_monitor_demo` | gauge (0/1) | – | k8s 클라이언트 활성 / 데모 모드 |
| `gpu_monitor_up` | gauge (0/1) | – | 스냅샷 존재 (0 = 첫 수집 전) |
| `gpu_monitor_last_success_timestamp_seconds` | gauge (epoch) | – | 마지막 성공 수집 — 성공 이력 없으면 미방출 |
| `gpu_monitor_refreshes_total` / `gpu_monitor_refresh_failures_total` | counter | – | refresh 시도(heartbeat) / 예상외 예외 실패 |

- 알럿 룰 예시 10종: [deploy/prometheus-alerts.yaml](deploy/prometheus-alerts.yaml) ·
  Grafana 대시보드: [deploy/grafana-dashboard.json](deploy/grafana-dashboard.json) (import 해서 사용)
- `failures_total`/`last_success` 는 **예상외 예외·루프 정지 전용** 신호 — 통상 수집 실패
  (RBAC, 노드 조회 실패)는 예외 없이 흡수되므로 `collect_errors`/`node_collect_error` 로 잡는다.
- 네임스페이스·노드·워크로드명이 라벨로 노출된다 — `/metrics` 는 무인증이므로 외부 노출 시 주의(배포 절 참고).

## 필요한 RBAC

읽기 전용, cluster-scope (컬렉션 조회만 하므로 `list` 만 필요):

- `nodes`: `list`
- `pods`: `list` (전 네임스페이스 — `fieldSelector=spec.nodeName` 로 노드별 조회)

## 배포 (in-cluster)

`deploy/` 에 매니페스트가 있다:

```bash
./ci.sh && ./push.sh                  # 이미지 빌드 -> 사내 레지스트리 push (product 라인)
# develop/feature 라인은 :latest 를 덮지 않게 반드시 BRANCH= 를 넘긴다:
#   BRANCH=develop ./ci.sh && BRANCH=develop ./push.sh
kubectl apply -f deploy/k8s.yaml      # Namespace/SA/ClusterRole(+Binding)/Deployment/Service/Ingress
kubectl apply -f deploy/podmonitor.yaml          # (선택) PodMonitor — Prometheus Operator 있을 때만
kubectl apply -f deploy/prometheus-alerts.yaml   # (선택) PrometheusRule (알럿 10종)
# Grafana: deploy/grafana-dashboard.json 을 대시보드로 import
```

- 위 RBAC(nodes/pods `list`)은 `deploy/k8s.yaml` 의 ClusterRole 로 함께 배포된다. 없으면 앱이
  토큰은 있어도 조회가 403 → 에러 없이 **빈 데이터**로 뜨니(readyz 는 200 유지) 반드시 적용.
- 컨테이너는 포트 **8089 고정**(Dockerfile ENTRYPOINT) — `MONITOR_PORT` env 는 컨테이너에선
  무시된다. Service/probe 도 8089 기준.
- `PodMonitor` 는 `monitoring.coreos.com` CRD 의존이라 core 매니페스트에서 분리했다 — Operator
  없이 `k8s.yaml` 만 적용해도 실패하지 않는다.
- 이미지 태그 주의: `:latest` 는 **product 라인 전용**이다. develop/feature 빌드를 배포하려면
  `deploy/k8s.yaml` 의 `image` 를 그 브랜치 태그(`:<version>-<branch>`)로 바꿀 것.
- `/metrics`·`/api/snapshot` 은 현재 **무인증**이다(형제 프로젝트의 metrics 토큰 인증 미이식).
  Ingress 로 외부 노출하면 이 경로들도 공개되니, 민감하면 경로 제한/인증 프록시/내부 전용으로 둘 것.
- MIG / time-slicing 노드 대응은 아직 없다 — [docs/gpu-sharing-plan.md](docs/gpu-sharing-plan.md) 참고.

## 테스트

```bash
python3 -m unittest -v            # stdlib unittest, FastAPI 불필요
```

## 설정 (env)

| env | 기본 | 의미 |
|---|---|---|
| `MONITOR_HOST` | `0.0.0.0` | 바인드 주소 |
| `MONITOR_PORT` | `8089` | 포트 |
| `MONITOR_INTERVAL` | `15` | 백그라운드 수집 주기(초) |
| `MONITOR_DEMO` | `false` | 샘플 데이터 미리보기 |
| `MONITOR_NODE_SELECTOR` | – | GPU 노드 필터 라벨셀렉터(예: `nvidia.com/gpu.present=true`) |
| `MONITOR_METRICS` | `true` | `/metrics` on/off |
| `MONITOR_ROOT_PATH` | – | 리버스 프록시 프리픽스(예: `/service/gpu-monitor`) — 직접 접근 시엔 비울 것 |
| `MONITOR_CONFIG_FILE` | – | 설정 파일(`.json`, PyYAML 있으면 `.yaml`) |
| `MONITOR_K8S_*` | – | api_server / token_file / ca_file / insecure / timeout |

## 이미지

```bash
./ci.sh        # docker build -> ai-tool/gpu-monitor:<version> + :latest (product 라인 전용)
./push.sh      # retag -> 10.92.20.77:5002 로 push

# develop 등 다른 브랜치 라인: <version>-<branch> + :<branch> 로 갈려 :latest 를 덮지 않음
BRANCH=develop ./ci.sh && BRANCH=develop ./push.sh
```
