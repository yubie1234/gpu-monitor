"""Prometheus text exposition 0.0.4 렌더 (스크레이프 경로에서 수집하지 않음)."""

from app import __version__


def _esc(v):
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def render_prometheus_metrics(snap, meta=None):
    """snap 계열은 항상, 관측성 계열(up/last_success/counters)은 meta 가 있을 때만.

    meta=None(기존 1-인자 호출)이면 관측성 라인을 전부 생략한다 — 0 으로 방출하면
    up=0(다운 오탐)/last_success=0(staleness 즉시 오발화)이 된다.
    """
    out = []
    snap = snap or {}
    s = snap.get("summary") or {}
    nodes = snap.get("nodes") or []

    out.append("# HELP gpu_monitor_build_info Build info.")
    out.append("# TYPE gpu_monitor_build_info gauge")
    out.append('gpu_monitor_build_info{version="%s"} 1' % _esc(__version__))

    for metric, key in (("capacity", "gpu_capacity"),
                        ("allocated", "gpu_allocated"),
                        ("free", "gpu_free")):
        out.append("# HELP gpu_monitor_cluster_gpu_%s Cluster GPU %s." % (metric, metric))
        out.append("# TYPE gpu_monitor_cluster_gpu_%s gauge" % metric)
        out.append("gpu_monitor_cluster_gpu_%s %d" % (metric, int(s.get(key) or 0)))

    # 물리 GPU 총수(gpu.count 합) — 온전+공유로 빠진 장수 모두 포함.
    out.append("# HELP gpu_monitor_cluster_gpu_physical"
               " Cluster physical GPU count (nvidia.com/gpu.count sum).")
    out.append("# TYPE gpu_monitor_cluster_gpu_physical gauge")
    out.append("gpu_monitor_cluster_gpu_physical %d" % int(s.get("gpu_physical") or 0))

    # 공유(타임슬라이스/MPS) 슬롯 — 온전 GPU 와 단위가 다르다(1 슬롯 ≠ 1 물리장).
    shared = s.get("shared") or {}
    out.append("# HELP gpu_monitor_cluster_shared_slots"
               " Cluster shared (time-sliced/MPS) slots by state — NOT physical GPUs.")
    out.append("# TYPE gpu_monitor_cluster_shared_slots gauge")
    for state in ("capacity", "allocated", "free"):
        out.append('gpu_monitor_cluster_shared_slots{state="%s"} %d'
                   % (state, int(shared.get(state) or 0)))

    out.append("# HELP gpu_monitor_nodes GPU node count.")
    out.append("# TYPE gpu_monitor_nodes gauge")
    out.append("gpu_monitor_nodes %d" % int(s.get("node_count") or 0))

    out.append("# HELP gpu_monitor_node_gpu Per-node GPU by state"
               " (capacity/allocatable/allocated/free).")
    out.append("# TYPE gpu_monitor_node_gpu gauge")
    for n in nodes:
        node = _esc(n.get("name"))
        prod = _esc(n.get("product") or "GPU")
        for state in ("capacity", "allocatable", "allocated", "free"):
            val = n.get(state)
            if val is None:
                continue
            out.append('gpu_monitor_node_gpu{node="%s",product="%s",state="%s"} %d'
                       % (node, prod, state, int(val)))

    # 노드 파생 계열은 node_gpu 루프에 끼우지 않는다 —
    # text format 은 같은 계열의 샘플이 한 그룹으로 이어져야 한다.
    out.append("# HELP gpu_monitor_node_ready Node Ready condition (1=Ready).")
    out.append("# TYPE gpu_monitor_node_ready gauge")
    for n in nodes:
        out.append('gpu_monitor_node_ready{node="%s"} %d'
                   % (_esc(n.get("name")), 1 if n.get("ready") else 0))

    out.append("# HELP gpu_monitor_node_info"
               " Node GPU product info (short name vs GFD raw label).")
    out.append("# TYPE gpu_monitor_node_info gauge")
    for n in nodes:
        out.append('gpu_monitor_node_info{node="%s",product="%s",product_raw="%s"} 1'
                   % (_esc(n.get("name")), _esc(n.get("product") or "GPU"),
                      _esc(n.get("product_raw") or "")))

    out.append("# HELP gpu_monitor_node_collect_error"
               " Per-node collect failure (1=error; node allocation undercounted).")
    out.append("# TYPE gpu_monitor_node_collect_error gauge")
    for n in nodes:
        out.append('gpu_monitor_node_collect_error{node="%s",product="%s"} %d'
                   % (_esc(n.get("name")), _esc(n.get("product") or "GPU"),
                      1 if n.get("error") else 0))

    out.append("# HELP gpu_monitor_node_physical"
               " Physical GPU count per node (nvidia.com/gpu.count).")
    out.append("# TYPE gpu_monitor_node_physical gauge")
    for n in nodes:
        phys = n.get("physical")
        if phys is None:
            continue
        out.append('gpu_monitor_node_physical{node="%s",product="%s"} %d'
                   % (_esc(n.get("name")), _esc(n.get("product") or "GPU"), int(phys)))

    out.append("# HELP gpu_monitor_node_shared_backing"
               " Physical GPUs on the node carved into shared pools (physical - whole).")
    out.append("# TYPE gpu_monitor_node_shared_backing gauge")
    for n in nodes:
        b = n.get("shared_backing")
        if b is None:
            continue
        out.append('gpu_monitor_node_shared_backing{node="%s",product="%s"} %d'
                   % (_esc(n.get("name")), _esc(n.get("product") or "GPU"), int(b)))

    out.append("# HELP gpu_monitor_node_shared"
               " Per-node partition slots by resource/mode/state"
               " (mode=timeslice/mps/mig; NOT physical GPUs).")
    out.append("# TYPE gpu_monitor_node_shared gauge")
    for n in nodes:
        node = _esc(n.get("name"))
        prod = _esc(n.get("product") or "GPU")
        for pool in n.get("shared_pools") or []:
            res = _esc(pool.get("resource"))
            mode = _esc(pool.get("mode") or "shared")
            for state in ("capacity", "allocatable", "allocated", "free"):
                val = pool.get(state)
                if val is None:
                    continue
                out.append('gpu_monitor_node_shared{node="%s",product="%s",'
                           'resource="%s",mode="%s",state="%s"} %d'
                           % (node, prod, res, mode, state, int(val)))

    out.append("# HELP gpu_monitor_gpu_allocated_by_type Allocated GPU by workload type.")
    out.append("# TYPE gpu_monitor_gpu_allocated_by_type gauge")
    for t, v in (s.get("by_workload_type") or {}).items():
        out.append('gpu_monitor_gpu_allocated_by_type{type="%s"} %d' % (_esc(t), int(v)))

    out.append("# HELP gpu_monitor_gpu_allocated_by_namespace Allocated GPU by namespace.")
    out.append("# TYPE gpu_monitor_gpu_allocated_by_namespace gauge")
    for ns, v in (s.get("by_namespace") or {}).items():
        out.append('gpu_monitor_gpu_allocated_by_namespace{namespace="%s"} %d'
                   % (_esc(ns), int(v)))

    out.append("# HELP gpu_monitor_gpu_allocated_by_purpose"
               " Allocated GPU by usage purpose (serving/training/interactive/batch/system).")
    out.append("# TYPE gpu_monitor_gpu_allocated_by_purpose gauge")
    for p, v in (s.get("by_purpose") or {}).items():
        out.append('gpu_monitor_gpu_allocated_by_purpose{purpose="%s"} %d' % (_esc(p), int(v)))

    out.append("# HELP gpu_monitor_gpu_allocated_by_environment"
               " Allocated GPU by deploy environment (prod/staging/dev).")
    out.append("# TYPE gpu_monitor_gpu_allocated_by_environment gauge")
    for e, v in (s.get("by_environment") or {}).items():
        out.append('gpu_monitor_gpu_allocated_by_environment{environment="%s"} %d'
                   % (_esc(e), int(v)))

    # ready 는 양쪽 라벨을 항상 방출한다(0 이어도) — 알럿식이 absent 에 걸리지 않게.
    by_ready = s.get("by_ready") or {}
    out.append("# HELP gpu_monitor_gpu_allocated_by_ready Allocated GPU by pod readiness.")
    out.append("# TYPE gpu_monitor_gpu_allocated_by_ready gauge")
    for r in ("true", "false"):
        out.append('gpu_monitor_gpu_allocated_by_ready{ready="%s"} %d'
                   % (r, int(by_ready.get(r) or 0)))

    out.append("# HELP gpu_monitor_collect_errors Snapshot-level collect error count.")
    out.append("# TYPE gpu_monitor_collect_errors gauge")
    out.append("gpu_monitor_collect_errors %d" % len(snap.get("errors") or []))

    out.append("# HELP gpu_monitor_k8s_enabled K8s client active (0=no in-cluster token).")
    out.append("# TYPE gpu_monitor_k8s_enabled gauge")
    out.append("gpu_monitor_k8s_enabled %d" % (1 if snap.get("k8s_enabled") else 0))

    out.append("# HELP gpu_monitor_demo Demo snapshot (1=MONITOR_DEMO).")
    out.append("# TYPE gpu_monitor_demo gauge")
    out.append("gpu_monitor_demo %d" % (1 if snap.get("demo") else 0))

    if meta is not None:
        out.append("# HELP gpu_monitor_up Snapshot present in store (0=first collect pending).")
        out.append("# TYPE gpu_monitor_up gauge")
        out.append("gpu_monitor_up %d" % (1 if meta.get("up") else 0))

        # 성공 이력이 없으면 라인 자체를 생략 — 0 방출은 staleness 즉시 오발화.
        last = meta.get("last_success_epoch")
        if last is not None:
            out.append("# HELP gpu_monitor_last_success_timestamp_seconds"
                       " Last successful refresh (unix epoch).")
            out.append("# TYPE gpu_monitor_last_success_timestamp_seconds gauge")
            out.append("gpu_monitor_last_success_timestamp_seconds %.3f" % float(last))

        for name, key, help_ in (
                ("gpu_monitor_refreshes_total", "refreshes",
                 "Refresh attempts (heartbeat)."),
                ("gpu_monitor_refresh_failures_total", "failures",
                 "Refresh attempts failed with unexpected exception.")):
            val = meta.get(key)
            if val is None:
                continue
            out.append("# HELP %s %s" % (name, help_))
            out.append("# TYPE %s counter" % name)
            out.append("%s %d" % (name, int(val)))

    return "\n".join(out) + "\n"
