"""Pod 을 여러 축으로 분류 (owner reference + 라벨). 추가 API 호출 없이 Pod 만으로 판단.

세 가지 축(모두 순수 함수·휴리스틱):
  - classify_workload  -> 워크로드 '무엇'  : KServe/Job/Notebook/Deployment/...
  - classify_purpose   -> 사용 '목적'      : serving/training/interactive/batch/system/기타
  - classify_environment -> 배포 '환경'    : prod/staging/dev/<raw>/기타

purpose/environment 는 워크로드 타입과 독립된 재집계 축이다(할당 GPU 를 다른 라벨로
그룹핑). 각 축은 우선순위 목록에서 첫 매치가 이기며, 운영자가 `gpu-monitor.io/<축>`
라벨로 최우선 override 할 수 있다. 매치 실패는 항상 단일 fallback('기타')로 — GPU 를
흘리지 않는다.
"""

KSERVE_ISVC_LABEL = "serving.kserve.io/inferenceservice"

# --- 사용 목적(purpose) ---
PURPOSE_OVERRIDE_LABEL = "gpu-monitor.io/purpose"
# override 라벨 값 정규화 (자유값 -> 표준 버킷). 미매치 override 는 기타.
_PURPOSE_MAP = {
    "serving": "serving", "serve": "serving", "inference": "serving", "infer": "serving",
    "training": "training", "train": "training", "trainer": "training",
    "finetune": "training", "sft": "training", "rl": "training", "pretrain": "training",
    "interactive": "interactive", "notebook": "interactive", "jupyter": "interactive",
    "batch": "batch", "job": "batch", "eval": "batch", "preprocess": "batch",
    "system": "system", "infra": "system",
}
_TRAIN_KINDS = ("PyTorchJob", "TFJob", "MPIJob", "XGBoostJob", "PaddleJob", "MXJob")
_SERVING_COMPONENTS = ("predictor", "transformer", "server", "inference")
_SYSTEM_NS = ("kube-system", "gpu-operator", "nvidia-gpu-operator", "nvidia")

# --- 배포 환경(environment) ---
ENV_OVERRIDE_LABEL = "gpu-monitor.io/environment"
ENV_LABEL_KEYS = (ENV_OVERRIDE_LABEL, "app.kubernetes.io/environment",
                  "environment", "env", "stage")
_ENV_MAP = {
    "production": "prod", "prod": "prod", "prd": "prod", "live": "prod",
    "staging": "staging", "stage": "staging", "stg": "staging",
    "qa": "staging", "uat": "staging",
    "development": "dev", "develop": "dev", "dev": "dev",
    "test": "dev", "testing": "dev", "sandbox": "dev", "sbx": "dev",
}


def _controller_owner(pod):
    refs = (pod.get("metadata") or {}).get("ownerReferences") or []
    for r in refs:
        if r.get("controller"):
            return r
    return refs[0] if refs else None


def _strip_hash(name):
    """ReplicaSet 이름 <deploy>-<hash> -> <deploy> (해시로 보이는 마지막 토큰만 제거)."""
    parts = (name or "").rsplit("-", 1)
    if len(parts) == 2 and parts[1] and len(parts[1]) >= 5 and parts[1].isalnum():
        return parts[0]
    return name


def classify_workload(pod):
    meta = pod.get("metadata") or {}
    labels = meta.get("labels") or {}

    if labels.get(KSERVE_ISVC_LABEL):
        return {"type": "KServe", "name": labels[KSERVE_ISVC_LABEL]}

    owner = _controller_owner(pod)
    okind = (owner or {}).get("kind")
    oname = (owner or {}).get("name")

    job = labels.get("job-name") or labels.get("batch.kubernetes.io/job-name")
    if job or okind == "Job":
        return {"type": "Job", "name": job or oname or meta.get("name")}
    if labels.get("notebook-name"):
        return {"type": "Notebook", "name": labels["notebook-name"]}
    if okind == "StatefulSet":
        return {"type": "StatefulSet", "name": oname}
    if okind == "DaemonSet":
        return {"type": "DaemonSet", "name": oname}
    if okind == "ReplicaSet":
        return {"type": "Deployment", "name": _strip_hash(oname)}
    if okind:
        return {"type": okind, "name": oname or meta.get("name")}
    return {"type": "Pod", "name": meta.get("name")}


def classify_purpose(pod, wl=None):
    """Pod 의 사용 목적 추정 -> serving|training|interactive|batch|system|기타.

    우선순위(첫 매치 승리): override 라벨 > 서빙 > 인터랙티브 > 학습 > 배치 > 시스템.
    wl(classify_workload 결과)을 주면 재계산을 아낀다.
    """
    meta = pod.get("metadata") or {}
    labels = meta.get("labels") or {}
    ns = meta.get("namespace") or ""

    ov = labels.get(PURPOSE_OVERRIDE_LABEL)
    if ov:
        return _PURPOSE_MAP.get(str(ov).strip().lower(), "기타")

    if wl is None:
        wl = classify_workload(pod)
    wtype = wl.get("type")
    okind = (_controller_owner(pod) or {}).get("kind") or ""
    component = (labels.get("app.kubernetes.io/component") or "").lower()
    app = (labels.get("app") or "").lower()

    # 서빙 — KServe/Seldon/Triton 등 온라인 추론
    if (wtype == "KServe" or labels.get(KSERVE_ISVC_LABEL)
            or labels.get("seldon-deployment-id") or labels.get("seldon-app")
            or okind == "InferenceService" or component in _SERVING_COMPONENTS):
        return "serving"

    # 인터랙티브 — 노트북/IDE 세션
    if (wtype == "Notebook" or labels.get("notebook-name")
            or app in ("jupyter", "jupyterlab", "notebook", "code-server", "vscode")):
        return "interactive"

    # 학습 — 분산 학습 오퍼레이터 / 학습 역할 라벨 (일반 Job 은 배치로 남김)
    role = (labels.get("job-role") or labels.get("job-type")
            or labels.get("training.kubeflow.org/job-role") or "").lower()
    if (okind in _TRAIN_KINDS or labels.get("training.kubeflow.org/job-name")
            or role in ("train", "trainer", "finetune", "sft", "rl", "pretrain")):
        return "training"

    # 배치 — 일반 Job/CronJob (eval·preprocess 등)
    if wtype == "Job" or okind in ("Job", "CronJob"):
        return "batch"

    # 시스템 — 데몬셋/GPU 오퍼레이터 등 인프라
    if (wtype == "DaemonSet" or okind == "DaemonSet" or ns in _SYSTEM_NS
            or (labels.get("app.kubernetes.io/part-of") or "") in ("gpu-operator", "nvidia-gpu-operator")):
        return "system"

    return "기타"


def classify_environment(pod):
    """Pod 라벨로 배포 환경 추정 -> prod|staging|dev|<raw>|기타.

    ENV_LABEL_KEYS 우선순위로 첫 값. 알려진 동의어는 정규화, 그 외 비어있지 않은 값은
    원값(소문자)으로 통과(운영자 커스텀 환경명 보존). 라벨 없음 -> 기타.
    """
    labels = (pod.get("metadata") or {}).get("labels") or {}
    for k in ENV_LABEL_KEYS:
        v = labels.get(k)
        if v:
            key = str(v).strip().lower()
            return _ENV_MAP.get(key, key)
    return "기타"
