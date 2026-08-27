# AI-agent용 도로 상태 분석 플랫폼 단계별 실행 지침

> 이 문서는 `depth_map`의 기존 매핑 기능을 훼손하지 않고 도로 상태 분석 코어, HTTP API,
> 웹 시뮬레이터, Docker Compose, 리포트와 후속 AI 기능을 단계적으로 구현하기 위한 최상위
> 실행 인덱스다. 각 상세 문서는 아이디어가 아니라 **질문, 기본값, 수정 파일, 명령, 테스트,
> 수용 기준, 롤백과 다음 승인 게이트**를 고정하는 작업 명세다.

## 0. 반드시 읽을 문서

AI-agent는 작업을 시작할 때 다음 순서로 읽는다.

1. `/AGENTS.md`
2. `/docs/road_condition/AI_AGENT_EXECUTION_PLAN.md`
3. 현재 단계가 포함된 `/docs/road_condition/agent_stages/*.md`
4. `/docs/road_condition/ARCHITECTURE.md`
5. `/docs/road_condition/IMPLEMENTATION_STATUS.md`
6. `/docs/road_condition/USER_QUICKSTART.md`
7. 기존 `/README.md`
8. 기존 `/docs/DEPTH_MAP_FOLLOW_UP_PLAN.md`

문서와 코드가 충돌하면 다음 순서로 판단한다.

1. 사용자가 현재 대화에서 명시한 요구
2. `AGENTS.md`의 안전·서비스 분리 규칙
3. 현재 단계 상세 문서의 수용 기준
4. 아키텍처 문서
5. 기존 코드 관행

## 1. 상세 실행 문서

| 순서 | 문서 | 포함 내용 |
|---:|---|---|
| 1 | [`00_COMMON_AND_STAGE_MAP.md`](agent_stages/00_COMMON_AND_STAGE_MAP.md) | 목표·비목표, 고정 아키텍처, 공통 실행 규칙, 전체 단계 지도 |
| 2 | [`01_STAGE_00_TO_02_MVP.md`](agent_stages/01_STAGE_00_TO_02_MVP.md) | 결과 계약, Compose 분리, 합성 형상 분석 MVP |
| 3 | [`02_STAGE_03_CALIBRATION.md`](agent_stages/02_STAGE_03_CALIBRATION.md) | 실데이터 보정, camera pose·장착값·ground truth 계약 |
| 4 | [`03_STAGE_04_TO_06_ROUTE_GEOMETRY.md`](agent_stages/03_STAGE_04_TO_06_ROUTE_GEOMETRY.md) | 도로 ROI, 타일·halo·중복 병합, 단차·경사·물고임 screening |
| 5 | [`04_STAGE_07_TO_09_WEB_REPORT_RGB.md`](agent_stages/04_STAGE_07_TO_09_WEB_REPORT_RGB.md) | 지도·3D 웹, PDF/evidence, RGB 크랙 AI |
| 6 | [`05_STAGE_10_TO_12_WORKFLOW_INTEGRATION.md`](agent_stages/05_STAGE_10_TO_12_WORKFLOW_INTEGRATION.md) | 점수 profile, 작업자 검수, 보수 시나리오, RoadInventory-MMS 연동 |
| 7 | [`06_STAGE_13_TO_14_OPERATIONS_RELEASE.md`](agent_stages/06_STAGE_13_TO_14_OPERATIONS_RELEASE.md) | 분산 실행·보안·관측성, holdout 검증, v1 릴리스, 공통 운영 템플릿 |

현재 단계만 골라 읽지 말고 먼저 공통 문서를 읽은 뒤 해당 단계 파일로 이동한다.

## 2. 제품 경계

```text
기존 depth_map
  ├─ RGB-D/GNSS 수집 및 동기화
  ├─ trajectory와 local ENU 점군
  ├─ raw/clean/removed 산출물
  └─ LAS/기존 viewer

신규 road-condition platform
  ├─ road_condition_core      순수 분석 로직
  ├─ road-condition-api       작업·검증·결과 전달
  ├─ road-condition-web       웹 시뮬레이터
  ├─ compose.road-condition   서비스 실행
  └─ 후속 crack GPU worker / report worker / integration
```

도로 상태 기능 때문에 기존 mapping preset, 후처리 기본값 또는 출력 파일의 의미를 바꾸지
않는다. 포트홀 분석 기본 입력은 `cloud_raw_enu.ply`다. 기존 local-ground/SMRF 결과가 실제
함몰점을 제거할 수 있으므로 `ground-only LAS`만으로 결함을 판정하지 않는다.

## 3. 현재 구현된 체크포인트

- Stage 00: 용어, 결과 schema와 단위 규칙
- Stage 01: 독립 API·웹 Dockerfile과 Docker Compose
- Stage 02: 합성 도로, robust reference surface, 포트홀·러팅·범프 분석 MVP
- JSON/GeoJSON/NPZ/HTML 산출물
- 내부 geometry score와 roughness proxy
- FastAPI 파일 기반 단일 worker와 read-only workspace
- residual heatmap, 결함 overlay, 횡단 profile, 유지보수 비용 시나리오 웹

공식 PCI, 표준 IRI, 실제 배수망 침수예측, 크랙 AI와 전체 노선 청크 stitching은 완료로
표시하지 않는다.

## 4. 단계 실행 프로토콜

AI-agent는 한 번에 하나의 단계만 완료한다.

1. 현재 branch, base SHA, working tree를 기록한다.
2. 단계 선행조건과 `IMPLEMENTATION_STATUS.md`를 확인한다.
3. 상세 문서의 필수 질문을 사용자에게 제시한다.
4. 사용자가 이미 답한 항목은 반복해서 묻지 않는다.
5. 사용자가 `권장 기본값으로 계속`을 지시한 경우 문서의 기본값을 적용하고 decision log에
   기록한다.
6. 구현 전에 출력 계약, 정확도 지표, 최대 입력 크기와 중단 조건을 고정한다.
7. 정적 검사 → 단위 테스트 → 합성 fixture → 짧은 실데이터 → 승인된 대표 구간 순으로
   검증한다.
8. 단계 완료 시 독립적으로 checkout 가능한 commit을 만든다.
9. 단계 완료 보고에는 명령, 실제 수치, skipped test 사유, 제한과 rollback을 포함한다.
10. 다음 단계가 정확도·비용·데이터 계약을 바꾸면 사용자 승인 게이트를 제시한다.

## 5. 단계 완료 보고 형식

```text
[Stage XX 완료]

구현:
- ...

검증:
- static: PASS/FAIL
- unit: PASS/FAIL
- synthetic: PASS/FAIL
- real smoke: PASS/FAIL/SKIPPED
- docker: PASS/FAIL/SKIPPED

측정값:
- input point count:
- analyzed point count:
- valid coverage:
- defect count:
- wall time:
- peak RSS:

체크포인트:
- branch:
- base SHA:
- commit SHA:
- rollback command:

알려진 제한:
- ...

다음 단계 선택:
A) 권장 기본값으로 진행
B) 설정을 지정해 진행
C) 현재 단계 수정
D) 중단
```

## 6. 공통 테스트

```bash
python -m compileall -q road_condition_core services/road_condition_api/app
node --check services/road_condition_web/app.js
PYTHONPATH=.:services/road_condition_api pytest -q tests/road_condition

docker compose -f compose.road-condition.yml config
docker compose -f compose.road-condition.yml build
docker compose -f compose.road-condition.yml up -d
./scripts/road_condition_smoke.sh
docker compose -f compose.road-condition.yml down
```

Docker가 설치되지 않은 실행 환경에서는 Docker 테스트를 통과했다고 기록하지 않는다. YAML
parse와 정적 검사를 수행한 뒤 `SKIPPED: docker executable unavailable`로 명시한다.

## 7. 현재 다음 승인 게이트

다음 우선순위는 **Stage 03: 실데이터 보정과 camera pose 계약**이다. 다음 값을 먼저 기록한다.

```text
camera_model:
rgb_resolution:
depth_resolution:
fps:
depth_alignment:
camera_height_m:
mount_yaw_deg:
mount_pitch_deg:
mount_roll_deg:
camera_offset_right_m:
camera_offset_down_m:
camera_offset_forward_m:
flat_reference_dataset_path:
pothole_ground_truth_available:
rut_ground_truth_available:
first_allowed_smoke_range:
```

값을 모르면 임의의 0으로 채우지 않고 `unknown`으로 기록한다. Calibration이 unknown이면 자동
확정 결과를 만들지 않고 `manual_review_required`와 재측정 항목을 결과에 남긴다.
