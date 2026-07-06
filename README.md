# gpu-monitor `v0.1.0`

노드별 **GPU 할당(allocation) 현황** 대시보드. 클러스터의 각 노드가 어떤 GPU를 몇 개
가졌고(capacity), 그중 몇 개가 어떤 워크로드에 **할당**됐는지(allocated), 몇 개가
비어 있는지(free)를 한눈에 보여준다.

> **할당 ≠ 실사용률.** 이 도구는 Pod 이 요청/점유한 `nvidia.com/gpu` 수를 본다.
> GPU가 실제 몇 % 바쁜지(사용률·VRAM·온도)는 다루지 않는다 — 그건 DCGM + Grafana 영역.

`model-monitor`(LiteLLM 서빙 모니터)의 형제 프로젝트다. 다만 축이 다르다:
`model-monitor` 는 *LiteLLM model → backend*, `gpu-monitor` 는 *Node → GPU → Workload*.

## 무엇을 보여주나

- **노드별**: GPU 장치명(H100/B200…) · 할당/총량 · 유휴
- **노드 내 할당 목록**: 각 GPU 점유 Pod 을 **워크로드 타입**과 함께
  (KServe · Job · Notebook · Deployment · StatefulSet · …, Pod 라벨/owner 로 추정)
- **클러스터 집계**: 총/할당/유휴 GPU, 장치별·워크로드 타입별 분포
- Prometheus `/metrics`

## 실행

```bash
python3 -m pip install -r requirements.txt

# 라이브 (in-cluster; ServiceAccount 토큰 자동 사용)
uvicorn app.main:app --host 0.0.0.0 --port 8089
python3 -m app

# 라이브 엔드포인트 없이 미리보기
MONITOR_DEMO=true uvicorn app.main:app --port 8089
```

## 엔드포인트

- `/` — 대시보드(HTML)
- `/api/snapshot` — 노드별 GPU 할당 JSON
- `/snapshot.json` — 다운로드
- `/metrics` — Prometheus
- `/healthz`, `/readyz`

## 필요한 RBAC

읽기 전용, cluster-scope:

- `nodes`: `get`, `list`
- `pods`: `list` (전 네임스페이스 — `fieldSelector=spec.nodeName` 로 노드별 조회)

## 배포 (in-cluster)

`deploy/` 에 매니페스트가 있다:

```bash
./ci.sh && ./push.sh                  # 이미지 빌드 -> 사내 레지스트리 push
kubectl apply -f deploy/k8s.yaml      # Namespace/SA/ClusterRole(+Binding)/Deployment/Service/Ingress/PodMonitor
kubectl apply -f deploy/prometheus-alerts.yaml   # (선택) PrometheusRule
```

- 위 RBAC(nodes/pods 읽기)은 `deploy/k8s.yaml` 의 ClusterRole 로 함께 배포된다. 없으면 앱이
  토큰은 있어도 조회에 실패하니 반드시 적용.
- 컨테이너는 포트 **8089 고정**(Dockerfile ENTRYPOINT) — `MONITOR_PORT` env 는 컨테이너에선
  무시된다. Service/probe 도 8089 기준.
- `/metrics` 는 현재 **무인증**이다(형제 프로젝트의 metrics 토큰 인증 미이식). 노출이 걱정되면
  NetworkPolicy 로 스크레이프 소스를 제한할 것.
- MIG / time-slicing 노드 대응은 아직 없다 — [docs/gpu-sharing-plan.md](docs/gpu-sharing-plan.md) 참고.

## 테스트

```bash
python3 -m unittest -v            # stdlib unittest, FastAPI 불필요
```

## 설정 (env)

| env | 기본 | 의미 |
|---|---|---|
| `MONITOR_PORT` | `8089` | 포트 |
| `MONITOR_INTERVAL` | `15` | 백그라운드 수집 주기(초) |
| `MONITOR_DEMO` | `false` | 샘플 데이터 미리보기 |
| `MONITOR_NODE_SELECTOR` | – | GPU 노드 필터 라벨셀렉터(예: `nvidia.com/gpu.present=true`) |
| `MONITOR_METRICS` | `true` | `/metrics` on/off |
| `MONITOR_K8S_*` | – | api_server / token_file / ca_file / insecure / timeout |

## 이미지

```bash
./ci.sh        # docker build -> ai-tool/gpu-monitor:<version> + :latest
./push.sh      # retag -> 10.92.20.77:5002 로 push
```
