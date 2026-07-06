"""Pod 이 어떤 워크로드로 GPU 를 잡았는지 추정 (owner reference + 라벨).

추가 API 호출 없이 Pod 오브젝트만으로 판단한다(휴리스틱). ReplicaSet 은 Deployment 로
환원(이름 끝 해시 suffix 제거)하되 정확한 Deployment 조회는 하지 않는다.

-> {"type": "KServe"|"Job"|"Notebook"|"StatefulSet"|"DaemonSet"|"Deployment"|..., "name": str}
"""

KSERVE_ISVC_LABEL = "serving.kserve.io/inferenceservice"


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
