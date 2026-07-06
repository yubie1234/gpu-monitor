"""GPU 원시 계산: Pod 점유 GPU 수, 노드 GPU capacity/allocatable, 장치 모델명.

할당(allocation) 기준 — Pod resources.limits(없으면 requests) 의 nvidia.com/gpu.
장치명 -> 노드 라벨 nvidia.com/gpu.product (GPU Operator/GFD). 실사용률(DCGM)이 아니다.
"""

GPU_RESOURCE = "nvidia.com/gpu"
GPU_PRODUCT_LABEL = "nvidia.com/gpu.product"


def gpu_qty(v):
    """nvidia.com/gpu 수량 문자열("1","8") -> int. 실패하면 0."""
    try:
        return int(str(v))
    except (TypeError, ValueError):
        return 0


def pod_gpu(pod):
    """Pod 한 개가 점유하는 GPU 수 = 컨테이너 limits(없으면 requests) 의 nvidia.com/gpu 합."""
    total = 0
    for ctr in ((pod.get("spec") or {}).get("containers") or []):
        res = ctr.get("resources") or {}
        q = (res.get("limits") or {}).get(GPU_RESOURCE)
        if q is None:
            q = (res.get("requests") or {}).get(GPU_RESOURCE)
        total += gpu_qty(q)
    return total


def pod_ready(pod):
    """Running + Ready condition True 인 Pod 만 '서빙 중'으로 본다."""
    st = pod.get("status") or {}
    if st.get("phase") != "Running":
        return False
    for cnd in st.get("conditions") or []:
        if cnd.get("type") == "Ready":
            return cnd.get("status") == "True"
    return False


def short_gpu_product(prod):
    """NVIDIA-H100-80GB-HBM3 -> H100, NVIDIA-B200 -> B200, NVIDIA-A100-SXM4-80GB -> A100."""
    if not prod:
        return None
    s = prod
    if s.upper().startswith("NVIDIA-"):
        s = s[len("NVIDIA-"):]
    return s.split("-")[0] or prod


def node_gpu(node):
    """노드의 GPU capacity/allocatable + 장치 모델명 -> dict."""
    status = node.get("status") or {}
    cap = gpu_qty((status.get("capacity") or {}).get(GPU_RESOURCE))
    alloc = gpu_qty((status.get("allocatable") or {}).get(GPU_RESOURCE))
    labels = (node.get("metadata") or {}).get("labels") or {}
    raw = labels.get(GPU_PRODUCT_LABEL)
    return {"capacity": cap, "allocatable": alloc,
            "product": short_gpu_product(raw) or ("GPU" if cap else None),
            "product_raw": raw}


def node_ready(node):
    for cnd in ((node.get("status") or {}).get("conditions") or []):
        if cnd.get("type") == "Ready":
            return cnd.get("status") == "True"
    return False
