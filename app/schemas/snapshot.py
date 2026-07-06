"""응답 모델. 전 필드 Optional + extra='allow' — 스냅샷 dict 에서 아무것도 떨어뜨리지 않는다.

수집기는 dict 로 만들고 테스트하며, 이 모델은 OpenAPI 문서/경계 검증에만 쓴다.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict


class Allocation(BaseModel):
    model_config = ConfigDict(extra="allow")
    namespace: Optional[str] = None
    pod: Optional[str] = None
    workload: Optional[str] = None
    workload_type: Optional[str] = None
    gpu: Optional[int] = None
    ready: Optional[bool] = None


class Node(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: Optional[str] = None
    ready: Optional[bool] = None
    product: Optional[str] = None
    product_raw: Optional[str] = None
    capacity: Optional[int] = None
    allocatable: Optional[int] = None
    allocated: Optional[int] = None
    free: Optional[int] = None
    allocations: Optional[List[Allocation]] = None
    error: Optional[str] = None


class Summary(BaseModel):
    model_config = ConfigDict(extra="allow")
    node_count: Optional[int] = None
    gpu_capacity: Optional[int] = None
    gpu_allocated: Optional[int] = None
    gpu_free: Optional[int] = None
    products: Optional[Dict[str, Any]] = None
    by_workload_type: Optional[Dict[str, int]] = None


class Snapshot(BaseModel):
    model_config = ConfigDict(extra="allow")
    version: Optional[str] = None
    ts: Optional[str] = None
    nodes: Optional[List[Node]] = None
    summary: Optional[Summary] = None
    k8s_enabled: Optional[bool] = None
    errors: Optional[List[str]] = None
