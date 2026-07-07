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

> **완료(feature/metrics-observability):** `deploy/grafana-dashboard.json` 작성 —
> row 5단(개요/장치/워크로드·네임스페이스/노드/상태·수집 헬스), 템플릿 변수
> node/product/namespace + datasource, 알럿 어노테이션(`.*Gpu.*` — 원안의
> `Gpu.*|Node.*` 는 앵커드 regex 라 기존 `Cluster*` 2종 미매칭), refresh 30s.
> 원안 설계에서 반영한 것: 할당률 임계 70/90(높을수록 위험 — model-monitor 와 반대),
> 분모는 capacity(기존 알럿과 정합), 노드 테이블은 Labels-to-fields 피벗, heatmap
> 대신 state-timeline, 만석/NotReady stat 은 `or vector(0)` 로 No-data 방어,
> '할당≠사용률' 텍스트 패널 고정. model-monitor 원본 JSON 이 저장소에 없어
> 미러링이 아닌 신규 작성.
> **남은 주의:** 수집 실패 노드는 free 시리즈가 없어 만석 stat 에 안 잡힌다 —
> 대시보드 '노드 수집 실패' stat 및 `GpuNodeCollectError` 알럿과 함께 볼 것.

---

## 4. 관측성(self-observability) 메트릭 + staleness 알럿

> **메트릭 구현 완료(feature/metrics-observability):** `up`(meta)/`last_success_timestamp`
> (성공 분기만 갱신)/`collect_errors`/`node_collect_error`/`k8s_enabled`/`demo`(snap-only),
> `refreshes_total`/`refresh_failures_total`(counter). `render_prometheus_metrics(snap,
> meta=None)` + `state.build_meta()` 로 배선 — meta=None 이면 관측성 라인 생략(0 방출 금지).
> **주의(구현 시 확인됨):** `build_snapshot` 은 통상 실패(RBAC 403, 노드 조회 실패)를
> 예외로 던지지 않으므로 `failures`/`last_success` 는 예상외 예외·루프 정지 전용 신호다 —
> 통상 수집 실패 감시는 `collect_errors`/`node_collect_error` 가 담당한다.

### PrometheusRule 알럿

> **완료(feature/metrics-observability):** 3종 → 10종. 원안에서 정정한 것 —
> `GpuClusterNoFreeCapacity` 는 **미추가**(기존 `ClusterGpuExhausted` 와 동일 조건인
> 이중 발화 + `capacity>0` 가드 누락으로 k8s 비활성 시 오발화), `GpuMonitorDown` 은
> 기존 `absent(up{job})` 가드를 **유지**한 채 `gpu_monitor_up==0` 만 보강(타깃 미발견
> 침묵 회귀 방지), `GpuMonitorStale` 임계는 refresh 15s×3(45s)이 아니라 **스크레이프
> 주기 30s 기준 75s**(45s 는 스크레이프 1.5주기라 플랩), `GpuMonitorK8sDisabled` 는
> `gpu_monitor_demo==0` 조건으로 demo 배포 오발화 제외.
> 대시보드 어노테이션 regex 는 `Gpu.*|Node.*` 가 아니라 **`.*Gpu.*`** 로 —
> 기존 `Cluster*` 2종이 앵커드 regex 에 안 걸린다. 실사용률 알럿은 도입하지 않음.

---

## 5. 산출물 TODO 체크리스트

우선순위: **[P1]** 데이터 있음·변경 최소·가치 높음 → **[P2]** 소규모 집계 → **[P3]** 카디널리티/파생 트레이드오프.

### 코드 — 할당 메트릭
- [ ] **[P3]** `gpu_monitor_workload_gpu{node,namespace,workload,workload_type}` — **pod 명 라벨 제외**(카디널리티). **주의:** 같은 워크로드 레플리카 여러 개가 한 노드에 있으면 동일 라벨셋 중복 방출로 exposition 이 깨진다 — 방출 전 키별 **사전 합산 필수**
- [ ] **[P3]** (선택) `gpu_monitor_product_gpu{product,state}` — 파생 가능하므로 recording rule 우선 검토

### 문서
- [ ] **[P2]** README/CLAUDE.md 의 노출 메트릭 목록을 신규 메트릭으로 갱신 (할당 경계 문구 유지)
