# AI-agent 공통 운영 규칙과 전체 단계 지도

> 목적: AI-agent가 `depth_map`의 기존 매핑 기능을 훼손하지 않고, 도로 상태 분석 백엔드와 웹
> 시뮬레이터를 단계적으로 구현·검증·승인받도록 하는 공통 실행 명세다. 이 문서는 아이디어가
> 아니라 **작업 순서, 질문, 파일, 명령, 수용 기준, 롤백 조건**을 고정한다.

---

## 0. 문서 사용법

AI-agent는 작업 시작 전에 아래 문서를 순서대로 읽는다.

1. `/AGENTS.md`
2. `/docs/road_condition/AI_AGENT_EXECUTION_PLAN.md`
3. 현재 단계가 포함된 이 디렉터리의 상세 문서
4. `/docs/road_condition/ARCHITECTURE.md`
5. `/docs/road_condition/IMPLEMENTATION_STATUS.md`
6. `/docs/road_condition/USER_QUICKSTART.md`
7. 기존 `/README.md`
8. 기존 `/docs/DEPTH_MAP_FOLLOW_UP_PLAN.md`

이 문서와 코드가 충돌할 때는 다음 순서로 판단한다.

1. 사용자가 현재 대화에서 명시한 요구
2. `AGENTS.md`의 안전·분리 규칙
3. 단계별 수용 기준
4. 아키텍처 문서
5. 기존 코드 관행

각 단계는 독립된 체크포인트다. 사용자가 전체 진행을 지시했더라도 다음 단계의 선택지가 실제
정확도·비용·데이터 계약을 바꾸는 경우에는 단계 완료 보고와 질문을 남긴다. 사용자가
`권장 기본값으로 계속`이라고 답하면 기본값을 적용한다.

---

# 1. 제품 목표와 비목표

## 1.1 최종 목표

```text
RGB-D + GNSS 원본/매핑 결과
        │
        ├─ 형상 분석: 포트홀, 러팅, 범프, 단차, 횡단경사, roughness
        ├─ RGB 분석: 크랙, 패칭, 박리, 블리딩
        ├─ 융합: 위치, 길이, 폭, 깊이, 면적, 체적, 신뢰도
        ├─ 구간 평가: 내부 점수 및 발주처 profile
        ├─ 유지보수: 물량, 단가, 우선순위, 전후 시나리오
        └─ 전달: 웹 시뮬레이터, API, GeoJSON, 리포트
```

## 1.2 현재 MVP가 보장하는 범위

- Docker Compose로 API와 웹 분리 실행
- 합성 입력을 사용한 설치 확인
- `depth_map` PLY/trajectory/summary 입력
- 로컬 도로 좌표 `(s,t)` 변환
- robust reference surface
- 포트홀·러팅·범프 검출
- 구간별 내부 형상 점수
- JSON/GeoJSON/NPZ/HTML
- 파일 기반 단일 호스트 worker

## 1.3 현재 MVP가 보장하지 않는 범위

- 크랙 AI 정확도
- 표준 IRI
- 공식 PCI
- 실제 침수/배수 해석
- 법적·계약적 도로 안전 판정
- survey-grade 절대 좌표 정확도
- 다중 사용자 인증
- 수평 확장 worker queue
- 전체 노선 청크 중복 병합

이 항목을 완료되지 않았는데 완료했다고 표시하지 않는다.

---

# 2. 고정된 아키텍처 결정

다음은 사용자가 별도로 변경을 요청하기 전까지 질문 없이 유지한다.

| 항목 | 결정 |
|---|---|
| 저장소 | 기존 `depth_map` monorepo |
| 분석 코어 | `road_condition_core/` |
| HTTP API | `services/road_condition_api/` |
| 웹 | `services/road_condition_web/` |
| 오케스트레이션 | `compose.road-condition.yml` |
| 웹 공개 포트 | 기본 8080 |
| API 공개 포트 | 기본 8081 |
| API 프레임워크 | FastAPI |
| 웹 MVP | Nginx + dependency-free HTML/JS/CSS |
| 작업 저장 | `/data/jobs/<job_id>` 파일 계약 |
| 실데이터 입력 | `/workspace` read-only 상대 경로 |
| 기본 worker | 1개 |
| 포트홀 기본 입력 | `cloud_raw_enu.ply` |
| 결과 좌표 | local ST + local ENU 둘 다 |
| 공식 지표 표현 | PCI/IRI라는 명칭 사용 금지 |
| 최초 리포트 | HTML |
| 최초 시뮬레이터 | local ST residual + defect overlay |

변경하려면 ADR을 추가하고 영향 범위와 migration을 기록한다.

---

# 3. AI-agent 공통 실행 규칙

## 3.1 시작 전 저장소 확인

```bash
git status --short --branch
git log -5 --oneline
git remote -v
python --version
```

기록할 값:

```text
base_branch:
base_commit_sha:
working_tree_clean:
python_version:
docker_version:
docker_compose_version:
node_version:
```

작업 트리가 깨끗하지 않으면 사용자 변경을 덮어쓰지 않는다. 관련 없는 변경은 stash하거나
별도 worktree를 사용한다. 사용자의 미커밋 파일을 삭제하지 않는다.

## 3.2 단계별 브랜치

권장:

```text
feat/road-condition-stage-03-calibration
feat/road-condition-stage-04-road-roi
feat/road-condition-stage-05-chunk-stitching
```

단계 하나에 PR 하나를 기본으로 한다. 여러 단계가 하나의 PR에 들어가면 commit별로 완전한
체크포인트를 남기고 PR 본문에 단계 경계를 표시한다.

## 3.3 테스트 순서

항상 아래 순서를 지킨다.

```text
정적 검사
  ↓
단위 테스트
  ↓
합성 fixture
  ↓
20프레임 이하 smoke
  ↓
10~30m 또는 60초 청크
  ↓
승인된 대표 구간
  ↓
전체 데이터 1회
```

실패 원인을 모른 채 전체 데이터 실행을 반복하지 않는다.

## 3.4 단계 완료 보고 필수 항목

```markdown
## Stage XX completion

- Base SHA:
- Checkpoint SHA:
- Files added:
- Files modified:
- Commands executed:
- Tests passed:
- Tests skipped and reason:
- Actual data used:
- Metrics before:
- Metrics after:
- Known limitations:
- Rollback command:
- Next-stage questions:
```

## 3.5 금지 행동

- 기존 매핑 기본 preset을 도로 상태 목적 때문에 바꾸기
- 포트홀 후보를 below-ground noise로 먼저 삭제하기
- 실측 없는 threshold를 표준값으로 표현하기
- 실제 노선 전체를 첫 테스트로 실행하기
- 대용량 결과를 Git에 추가하기
- UI에서만 결과를 계산하고 API 산출물을 남기지 않기
- API에 임의 절대 경로 또는 shell command 받기
- 한 PR에서 RGB AI, DB, UI, scoring을 동시에 구현하기
- 테스트를 통과시키기 위해 수용 기준을 결과 확인 후 낮추기

---

# 4. 단계 전체 지도

| Stage | 제목 | 상태 | 기본 체크포인트 |
|---:|---|---|---|
| 00 | 기준선·용어·결과 계약 | MVP 완료 | docs/schema |
| 01 | 서비스 분리·Docker Compose | MVP 완료 | compose 실행 |
| 02 | 합성 도로·형상 분석 MVP | MVP 완료 | geometry smoke |
| 03 | 실데이터 보정·camera pose 계약 | 다음 우선순위 | calibration bundle |
| 04 | 도로 ROI·차로 좌표계 | 계획 | road ROI |
| 05 | 타일·청크 halo·결함 병합 | 계획 | route aggregation |
| 06 | 형상 검출 고도화 | 계획 | step/crossfall/ponding proxy |
| 07 | 웹 지도·3D 시뮬레이터 고도화 | 계획 | geospatial viewer |
| 08 | 자동 리포트·PDF·증거 패키지 | 계획 | report v2 |
| 09 | RGB 크랙 AI | 계획 | crack worker |
| 10 | 점수 profile·검수 workflow | 계획 | score/review |
| 11 | 유지보수·열화 시나리오 | 계획 | simulation v2 |
| 12 | RoadInventory-MMS 연동 | 계획 | integration API |
| 13 | 분산 실행·운영 보안 | 계획 | production stack |
| 14 | 검증·릴리스 | 계획 | v1 release |
