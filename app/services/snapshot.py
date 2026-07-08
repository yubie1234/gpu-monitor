"""스냅샷 파이프라인: 수집 -> 정규화 -> 집계.

build_snapshot(settings) -> snap dict 가 API/대시보드/JSON/Prometheus 가 똑같이 소비하는
단일 산출물이다. 렌더러가 아니라 이 스냅샷을 바꿔서 모든 출력을 동기화한다.
"""

from datetime import datetime

from app import __version__
from app.core.k8s import K8sClient
from app.services.collect import collect_allocations, collect_gpu_nodes


def build_snapshot(settings):
    if settings.get("demo"):
        from app.services.demo import demo_snapshot  # 지연 import (순환 방지)
        return demo_snapshot()

    snap = {"version": __version__,
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "nodes": [], "summary": {}, "k8s_enabled": False, "errors": []}

    client = K8sClient.from_settings(settings)
    snap["k8s_enabled"] = bool(client)
    if not client:
        snap["errors"].append(
            "k8s 비활성 (in-cluster 토큰 없음) · 미리보기는 MONITOR_DEMO=true")
        snap["summary"] = summarize(snap)
        return snap

    nodes, errs = collect_gpu_nodes(client, settings)
    snap["errors"].extend(errs)
    for node in nodes:
        try:
            collect_allocations(client, node)
        except Exception as e:  # noqa: BLE001  (한 노드 실패가 전체를 막지 않게)
            node["error"] = "%s: %s" % (type(e).__name__, e)
    nodes.sort(key=lambda n: str(n.get("name") or ""))
    snap["nodes"] = nodes
    snap["summary"] = summarize(snap)
    return snap


def summarize(snap):
    """클러스터/장치/워크로드 타입/네임스페이스/ready 별 GPU 집계.

    gpu_capacity/allocated/free 와 by_* 집계는 온전(nvidia.com/gpu) GPU 기준(하위호환).
    물리 장수(gpu_physical)와 공유 슬롯(shared)은 단위가 달라 별도로 집계한다.
    """
    s = {"node_count": 0, "gpu_capacity": 0, "gpu_allocated": 0, "gpu_free": 0,
         "gpu_physical": 0, "gpu_shared_backing": 0,
         "products": {}, "by_workload_type": {}, "by_namespace": {},
         # 파티션(타임슬라이스/MPS/MIG) 슬롯 — 온전 GPU 와 단위가 달라 절대 합치지 않는다.
         # pools: 리소스별 집계(mode/profile 포함) — "분할된 1장이 무엇인지" 표시용.
         "shared": {"capacity": 0, "allocated": 0, "free": 0,
                    "by_profile": {}, "by_mode": {}, "pools": {}},
         # ready 버킷은 항상 양쪽을 채운다(0 이어도) — 메트릭 absent 방지.
         # 키는 소문자 문자열: JSON API 와 Prometheus 라벨이 같은 표현을 쓴다.
         "by_ready": {"true": 0, "false": 0}}
    nodes = snap.get("nodes") or []
    s["node_count"] = len(nodes)
    for n in nodes:
        cap = n.get("capacity") or 0
        alloc = n.get("allocated") or 0
        free = n.get("free")
        if free is None:
            free = max((n.get("allocatable") or cap) - alloc, 0)
        # 물리 장수는 라벨(gpu.count) 기준, 없으면 온전 capacity 로 폴백.
        phys = n.get("physical")
        if phys is None:
            phys = cap
        s["gpu_capacity"] += cap
        s["gpu_allocated"] += alloc
        s["gpu_free"] += free
        s["gpu_physical"] += phys
        s["gpu_shared_backing"] += n.get("shared_backing") or 0
        prod = n.get("product") or "GPU"
        p = s["products"].setdefault(
            prod, {"capacity": 0, "allocated": 0, "free": 0, "physical": 0})
        p["capacity"] += cap
        p["allocated"] += alloc
        p["free"] += free
        p["physical"] += phys
        for pool in n.get("shared_pools") or []:
            pc = pool.get("capacity") or 0
            pa = pool.get("allocated") or 0
            pf = pool.get("free")
            if pf is None:
                pf = max((pool.get("allocatable") or pc) - pa, 0)
            s["shared"]["capacity"] += pc
            s["shared"]["allocated"] += pa
            s["shared"]["free"] += pf
            prof = pool.get("profile") or pool.get("resource") or "shared"
            bp = s["shared"]["by_profile"].setdefault(
                prof, {"capacity": 0, "allocated": 0, "free": 0})
            bp["capacity"] += pc
            bp["allocated"] += pa
            bp["free"] += pf
            mode = pool.get("mode") or "shared"
            bm = s["shared"]["by_mode"].setdefault(
                mode, {"capacity": 0, "allocated": 0, "free": 0})
            bm["capacity"] += pc
            bm["allocated"] += pa
            bm["free"] += pf
            res = pool.get("resource") or prof
            ep = s["shared"]["pools"].setdefault(res, {
                "resource": res, "profile": pool.get("profile"), "mode": mode,
                "capacity": 0, "allocated": 0, "free": 0})
            ep["capacity"] += pc
            ep["allocated"] += pa
            ep["free"] += pf
        for a in n.get("allocations") or []:
            gpu = a.get("gpu") or 0
            t = a.get("workload_type") or "기타"
            s["by_workload_type"][t] = s["by_workload_type"].get(t, 0) + gpu
            ns = a.get("namespace") or "기타"
            s["by_namespace"][ns] = s["by_namespace"].get(ns, 0) + gpu
            s["by_ready"]["true" if a.get("ready") else "false"] += gpu
    return s
