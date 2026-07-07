"""GPU 원시 계산: Pod 점유 GPU 수, 노드 GPU capacity/allocatable, 장치 모델명.

할당(allocation) 기준 — Pod resources.limits(없으면 requests).

두 종류의 GPU 리소스를 구분한다:
  - 온전(whole) GPU: `nvidia.com/gpu` — 1 = 물리 1장.
  - 공유(shared) 풀: `nvidia.com/gpu.<프로파일>` (예: `nvidia.com/gpu.10gb`,
    `...10gb-mps`, `...full`) — 물리 1장을 타임슬라이스/MPS 로 쪼갠 슬롯. 1 슬롯 ≠ 1 물리장.

물리 장수는 노드 라벨 `nvidia.com/gpu.count`, 공유 GPU 1장당 슬롯 수는
`nvidia.com/gpu.replicas`, 방식은 `nvidia.com/gpu.sharing-strategy`(time-slicing/mps).
장치명 -> `nvidia.com/gpu.product`. 실사용률(DCGM)이 아니다.
"""

GPU_RESOURCE = "nvidia.com/gpu"
GPU_SHARED_PREFIX = "nvidia.com/gpu."   # 공유/명명 풀 (.10gb, .10gb-mps, .full ...)
GPU_PRODUCT_LABEL = "nvidia.com/gpu.product"
GPU_COUNT_LABEL = "nvidia.com/gpu.count"              # 물리 GPU 장수
GPU_REPLICAS_LABEL = "nvidia.com/gpu.replicas"        # 공유 GPU 1장당 슬롯(replica) 수
GPU_SHARING_LABEL = "nvidia.com/gpu.sharing-strategy"  # time-slicing / mps / none


def gpu_qty(v):
    """nvidia.com/gpu 수량 문자열("1","8") -> int. 실패하면 0."""
    try:
        return int(str(v))
    except (TypeError, ValueError):
        return 0


def is_gpu_resource(key):
    """리소스 키가 온전(nvidia.com/gpu) 또는 공유(nvidia.com/gpu.<프로파일>) GPU 인가."""
    return key == GPU_RESOURCE or key.startswith(GPU_SHARED_PREFIX)


def shared_profile(resource):
    """nvidia.com/gpu.10gb -> '10gb', nvidia.com/gpu.full-mps -> 'full-mps'."""
    if resource and resource.startswith(GPU_SHARED_PREFIX):
        return resource[len(GPU_SHARED_PREFIX):]
    return resource


def pod_gpu_resources(pod):
    """Pod 이 점유한 nvidia.com/gpu* 리소스별 수량 -> {resource: qty}.

    컨테이너별로 리소스 키마다 limits(없으면 requests)를 취해 합산. 0 은 제외.
    온전/공유가 섞여 있으면 키별로 따로 집계한다(단위가 다르므로 합치지 않음).
    """
    totals = {}
    for ctr in ((pod.get("spec") or {}).get("containers") or []):
        res = ctr.get("resources") or {}
        limits = res.get("limits") or {}
        requests = res.get("requests") or {}
        keys = set(k for k in limits if is_gpu_resource(k))
        keys |= set(k for k in requests if is_gpu_resource(k))
        for k in keys:
            q = limits.get(k)
            if q is None:
                q = requests.get(k)
            n = gpu_qty(q)
            if n:
                totals[k] = totals.get(k, 0) + n
    return totals


def pod_gpu(pod):
    """Pod 한 개가 점유하는 온전(nvidia.com/gpu) GPU 수 (하위호환)."""
    return pod_gpu_resources(pod).get(GPU_RESOURCE, 0)


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
    """노드의 GPU capacity/allocatable + 공유 풀 + 물리 장수/방식 -> dict.

    capacity/allocatable 는 온전(nvidia.com/gpu) 값(하위호환). 공유 풀은 shared_pools 로,
    물리 장수는 라벨 nvidia.com/gpu.count 로 별도 노출한다.
    """
    status = node.get("status") or {}
    cap = status.get("capacity") or {}
    alloc = status.get("allocatable") or {}
    labels = (node.get("metadata") or {}).get("labels") or {}

    whole_cap = gpu_qty(cap.get(GPU_RESOURCE))
    whole_alloc = gpu_qty(alloc.get(GPU_RESOURCE))

    # 공유 풀: capacity/allocatable 의 nvidia.com/gpu.<프로파일> 키 합집합.
    # (라벨은 metadata.labels 라 여기 capacity/allocatable 에는 섞이지 않는다.)
    shared = {}
    for field, src in (("capacity", cap), ("allocatable", alloc)):
        for k, v in src.items():
            if k == GPU_RESOURCE or not k.startswith(GPU_SHARED_PREFIX):
                continue
            pool = shared.setdefault(k, {"resource": k, "profile": shared_profile(k),
                                         "capacity": 0, "allocatable": 0})
            pool[field] = gpu_qty(v)
    shared_pools = [shared[k] for k in sorted(shared)
                    if shared[k]["capacity"] or shared[k]["allocatable"]]

    raw = labels.get(GPU_PRODUCT_LABEL)
    physical = gpu_qty(labels.get(GPU_COUNT_LABEL)) or None
    replicas = gpu_qty(labels.get(GPU_REPLICAS_LABEL)) or None
    has_gpu = bool(whole_cap or shared_pools or physical)
    return {"capacity": whole_cap, "allocatable": whole_alloc,
            "product": short_gpu_product(raw) or ("GPU" if has_gpu else None),
            "product_raw": raw,
            "physical": physical, "replicas": replicas,
            "sharing_strategy": labels.get(GPU_SHARING_LABEL),
            "shared_pools": shared_pools}


def node_ready(node):
    for cnd in ((node.get("status") or {}).get("conditions") or []):
        if cnd.get("type") == "Ready":
            return cnd.get("status") == "True"
    return False
