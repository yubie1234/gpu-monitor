# 브랜치 정리 (develop 미반영 브랜치 분석)

> 2026-07-12 기준. `git branch -r --no-merged origin/develop` 으로 잡히는 브랜치 7개를
> 전수 분석한 결과 — **전부 내용이 develop 에 이미 반영/대체되어 삭제해도 안전**하다.
> (이 세션의 토큰은 지정 브랜치 push 만 허용해 원격 삭제가 403 으로 거부됨 →
> 아래 명령을 push 권한 있는 로컬에서 실행할 것.)

## 1. 미반영으로 보이지만 실제로는 반영/대체 완료 (삭제 대상 7개)

git 커밋 그래프상으로만 미반영(`--no-merged`)일 뿐, 내용은 스쿼시/재작성 형태로
develop 에 들어가 있다.

| 브랜치 | tip SHA | 근거 |
|---|---|---|
| `feature/dashboard-alloc-integrity` | `f79ef58` | **PR #10** 이 4개 dashboard 브랜치의 기능을 develop 최신 기준으로 재작성·통합(충돌 해소 포함)해 머지함 (2026-07-09) |
| `feature/dashboard-freshness-filter` | `5d91048` | PR #10 통합 (검색·유휴/FULL 칩 반영, `ts_epoch` 방식은 의도적 폐기) |
| `feature/dashboard-review-improvements` | `150a3a6` | PR #10 통합 (문제 요약 칩·tooltip 반영, 클라이언트 시계 기반 정체 감지는 의도적 폐기) |
| `feature/dashboard-staleness-ux` | `3bda9f8` | PR #10 통합 (서버 기준 정체 감지가 최종 채택안) |
| `claude/dashboard-review-improvements-ac1z56` | `f79ef58` | `feature/dashboard-alloc-integrity` 와 **동일 커밋** (완전 중복) |
| `claude/dashboard-review-improvements-w6glt4` | `150a3a6` | `feature/dashboard-review-improvements` 와 **동일 커밋** (완전 중복) |
| `feature/shared-gpu-timeslicing` | `fba6718` | **PR #7** 로 머지됨(amend 이전 커밋 `3bd6ff7` 기준). tip 과의 차이는 k8s.yaml 버전 라벨 2줄뿐이며 이는 **PR #8**(0.3.1 bump)이 대체. 나머지 diff 는 브랜치가 develop 보다 뒤처진 부분(구 네임스페이스 등)뿐 |

부수 메모: `feature/dashboard-review-improvements` 의 `docs/dashboard-review.md` 는
PR #10 에 포함되지 않았으나, 리뷰 결과가 통합 구현으로 소비된 작업 문서라 유실 아님.

### 삭제 명령

```bash
git push origin --delete \
  claude/dashboard-review-improvements-ac1z56 \
  claude/dashboard-review-improvements-w6glt4 \
  feature/dashboard-alloc-integrity \
  feature/dashboard-freshness-filter \
  feature/dashboard-review-improvements \
  feature/dashboard-staleness-ux \
  feature/shared-gpu-timeslicing
```

### 복구 방법 (필요 시)

위 표의 tip SHA 로 언제든 재생성 가능:

```bash
git push origin <tip SHA>:refs/heads/<브랜치명>
```

## 2. 이미 머지 완료된 브랜치 (선택 — 함께 지워도 됨)

`--merged origin/develop` 에 잡히는 잔존 브랜치. 커밋이 develop 이력에 그대로 있어
삭제해도 아무것도 잃지 않는다.

| 브랜치 | tip SHA | 머지 경로 |
|---|---|---|
| `chore/bump-0.1.1` | `0727fcb` | PR #4 |
| `chore/bump-0.3.1` | `6290262` | PR #8 |
| `claude/gpu-monitor-dashboard-review-xel80u` | `18e03c1` | PR #6 (→ #7 경유) |
| `claude/open-branch-list-w50hkj` | `36b571f` | PR #10 |
| `feature/deploy-manifests` | `dbc36ef` | PR #1 |
| `feature/deploy-namespace-dashboard` | `1221219` | PR #3 |
| `feature/gpu-purpose-env` | `f32e352` | PR #9 |
| `feature/metrics-observability` | `f615c79` | PR #2 |

```bash
git push origin --delete \
  chore/bump-0.1.1 \
  chore/bump-0.3.1 \
  claude/gpu-monitor-dashboard-review-xel80u \
  claude/open-branch-list-w50hkj \
  feature/deploy-manifests \
  feature/deploy-namespace-dashboard \
  feature/gpu-purpose-env \
  feature/metrics-observability
```

정리 후 남는 장수 브랜치: `main` · `develop` · `product` (+ 진행 중 feature 브랜치).
