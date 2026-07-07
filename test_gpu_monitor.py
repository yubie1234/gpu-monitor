"""단위 테스트 — app.services 만 import (FastAPI 불필요).

FakeClient 는 path substring 으로 응답을 라우팅한다(model-monitor 와 동일 패턴).
파싱/집계/워크로드 분류/메트릭 로직을 고정한다.
"""

import asyncio
import unittest

from app.services.gpu import (
    node_gpu, node_ready, pod_gpu, pod_ready, short_gpu_product,
)
from app.services.workload import classify_workload
from app.services.collect import collect_allocations, collect_gpu_nodes
from app.services.snapshot import summarize
from app.services.prometheus import render_prometheus_metrics
from app.services.state import Refresher, SnapshotStore, build_meta


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
                 {"workload_type": "KServe", "gpu": 8, "namespace": "kserve",
                  "ready": True}]},
            {"name": "n2", "product": "H100", "capacity": 8, "allocatable": 8,
             "allocated": 3, "free": 5, "allocations": [
                 {"workload_type": "Job", "gpu": 2, "namespace": "team-ml",
                  "ready": False},
                 {"workload_type": "KServe", "gpu": 1, "namespace": "kserve",
                  "ready": True}]},
            {"name": "n3", "product": "B200", "capacity": 8, "allocatable": 8,
             "allocated": 1, "free": 7, "allocations": [
                 {"workload_type": "Notebook", "gpu": 1, "namespace": "research",
                  "ready": True}]},
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

    def test_by_namespace(self):
        s = summarize(self._snap())
        self.assertEqual(s["by_namespace"],
                         {"kserve": 9, "team-ml": 2, "research": 1})

    def test_by_ready(self):
        s = summarize(self._snap())
        self.assertEqual(s["by_ready"], {"true": 10, "false": 2})

    def test_by_namespace_and_ready_missing_keys(self):
        # namespace 없음 -> "기타", ready 없음 -> false 버킷
        snap = {"nodes": [{"name": "n", "capacity": 8, "allocated": 2,
                           "allocations": [{"workload_type": "Job", "gpu": 2}]}]}
        s = summarize(snap)
        self.assertEqual(s["by_namespace"], {"기타": 2})
        self.assertEqual(s["by_ready"], {"true": 0, "false": 2})

    def test_by_ready_zero_filled_without_allocations(self):
        s = summarize({"nodes": []})
        self.assertEqual(s["by_ready"], {"true": 0, "false": 0})
        self.assertEqual(s["by_namespace"], {})


class TestPrometheus(unittest.TestCase):
    def _snap(self):
        return {
            "summary": {"gpu_capacity": 16, "gpu_allocated": 9, "gpu_free": 7,
                        "node_count": 2,
                        "by_workload_type": {"KServe": 9},
                        "by_namespace": {"kserve": 9},
                        "by_ready": {"true": 9, "false": 0}},
            "nodes": [
                {"name": "n1", "product": "H100",
                 "product_raw": "NVIDIA-H100-80GB-HBM3", "ready": True,
                 "capacity": 8, "allocatable": 8, "allocated": 8, "free": 0,
                 "error": None},
                # 수집 실패 노드: error + free=None (allocated 과소집계 상태)
                {"name": "n2", "product": "H100",
                 "product_raw": "NVIDIA-H100-80GB-HBM3", "ready": False,
                 "capacity": 8, "allocatable": 8, "allocated": 1, "free": None,
                 "error": "pods: HTTP 403"},
            ],
            "k8s_enabled": True,
            "errors": ["nodes: 부분 실패"],
        }

    def test_render(self):
        text = render_prometheus_metrics(self._snap())
        self.assertIn("gpu_monitor_build_info{version=", text)
        self.assertIn("gpu_monitor_cluster_gpu_allocated 9", text)
        self.assertIn('gpu_monitor_node_gpu{node="n1",product="H100",state="allocated"} 8', text)
        self.assertIn('gpu_monitor_gpu_allocated_by_type{type="KServe"} 9', text)

    def test_render_new_families(self):
        text = render_prometheus_metrics(self._snap())
        self.assertIn("gpu_monitor_nodes 2", text)
        self.assertIn('gpu_monitor_node_gpu{node="n1",product="H100",state="allocatable"} 8',
                      text)
        self.assertIn('gpu_monitor_node_ready{node="n1"} 1', text)
        self.assertIn('gpu_monitor_node_ready{node="n2"} 0', text)
        self.assertIn('gpu_monitor_node_info{node="n1",product="H100",'
                      'product_raw="NVIDIA-H100-80GB-HBM3"} 1', text)
        self.assertIn('gpu_monitor_node_collect_error{node="n1",product="H100"} 0', text)
        self.assertIn('gpu_monitor_node_collect_error{node="n2",product="H100"} 1', text)
        self.assertIn('gpu_monitor_gpu_allocated_by_namespace{namespace="kserve"} 9', text)
        self.assertIn('gpu_monitor_gpu_allocated_by_ready{ready="true"} 9', text)
        self.assertIn('gpu_monitor_gpu_allocated_by_ready{ready="false"} 0', text)
        self.assertIn("gpu_monitor_collect_errors 1", text)
        self.assertIn("gpu_monitor_k8s_enabled 1", text)
        self.assertIn("gpu_monitor_demo 0", text)

    def test_render_error_node_free_none_skipped(self):
        # 수집 실패 노드(free=None)는 state="free" 라인이 없어야 한다(0 으로 오인 금지).
        text = render_prometheus_metrics(self._snap())
        self.assertNotIn('gpu_monitor_node_gpu{node="n2",product="H100",state="free"}', text)
        self.assertIn('gpu_monitor_node_gpu{node="n2",product="H100",state="capacity"} 8',
                      text)

    def test_render_metric_families_grouped(self):
        # text format 0.0.4: 같은 계열의 샘플은 한 그룹으로 이어져야 한다.
        text = render_prometheus_metrics(self._snap())
        groups, seen = [], set()
        for line in text.splitlines():
            if not line or line.startswith("#"):
                continue
            name = line.split("{")[0].split(" ")[0]
            if not groups or groups[-1] != name:
                self.assertNotIn(name, seen, "계열 %s 가 두 그룹으로 갈라짐" % name)
                seen.add(name)
                groups.append(name)


class TestObservability(unittest.TestCase):
    def test_render_without_meta_omits_observability(self):
        # 1-인자 호출(하위호환) — 관측성 계열은 전부 부재해야 한다(0 방출 금지).
        text = render_prometheus_metrics({"summary": {}, "nodes": []})
        self.assertNotIn("gpu_monitor_up", text)
        self.assertNotIn("gpu_monitor_last_success_timestamp_seconds", text)
        self.assertNotIn("gpu_monitor_refreshes_total", text)
        self.assertNotIn("gpu_monitor_refresh_failures_total", text)

    def test_render_with_meta(self):
        meta = {"up": True, "last_success_epoch": 1751852000.5,
                "refreshes": 7, "failures": 2}
        text = render_prometheus_metrics({"summary": {}, "nodes": []}, meta)
        self.assertIn("gpu_monitor_up 1", text)
        self.assertIn("gpu_monitor_last_success_timestamp_seconds 1751852000.500", text)
        self.assertIn("# TYPE gpu_monitor_refreshes_total counter", text)
        self.assertIn("gpu_monitor_refreshes_total 7", text)
        self.assertIn("# TYPE gpu_monitor_refresh_failures_total counter", text)
        self.assertIn("gpu_monitor_refresh_failures_total 2", text)

    def test_render_meta_without_success_omits_timestamp(self):
        # 성공 이력 없음 -> timestamp 라인 생략(0 방출 시 staleness 즉시 오발화).
        meta = {"up": False, "last_success_epoch": None, "refreshes": 0, "failures": 0}
        text = render_prometheus_metrics({"summary": {}, "nodes": []}, meta)
        self.assertIn("gpu_monitor_up 0", text)
        self.assertNotIn("gpu_monitor_last_success_timestamp_seconds", text)
        self.assertIn("gpu_monitor_refreshes_total 0", text)

    def test_build_meta_empty_store(self):
        store = SnapshotStore()
        meta = build_meta(store, Refresher({}, store, 15))
        self.assertEqual(meta, {"up": False, "last_success_epoch": None,
                                "refreshes": 0, "failures": 0})

    def test_build_meta_after_set(self):
        store = SnapshotStore()
        store.set({"nodes": []})
        self.assertTrue(build_meta(store, Refresher({}, store, 15))["up"])

    def test_refresh_once_success_updates_meta(self):
        store = SnapshotStore()
        r = Refresher({"demo": True}, store, 15)
        asyncio.run(r._refresh_once())
        self.assertEqual((r.refreshes, r.failures), (1, 0))
        self.assertIsNotNone(store.last_success_epoch)
        self.assertTrue(store.get().get("demo"))

    def test_refresh_once_failure_counts_and_keeps_last_success(self):
        store = SnapshotStore()
        r = Refresher(None, store, 15)   # settings=None -> build_snapshot 예외
        asyncio.run(r._refresh_once())
        self.assertEqual((r.refreshes, r.failures), (1, 1))
        self.assertIsNone(store.last_success_epoch)   # 실패 사이클은 갱신 금지
        self.assertIsNone(store.get())


if __name__ == "__main__":
    unittest.main()
