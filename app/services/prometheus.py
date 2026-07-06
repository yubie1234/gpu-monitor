"""Prometheus text exposition 0.0.4 렌더 (스크레이프 경로에서 수집하지 않음)."""

from app import __version__


def _esc(v):
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def render_prometheus_metrics(snap):
    out = []
    snap = snap or {}
    s = snap.get("summary") or {}

    out.append("# HELP gpu_monitor_build_info Build info.")
    out.append("# TYPE gpu_monitor_build_info gauge")
    out.append('gpu_monitor_build_info{version="%s"} 1' % _esc(__version__))

    for metric, key in (("capacity", "gpu_capacity"),
                        ("allocated", "gpu_allocated"),
                        ("free", "gpu_free")):
        out.append("# HELP gpu_monitor_cluster_gpu_%s Cluster GPU %s." % (metric, metric))
        out.append("# TYPE gpu_monitor_cluster_gpu_%s gauge" % metric)
        out.append("gpu_monitor_cluster_gpu_%s %d" % (metric, int(s.get(key) or 0)))

    out.append("# HELP gpu_monitor_node_gpu Per-node GPU by state (capacity/allocated/free).")
    out.append("# TYPE gpu_monitor_node_gpu gauge")
    for n in snap.get("nodes") or []:
        node = _esc(n.get("name"))
        prod = _esc(n.get("product") or "GPU")
        for state in ("capacity", "allocated", "free"):
            val = n.get(state)
            if val is None:
                continue
            out.append('gpu_monitor_node_gpu{node="%s",product="%s",state="%s"} %d'
                       % (node, prod, state, int(val)))

    out.append("# HELP gpu_monitor_gpu_allocated_by_type Allocated GPU by workload type.")
    out.append("# TYPE gpu_monitor_gpu_allocated_by_type gauge")
    for t, v in (s.get("by_workload_type") or {}).items():
        out.append('gpu_monitor_gpu_allocated_by_type{type="%s"} %d' % (_esc(t), int(v)))

    return "\n".join(out) + "\n"
