# 분류 축 (Classification) — 워크로드 / 사용 목적 / 배포 환경

이 문서는 gpu-monitor 가 **할당된 GPU 를 어떤 타입으로 나누는지**, 그리고 운영자가
**타입을 어떻게 정의(지정)하는지** 설명한다.

정의 위치: [`app/services/workload.py`](../app/services/workload.py). 세 축 모두

- **Pod 오브젝트만으로** 판단하는 순수 함수 — 추가 API 호출 없음(라벨 + ownerReference).
- **할당(allocation) 경계 유지** — "누가 GPU 를 몇 개 점유했나"의 재분류일 뿐, 실사용률(DCGM %/VRAM)과 무관.
- **우선순위 첫-매치** — 위에서부터 처음 걸리는 규칙이 이긴다.
- **override 최우선** — `gpu-monitor.io/<축>` 라벨이 있으면 자동 판정을 무시하고 그 값을 쓴다.
- **전수 보존** — 어디에도 안 걸리면 단일 `기타` 버킷으로. 축별 버킷 합 == `gpu_allocated`.

집계 단위는 **온전 GPU**(`nvidia.com/gpu`). 타임슬라이스/MIG **슬롯**은 단위가 달라
클러스터 집계(`by_*`)에 합산하지 않는다(각 slot allocation 에 타입 필드 자체는 붙는다).

---

## 축 1 — 워크로드 타입 (`classify_workload`)

Pod 이 "무엇"인지. 우선순위:

| 순위 | 신호 | → 타입 |
|---|---|---|
| 1 | 라벨 `serving.kserve.io/inferenceservice` | `KServe` |
| 2 | 라벨 `job-name` / `batch.kubernetes.io/job-name` 또는 owner `Job` | `Job` |
| 3 | 라벨 `notebook-name` | `Notebook` |
| 4 | owner `StatefulSet` / `DaemonSet` | 동명 |
| 5 | owner `ReplicaSet` | `Deployment` (해시 suffix 제거) |
| 6 | 그 외 owner kind | 그 kind 이름 |
| 7 | owner 없음 | `Pod` |

> 워크로드 타입은 override 라벨이 없다(owner/라벨로만 추정). 사용 목적/환경 축이 override 를 지원한다.

---

## 축 2 — 사용 목적 (`classify_purpose`)

같은 할당 GPU 를 "무슨 용도"로 쓰는지. 버킷: `serving · training · interactive · batch · system · 기타`.

| 순위 | 버킷 | 자동 인식 신호 |
|---|---|---|
| 0 | (override) | 라벨 `gpu-monitor.io/purpose` — 아래 [허용 값](#override-허용-값) 으로 정규화 |
| 1 | `serving` | 워크로드 `KServe` · `seldon-deployment-id` · `seldon-app` · owner `InferenceService` · `app.kubernetes.io/component ∈ {predictor, transformer, server, inference}` |
| 2 | `interactive` | 워크로드 `Notebook` · `notebook-name` · `app ∈ {jupyter, jupyterlab, notebook, code-server, vscode}` |
| 3 | `training` | owner `PyTorchJob · TFJob · MPIJob · XGBoostJob · PaddleJob · MXJob` · `training.kubeflow.org/job-name` · `job-role\|job-type\|training.kubeflow.org/job-role ∈ {train, trainer, finetune, sft, rl, pretrain}` |
| 4 | `batch` | 그 외 일반 `Job` / `CronJob` (학습 신호가 없는 배치·eval·preprocess) |
| 5 | `system` | 워크로드/owner `DaemonSet` · 네임스페이스 `kube-system \| gpu-operator \| nvidia-gpu-operator \| nvidia` · `app.kubernetes.io/part-of ∈ {gpu-operator, nvidia-gpu-operator}` |
| 6 | `기타` | 위 어디에도 안 걸림 (예: 라벨 없는 순수 Deployment) |

### override 허용 값

`gpu-monitor.io/purpose` 값은 아래로 정규화된다(목록 밖 값 → `기타`):

| 지정 값 | → 버킷 |
|---|---|
| `serving` `serve` `inference` `infer` | serving |
| `training` `train` `trainer` `finetune` `sft` `rl` `pretrain` | training |
| `interactive` `notebook` `jupyter` | interactive |
| `batch` `job` `eval` `preprocess` | batch |
| `system` `infra` | system |

---

## 축 3 — 배포 환경 (`classify_environment`)

어떤 환경의 워크로드인지. 버킷: `prod · staging · test · dev · <커스텀> · 기타`
(배포 사다리 `dev < test < staging < prod`).

**라벨 우선순위** (첫 값 채택):
`gpu-monitor.io/environment` → `app.kubernetes.io/environment` → `environment` → `env` → `stage`

**값 정규화:**

| 지정 값(대소문자 무관) | → 버킷 |
|---|---|
| `production` `prod` `prd` `live` | prod |
| `staging` `stage` `stg` `qa` `uat` | staging |
| `test` `testing` | test |
| `development` `develop` `dev` `sandbox` `sbx` | dev |
| 그 외 비어있지 않은 값 | **원값(소문자)** — 예: `canary` |
| 라벨 없음 | 기타 |

---

## 타입 정의(라벨링) 방법

자동 판정이 맞으면 **아무것도 안 해도 된다.** 틀리거나 명시하고 싶을 때만 라벨을 단다.

**핵심: pod 자체가 아니라 워크로드 컨트롤러의 `spec.template.metadata.labels` 에 단다** —
그래야 모든 replica 가 상속한다.

```yaml
# Deployment / StatefulSet / Job / RayCluster ... 의 pod template
spec:
  template:
    metadata:
      labels:
        gpu-monitor.io/purpose: serving       # 목적 강제 (자동 판정보다 우선)
        gpu-monitor.io/environment: prod        # 환경 지정
```

KServe `InferenceService` 처럼 자체 라벨을 뿌리는 리소스는 predictor pod 에 그대로 전파되도록
`spec.predictor` 의 `annotations`/`labels` 또는 컴포넌트 template 에 단다.

**자주 쓰는 경우**

- vLLM/TGI 를 **순수 Deployment** 로 서빙 → KServe 라벨이 없어 `기타`. `gpu-monitor.io/purpose: serving` 한 줄로 교정.
- 일반 `Job` 이 실제 **학습** → 기본 `batch`. `gpu-monitor.io/purpose: training` (또는 `job-role: train`).
- 환경 라벨 표준이 이미 있으면(`environment`/`env`) 그대로 인식되므로 override 불필요.

---

## 결과 확인

**대시보드** (`/`) — 클러스터 카드에 **"사용 목적"·"배포 환경" 도넛** 추가.

**JSON** (`/api/snapshot`):

```jsonc
"summary": {
  "by_purpose":     { "serving": 10, "training": 2, "interactive": 1 },
  "by_environment": { "prod": 9, "dev": 3, "staging": 1 }
}
// 각 allocation 에 "purpose", "environment" 필드가 붙는다
```

**Prometheus** (`/metrics`):

```promql
# 비프로덕션이 점유 중인 GPU
sum(gpu_monitor_gpu_allocated_by_environment{environment!="prod"})

# 목적별 비중
sum by (purpose) (gpu_monitor_gpu_allocated_by_purpose)

# 알럿: dev 가 GPU 8장 초과 점유
sum(gpu_monitor_gpu_allocated_by_environment{environment="dev"}) > 8
```

---

## 한계

- **온전 GPU 기준 집계** — 클러스터 도넛/메트릭은 통짜 `nvidia.com/gpu` 만. 타임슬라이스/MIG 슬롯은 미합산(기존 `by_workload_type` 과 동일).
- **pod 레벨 라벨** — 컨트롤러 template 에 달아야 replica 전체 반영.
- **휴리스틱** — 라벨/owner 가 없으면 `기타`. 정확도가 필요하면 override 라벨을 쓴다.

---

## 확장 방법 (코드)

새 신호/버킷은 [`workload.py`](../app/services/workload.py) 의 상수만 손대면 된다(로직 분기 최소):

- 사용 목적: `_PURPOSE_MAP`(override 값), `_TRAIN_KINDS`, `_SERVING_COMPONENTS`, `_SYSTEM_NS` 확장.
- 배포 환경: `ENV_LABEL_KEYS`(라벨 우선순위), `_ENV_MAP`(정규화) 확장.

집계·메트릭·대시보드는 버킷 이름만 새로 나타나면 자동 반영된다(고정 버킷 하드코딩 없음).
분류 로직을 바꾸면 [`test_gpu_monitor.py`](../test_gpu_monitor.py) 의 `TestPurposeEnv` 에 회귀 테스트를 추가한다.
