# TODO — 메트릭 & Grafana 로드맵 (잔여)

`gpu-monitor` 관측 표면(`/metrics`, Grafana, 알럿) 확장 계획의 **잔여분**.

> **경계 재확인 — 할당(allocation) 전용, DCGM 아님.** 모든 메트릭/패널은 `nvidia.com/gpu`
> **할당 수**(capacity/allocatable/allocated/free)와 노드·Pod 메타(ready/phase/error)만
> 근거한다. 실사용률(GPU %, VRAM, 온도, power)은 **스코프 밖 — dcgm-exporter + Grafana**
> 영역이며 여기서 절대 방출하지 않는다.

## 완료 (feature/metrics-observability, 2026-07-07)

로드맵의 P1/P2 전체 구현 — **현재 노출 메트릭 목록은 README `메트릭` 절이 단일 문서**다.

- `summarize` 에 `by_namespace`/`by_ready` 집계, snap-only 계열 확장(allocatable state,
  `node_ready`, `node_info`, `node_collect_error`, `nodes`, `collect_errors`,
  `k8s_enabled`, `demo`), 관측성 meta 계열(`up`/`last_success`/counter 2종) +
  `render_prometheus_metrics(snap, meta=None)`/`state.build_meta()` 배선.
- 알럿 3종 → 10종(`deploy/prometheus-alerts.yaml`), Grafana 대시보드 신규
  (`deploy/grafana-dashboard.json`, row 5단). 회귀 테스트 23 → 37개.
- **원안에서 정정한 것:** `GpuClusterNoFreeCapacity` 미추가(기존 `ClusterGpuExhausted`
  와 동일 조건 + `capacity>0` 가드 누락 오발화), `GpuMonitorDown` 의 `absent()` 가드
  유지, `GpuMonitorStale` 임계는 스크레이프 주기 기준 75s(refresh 기준 45s 는 플랩),
  어노테이션 regex `.*Gpu.*`(앵커드 `Gpu.*|Node.*` 는 `Cluster*` 미매칭),
  `node_ready` 는 별도 계열 블록(text format 그룹핑), `by_ready` 라벨은 소문자
  `"true"/"false"`, `GpuMonitorK8sDisabled` 는 `gpu_monitor_demo==0` 으로 demo 제외.

## 남은 작업

### 코드 — 할당 메트릭 (P3, 선택)

- [ ] `gpu_monitor_workload_gpu{node,namespace,workload,workload_type}` — **pod 명 라벨
  제외**(카디널리티). **주의 1:** 같은 워크로드 레플리카 여러 개가 한 노드에 있으면 동일
  라벨셋 중복 방출로 exposition 이 깨진다 — 방출 전 키별 **사전 합산 필수**.
  **주의 2:** pod 을 빼도 Job 이름(`sft-run-42` 등)은 실행마다 새로 생겨 시리즈 증식
  리스크 잔존 — 보존 기간/알럿에서 인지할 것.
- [ ] (선택) `gpu_monitor_product_gpu{product,state}` — `sum by(product,state)
  (gpu_monitor_node_gpu)` 로 파생 가능하므로 recording rule 우선 검토. **주의:** 수집
  실패 노드(free=None → 라인 생략)가 있으면 파생값과 `summary.products` 합산값이
  어긋난다 — 도입 시 한쪽만 쓸 것.

### 연계 잔여 (이 로드맵 밖 — review.md / gpu-sharing-plan.md)

- **무인증 `/metrics`·`/api/snapshot` 노출** (review.md P4/M3/M4): `by_namespace` 도입으로
  네임스페이스명까지 라벨로 노출 표면이 넓어짐 — NetworkPolicy 또는 metrics 토큰 인증
  이식 결정 필요.
- **B2/B4 는 이제 '탐지'만 된다** (`GpuNodeCollectError`/`GpuMonitorCollectErrors`):
  free=None 폴백이 전량 유휴로 계산되는 로직 자체(snapshot.py `summarize`)와 readyz
  판정은 그대로 — 의미 변경은 기존 테스트·'과소 경보' 문서와 충돌하는 별도 논의.
- **MIG/공유모드 노드는 신규 메트릭에서도 소멸** (B3/M5): 수집이 `nvidia.com/gpu` 단일
  키 기준이라 `gpu.shared`/`mig-*` 노드는 `node_ready`/`node_info` 시리즈에도 없다 —
  `docs/gpu-sharing-plan.md` Phase 1(리소스 매처 일반화)이 선행돼야 함.
