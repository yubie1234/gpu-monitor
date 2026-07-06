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
    """클러스터/장치/워크로드 타입별 GPU 집계."""
    s = {"node_count": 0, "gpu_capacity": 0, "gpu_allocated": 0, "gpu_free": 0,
         "products": {}, "by_workload_type": {}}
    nodes = snap.get("nodes") or []
    s["node_count"] = len(nodes)
    for n in nodes:
        cap = n.get("capacity") or 0
        alloc = n.get("allocated") or 0
        free = n.get("free")
        if free is None:
            free = max((n.get("allocatable") or cap) - alloc, 0)
        s["gpu_capacity"] += cap
        s["gpu_allocated"] += alloc
        s["gpu_free"] += free
        prod = n.get("product") or "GPU"
        p = s["products"].setdefault(prod, {"capacity": 0, "allocated": 0, "free": 0})
        p["capacity"] += cap
        p["allocated"] += alloc
        p["free"] += free
        for a in n.get("allocations") or []:
            t = a.get("workload_type") or "기타"
            s["by_workload_type"][t] = s["by_workload_type"].get(t, 0) + (a.get("gpu") or 0)
    return s
