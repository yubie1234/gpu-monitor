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
    purpose: Optional[str] = None      # serving/training/interactive/batch/system/기타
    environment: Optional[str] = None  # prod/staging/dev/<raw>/기타
    gpu: Optional[int] = None
    ready: Optional[bool] = None


class SharedAllocation(BaseModel):
    model_config = ConfigDict(extra="allow")
    namespace: Optional[str] = None
    pod: Optional[str] = None
    workload: Optional[str] = None
    workload_type: Optional[str] = None
    purpose: Optional[str] = None
    environment: Optional[str] = None
    slots: Optional[int] = None  # 온전 GPU 와 단위가 다르다(타임슬라이스/MPS 슬롯)
    ready: Optional[bool] = None


class SharedPool(BaseModel):
    """파티션 풀 — 공유(nvidia.com/gpu.<프로파일>) 또는 MIG(nvidia.com/mig-<프로파일>).
    슬롯/인스턴스 단위(1 ≠ 1 물리장)."""
    model_config = ConfigDict(extra="allow")
    resource: Optional[str] = None   # 예: nvidia.com/gpu.10gb, nvidia.com/mig-1g.10gb
    profile: Optional[str] = None    # 예: 10gb, 1g.10gb
    mode: Optional[str] = None       # timeslice | mps | mig
    capacity: Optional[int] = None
    allocatable: Optional[int] = None
    allocated: Optional[int] = None
    free: Optional[int] = None
    allocations: Optional[List[SharedAllocation]] = None


class Node(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: Optional[str] = None
    ready: Optional[bool] = None
    product: Optional[str] = None
    product_raw: Optional[str] = None
    # capacity/allocated/free 는 온전(nvidia.com/gpu) GPU 기준.
    capacity: Optional[int] = None
    allocatable: Optional[int] = None
    allocated: Optional[int] = None
    free: Optional[int] = None
    physical: Optional[int] = None        # 물리 GPU 장수 (nvidia.com/gpu.count)
    replicas: Optional[int] = None        # 공유 GPU 1장당 슬롯 수
    sharing_strategy: Optional[str] = None  # time-slicing / mps
    mig_strategy: Optional[str] = None    # single / mixed
    shared_backing: Optional[int] = None  # 파티션(공유/MIG)으로 빠진 물리 장수 (= physical - capacity)
    shared_pools: Optional[List[SharedPool]] = None
    allocations: Optional[List[Allocation]] = None
    error: Optional[str] = None


class Summary(BaseModel):
    model_config = ConfigDict(extra="allow")
    node_count: Optional[int] = None
    # gpu_* 와 by_* 는 온전(nvidia.com/gpu) GPU 기준.
    gpu_capacity: Optional[int] = None
    gpu_allocated: Optional[int] = None
    gpu_free: Optional[int] = None
    gpu_physical: Optional[int] = None       # 물리 GPU 총수 (gpu.count 합)
    gpu_shared_backing: Optional[int] = None  # 공유로 빠진 물리 장수 합
    products: Optional[Dict[str, Any]] = None
    shared: Optional[Dict[str, Any]] = None  # 공유 슬롯 집계(capacity/allocated/free/by_profile)
    by_workload_type: Optional[Dict[str, int]] = None
    by_namespace: Optional[Dict[str, int]] = None
    by_purpose: Optional[Dict[str, int]] = None      # 사용 목적별 할당 GPU
    by_environment: Optional[Dict[str, int]] = None  # 배포 환경별 할당 GPU
    by_ready: Optional[Dict[str, int]] = None  # 키는 "true"/"false"


class Snapshot(BaseModel):
    model_config = ConfigDict(extra="allow")
    version: Optional[str] = None
    ts: Optional[str] = None
    nodes: Optional[List[Node]] = None
    summary: Optional[Summary] = None
    k8s_enabled: Optional[bool] = None
    errors: Optional[List[str]] = None
    # 신선도 meta — 요청 시점에 라우트가 채운다(스냅샷 빌드가 아니라).
    # age_seconds/stale/interval_seconds. 첫 수집 전이면 age=None, stale=False.
    meta: Optional[Dict[str, Any]] = None
