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

> **P1/P2 완료(feature/metrics-observability):** `state="allocatable"`, `node_ready`,
> `by_namespace`, `by_ready`, `nodes`, `node_info` 는 구현돼 아래에서 제거됨.

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

> **메트릭 구현 완료(feature/metrics-observability):** `up`(meta)/`last_success_timestamp`
> (성공 분기만 갱신)/`collect_errors`/`node_collect_error`/`k8s_enabled`/`demo`(snap-only),
> `refreshes_total`/`refresh_failures_total`(counter). `render_prometheus_metrics(snap,
> meta=None)` + `state.build_meta()` 로 배선 — meta=None 이면 관측성 라인 생략(0 방출 금지).
> **주의(구현 시 확인됨):** `build_snapshot` 은 통상 실패(RBAC 403, 노드 조회 실패)를
> 예외로 던지지 않으므로 `failures`/`last_success` 는 예상외 예외·루프 정지 전용 신호다 —
> 통상 수집 실패 감시는 `collect_errors`/`node_collect_error` 가 담당한다.

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
- [ ] **[P3]** `gpu_monitor_workload_gpu{node,namespace,workload,workload_type}` — **pod 명 라벨 제외**(카디널리티). **주의:** 같은 워크로드 레플리카 여러 개가 한 노드에 있으면 동일 라벨셋 중복 방출로 exposition 이 깨진다 — 방출 전 키별 **사전 합산 필수**
- [ ] **[P3]** (선택) `gpu_monitor_product_gpu{product,state}` — 파생 가능하므로 recording rule 우선 검토

### 배포물
- [ ] **[P1]** `deploy/grafana-dashboard.json` 신규 — row 4단(개요/장치/워크로드·ns/노드/상태), 어노테이션 오버레이, 템플릿 변수(node/product/namespace), 기존 메트릭만으로 되는 패널 우선
- [ ] **[P2]** `deploy/prometheus-alerts.yaml` 확장 — 위 알럿 세트 추가(기존 3종은 신규 메트릭으로 보강, 임계 방향 반전 주의, not-ready 는 `for:` 지연)

### 문서
- [ ] **[P2]** README/CLAUDE.md 의 노출 메트릭 목록을 신규 메트릭으로 갱신 (할당 경계 문구 유지)
