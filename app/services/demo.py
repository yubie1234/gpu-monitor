"""라이브 클러스터 없이 미리보기용 샘플 스냅샷 (MONITOR_DEMO=true)."""

from datetime import datetime

from app import __version__
from app.services.snapshot import summarize


def demo_snapshot():
    nodes = [
        {"name": "gpu-node-01", "ready": True, "product": "H100",
         "product_raw": "NVIDIA-H100-80GB-HBM3", "capacity": 8, "allocatable": 8,
         "allocated": 8, "free": 0, "error": None,
         "allocations": [
             {"namespace": "kserve", "pod": "qwen3-72b-instruct-predictor-0",
              "workload": "qwen3-72b-instruct", "workload_type": "KServe",
              "gpu": 8, "ready": True}]},
        {"name": "gpu-node-02", "ready": True, "product": "H100",
         "product_raw": "NVIDIA-H100-80GB-HBM3", "capacity": 8, "allocatable": 8,
         "allocated": 3, "free": 5, "error": None,
         "allocations": [
             {"namespace": "team-ml", "pod": "sft-run-42-9f2k7",
              "workload": "sft-run-42", "workload_type": "Job", "gpu": 2, "ready": True},
             {"namespace": "kserve", "pod": "llama3-8b-eval-predictor-6c8b9-abcde",
              "workload": "llama3-8b-eval", "workload_type": "KServe", "gpu": 1,
              "ready": True}]},
        {"name": "gpu-node-03", "ready": True, "product": "B200",
         "product_raw": "NVIDIA-B200", "capacity": 8, "allocatable": 8,
         "allocated": 1, "free": 7, "error": None,
         "allocations": [
             {"namespace": "research", "pod": "jhwang-notebook-0",
              "workload": "jhwang", "workload_type": "Notebook", "gpu": 1,
              "ready": True}]},
    ]
    snap = {"version": __version__,
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "nodes": nodes, "summary": {}, "k8s_enabled": False,
            "errors": [], "demo": True}
    snap["summary"] = summarize(snap)
    return snap
