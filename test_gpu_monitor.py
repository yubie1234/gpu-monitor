"""단위 테스트 — app.services 만 import (FastAPI 불필요).

FakeClient 는 path substring 으로 응답을 라우팅한다(model-monitor 와 동일 패턴).
파싱/집계/워크로드 분류/메트릭 로직을 고정한다.
"""

import unittest

from app.services.gpu import (
    node_gpu, node_ready, pod_gpu, pod_ready, short_gpu_product,
)
from app.services.workload import classify_workload
from app.services.collect import collect_allocations, collect_gpu_nodes
from app.services.snapshot import summarize
from app.services.prometheus import render_prometheus_metrics


class FakeClient:
    """routes: {path_substring: response_dict}. 매칭 안 되면 404."""

    def __init__(self, routes, default_namespace="default"):
        self.routes = routes
        self.default_namespace = default_namespace
        self.enabled = True

    def get(self, path):
        for frag, resp in self.routes.items():
            if frag in path:
                return True, resp, None
        return False, None, "HTTP 404 not found: %s" % path


def _node(name, product, cap, alloc=None):
    return {
        "metadata": {"name": name,
                     "labels": {"nvidia.com/gpu.product": product} if product else {}},
        "status": {
            "capacity": {"nvidia.com/gpu": str(cap)},
            "allocatable": {"nvidia.com/gpu": str(alloc if alloc is not None else cap)},
            "conditions": [{"type": "Ready", "status": "True"}],
        },
    }


def _pod(name, ns, gpu, node="gpu-node-01", labels=None, owner=None, phase="Running",
         ready=True):
    meta = {"name": name, "namespace": ns, "labels": labels or {}}
    if owner:
        meta["ownerReferences"] = [dict(owner, controller=True)]
    return {
        "metadata": meta,
        "spec": {"nodeName": node,
                 "containers": [{"resources": {"limits": {"nvidia.com/gpu": str(gpu)}}}]},
        "status": {"phase": phase,
                   "conditions": [{"type": "Ready",
                                   "status": "True" if ready else "False"}]},
    }


class TestGpuPrimitives(unittest.TestCase):
    def test_pod_gpu_limits_then_requests(self):
        self.assertEqual(pod_gpu(_pod("p", "ns", 4)), 4)
        pod = {"spec": {"containers": [
            {"resources": {"requests": {"nvidia.com/gpu": "2"}}}]}}
        self.assertEqual(pod_gpu(pod), 2)

    def test_pod_gpu_multi_container_sum(self):
        pod = {"spec": {"containers": [
            {"resources": {"limits": {"nvidia.com/gpu": "1"}}},
            {"resources": {"limits": {"nvidia.com/gpu": "3"}}}]}}
        self.assertEqual(pod_gpu(pod), 4)

    def test_pod_gpu_none(self):
        self.assertEqual(pod_gpu({"spec": {"containers": [{"resources": {}}]}}), 0)

    def test_short_gpu_product(self):
        self.assertEqual(short_gpu_product("NVIDIA-H100-80GB-HBM3"), "H100")
        self.assertEqual(short_gpu_product("NVIDIA-B200"), "B200")
        self.assertEqual(short_gpu_product("NVIDIA-A100-SXM4-80GB"), "A100")
        self.assertIsNone(short_gpu_product(None))

    def test_node_gpu(self):
        g = node_gpu(_node("n", "NVIDIA-H100-80GB-HBM3", 8, 8))
        self.assertEqual(g["capacity"], 8)
        self.assertEqual(g["allocatable"], 8)
        self.assertEqual(g["product"], "H100")

    def test_pod_ready(self):
        self.assertTrue(pod_ready(_pod("p", "ns", 1, ready=True)))
        self.assertFalse(pod_ready(_pod("p", "ns", 1, ready=False)))
        self.assertFalse(pod_ready(_pod("p", "ns", 1, phase="Pending")))

    def test_node_ready(self):
        self.assertTrue(node_ready(_node("n", "H100", 8)))


class TestClassifyWorkload(unittest.TestCase):
    def test_kserve_label(self):
        w = classify_workload(_pod("q-predictor-0", "kserve", 8,
                                   labels={"serving.kserve.io/inferenceservice": "qwen3"}))
        self.assertEqual(w, {"type": "KServe", "name": "qwen3"})

    def test_job(self):
        w = classify_workload(_pod("sft-abc", "ml", 2, labels={"job-name": "sft-42"}))
        self.assertEqual(w["type"], "Job")
        self.assertEqual(w["name"], "sft-42")

    def test_job_owner(self):
        w = classify_workload(_pod("j-xyz", "ml", 1, owner={"kind": "Job", "name": "train-9"}))
        self.assertEqual(w, {"type": "Job", "name": "train-9"})

    def test_notebook(self):
        w = classify_workload(_pod("nb-0", "research", 1, labels={"notebook-name": "jhwang"}))
        self.assertEqual(w, {"type": "Notebook", "name": "jhwang"})

    def test_deployment_from_replicaset(self):
        w = classify_workload(_pod("api-6c8b9d-abcde", "svc", 1,
                                   owner={"kind": "ReplicaSet", "name": "api-6c8b9d"}))
        self.assertEqual(w, {"type": "Deployment", "name": "api"})

    def test_statefulset(self):
        w = classify_workload(_pod("db-0", "svc", 1,
                                   owner={"kind": "StatefulSet", "name": "db"}))
        self.assertEqual(w, {"type": "StatefulSet", "name": "db"})

    def test_bare_pod(self):
        w = classify_workload(_pod("lonely", "ns", 1))
        self.assertEqual(w, {"type": "Pod", "name": "lonely"})


class TestCollect(unittest.TestCase):
    def test_collect_gpu_nodes_filters_non_gpu(self):
        cpu_node = {"metadata": {"name": "cpu-1", "labels": {}},
                    "status": {"capacity": {}, "allocatable": {},
                               "conditions": [{"type": "Ready", "status": "True"}]}}
        client = FakeClient({"/api/v1/nodes": {"items": [
            _node("gpu-1", "NVIDIA-H100-80GB-HBM3", 8, 8), cpu_node]}})
        nodes, errs = collect_gpu_nodes(client, {})
        self.assertEqual(errs, [])
        self.assertEqual([n["name"] for n in nodes], ["gpu-1"])
        self.assertEqual(nodes[0]["product"], "H100")

    def test_collect_gpu_nodes_error(self):
        client = FakeClient({})  # /api/v1/nodes 미매칭 -> 404
        nodes, errs = collect_gpu_nodes(client, {})
        self.assertEqual(nodes, [])
        self.assertTrue(errs and "nodes:" in errs[0])

    def test_collect_allocations_sums_and_free(self):
        pods = {"items": [
            _pod("q-predictor-0", "kserve", 8,
                 labels={"serving.kserve.io/inferenceservice": "qwen3"}),
            _pod("done-job", "ml", 4, phase="Succeeded"),   # 종료 -> 제외
            _pod("no-gpu", "ml", 0),                        # GPU 0 -> 제외
        ]}
        client = FakeClient({"spec.nodeName=gpu-node-01": pods})
        node = {"name": "gpu-node-01", "capacity": 8, "allocatable": 8,
                "allocated": 0, "free": None, "allocations": [], "error": None}
        collect_allocations(client, node)
        self.assertEqual(node["allocated"], 8)
        self.assertEqual(node["free"], 0)
        self.assertEqual(len(node["allocations"]), 1)
        self.assertEqual(node["allocations"][0]["workload_type"], "KServe")

    def test_collect_allocations_error(self):
        client = FakeClient({})  # pods 미매칭 -> 404
        node = {"name": "gpu-node-01", "capacity": 8, "allocatable": 8,
                "allocated": 0, "free": None, "allocations": [], "error": None}
        collect_allocations(client, node)
        self.assertIn("pods:", node["error"] or "")


class TestSummarize(unittest.TestCase):
    def _snap(self):
        return {"nodes": [
            {"name": "n1", "product": "H100", "capacity": 8, "allocatable": 8,
             "allocated": 8, "free": 0, "allocations": [
                 {"workload_type": "KServe", "gpu": 8}]},
            {"name": "n2", "product": "H100", "capacity": 8, "allocatable": 8,
             "allocated": 3, "free": 5, "allocations": [
                 {"workload_type": "Job", "gpu": 2},
                 {"workload_type": "KServe", "gpu": 1}]},
            {"name": "n3", "product": "B200", "capacity": 8, "allocatable": 8,
             "allocated": 1, "free": 7, "allocations": [
                 {"workload_type": "Notebook", "gpu": 1}]},
        ]}

    def test_totals(self):
        s = summarize(self._snap())
        self.assertEqual(s["node_count"], 3)
        self.assertEqual(s["gpu_capacity"], 24)
        self.assertEqual(s["gpu_allocated"], 12)
        self.assertEqual(s["gpu_free"], 12)

    def test_products(self):
        s = summarize(self._snap())
        self.assertEqual(s["products"]["H100"], {"capacity": 16, "allocated": 11, "free": 5})
        self.assertEqual(s["products"]["B200"]["capacity"], 8)

    def test_by_workload_type(self):
        s = summarize(self._snap())
        self.assertEqual(s["by_workload_type"]["KServe"], 9)
        self.assertEqual(s["by_workload_type"]["Job"], 2)
        self.assertEqual(s["by_workload_type"]["Notebook"], 1)

    def test_free_fallback_when_missing(self):
        snap = {"nodes": [{"name": "n", "product": "H100", "capacity": 8,
                           "allocatable": 8, "allocated": 3, "allocations": []}]}
        s = summarize(snap)
        self.assertEqual(s["gpu_free"], 5)


class TestPrometheus(unittest.TestCase):
    def test_render(self):
        snap = {"summary": {"gpu_capacity": 16, "gpu_allocated": 9, "gpu_free": 7,
                            "by_workload_type": {"KServe": 9}},
                "nodes": [{"name": "n1", "product": "H100", "capacity": 8,
                           "allocated": 8, "free": 0}]}
        text = render_prometheus_metrics(snap)
        self.assertIn("gpu_monitor_build_info{version=", text)
        self.assertIn("gpu_monitor_cluster_gpu_allocated 9", text)
        self.assertIn('gpu_monitor_node_gpu{node="n1",product="H100",state="allocated"} 8', text)
        self.assertIn('gpu_monitor_gpu_allocated_by_type{type="KServe"} 9', text)


if __name__ == "__main__":
    unittest.main()
