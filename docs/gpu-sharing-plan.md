# MIG / Time-slicing 대응 계획

> 범위: **NVIDIA 전용**. AMD/Intel 등 타 벤더는 스코프 밖(확정). 이 문서는 NVIDIA GPU 의
> **공유 모드**(MIG, time-slicing, MPS)에서 gpu-monitor 가 지금 무엇을 놓치는지와 어떻게
> 대응할지를 정리한다. "할당(allocation)이지 실사용률이 아니다"라는 프로젝트 경계는 유지한다.

## 1. 지금 왜 틀리나

현재 수집기는 GPU 리소스를 단일 키로만 본다:

- `GPU_RESOURCE = "nvidia.com/gpu"` — 단일 상수 ([app/services/gpu.py:7](../app/services/gpu.py#L7))
- `node_gpu` 가 이 키의 capacity/allocatable 만 읽음 ([gpu.py:52-61](../app/services/gpu.py#L52-L61))
- `collect_gpu_nodes` 가 이 키 capacity==0 이면 **노드를 통째로 드롭** ([collect.py:23-25](../app/services/collect.py#L23-L25))
- `pod_gpu` 가 이 키만 합산 ([gpu.py:19-28](../app/services/gpu.py#L19-L28))
- `short_gpu_product` 가 첫 `-` 토큰만 남김 → `-SHARED`/`-MIG-...` 접미가 소실 ([gpu.py:42-49](../app/services/gpu.py#L42-L49))

결과적으로 아래 두 모드에서 **GPU 노드가 대시보드에서 조용히 사라진다**(허위 음성 — 모니터링
도구로서 최악의 실패). 다른 두 모드에서는 **숫자의 의미가 왜곡**된다.

## 2. NVIDIA 공유 모드가 k8s 에 드러나는 방식

GFD(GPU Feature Discovery) + device-plugin 이 노드 status(리소스)와 라벨에 아래처럼 노출한다.

| 모드 | 리소스명 | capacity 의미 | 대표 노드 라벨 | 현재 동작 |
|---|---|---|---|---|
| **Whole GPU**(기본) | `nvidia.com/gpu` | 물리 GPU 수 | `nvidia.com/gpu.product=NVIDIA-H100-80GB-HBM3` | ✅ 정상 |
| **Time-slicing** `renameByDefault=false`(기본) | `nvidia.com/gpu` | 물리 × replicas (부풀려짐) | product 에 `-SHARED` 접미, `nvidia.com/gpu.replicas`, `nvidia.com/gpu.count`, `nvidia.com/gpu.sharing-strategy=time-slicing` | ⚠ 집계는 되나 capacity 가 물리수 아님 |
| **Time-slicing** `renameByDefault=true` / **MPS** | `nvidia.com/gpu.shared` | 논리 슬롯 수 | product 라벨 **미변경**(접미 없음), `sharing-strategy=time-slicing`\|`mps` | ❌ 노드 소멸(capacity 0) |
| **MIG single** | `nvidia.com/gpu` | MIG 인스턴스 수(예 8×7=56) | product 에 `-MIG-1g.10gb` 등, `nvidia.com/mig.strategy=single` | ⚠ 집계는 되나 물리수 아님 |
| **MIG mixed** | `nvidia.com/mig-1g.5gb`, `nvidia.com/mig-2g.10gb`, … | 프로파일별 인스턴스 수 | `nvidia.com/mig.strategy=mixed`, `nvidia.com/mig-<profile>.count` | ❌ 노드 소멸(gpu 키 0) |

> **NVIDIA 규격 주의**: `-SHARED` 접미는 `renameByDefault=false`(기본, 리소스명은 `nvidia.com/gpu`
> 유지) 일 때만 product 라벨에 붙는다. `renameByDefault=true`(리소스명이 `nvidia.com/gpu.shared`
> 로 바뀜)에서는 product 라벨이 **변경되지 않는다**. 즉 공유 모드 판별은 product 접미가 아니라
> **리소스명(`.shared`) + `nvidia.com/gpu.sharing-strategy` 라벨**을 우선 신호로 삼아야 한다.

핵심 구분: **물리 GPU 수**(사람이 "GPU 몇 장"이라 할 때의 수)와 **스케줄 가능한 논리 단위
수**(k8s 가 세는 수)가 공유 모드에서 갈라진다. 할당 모니터의 산수(allocated/free)는 논리
단위를 기준으로 해야 맞고, 사람에게 보여줄 때는 물리 수도 함께 있어야 오해가 없다.

## 3. 대응 옵션

### Option A — 광역 리소스 탐지 (최소 수정, 출혈 정지)
단일 키를 **NVIDIA GPU 리소스 매처**로 일반화한다: `nvidia.com/gpu`,
`nvidia.com/gpu.shared`, `nvidia.com/mig-*`(prefix) 를 모두 합산(노드 capacity/allocatable,
Pod requests 양쪽). 노드가 사라지지 않고 Pod 도 집계된다.

- 장점: 변경 최소, 허위 음성(노드 소멸) 제거. allocated/capacity 가 같은 단위라 내부 일관성 유지.
- 단점: "총 GPU 24" 가 실은 "논리 슬라이스 24" 일 수 있음 — **숫자 의미가 조용히 바뀜**.
  H100 whole 노드와 MIG 노드가 한 capacity 합계에 섞이면 단위가 혼재.

### Option B — 물리/논리 분리 + 공유 모드 인식 (권장)
A 의 탐지에 더해, GFD 라벨로 **물리 GPU 수와 논리 단위를 분리**한다.

- 노드별 `mode` 판정: `nvidia.com/mig.strategy`, `nvidia.com/gpu.sharing-strategy`, product
  의 `-SHARED`/`-MIG-` 접미로 `whole|time-sliced|mps|mig-single|mig-mixed` 도출.
- 노드에 두 축을 둔다: `gpu_units`(스케줄 단위 = 매칭 리소스 합, 할당 산수의 기준)와
  `gpu_physical`(`nvidia.com/gpu.count` 라벨, 없으면 미상).
- product 전체명 보존 + `mode` 배지 표시.
- summarize 는 할당 산수를 논리 단위로 유지하되 물리 수를 별도 필드로 보고 → "GPU 24" 와
  "슬라이스 168" 이 섞이지 않음.

- 장점: 정직함. 단위 혼동 없음. "할당≠실사용률" 경계 유지. MIG/공유 노드가 올바른 의미로 보임.
- 단점: 코드 증가 + 데이터 모델 추가(`node.mode`, `node.gpu_physical`), UI 배지/보조 숫자,
  신규 테스트 필요.

### Option C — 광역 탐지 + 경고 배지만 (중간)
A 의 탐지 + 공유 모드 노드에는 노드별 배지/경고("MIG/공유 — capacity 는 논리 슬라이스 수")만
붙이고 물리 수 환원은 하지 않는다. 데이터 모델은 `mode` 문자열 하나만 추가.

- 장점: A 의 단위 혼동을 사용자에게 최소한 **명시**. B 보다 훨씬 가벼움.
- 단점: 물리 수를 안 보여주므로 "실제 몇 장인지"는 여전히 답 못 함.

## 4. 확정 방향 (2026-07-06 결정)

**채택: Option B — 물리 우선 + 논리 병기.** capacity 는 GFD 라벨로 물리 GPU 수와 논리 단위를
분리해 둘 다 표기하고, 공유 모드 배지를 붙인다. "GPU 24" 와 "슬라이스 168" 이 섞이지 않는다.

**구현 시점: 나중(deferred).** 이 문서로 방향만 확정하고, 실제 코드 구현은 별도 작업으로
분리한다. 지금은 배포 파일(`deploy/`)만 사용한다.

구현은 두 단계로 나눈다(착수 시):

1. **Phase 1 (긴급):** 리소스 매처 일반화(§5). 노드 소멸/Pod 미집계(허위 음성)부터 없앤다.
   단독 배포 시 cluster 총계의 단위 혼동이 남으므로 Phase 2 와 가깝게 간다.
2. **Phase 2 (정합):** GFD 라벨로 물리/논리 분리 + `mode` 배지(= Option B 완성). 공유 모드에서도
   숫자가 정직해진다.

## 5. 구현 스케치 (Option B 채택 시)

- **`gpu.py`**
  - `GPU_RESOURCES` 매처 도입: 정확 키 `nvidia.com/gpu`·`nvidia.com/gpu.shared` + prefix
    `nvidia.com/mig-`. `pod_gpu`/`node_gpu` 가 매칭 키를 모두 합산.
  - `node_gpu` 가 라벨에서 `gpu.count`(물리), `gpu.replicas`, `mig.strategy`,
    `sharing-strategy` 를 읽어 `mode` 와 `gpu_physical` 도출. **모드 판별은 리소스명
    (`.shared`, `mig-*`) + `sharing-strategy`/`mig.strategy` 라벨을 우선 신호로** 삼는다
    (product 접미는 renameByDefault=true 에선 없으므로 보조 신호로만).
  - `short_gpu_product` 는 `-MIG-` 접미(및 renameByDefault=false 의 `-SHARED`)를 **보존하거나
    별도 배지로** — 첫 토큰 절단 규칙을 공유 모드에서 조건부로 완화.
- **`collect.py`**
  - capacity 필터를 "매칭 리소스 중 하나라도 >0" 으로 확대(현 `if not g["capacity"]`).
  - 노드 dict 에 `mode`, `gpu_physical` 추가.
- **`snapshot.py`**: `summarize` 에 물리 수 합계(`gpu_physical_total`)와 모드별 분포 추가.
  기존 allocated/free 산수는 논리 단위 유지.
- **`prometheus.py`**: `gpu_monitor_node_gpu` 에 `mode` 라벨 추가 검토, `..._gpu_physical`
  게이지 신설(선택).
- **`dashboard.html`**: 노드 카드에 `mode` 배지, capacity 옆 물리 수 병기.
- **테스트**: MIG mixed/single, time-slicing rename on/off, MPS 각각의 노드+Pod 픽스처로
  회귀 추가(CLAUDE.md 규약: 집계 로직 변경 시 회귀 테스트 필수).

## 6. 결정 현황

- ✅ **capacity 표기 semantics** — **물리 우선 + 논리 병기(Option B)** 로 확정(§4).
- ✅ **구현 시점** — 계획만 확정, 구현은 **나중**(deferred). 지금은 배포 파일만 사용.
- ⬜ **테스트 픽스처 확보** (구현 착수 시) — 실제 클러스터의 MIG/time-slicing 노드 JSON
  샘플을 얻을 수 있으면 픽스처 정확도가 크게 오른다(없으면 GFD 문서 기준으로 합성).
