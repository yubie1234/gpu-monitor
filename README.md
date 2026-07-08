# gpu-monitor `v0.4.0`

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
- **사용 목적·환경 축**: 같은 할당 GPU 를 **용도**(serving/training/interactive/batch/system)와
  **배포 환경**(prod/staging/dev)으로도 재집계. Pod 라벨 기반이며 `gpu-monitor.io/purpose`·
  `gpu-monitor.io/environment` 라벨로 직접 지정(override) 가능. 할당(allocation) 경계는 그대로
- **파티션 GPU (공유·MIG)**: 물리 GPU 1장을 쪼갠 `nvidia.com/gpu.<프로파일>`(타임슬라이스/MPS)
  또는 `nvidia.com/mig-<프로파일>`(MIG)을 **슬롯/인스턴스 단위로 별도** 표시. "물리 8장 중 1장이
  분할"까지 재구성 (아래 [파티션 GPU](#파티션-gpu--공유타임슬라이스mps--mig) 참고)
- **클러스터 집계**: 총/할당/유휴 GPU, 장치별·워크로드 타입별·**사용 목적별**·**환경별** 분포
- **노드 필터·정렬**: 대시보드에서 검색(노드/워크로드/네임스페이스)·장치 필터·정렬(이름/유휴/할당률)·
  "유휴 노드만" 토글로 배치 가능한 노드를 빠르게 추린다(클라이언트 측, 재수집 없음)
- **수집 실패 노드는 '미상'으로 격리**: Pod 조회에 실패한 노드(RBAC 403 등)는 할당/유휴가 미상이라
  capacity 를 유휴로 착시시키지 않고 별도 표기 — 잘못된 여유 GPU 판단을 막는다
- **정체(stale) 감지**: 스냅샷이 갱신되지 않으면(백그라운드 수집 지연) 상대시간과 함께 ⚠ 표시
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

- `/` — 대시보드(HTML). `MONITOR_GRAFANA_URL` 설정 시 헤더에 📈 Grafana 딥링크 노출(히스토리·추세용)
- `/api/snapshot` — 노드별 GPU 할당 JSON
- `/snapshot.json` — 다운로드
- `/metrics` — Prometheus
- `/healthz`, `/readyz`

## 메트릭

`/metrics` (Prometheus text 0.0.4). **전부 할당·수집 헬스 지표다 — 사용률(DCGM) 아님.**

| 계열 | 타입 | 라벨 | 의미 |
|---|---|---|---|
| `gpu_monitor_build_info` | gauge (상수 1) | `version` | 빌드 정보 |
| `gpu_monitor_cluster_gpu_capacity` / `_allocated` / `_free` | gauge | – | 클러스터 **온전(whole)** GPU 총/할당/유휴 (`nvidia.com/gpu`) |
| `gpu_monitor_cluster_gpu_physical` | gauge | – | 클러스터 **물리** GPU 총수 (`nvidia.com/gpu.count` 합) — 공유로 빠진 장수 포함 |
| `gpu_monitor_cluster_gpu_unknown` | gauge | – | Pod 조회 실패 노드의 온전 GPU — 할당/유휴가 **미상**이라 `_free` 에서 제외(유휴 착시 방지) |
| `gpu_monitor_cluster_shared_slots` | gauge | `state` (capacity/allocated/free) | 클러스터 **공유 슬롯** — 타임슬라이스/MPS. **물리 장수 아님**(1 슬롯 ≠ 1장) |
| `gpu_monitor_nodes` | gauge | – | GPU 노드 수 |
| `gpu_monitor_node_gpu` | gauge | `node`, `product`, `state` | 노드별 온전 GPU — state=capacity/allocatable/allocated/free (값 `None` 이면 라인 생략) |
| `gpu_monitor_node_physical` | gauge | `node`, `product` | 노드 물리 GPU 장수 (`nvidia.com/gpu.count`; 라벨 없으면 라인 생략) |
| `gpu_monitor_node_shared_backing` | gauge | `node`, `product` | 그 노드에서 파티션(공유/MIG)으로 빠진 물리 장수 (= physical − whole) |
| `gpu_monitor_node_shared` | gauge | `node`, `product`, `resource`, `mode`, `state` | 노드 파티션 풀 슬롯/인스턴스 — resource=`nvidia.com/gpu.10gb`·`nvidia.com/mig-1g.10gb` 등, mode=timeslice/mps/mig, state=capacity/allocatable/allocated/free |
| `gpu_monitor_node_ready` | gauge (0/1) | `node` | 노드 Ready — free>0 이어도 0 이면 스케줄 불가 |
| `gpu_monitor_node_info` | gauge (상수 1) | `node`, `product`, `product_raw` | 축약 제품명 ↔ GFD 원문 라벨 매핑 |
| `gpu_monitor_node_collect_error` | gauge (0/1) | `node`, `product` | 노드 Pod 조회 실패 — 1 이면 그 노드 allocation 이 과소집계 중 |
| `gpu_monitor_gpu_allocated_by_type` | gauge | `type` | 워크로드 타입별 할당 |
| `gpu_monitor_gpu_allocated_by_namespace` | gauge | `namespace` | 네임스페이스별 할당 |
| `gpu_monitor_gpu_allocated_by_purpose` | gauge | `purpose` | 사용 목적별 할당 (serving/training/interactive/batch/system) |
| `gpu_monitor_gpu_allocated_by_environment` | gauge | `environment` | 배포 환경별 할당 (prod/staging/dev) |
| `gpu_monitor_gpu_allocated_by_ready` | gauge | `ready` (`true`/`false`) | Pod ready 별 할당 — false = 점유만 하고 아직 안 뜬 GPU |
| `gpu_monitor_collect_errors` | gauge | – | 스냅샷 수준 수집 오류 수 — RBAC 403 등. `readyz` 는 이때도 200 이므로 이 메트릭이 유일한 신호 |
| `gpu_monitor_k8s_enabled` / `gpu_monitor_demo` | gauge (0/1) | – | k8s 클라이언트 활성 / 데모 모드 |
| `gpu_monitor_up` | gauge (0/1) | – | 스냅샷 존재 (0 = 첫 수집 전) |
| `gpu_monitor_last_success_timestamp_seconds` | gauge (epoch) | – | 마지막 성공 수집 — 성공 이력 없으면 미방출 |
| `gpu_monitor_refreshes_total` / `gpu_monitor_refresh_failures_total` | counter | – | refresh 시도(heartbeat) / 예상외 예외 실패 |

- 알럿 룰 예시 11종: [deploy/prometheus-alerts.yaml](deploy/prometheus-alerts.yaml) ·
  Grafana 대시보드: [deploy/grafana-dashboard.json](deploy/grafana-dashboard.json) (import 해서 사용)
- `failures_total`/`last_success` 는 **예상외 예외·루프 정지 전용** 신호 — 통상 수집 실패
  (RBAC, 노드 조회 실패)는 예외 없이 흡수되므로 `collect_errors`/`node_collect_error` 로 잡는다.
- 네임스페이스·노드·GPU 제품명이 라벨로 노출된다 — `/metrics` 는 무인증이므로 외부 노출 시 주의.
  워크로드·Pod 명은 라벨엔 없지만 무인증 `/api/snapshot` 에 노출된다(배포 절 참고).

## 파티션 GPU — 공유(타임슬라이스/MPS) · MIG

한 노드가 물리 GPU 일부를 쪼개 쓰면 K8s 는 온전 GPU 와 **다른 리소스명**으로 하위 단위를
광고한다. 하위 단위는 물리 장수와 **단위가 다르다**(1 슬롯/인스턴스 ≠ 1장) — 합치지 않고
**따로** 집계하고, 각 풀에 `mode` 를 붙인다.

| mode | 리소스명 | 격리 | 비고 |
|---|---|---|---|
| `timeslice` | `nvidia.com/gpu.10gb`, `...-ts` | **없음**(전체 VRAM 공유) | 이름의 GB 는 라벨일 뿐 |
| `mps` | `nvidia.com/gpu.10gb-mps` | 부분 | MPS 병렬 |
| `mig` | `nvidia.com/mig-1g.10gb` | **하드웨어**(GB 보장) | 실제 파티션 (`mig.strategy=mixed`) |

```
Capacity:  nvidia.com/gpu: 7   nvidia.com/gpu.10gb: 8
Labels:    nvidia.com/gpu.count: 8   nvidia.com/gpu.replicas: 8
           nvidia.com/gpu.sharing-strategy: time-slicing
        →  물리 8 − 온전 7 = 1장이 분할로 빠짐 → .10gb 8슬롯 (= 1장 × replicas 8)
```

- **물리 장수**는 라벨 `nvidia.com/gpu.count`. **분할로 빠진 장수** = `count − nvidia.com/gpu`
  (추정 아님, 산수). 라벨 없으면 물리 = 온전 capacity 로 폴백.
- 온전 GPU 집계(`gpu_capacity`/`by_workload_type`/…)는 **그대로**라 기존 Grafana·알럿 불변.
  파티션은 `*_physical` / `*_shared*` / `cluster_shared_slots` **신규 계열**로만 노출
  (`node_shared` 는 `mode` 라벨로 timeslice/mps/mig 구분).
- 대시보드: 노드 헤더 `물리 8 · 온전 7 · 분할 1장`, 물리 도트에서 분할된 장은 **parent 링**
  으로 남고 바로 아래 `└→` 로 그 슬롯/인스턴스에 연결(1장의 '행방'). 풀마다 **모드 배지**
  (TS/MPS/MIG), MIG 셀은 실선(격리)·TS/MPS 는 점선(소프트).
- `mig.strategy=single` 은 인스턴스가 `nvidia.com/gpu` 로 나와 온전처럼 처리된다(별도 작업 없음).
- **실사용률은 여전히 다루지 않는다** — 타임슬라이스 `.10gb` 는 이름표일 뿐 강제 한도가
  아니고, 실제 %/VRAM 은 DCGM 영역.

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
kubectl apply -f deploy/prometheus-alerts.yaml   # (선택) PrometheusRule (알럿 11종)
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
| `MONITOR_GRAFANA_URL` | – | 대시보드 헤더 📈 Grafana 딥링크(외부 절대 URL). 히스토리·추세는 Grafana 담당 — 비우면 링크 숨김 |
| `MONITOR_CONFIG_FILE` | – | 설정 파일(`.json`, PyYAML 있으면 `.yaml`) |
| `MONITOR_K8S_*` | – | api_server / token_file / ca_file / insecure / timeout |

## 이미지

```bash
./ci.sh        # docker build -> ai-tool/gpu-monitor:<version> + :latest (product 라인 전용)
./push.sh      # retag -> 10.92.20.77:5002 로 push

# develop 등 다른 브랜치 라인: <version>-<branch> + :<branch> 로 갈려 :latest 를 덮지 않음
BRANCH=develop ./ci.sh && BRANCH=develop ./push.sh
```
