"""노드 중심 수집: GPU 노드 목록 + 각 노드의 GPU 점유 Pod(allocation).

한 노드 수집 실패가 전체를 막지 않게, 노드별 예외는 호출측(snapshot)에서 잡아 계속한다.
"""

import urllib.parse

from app.services.gpu import node_gpu, node_ready, pod_gpu, pod_ready
from app.services.workload import classify_workload


def collect_gpu_nodes(client, settings):
    """GPU capacity>0 인 노드만 수집 -> (nodes[], errors[]). GPU 없는 노드는 제외."""
    sel = settings.get("node_label_selector")
    path = "/api/v1/nodes"
    if sel:
        path += "?labelSelector=%s" % urllib.parse.quote(sel, safe="=,")
    ok, data, err = client.get(path)
    if not ok:
        return [], ["nodes: %s" % err]
    nodes = []
    for n in data.get("items") or []:
        g = node_gpu(n)
        if not g["capacity"]:
            continue
        nodes.append({
            "name": (n.get("metadata") or {}).get("name"),
            "ready": node_ready(n),
            "product": g["product"],
            "product_raw": g["product_raw"],
            "capacity": g["capacity"],
            "allocatable": g["allocatable"],
            "allocated": 0,
            "free": None,
            "allocations": [],
            "error": None,
        })
    return nodes, []


def collect_allocations(client, node):
    """노드 위 GPU 점유 Pod 들을 allocation 으로 채운다(node dict in-place)."""
    ok, data, err = client.get(
        "/api/v1/pods?fieldSelector=spec.nodeName=%s"
        % urllib.parse.quote(node["name"]))
    if not ok:
        node["error"] = "pods: %s" % err
        return
    allocated = 0
    for pod in data.get("items") or []:
        # 종료/실패 Pod 은 GPU 를 놓았다고 본다(completed Job 등).
        phase = ((pod.get("status") or {}).get("phase")) or ""
        if phase in ("Succeeded", "Failed"):
            continue
        g = pod_gpu(pod)
        if g <= 0:
            continue
        meta = pod.get("metadata") or {}
        wl = classify_workload(pod)
        allocated += g
        node["allocations"].append({
            "namespace": meta.get("namespace"),
            "pod": meta.get("name"),
            "workload": wl["name"],
            "workload_type": wl["type"],
            "gpu": g,
            "ready": pod_ready(pod),
        })
    node["allocated"] = allocated
    base = node["allocatable"] or node["capacity"] or 0
    node["free"] = max(base - allocated, 0)
    node["allocations"].sort(key=lambda a: (-(a["gpu"] or 0), str(a["workload"] or "")))
