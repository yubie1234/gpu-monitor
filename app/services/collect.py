"""노드 중심 수집: GPU 노드 목록 + 각 노드의 GPU 점유 Pod(allocation).

한 노드 수집 실패가 전체를 막지 않게, 노드별 예외는 호출측(snapshot)에서 잡아 계속한다.
온전(nvidia.com/gpu)과 공유 풀(nvidia.com/gpu.<프로파일>)은 단위가 달라 따로 집계한다.
"""

import urllib.parse

from app.services.gpu import (GPU_RESOURCE, node_gpu, node_ready,
                              pod_gpu_resources, pod_ready, shared_profile)
from app.services.workload import classify_workload


def collect_gpu_nodes(client, settings):
    """GPU 리소스가 있는 노드만 수집 -> (nodes[], errors[]).

    온전 GPU capacity, 공유 풀, 또는 물리 장수(gpu.count) 중 하나라도 있으면 GPU 노드로 본다
    (전부 타임슬라이스라 nvidia.com/gpu 가 0 인 노드도 놓치지 않게).
    """
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
        if not (g["capacity"] or g["shared_pools"] or g["physical"]):
            continue
        physical = g["physical"]
        # 공유로 빠진 물리 장수 = 물리 총 - 온전 노출분 (라벨 없으면 미상).
        backing = max(physical - g["capacity"], 0) if physical is not None else None
        pools = [dict(p, allocated=0, free=None, allocations=[])
                 for p in g["shared_pools"]]
        nodes.append({
            "name": (n.get("metadata") or {}).get("name"),
            "ready": node_ready(n),
            "product": g["product"],
            "product_raw": g["product_raw"],
            "capacity": g["capacity"],
            "allocatable": g["allocatable"],
            "allocated": 0,
            "free": None,
            "physical": physical,
            "replicas": g["replicas"],
            "sharing_strategy": g["sharing_strategy"],
            "shared_backing": backing,
            "shared_pools": pools,
            "allocations": [],
            "error": None,
        })
    return nodes, []


def collect_allocations(client, node):
    """노드 위 GPU 점유 Pod 들을 allocation 으로 채운다(node dict in-place).

    온전 GPU 점유는 node['allocations'] 에, 공유 슬롯 점유는 해당 shared_pool 의
    allocations 에 나눠 기록한다(단위가 달라 합치지 않음).
    """
    ok, data, err = client.get(
        "/api/v1/pods?fieldSelector=spec.nodeName=%s"
        % urllib.parse.quote(node["name"]))
    if not ok:
        node["error"] = "pods: %s" % err
        return
    pools = {p["resource"]: p for p in node.get("shared_pools") or []}
    whole_allocated = 0
    for pod in data.get("items") or []:
        # 종료/실패 Pod 은 GPU 를 놓았다고 본다(completed Job 등).
        phase = ((pod.get("status") or {}).get("phase")) or ""
        if phase in ("Succeeded", "Failed"):
            continue
        usage = pod_gpu_resources(pod)
        if not usage:
            continue
        meta = pod.get("metadata") or {}
        wl = classify_workload(pod)
        base = {"namespace": meta.get("namespace"), "pod": meta.get("name"),
                "workload": wl["name"], "workload_type": wl["type"],
                "ready": pod_ready(pod)}
        whole = usage.get(GPU_RESOURCE, 0)
        if whole > 0:
            whole_allocated += whole
            node["allocations"].append(dict(base, gpu=whole))
        for res, qty in usage.items():
            if res == GPU_RESOURCE:
                continue
            pool = pools.get(res)
            if pool is None:  # capacity 엔 없던 공유 리소스를 Pod 이 점유 -> 방어적으로 풀 생성
                pool = {"resource": res, "profile": shared_profile(res),
                        "capacity": 0, "allocatable": 0, "allocated": 0,
                        "free": None, "allocations": []}
                pools[res] = pool
                node.setdefault("shared_pools", []).append(pool)
            pool["allocated"] = (pool.get("allocated") or 0) + qty
            pool["allocations"].append(dict(base, slots=qty))
    node["allocated"] = whole_allocated
    whole_base = node["allocatable"] or node["capacity"] or 0
    node["free"] = max(whole_base - whole_allocated, 0)
    node["allocations"].sort(key=lambda a: (-(a["gpu"] or 0), str(a["workload"] or "")))
    for p in node.get("shared_pools") or []:
        pbase = p.get("allocatable") or p.get("capacity") or 0
        p["free"] = max(pbase - (p.get("allocated") or 0), 0)
        p["allocations"].sort(
            key=lambda a: (-(a.get("slots") or 0), str(a.get("workload") or "")))
