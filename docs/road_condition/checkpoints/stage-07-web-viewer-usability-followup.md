# Stage 07 follow-up checkpoint — real-data readability and display units

## 기준과 결정

- Branch: `feat/road-condition-stage-14-release-gate`
- Base SHA: `8d07525ef1acc09d52f93a9b2d21b0e9c27452f2`
- 실데이터: `2026-08-19_09-51-22_hardlinked/temporal_60sec/chunk_0000`
- 분석 job: `631cb61ed4c24257b98bff56e27d6b29`
- API/JSON의 SI 단위 계약은 유지하고 웹 사용자 표시만 변환한다.
- 세로 형상은 1 m 미만일 때 cm, 1 m 이상일 때 m로 표시한다.
- 진행거리, 횡방향 거리와 길이는 m, 면적은 ㎡를 유지한다.
- 깊이 threshold 입력은 cm로 받고 API 요청에서는 m로 변환한다.
- 낮은 전체 데이터 품질은 개별 후보 confidence와 점수보다 먼저 표시한다.

## 실데이터 판독 결과

- 원본 점: 39,362,186
- 분석 점: 2,000,000, deterministic sampling 적용
- 유효 표면 커버리지: 3.8993%, 내부 최소 기준 50% 미달
- 보정 상태: `unknown`, `manual_review_required=true`
- 도로 ROI: 없음, `trajectory_corridor_fallback`
- 후보: 56건(포트홀 11, 러팅 26, 범프 19)
- 최대 포트홀 깊이: 16.8 cm
- 최대 러팅 깊이: 16.4 cm
- 범프 높이 중앙값/최대: 90.7 cm / 1.76 m
- 범프 19건 중 13건이 중심선에서 2.5 m보다 바깥에 있어 도로변 구조물 또는 노이즈일
  가능성이 크다.

현재 결과는 파이프라인 smoke와 수동 후보 검토에는 사용할 수 있지만 자동 도로 상태 판정,
내부 점수 승인 또는 유지보수 물량 확정에는 사용하지 않는다.

## 구현

- 결과 상단에 `자동 판정 보류`/`검수 가능한 결과` readiness banner 추가
- 낮은 coverage, 미보정, ROI fallback 사유를 한 문장으로 표시
- 사용자가 볼 순서를 품질 → 위치 → 측정값/단면 → 현장 검수로 고정
- 내부 형상 점수와 E 등급을 판정 보류 상태의 참고값으로 시각적으로 낮춤
- 결함 표현을 미검증 상태에 맞춰 `결함 후보`로 변경
- 최대 깊이, 후보 목록, 구간 표, 횡·종단 잔차를 cm/m로 표시
- 포트홀/러팅 threshold 입력을 cm로 변경하고 API의 기존 m 계약 보존
- 분석 점이 표본이면 원본 점 수와 sampling 사실을 summary card에 표시

## 검증

- `node --check services/road_condition_web/app.js`: PASS(node:22-alpine)
- `node tests/road_condition/viewer_core.test.js`: PASS(node:22-alpine)
- `python -m compileall -q road_condition_core services/road_condition_api/app`: PASS
- `PYTHONPATH=.:services/road_condition_api .venv/bin/pytest -q tests/road_condition`: 53 passed
- Compose YAML parse/config: PASS
- web image build/recreate: PASS, host `18080`, API `18081`
- headless Chrome: 실데이터 완료 job 자동 로드와 1800×1600 화면 확인
- API metre 단위와 저장 결과 파일은 변경하지 않음

## 알려진 제한과 다음 조치

- coverage 3.9%인 현재 결과의 56개 후보는 ground truth 없이 참/거짓을 확정할 수 없다.
- 후보 confidence는 후보 내부 점수이며 전체 데이터 readiness를 대체하지 않는다.
- 수동 `road_roi.geojson`, measured calibration과 평탄면/포트홀/러팅 실측이 필요하다.
- RGB evidence 연결 전에는 웹만으로 후보를 승인하지 않는다.
- 알고리즘 threshold 조정은 별도의 Stage 03 실데이터 보정 체크포인트에서 수행한다.
