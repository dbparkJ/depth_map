# Stage 04 checkpoint — road ROI and lane coordinates

## 기준과 결정

- Branch: `feat/road-condition-stage-04-road-roi`
- Base SHA: `9f8d9608f8b062ecf64021ba6bb5e125521148db`
- 차로 수/폭, 중심선, 작업자 ROI, 갓길/보도, 교차로 정책: 실데이터 답변 없음
- 기본값: 수동 local-ST ROI + trajectory fallback, shoulder 별도 class, intersection exclusion
- RGB lane/curb AI: Stage 09까지 보류
- 입력 상한: GeoJSON 5 MB, 10,000 features, 100,000 coordinates
- 중단 조건: bundle path 이탈, 잘못된 polygon/속성, ROI retained point가 reference 최소치 미만

## 구현

- format v1 `road_roi.geojson` strict parser와 bundle-relative path guard
- road/lane/shoulder/exclusion 분류 및 exclusion 최우선 precedence
- surface fitting 전 road/lane point filtering
- grid zone/lane code, ROI/unknown/exclusion/shoulder coverage
- defect `lane_id`/`road_zone`, 전체 및 차로별 segment metric
- preview NPZ/JSON과 웹 overlay에 ROI/unknown/exclusion 표시
- ROI가 없는 기존 API/분석의 corridor fallback 유지

## 검증

- Python compileall: PASS
- Node 22 container `--check`: PASS
- road-condition tests: 11 passed
- full repository pytest: 135 passed, 1 third-party deprecation warning
- synthetic mixed ROI: 168,000 input, 149,760 analyzed, ROI coverage 89.14%, unknown 2.86%
- exclusion precedence: PASS; excluded first pothole, remaining pothole attributed to L2
- lane segment: L1/L2 6 segments
- synthetic wall/peak RSS: 3.74 s / 240,660 KiB
- Compose config/build: PASS
- Docker no-ROI fallback smoke: PASS
- Docker mapping-bundle auto ROI smoke: PASS; 145,600 retained points
- real short smoke: SKIPPED — allowed real path and lane ground truth are unknown

## 알려진 제한

- format v1은 polygon hole을 허용하지 않는다.
- ROI는 local ST 좌표만 지원하며 WGS84/ENU polygon 변환은 Stage 07 범위다.
- 수동 ROI 정확도와 실제 보도/차량 제외율은 승인된 실데이터가 없어 미검증이다.
