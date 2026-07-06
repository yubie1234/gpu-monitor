"""in-cluster Kubernetes API 클라이언트 (표준 라이브러리 urllib + ssl 만 사용).

노드의 GPU capacity/allocatable, 노드 위 Pod 의 GPU 점유량은 컨트롤 플레인(k8s API)에
물어봐야 알 수 있다. 그 호출을 담당하는 얇은 클라이언트 (model-monitor 와 동일).
"""

import json
import os
import ssl
import urllib.error
import urllib.request


class K8sClient:
    """in-cluster Kubernetes API 를 표준 라이브러리만으로 호출."""

    def __init__(self, api_server, token, ssl_ctx, timeout, default_namespace):
        self.api_server = api_server.rstrip("/") if api_server else None
        self.token = token
        self.ssl_ctx = ssl_ctx
        self.timeout = timeout
        self.default_namespace = default_namespace or "default"

    @property
    def enabled(self):
        return bool(self.api_server)

    @classmethod
    def from_settings(cls, settings):
        """in-cluster ServiceAccount 토큰/CA 가 있으면 활성, 없으면 None(클러스터 밖)."""
        api_server = settings.get("k8s_api_server")
        if not api_server:
            host = os.environ.get("KUBERNETES_SERVICE_HOST")
            port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
            if host:
                api_server = "https://%s:%s" % (host, port)

        token = None
        token_file = settings.get("k8s_token_file")
        if token_file and os.path.exists(token_file):
            try:
                with open(token_file) as f:
                    token = f.read().strip()
            except OSError:
                token = None

        if not token:          # 토큰 없으면 클러스터 밖(개발환경) → 비활성
            return None

        ssl_ctx = None
        if settings.get("k8s_insecure"):
            ssl_ctx = ssl._create_unverified_context()
        else:
            ca = settings.get("k8s_ca_file")
            try:
                if ca and os.path.exists(ca):
                    ssl_ctx = ssl.create_default_context(cafile=ca)
                else:
                    ssl_ctx = ssl.create_default_context()
            except Exception:  # noqa: BLE001
                ssl_ctx = ssl._create_unverified_context()

        ns = settings.get("default_namespace")
        if not ns:
            ns_file = "/var/run/secrets/kubernetes.io/serviceaccount/namespace"
            if os.path.exists(ns_file):
                try:
                    with open(ns_file) as f:
                        ns = f.read().strip()
                except OSError:
                    ns = None

        return cls(api_server=api_server, token=token, ssl_ctx=ssl_ctx,
                   timeout=settings.get("k8s_timeout", 6.0), default_namespace=ns)

    def get(self, path):
        """k8s API GET -> (ok, data, err)."""
        if not self.api_server:
            return False, None, "k8s api server not configured"
        url = self.api_server + path
        headers = {"Accept": "application/json",
                   "Authorization": "Bearer %s" % self.token}
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(
                    req, timeout=self.timeout, context=self.ssl_ctx) as resp:
                return True, json.loads(resp.read().decode("utf-8", "replace")), None
        except urllib.error.HTTPError as e:
            return False, None, "HTTP %s %s" % (e.code, e.reason)
        except Exception as e:  # noqa: BLE001
            return False, None, "%s: %s" % (type(e).__name__, e)
