"""설정 로딩.

공통 설정은 환경변수(pydantic-settings)로, 선택적 중첩 설정(k8s.*)은 설정 파일(.json/.yaml)로.
우선순위: 환경변수 > 설정 파일 > 기본값.

수집기/K8sClient.from_settings 는 평범한 dict 를 받으므로 build_collector_settings() 가
Settings + 파일을 합쳐 그 dict 를 만든다. (단위 테스트는 순수 dict 를 넘겨 검증)
"""

import json
import os
from functools import lru_cache
from typing import Any, Dict, Optional

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- 웹 서버 ---
    host: str = Field("0.0.0.0", validation_alias=AliasChoices("MONITOR_HOST", "HOST"))
    port: int = Field(8089, validation_alias=AliasChoices("MONITOR_PORT", "PORT"))
    interval: float = Field(15.0, validation_alias=AliasChoices("MONITOR_INTERVAL"))
    demo: bool = Field(False, validation_alias=AliasChoices("MONITOR_DEMO"))
    root_path: str = Field("", validation_alias=AliasChoices("MONITOR_ROOT_PATH"))

    # --- 수집 ---
    node_label_selector: Optional[str] = Field(
        None, validation_alias=AliasChoices("MONITOR_NODE_SELECTOR"))
    metrics: bool = Field(True, validation_alias=AliasChoices("MONITOR_METRICS"))

    # --- k8s ---
    k8s_api_server: Optional[str] = Field(
        None, validation_alias=AliasChoices("MONITOR_K8S_API_SERVER"))
    k8s_token_file: str = Field(
        _SA_DIR + "/token", validation_alias=AliasChoices("MONITOR_K8S_TOKEN_FILE"))
    k8s_ca_file: str = Field(
        _SA_DIR + "/ca.crt", validation_alias=AliasChoices("MONITOR_K8S_CA_FILE"))
    k8s_insecure: bool = Field(
        False, validation_alias=AliasChoices("MONITOR_K8S_INSECURE"))
    k8s_timeout: float = Field(
        6.0, validation_alias=AliasChoices("MONITOR_K8S_TIMEOUT"))

    config_file: Optional[str] = Field(
        None, validation_alias=AliasChoices("MONITOR_CONFIG_FILE", "CONFIG_FILE"))


def normalize_root_path(root_path: str) -> str:
    rp = (root_path or "").strip()
    if not rp or rp == "/":
        return ""
    if not rp.startswith("/"):
        rp = "/" + rp
    return rp.rstrip("/")


def load_config_file(path: str) -> Dict[str, Any]:
    if not path or not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    if path.endswith((".yaml", ".yml")):
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "config '%s' 는 YAML 인데 PyYAML 이 없습니다. "
                "JSON(.json)을 쓰거나 PyYAML 을 설치하세요." % path) from exc
        return yaml.safe_load(text) or {}
    return json.loads(text)


def build_collector_settings(settings: Settings) -> Dict[str, Any]:
    cfg = load_config_file(settings.config_file) if settings.config_file else {}
    k8s = cfg.get("k8s") if isinstance(cfg.get("k8s"), dict) else {}
    return {
        "demo": settings.demo,
        "node_label_selector": (
            settings.node_label_selector or cfg.get("node_label_selector")),
        "metrics": settings.metrics,
        "k8s_api_server": settings.k8s_api_server or k8s.get("api_server"),
        "k8s_token_file": settings.k8s_token_file,
        "k8s_ca_file": settings.k8s_ca_file,
        "k8s_insecure": bool(settings.k8s_insecure or k8s.get("insecure")),
        "k8s_timeout": float(settings.k8s_timeout),
        "default_namespace": k8s.get("default_namespace"),
    }


@lru_cache
def get_settings() -> Settings:
    return Settings()
