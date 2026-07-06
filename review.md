# feature/deploy-manifests 리뷰

> 대상 브랜치: `feature/deploy-manifests` (main 기준 6 커밋)
> 작성: 2026-07-06 · 범위: 배포 매니페스트 + 대시보드 색상 + GPU 공유 계획 문서

## 검토 범위

| 커밋 | 내용 |
|---|---|
| `bd337b5` | k8s 배포 매니페스트 추가 |
| `7a84b87` | 대시보드 장치별 세그먼트 바에 제품별 고유 색 |
| `b761f38` | 매니페스트 결함/하드닝 수정 |
| `397fc06` | 제품 팔레트를 워크로드 색과 비충돌로 교체 + 색 안정화 |
| `d03cc4a` | MIG/공유모드 계획의 -SHARED 접미 서술 정정 |
| (`e535d00`) | MIG/time-slicing 대응 계획 문서 |

변경 파일: [deploy/k8s.yaml](deploy/k8s.yaml), [deploy/podmonitor.yaml](deploy/podmonitor.yaml),
[deploy/prometheus-alerts.yaml](deploy/prometheus-alerts.yaml),
[docs/gpu-sharing-plan.md](docs/gpu-sharing-plan.md),
[app/web/templates/dashboard.html](app/web/templates/dashboard.html), [README.md](README.md).

**총평:** 매니페스트 품질은 높다 — 최소권한 RBAC, restricted PodSecurity(비루트/RO루트fs/cap drop),
startupProbe 로 첫 수집 블로킹 대응, CRD 의존 리소스(PodMonitor/PrometheusRule) 분리, 알림의
"과소경보" 방향성까지 주석으로 근거를 남겼다. 문제 대부분은 **주석으로 인지·문서화**돼 있다.
다만 아래 몇 가지는 배포 시점에 실제로 물릴 수 있고, 관측성/보안 공백은 코드 이식이 필요하다.
Python 수집/집계 로직은 손대지 않았고 단위 테스트 23개 전부 통과한다(회귀 없음).

---

## 1. 발생 가능한 버그

### B1. 직접 접근(port-forward/Service)이면 대시보드가 빈 화면 — 높음
[deploy/k8s.yaml:104](deploy/k8s.yaml#L104) 가 `MONITOR_ROOT_PATH=/service/gpu-monitor` 를
Deployment 에 **하드코딩**한다. 대시보드는 이 값을 `BASE_PATH` 로 구워
([app/web/routes.py:17](app/web/routes.py#L17)) 모든 fetch 에 접두사를 붙인다
([dashboard.html:286](app/web/templates/dashboard.html#L286) `fetch(BASE_PATH+"/api/snapshot")`).
- FastAPI `root_path` 는 실제 라우트를 **다시 마운트하지 않는다** — 앱은 여전히 `/api/snapshot` 에서 서빙한다.
  접두사를 떼주는 건 nginx ingress rewrite 뿐이다.
- 따라서 `kubectl port-forward svc/gpu-monitor 8089:80` 후 브라우저로 열면: `/` 는 뜨지만
  JS 가 `/service/gpu-monitor/api/snapshot` 를 호출 → **404 → 데이터 안 뜸**. 디버깅할 때 바로 물린다.
- **권장:** base 매니페스트에선 `MONITOR_ROOT_PATH` 를 비우고, ingress 를 쓰는 환경에서만 오버레이로
  주입(→ A1). 최소한 README/주석에 "직접 접근 시 빈 화면" 경고 추가.

### B2. 노드 Pod 조회 실패 → free 과대계상 → 알림 미탐 — 중간
한 노드의 Pod 조회가 실패하면 `collect_allocations` 가 `node["error"]` 만 채우고 리턴
([collect.py:46-48](app/services/collect.py#L46-L48)) → `free` 는 `None`, `allocated` 는 0 유지.
`summarize` 는 `free is None` 이면 **전량 유휴로 계산**한다
([snapshot.py:53-55](app/services/snapshot.py#L53-L55)).
- 결과: 그 노드가 "capacity 만큼 전부 유휴" 로 잡혀 클러스터 free 과대·allocated 과소.
- `ClusterGpuExhausted`(free==0), `ClusterGpuHighAllocation`(alloc/cap>0.9) 알림이 **과소경보**가 된다.
  매니페스트는 이를 "under-alert safe" 라고 감수하지만
  ([prometheus-alerts.yaml:75-76](deploy/prometheus-alerts.yaml#L75-L76)), 실제 소진 상황을 놓칠 수 있고
  이 상태를 감지할 메트릭조차 없다(→ M2).

### B3. MIG / time-slicing(rename) / MPS / MIG-mixed 노드가 대시보드에서 소멸 — 중간(설계상 deferred)
수집기가 GPU 를 단일 키 `nvidia.com/gpu` 로만 본다([gpu.py:7](app/services/gpu.py#L7)).
`collect_gpu_nodes` 는 이 키 capacity==0 이면 노드를 통째로 드롭한다
([collect.py:24](app/services/collect.py#L24)).
- `nvidia.com/gpu.shared`(rename=true, MPS) 또는 `nvidia.com/mig-*`(MIG mixed) 만 있는 노드는
  **허위 음성으로 사라진다** — 모니터링 도구로서 최악의 실패.
- [docs/gpu-sharing-plan.md](docs/gpu-sharing-plan.md) 에 원인·대응(Option B)이 상세히 정리돼 있고
  구현은 **의도적으로 나중(deferred)**. 다만 이 배포를 그런 노드가 있는 실클러스터에 올리면 즉시 문제.
- **권장:** 최소한 Phase 1(리소스 매처 일반화)만이라도 배포 전 착수하거나, README 배포 절에 "공유모드 노드
  미지원" 을 명시(현재 링크만 있음).

### B4. RBAC 미적용/403 → readyz 는 200(ready) 로 남고 데이터만 빈다 — 중간
`collect_gpu_nodes` 는 조회 실패 시 예외 대신 `([], [에러])` 를 반환
([collect.py:19-20](app/services/collect.py#L19-L20)) → 빈 스냅샷이 정상 생성되고 store 에 저장됨 →
`/readyz` 는 store 가 non-None 이라 **200 ready**([routes.py:52-56](app/api/routes.py#L52-L56)).
- 즉 ClusterRole/Binding 이 없거나 pruned 되면 probe 는 초록인데 대시보드/metrics 는 계속 비어있다.
  인증 실패가 헬스로 드러나지 않는다([k8s.yaml:30-32](deploy/k8s.yaml#L30-L32) 에 문서화).
- 근본 해결은 수집 헬스 메트릭/error 개수 노출(→ M1, M2).

---

## 2. 발생 가능한 문제점 (운영 리스크)

### P1. 버전 라벨 하드코딩 드리프트
`app.kubernetes.io/version: "0.1.0"` 이 [k8s.yaml:66](deploy/k8s.yaml#L66),
[k8s.yaml:78](deploy/k8s.yaml#L78) 2곳에 **수동**으로 박혀있다. CLAUDE.md 는 `__version__`
([app/__init__.py:11](app/__init__.py#L11))을 단일 진실원으로 못박는데, 이 라벨은 그 구동 목록에
없는 **새 사본**이라 bump 시 스테일 확정. 주석 "맞출 것" 은 있으나 자동화 아님. (→ A2)

### P2. `:latest` + `imagePullPolicy: Always` + `replicas: 1`
[k8s.yaml:95-96](deploy/k8s.yaml#L95-L96). 재현/롤백 불가, 롤아웃 시 어느 코드인지 불명확.
develop/feature 라인에선 `:latest` 가 아예 없어 **다른 코드**를 받거나 ImagePullBackOff
([k8s.yaml:87-94](deploy/k8s.yaml#L87-L94) 에 문서화). 운영은 digest pin 권장(→ A4).

### P3. 메모리 상한 256Mi — 대형 클러스터 OOM 위험
스냅샷 1개에 전체 노드 + GPU 점유 Pod 를 담는다. 노드/Pod 가 수천이면 OOMKill → CrashLoop →
데이터 공백([k8s.yaml:126-129](deploy/k8s.yaml#L126-L129) 에 "올릴 것" 명시). 규모에 맞춘 상한 재산정 필요.

### P4. Ingress 가 `/api/snapshot`·`/metrics` 까지 무인증 노출
[k8s.yaml:166-168](deploy/k8s.yaml#L166-L168). 클러스터 노드 인벤토리·네임스페이스·워크로드 Pod 명이
외부로 샌다. NetworkPolicy 는 Pod 직접 접근만 막고 ingress 경로는 못 막는다(→ M3, M4, A8).

### P5. Prometheus 셀렉터 라벨/네임스페이스 불일치 시 조용히 로드 안됨
PodMonitor/PrometheusRule 의 `release: kube-prometheus-stack`
([podmonitor.yaml:20](deploy/podmonitor.yaml#L20), [prometheus-alerts.yaml:57](deploy/prometheus-alerts.yaml#L57))
가 실제 Prometheus 의 `podMonitorSelector`/`ruleSelector` 와 안 맞으면 스크레이프/룰이 침묵한다.
추가로 kube-prometheus-stack 은 **네임스페이스 셀렉터**(`ruleNamespaceSelector`)로도 거를 수 있어
`gpu-monitor` 네임스페이스의 룰이 로드 안 될 수 있다. `GpuMonitorDown` 의 `absent()` 가 일부 보완하나
그 알림 룰 자체가 로드돼야 동작하는 순환.

### P6. capacity 축과 free 축의 혼재
`summarize` 의 `gpu_capacity` 는 노드 **capacity** 합, `gpu_free` 는 **allocatable** 기반
([snapshot.py:51-58](app/services/snapshot.py#L51-L58)). allocatable<capacity 인 노드가 있으면
클러스터 합에서 `allocated + free ≠ capacity`. `ClusterGpuHighAllocation` 은 capacity 기준,
`ClusterGpuExhausted` 는 free 기준이라 축이 다르다. 실무상 GPU 는 allocatable==capacity 라 대개 무해하나,
드레인/문제 노드에서 미묘한 불일치 가능.

### P7. 롤아웃 중 2 Pod 동시 존재 → 메트릭 일시 중복
기본 RollingUpdate + PodMonitor(Pod 직접 스크레이프)라 롤아웃 순간 old/new 두 Pod 가 모두 스크레이프돼
`up{job="gpu-monitor"}` 시계열이 2개가 되고 게이지가 잠깐 겹친다. 단일 모니터라 `strategy: Recreate` 가
더 깔끔(→ A6).

---

## 3. 빠트린 기능

### M1. 수집 헬스 메트릭 부재 (가장 큰 공백)
`gpu_monitor_up` / `*_collect_errors` / `*_last_success_timestamp` 가 없다
([prometheus.py](app/services/prometheus.py), [prometheus-alerts.yaml:14-19](deploy/prometheus-alerts.yaml#L14-L19)
에 자인). → **stale 데이터/조용한 수집 실패를 메트릭으로 탐지 불가.** `GpuMonitorDown` 은 프로세스 생존과
스크레이프 성공만 본다(수집이 조용히 실패해 옛 스냅샷이 유지되는 상황을 못 잡음). 형제 model-monitor 에서 이식 필요.

### M2. 노드별 error 를 노출하는 메트릭 없음
B2/B4 를 알림으로 못 잡는 직접 원인. `gpu_monitor_node_collect_errors` 또는 `errored_nodes` 게이지가
있으면 "N개 노드 수집 실패" 를 알림화할 수 있다. (스냅샷 dict 엔 `node.error` 가 이미 있으나 메트릭 미노출)

### M3. NetworkPolicy 미포함
[k8s.yaml:168](deploy/k8s.yaml#L168) 이 언급만 하고 리소스는 없다. 무인증 `/metrics` 를 스크레이프
소스(Prometheus)로만 제한하는 최소 정책이라도 제공하면 P4 완화.

### M4. `/metrics`·`/api` 인증 미이식
model-monitor 의 `MONITOR_METRICS_TOKEN` Bearer 보호가 안 넘어왔다
([podmonitor.yaml:9-11](deploy/podmonitor.yaml#L9-L11)). 인증 프록시로 대체하든, 토큰 인증을 이식하든 선택 필요.

### M5. MIG / 공유모드 지원
계획만 확정, 구현 deferred(B3, [docs/gpu-sharing-plan.md](docs/gpu-sharing-plan.md)).

### M6. ServiceMonitor 는 주석으로만 제공
[prometheus-alerts.yaml:33-47](deploy/prometheus-alerts.yaml#L33-L47) 에 주석 예시만. PodMonitor 처럼
별도 파일로 두면 Service 경유 스크레이프 환경에서 바로 쓸 수 있다(선택).

### M7. 스케줄링 제약 없음
Deployment 에 nodeSelector/affinity/tolerations 없음. 모니터 Pod 가 GPU 노드에 스케줄될 수 있다
(GPU 를 요청하진 않으니 치명적이진 않으나, GPU 노드가 taint 됐다면 배치 실패 가능). control-plane/일반 노드
지정 또는 GPU 노드 회피 anti-affinity 권장.

### M8. Grafana 대시보드 JSON 미포함
메트릭은 노출하나 시각화 자산이 없다. 노출 6종 메트릭 기준 기본 대시보드 JSON 제공 시 채택성↑.

---

## 4. 추가하면 좋을 기능

### A1. Kustomize / Helm 파라미터화 (최우선 권장)
`MONITOR_ROOT_PATH`·이미지 태그·`app.kubernetes.io/version` 라벨·네임스페이스·`release` 라벨을
오버레이로 뺀다. 특히 **base 에선 `MONITOR_ROOT_PATH` 를 비우고 ingress 오버레이에서만 주입** → B1(직접
접근 빈 화면)과 P5(라벨 불일치)를 구조적으로 해결.

### A2. 버전 라벨 자동 주입
`ci.sh` 가 `__version__` 으로 라벨을 `sed` 치환하거나, 라벨을 없애고 이미지 태그로만 버전 추적 → P1 제거.

### A3. stale/에러 알림 (M1·M2 선행)
`last_success_timestamp` 기반 "N분 이상 수집 성공 없음" 알림 + `errored_nodes > 0` 알림. 현재 알림이
감수하는 "과소경보" 를 실제 탐지로 승격.

### A4. 이미지 digest pin + `IfNotPresent` 오버레이
운영/롤백 라인에서 `@sha256` 로 고정([k8s.yaml:93-94](deploy/k8s.yaml#L93-L94) 제안대로) → P2 완화, 재현성 확보.

### A5. Prometheus recording rule
할당률(`allocated / clamp_min(capacity,1)`) 사전계산, per-product 여유 등을 recording rule 로 두면
대시보드/알림 쿼리가 가벼워지고 일관.

### A6. Deployment `strategy: Recreate` 명시
단일 모니터라 롤아웃 중 중복 스크레이프(P7) 방지. 짧은 공백은 어차피 startupProbe 대기와 유사.

### A7. PriorityClass
`replicas: 1` 이라 축출되면 그동안 대시보드/메트릭 공백([k8s.yaml:68-69](deploy/k8s.yaml#L68-L69)).
낮은 우선순위라도 지정해 불필요한 선점 축출을 줄이면 가용성↑ (HA/PDB 는 단일 인스턴스라 여전히 불필요).

### A8. TLS + 인증 프록시 오버레이 예시
oauth2-proxy 또는 ingress basic-auth 스니펫 예시 → P4/M4 실전 대응.

### A9. 매니페스트 위생
`revisionHistoryLimit`(ReplicaSet 누적 제한), `automountServiceAccountToken: true` 명시(현재 기본값
의존), Deployment `strategy` 명시 등 소소한 하드닝.

---

## 부록 — 잘한 점 (유지)

- 최소권한 RBAC(`list` only, get 제외), 근거 주석까지 명확([k8s.yaml:24-43](deploy/k8s.yaml#L24-L43)).
- restricted PodSecurity 완비: 비루트/RO루트fs/`drop:[ALL]`/seccomp + 방어적 `/tmp` emptyDir.
- CRD 의존 리소스(PodMonitor/PrometheusRule)를 core 매니페스트에서 분리 → Operator 없이 `k8s.yaml` 단독 apply 가능.
- startupProbe 로 "첫 수집까지 서버 미기동" 을 흡수해 liveness 조기 사망 방지([k8s.yaml:107-124](deploy/k8s.yaml#L107-L124)).
- 알림의 "과소경보(under-alert safe)" 방향성을 근거와 함께 문서화.
- 대시보드 제품 색을 워크로드 팔레트와 비충돌 팔레트로 분리 + 해시 기반 고정 → 노드 집합이 바뀌어도 색 안정,
  기존 바/범례 색 불일치도 해소([dashboard.html:158-166](app/web/templates/dashboard.html#L158-L166)).
