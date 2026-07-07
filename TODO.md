# TODO — 메트릭 & Grafana 대시보드 로드맵

`gpu-monitor` 관측 표면(`/metrics`, Grafana, 알럿) 확장 계획. 형제 프로젝트 `model-monitor` 패리티를 참고하되 **축을 번안**한다(model → node/GPU/workload).

> **경계 재확인 — 할당(allocation) 전용, DCGM 아님.** 아래 모든 메트릭/패널은 `nvidia.com/gpu` **할당 수**(capacity/allocatable/allocated/free)와 노드·Pod 메타(ready/phase/error)만 근거한다. 실사용률(GPU %, VRAM, 온도, power)은 **스코프 밖 — dcgm-exporter + Grafana** 영역이며 여기서 절대 방출하지 않는다.

---

## 1. 현재 노출 메트릭 (as-is)

`render_prometheus_metrics(snap)` 가 내는 것 전부. **6개 계열, 모두 gauge, counter 없음.**

| 메트릭 | 타입 | 라벨 | 근거 |
|---|---|---|---|
| `gpu_monitor_build_info` | gauge (상수 1) | `version` | prometheus.py:15-17 ← `app.__version__` |
| `gpu_monitor_cluster_gpu_capacity` | gauge | (없음) | prometheus.py:19-24 ← `summary.gpu_capacity` (snapshot.py:56) |
| `gpu_monitor_cluster_gpu_allocated` | gauge | (없음) | ← `summary.gpu_allocated` (snapshot.py:57) |
| `gpu_monitor_cluster_gpu_free` | gauge | (없음) | ← `summary.gpu_free` (snapshot.py:58) |
| `gpu_monitor_node_gpu` | gauge | `node`, `product`, `state` (**capacity/allocated/free 만**) | prometheus.py:26-36 ← node dict. `None` 값은 스킵(prometheus.py:33) |
| `gpu_monitor_gpu_allocated_by_type` | gauge | `type` | prometheus.py:38-41 ← `summary.by_workload_type` (snapshot.py:64-66) |

**즉시 눈에 띄는 공백:** `allocatable`(node dict 에 있으나 `state` 에 없음), 노드 `ready`, 노드/클러스터 `error`, `k8s_enabled`, namespace 축, 스냅샷 신선도, 수집기 heartbeat 이 전부 미방출.

---

## 2. 추가 수집·노출 가능한 할당 메트릭 (P1/P2/P3)

스냅샷 dict 에 **이미 존재하는 필드**만 노출한다(수집기 K8s 호출 추가 없음). `render` 확장 ± `summarize` 집계 몇 줄이면 된다.

### P1 — 데이터 이미 있음, 변경 최소, 가치 높음

- **`gpu_monitor_node_gpu{state="allocatable"}`** — gauge · 라벨 `node, product, state`
  - 근거: `node["allocatable"]` (gpu.py:56, collect.py:32). prometheus.py:31 의 state 튜플에 `"allocatable"` 한 개 추가.
  - 이유: `free = allocatable − allocated`(collect.py:70-71) 의 분모를 노출해야 `capacity ≠ allocatable`(디바이스 플러그인 미기동/예약) 판별·free 검증이 된다. `None` 가드는 그대로 둔다.

- **`gpu_monitor_node_ready`** — gauge (0/1) · 라벨 `node`
  - 근거: `node["ready"]` = `node_ready()` (gpu.py:64-68, collect.py:29, demo.py:11). prometheus.py:28 노드 루프에 한 줄.
  - 이유: NotReady 노드의 capacity 는 스케줄 불가 → `free` 로 잡혀도 실제로 못 쓴다. `gpu_monitor_node_gpu{state="free"}>0 and gpu_monitor_node_ready==0` 로 "빈 GPU 인데 죽은 노드" 를 구분. **가장 가치 높은 신규 헬스 메트릭.**

- **`gpu_monitor_gpu_allocated_by_namespace`** — gauge · 라벨 `namespace` (값 = 해당 ns 할당 GPU 합)
  - 근거: `allocation.namespace` (collect.py:61). `summarize`(snapshot.py:44-67)에 `by_namespace` 집계 추가.
  - 이유: 팀/네임스페이스별 점유(간이 chargeback)는 할당 대시보드의 핵심 질문인데 현재 축이 없다. namespace 는 pod 명과 달리 카디널리티가 안정적이라 장기 보존 안전.

### P2 — 소규모 집계/루프 추가

- **`gpu_monitor_gpu_allocated_by_ready`** — gauge · 라벨 `ready` (0/1 로 분해한 할당 GPU 합)
  - 근거: `allocation.ready` = `pod_ready()` (gpu.py:31-39, collect.py:67). `summarize` 에 ready 축 추가.
  - 이유: `ready="false"` = 점유만 하고 아직 안 뜬 GPU(Pending/기동 중/좀비 점유). 여전히 할당 경계 내(phase/condition, 사용률 아님). 기동 중 not-ready 는 정상이라 알럿은 `for:` 지연 필요.

- **`gpu_monitor_nodes`** — gauge · 라벨 (없음)
  - 근거: `summary.node_count` (snapshot.py:49). `count(gpu_monitor_node_gpu{state="capacity"})` 로 파생 가능하나 pre-aggregated 값을 저렴하게 노출.
  - 이유: GPU 노드 급락(스케일 인/라벨셀렉터 미스매치로 0개) 직접 감시.

- **`gpu_monitor_node_info`** — gauge (상수 1) · 라벨 `node, product, product_raw`
  - 근거: `node["product_raw"]`(예 `NVIDIA-H100-80GB-HBM3`, collect.py:30, gpu.py:58-61). `build_info` 식 info-metric.
  - 이유: short `H100` ↔ GFD 원문 라벨 매핑을 노출해 다른 메트릭과 `node` 조인 시 세대/모델 필터링.

### P3 — 카디널리티/파생 트레이드오프 있음(선택)

- **`gpu_monitor_workload_gpu`** — gauge · 라벨 `node, namespace, workload, workload_type` (값 = `a.gpu`)
  - 근거: allocation dict (collect.py:61-68). prometheus.py 에 allocation 순회 추가.
  - 이유: "어떤 워크로드가 어느 노드에서 몇 GPU" 를 PromQL 로 직접 슬라이스. **주의:** pod 명(collect.py:62)은 Job/replica 재생성마다 시리즈 폭증 → **라벨에서 제외**하고 `workload`(안정 이름)로 집약. `model-monitor` 가 api_base 내부 URL 을 라벨에서 뺀 절제와 동일.

- **`gpu_monitor_product_gpu`** — gauge · 라벨 `product, state`(capacity/allocated/free) — **편의용, 파생 가능**
  - 근거: `summary.products` (snapshot.py:59-63)에 이미 집계됨.
  - 이유: `sum by(product,state)(gpu_monitor_node_gpu)` 로 유도 가능하므로 신규 시리즈보다 recording rule/대시보드 파생을 우선 고려. 쿼리 단순화가 필요할 때만 노출.

### 스코프 밖 (도입하지 않음) — dcgm-exporter 영역

GPU 사용률(%), VRAM 사용량, 온도, power/클럭. 스냅샷에 원천 데이터가 없고 CLAUDE.md 경계를 넘는다. 필요 시 dcgm-exporter + Grafana 로 별도 대시보드.

---

## 3. Grafana 대시보드 패널 설계

신규 `deploy/grafana-dashboard.json` — `model-monitor` 구조(row 4단, 어노테이션 오버레이, refresh 30s, shared tooltip)를 미러링. 템플릿 변수: `label_values(gpu_monitor_node_gpu, node)`, `label_values(gpu_monitor_node_gpu, product)`, (신규 도입 시) `label_values(gpu_monitor_gpu_allocated_by_namespace, namespace)`. 패널 쿼리에 `{node=~"$node", product=~"$product"}` 필터.

> **할당률(%)은 사용률이 아니다** — `allocated/capacity`. **임계 방향이 model-monitor 와 반대**: 높을수록 위험(스케줄 headroom 소진). thresholds 를 그대로 복사하지 말고 free 소진 관점으로 뒤집을 것(예 70/90).

### 클러스터 개요

| 패널 | 타입 | PromQL | 필요 메트릭 |
|---|---|---|---|
| 총 GPU 용량 | stat | `gpu_monitor_cluster_gpu_capacity` | 기존 |
| 할당된 GPU | stat | `gpu_monitor_cluster_gpu_allocated` | 기존 |
| 유휴 GPU | stat (≤0 red) | `gpu_monitor_cluster_gpu_free` | 기존 |
| 클러스터 할당률 | gauge (70/90) | `100 * gpu_monitor_cluster_gpu_allocated / clamp_min(gpu_monitor_cluster_gpu_capacity, 1)` | 기존 |
| GPU 노드 수 | stat | `gpu_monitor_nodes` (없으면 `count(gpu_monitor_node_gpu{state="capacity"})`) | P2 / 파생 |
| 할당·유휴·용량 추이 | timeseries | `gpu_monitor_cluster_gpu_capacity` / `_allocated` / `_free` | 기존 (Prometheus 스크레이프로 시계열 축적) |
| 모니터 버전 | stat (`{{version}}`) | `max by(version)(gpu_monitor_build_info)` | 기존 |

### 장치(제품)별

| 패널 | 타입 | PromQL | 필요 메트릭 |
|---|---|---|---|
| 제품별 용량 분포 | piechart(donut) | `sum by(product)(gpu_monitor_node_gpu{state="capacity"})` | 기존 |
| 제품별 할당/유휴 | bargauge | `sum by(product)(gpu_monitor_node_gpu{state=~"allocated\|free"})` | 기존 |
| 제품별 할당률 | bargauge (70/90) | `100 * sum by(product)(gpu_monitor_node_gpu{state="allocated"}) / clamp_min(sum by(product)(gpu_monitor_node_gpu{state="capacity"}), 1)` | 기존 |

### 워크로드 타입 / 네임스페이스별

| 패널 | 타입 | PromQL | 필요 메트릭 |
|---|---|---|---|
| 타입별 할당 분포 | piechart | `gpu_monitor_gpu_allocated_by_type` | 기존 |
| 타입별 할당 순위 | bargauge | `sort_desc(gpu_monitor_gpu_allocated_by_type)` | 기존 |
| 타입별 할당 추이 | timeseries(stacked) | `gpu_monitor_gpu_allocated_by_type` | 기존 |
| 네임스페이스별 할당 | bargauge | `sort_desc(gpu_monitor_gpu_allocated_by_namespace)` | **P1 신규** |
| (선택) 워크로드별 점유 | bargauge / table | `gpu_monitor_workload_gpu` (legend `{{workload}} ({{namespace}})`) | **P3 신규** |

### 노드별

| 패널 | 타입 | PromQL | 필요 메트릭 |
|---|---|---|---|
| 노드별 할당 현황 | table | `gpu_monitor_node_gpu` + Grafana transform *Labels to fields*(state → 컬럼 피벗, node/product → 행) | 기존 (PromQL 만으로 피벗 불가) |
| 노드별 할당률 | bargauge | `100 * gpu_monitor_node_gpu{state="allocated"} / ignoring(state) clamp_min(gpu_monitor_node_gpu{state="capacity"}, 1)` | 기존 (`ignoring(state)` 로 라벨 정렬) |
| 노드별 유휴 GPU | bargauge (`{{node}} ({{product}})`) | `gpu_monitor_node_gpu{state="free"}` | 기존 |
| 노드 할당률 타임라인 | state-timeline (y=node) | 위 노드별 할당률 식 | 기존 (진짜 heatmap 대신 state-timeline 권장) |

### 상태·이상 징후

| 패널 | 타입 | PromQL | 필요 메트릭 |
|---|---|---|---|
| 만석(Full) 노드 수 | stat | `count(gpu_monitor_node_gpu{state="free"} == 0)` | 기존 (파생) |
| NotReady GPU 노드 수 | stat | `count(gpu_monitor_node_ready == 0)` | **P1 신규** |
| NotReady 손실 용량 | stat | `sum(gpu_monitor_node_gpu{state="capacity"} and on(node) (gpu_monitor_node_ready == 0))` | **P1 신규** 의존 |
| Not-Ready Pod 점유 GPU | stat | `gpu_monitor_gpu_allocated_by_ready{ready="false"}` | **P2 신규** |
| NotReady 노드 위 할당(anomaly) | stat | `(gpu_monitor_node_ready == 0) and on(node) (sum by(node)(gpu_monitor_node_gpu{state="allocated"}) > 0)` | **P1 신규** |

수집 헬스 패널(수집 상태/오류 수/신선도)은 4절 관측성 메트릭 참조.

---

## 4. 관측성(self-observability) 메트릭 + staleness 알럿

수집기 자기관측. **모두 이미 캐시된 `store`/`refresher` 상태만 읽어 방출** — 스크레이프 경로에서 새 K8s 호출 없음(CLAUDE.md 준수). 이름은 `model-monitor` 형제와 정렬.

**staleness 이중 신호가 핵심:** `_refresh_once` 는 예외를 조용히 삼키고 옛 스냅샷을 그대로 둔다(state.py:35). `up==1` 인 채로 대시보드가 몇 시간이고 낡을 수 있다 → `up`(스냅샷 존재 이진)과 `last_success_timestamp`(무음 정체)를 **둘 다** 둬야 "떴지만 멈춘" 상태를 잡는다.

| 메트릭 | 타입 | 라벨 | 근거 / 배선 |
|---|---|---|---|
| `gpu_monitor_up` | gauge (0/1) | (없음) | `store.get() is not None` (state.py:14 초기 `None`). routes.py 의 `_snap` 은 빈 store 에 합성 dict(`ts=None`)를 돌려주므로 snap 만으로는 구분 불가 — **meta 로 넘겨야 함** |
| `gpu_monitor_last_success_timestamp_seconds` | gauge (epoch) | (없음) | `snap["ts"]` 는 문자열 포맷(snapshot.py:20)이라 epoch 불가·실패 사이 미보존. `state.py` 에 `import time` → 성공 분기(state.py:33-34)에서 `store.last_success_epoch = time.time()`, **except 분기(state.py:35)에선 갱신 금지**(나이가 계속 증가) |
| `gpu_monitor_collect_errors` | gauge | (없음) | `len(snap["errors"] or [])` (snapshot.py:21,26-28,32; nodes 조회 실패 collect.py:19-20). **snap 만으로 방출**(상태 변경 불필요) |
| `gpu_monitor_node_collect_error` | gauge (0/1) | `node, product` | 노드 루프에서 `1 if n.get("error") else 0`. 근거: pods 조회 실패 시 `node["error"]`(collect.py:46-48) + per-node 예외(snapshot.py:36-37). 이때 그 노드의 allocated=0/free=None 으로 남아 조용히 과소집계 → 어느 노드인지 지목. 카디널리티 GPU 노드 수 한정, 안전 |
| `gpu_monitor_k8s_enabled` | gauge (0/1) | (없음) | `snap.get("k8s_enabled")` (snapshot.py:24-25, k8s.py 토큰 없으면 client `None`). snap 만으로 방출 |
| `gpu_monitor_refreshes_total` | **counter** | (없음) | `Refresher.refreshes` 카운터(state.py:24-29 `__init__`, `_refresh_once` 진입 state.py:31 에서 +1). heartbeat + 실패율 분모. **meta 로 전달** |
| `gpu_monitor_refresh_failures_total` | **counter** | (없음) | `Refresher.failures`(except 분기 state.py:35 에서 +1). `rate()` 로 지속 실패 감시. meta 로 전달 |

> 이 두 counter 가 유일한 counter 제안이다. 나머지는 전부 gauge — 스냅샷은 "현재 상태" 만 보관하고(Refresher 가 매 interval 통째 교체) 누적 이벤트 원천이 없다.
> `last_success_epoch` 는 반드시 `store`/`refresher` 에 두고 실패 사이클에서 갱신하지 말 것. `build_snapshot` 안(snap)에 넣으면 k8s 비활성/부분실패 스냅샷도 매번 새 timestamp 를 찍어 정체를 가린다(snapshot.py:26-29).

### 코드 변경 표면

- **`app/services/prometheus.py`** — 시그니처 `render_prometheus_metrics(snap, meta=None)` 로 확장. **`meta` 는 필수 optional**: 기존 호출부 routes.py:43 와 test_gpu_monitor.py:219 가 인자 1개로 호출. `collect_errors`/`node_collect_error`/`k8s_enabled`/`node_ready`/`node_gpu{state="allocatable"}`/`by_namespace`/`by_ready`/`nodes`/`node_info` 는 snap 만으로, `up`/`last_success_timestamp`/counters 는 meta 로 방출.
- **`app/services/snapshot.py` (`summarize`)** — `by_namespace`, `by_ready` 집계 추가(products 는 이미 있음).
- **`app/services/state.py`** — `import time`; `SnapshotStore.last_success_epoch`; `Refresher.failures`/`refreshes`.
- **`app/api/routes.py` (`/metrics`, routes.py:38-44)** — `store`/`refresher`(app.state 배선 main.py:40)에서 `meta = {up, last_success_epoch, failures, refreshes}` 구성해 `render` 에 전달.

### PrometheusRule 알럿 (기존 `deploy/prometheus-alerts.yaml` 확장)

interval 기본 15s(config.py:28, state.py:27) 기준. `GpuMonitorDown`·`ClusterGpuExhausted`
(= 아래 `GpuClusterNoFreeCapacity`)·`ClusterGpuHighAllocation` 은 이미 존재 — 신규 관측
메트릭 도입 후 아래처럼 보강/추가한다.

- `GpuMonitorDown`: `up{job="gpu-monitor"} == 0 or gpu_monitor_up == 0` **for 2m** — 스냅샷 없음(기동 지연/Refresher 미기동).
- `GpuMonitorStale`: `time() - gpu_monitor_last_success_timestamp_seconds > 3 * 15` **for 5m** — Refresher 예외 지속(무음 정체).
- `GpuRefresherStalled`: `increase(gpu_monitor_refreshes_total[5m]) == 0 and gpu_monitor_up == 1` — 루프 정지.
- `GpuRefreshFailing`: `rate(gpu_monitor_refresh_failures_total[10m]) > 0` **for 10m**.
- `GpuMonitorK8sDisabled`: `gpu_monitor_k8s_enabled == 0 and gpu_monitor_up == 1` — SA 토큰 없음/클러스터 밖 실행(DEMO 아님).
- `GpuMonitorCollectErrors`: `gpu_monitor_collect_errors > 0` **for 5m** — 노드 목록 수집 실패(RBAC/도달성) → 총량 과소.
- `GpuNodeCollectError`: `max by(node)(gpu_monitor_node_collect_error) > 0` **for 5m** — 해당 노드 allocation 부정확.
- `GpuClusterNoFreeCapacity`: `gpu_monitor_cluster_gpu_free == 0` **for 10m** — 스케줄 headroom 소진(model-monitor `CapacityDegraded` 의 할당판 **반전**).
- `GpuAllocationOnNotReadyNode`: `(gpu_monitor_node_ready == 0) and on(node) (sum by(node)(gpu_monitor_node_gpu{state="allocated"}) > 0)` **for 10m** — booked-but-unschedulable anomaly.

대시보드 어노테이션: `ALERTS{alertname=~"Gpu.*|Node.*", alertstate="firing"}` 오버레이. 실사용률 알럿은 도입하지 않음.

---

## 5. 산출물 TODO 체크리스트

우선순위: **[P1]** 데이터 있음·변경 최소·가치 높음 → **[P2]** 소규모 집계 → **[P3]** 카디널리티/파생 트레이드오프.

### 코드 — 할당 메트릭
- [ ] **[P1]** `prometheus.py` node 루프 state 튜플에 `"allocatable"` 추가 → `gpu_monitor_node_gpu{state="allocatable"}` (`None` 가드 유지)
- [ ] **[P1]** `prometheus.py` node 루프에서 `gpu_monitor_node_ready{node}` (0/1) 방출
- [ ] **[P1]** `summarize` 에 `by_namespace` 집계 추가 + `prometheus.py` `gpu_monitor_gpu_allocated_by_namespace{namespace}` 방출
- [ ] **[P2]** `summarize` 에 `by_ready` 집계 추가 + `gpu_monitor_gpu_allocated_by_ready{ready}` 방출
- [ ] **[P2]** `gpu_monitor_nodes` (summary.node_count) + `gpu_monitor_node_info{node,product,product_raw}` 방출
- [ ] **[P3]** `gpu_monitor_workload_gpu{node,namespace,workload,workload_type}` — **pod 명 라벨 제외**(카디널리티)
- [ ] **[P3]** (선택) `gpu_monitor_product_gpu{product,state}` — 파생 가능하므로 recording rule 우선 검토

### 코드 — 관측성 메트릭 (model-monitor 이식)
- [ ] **[P1]** `render_prometheus_metrics(snap, meta=None)` 로 시그니처 확장 (routes.py:43·test:219 하위호환 위해 `meta` optional 필수)
- [ ] **[P1]** `prometheus.py` snap-only 방출: `gpu_monitor_collect_errors`, `gpu_monitor_node_collect_error{node,product}`, `gpu_monitor_k8s_enabled`
- [ ] **[P1]** `/metrics` 핸들러(routes.py:38-44)에서 `meta` 구성 + `gpu_monitor_up` 방출
- [ ] **[P2]** `state.py`: `import time`, `SnapshotStore.last_success_epoch`(성공 분기만 갱신) → `gpu_monitor_last_success_timestamp_seconds`
- [ ] **[P3]** `state.py`: `Refresher.refreshes`/`failures` 카운터 → `gpu_monitor_refreshes_total`/`gpu_monitor_refresh_failures_total` (counter)

### 배포물
- [ ] **[P1]** `deploy/grafana-dashboard.json` 신규 — row 4단(개요/장치/워크로드·ns/노드/상태), 어노테이션 오버레이, 템플릿 변수(node/product/namespace), 기존 메트릭만으로 되는 패널 우선
- [ ] **[P2]** `deploy/prometheus-alerts.yaml` 확장 — 위 알럿 세트 추가(기존 3종은 신규 메트릭으로 보강, 임계 방향 반전 주의, not-ready 는 `for:` 지연)

### 테스트 (CLAUDE.md: 파싱/집계/분류 변경 시 회귀 테스트 필수)
- [ ] **[P1]** `FakeClient` 패턴으로 `summarize` 신규 필드(`by_namespace`, `by_ready`) 회귀 테스트
- [ ] **[P1]** `render_prometheus_metrics` 신규 라인 + `meta=None` 하위호환 테스트(1-인자 호출 유지 확인)
- [ ] **[P2]** NotReady 노드·per-node error 스냅샷에서 `node_ready`/`node_collect_error` 방출 검증

### 문서
- [ ] **[P2]** README/CLAUDE.md 의 노출 메트릭 목록을 신규 메트릭으로 갱신 (할당 경계 문구 유지)
