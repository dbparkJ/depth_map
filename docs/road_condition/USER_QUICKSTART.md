# 도로 상태 시뮬레이터 사용자 실행 가이드

## 1. 준비물

- Git
- Docker Engine 또는 Docker Desktop
- Docker Compose v2
- 데모만 확인할 때는 별도 RGB-D 데이터가 필요하지 않다.

확인:

```bash
git --version
docker --version
docker compose version
```

## 2. 가장 빠른 데모 실행

```bash
git clone https://github.com/dbparkJ/depth_map.git
cd depth_map
git checkout feat/road-condition-platform-mvp

docker compose -f compose.road-condition.yml up --build
```

브라우저:

```text
웹 시뮬레이터: http://127.0.0.1:8080
API 문서:      http://127.0.0.1:8081/docs
API 상태:      http://127.0.0.1:8081/api/v1/health
```

웹은 첫 실행 시 `mixed` 합성 도로 분석을 자동 생성한다. 합성 도로에는 포트홀, 좌우 러팅,
범프가 포함되어 있어 별도 파일 없이 전체 흐름을 확인할 수 있다.

## 3. 백그라운드 실행

```bash
docker compose -f compose.road-condition.yml up -d --build
docker compose -f compose.road-condition.yml ps
docker compose -f compose.road-condition.yml logs -f
```

중지:

```bash
docker compose -f compose.road-condition.yml down
```

결과 named volume까지 삭제:

```bash
docker compose -f compose.road-condition.yml down -v
```

`-v`를 사용하면 기존 작업 결과가 삭제된다.

## 4. 포트와 workspace 변경

```bash
cp .env.road-condition.example .env.road-condition
```

예시:

```dotenv
ROAD_CONDITION_WEB_PORT=9080
ROAD_CONDITION_API_PORT=9081
ROAD_CONDITION_MAX_WORKERS=1
ROAD_CONDITION_WORKSPACE=/data/depth-map-artifacts
ROAD_CONDITION_CORS_ORIGINS=http://localhost:9080,http://127.0.0.1:9080
```

실행:

```bash
docker compose \
  --env-file .env.road-condition \
  -f compose.road-condition.yml \
  up -d --build
```

## 5. 실제 `depth_map` 결과 분석

호스트에 다음 결과가 있다고 가정한다.

```text
/data/depth-map-artifacts/
  route_a/
    temporal_60sec/
      chunk_0000/
        data/
          cloud_raw_enu.ply
          cloud_raw_metadata.npz
          trajectory.json
          summary.json
```

`.env.road-condition`:

```dotenv
ROAD_CONDITION_WORKSPACE=/data/depth-map-artifacts
```

웹에서 다음을 선택한다.

```text
입력 종류: depth_map 결과
워크스페이스 상대 경로: route_a/temporal_60sec/chunk_0000
점군 단계: raw
```

절대 경로 `/data/...`를 입력하지 않는다. 컨테이너의 `/workspace` 기준 상대 경로만 입력한다.

API 직접 호출:

```bash
curl -X POST http://127.0.0.1:8081/api/v1/jobs \
  -H 'Content-Type: application/json' \
  -d '{
    "source_type": "mapping_bundle",
    "mapping_output_path": "route_a/temporal_60sec/chunk_0000",
    "point_cloud_stage": "raw",
    "config": {
      "surface": {
        "grid_size_m": 0.10,
        "corridor_half_width_m": 3.5
      },
      "detection": {
        "pothole_min_depth_m": 0.035,
        "pothole_min_area_m2": 0.035,
        "rut_min_depth_m": 0.020,
        "segment_length_m": 20
      }
    }
  }'
```

응답의 `job_id`로 상태 확인:

```bash
curl http://127.0.0.1:8081/api/v1/jobs/JOB_ID
curl http://127.0.0.1:8081/api/v1/jobs/JOB_ID/summary
```

## 6. 자동 smoke test

스택이 실행 중인 상태에서:

```bash
./scripts/road_condition_smoke.sh
```

포트를 바꿨다면:

```bash
ROAD_CONDITION_API_URL=http://127.0.0.1:9081 \
ROAD_CONDITION_WEB_URL=http://127.0.0.1:9080 \
./scripts/road_condition_smoke.sh
```

Smoke test는 다음을 확인한다.

1. API health
2. 웹 index
3. 합성 `mixed` 작업 생성
4. 완료까지 polling
5. summary 조회

## 7. 결과 확인

API 내부 named volume 경로:

```text
/data/jobs/<job_id>/result/
```

브라우저에서는 다음을 확인한다.

- 형상 점수와 등급
- 포트홀 수와 최대 깊이
- 최대 러팅 깊이
- 도로면 residual heatmap
- 포트홀·러팅·범프 polygon
- 선택 지점 횡단 profile
- 20m 구간별 상태
- 유지보수 비용 시나리오
- HTML 리포트

API 결과:

```text
GET /api/v1/jobs/{id}/summary
GET /api/v1/jobs/{id}/defects
GET /api/v1/jobs/{id}/defects.local.geojson
GET /api/v1/jobs/{id}/defects.enu.geojson
GET /api/v1/jobs/{id}/segments
GET /api/v1/jobs/{id}/surface
GET /api/v1/jobs/{id}/report
GET /api/v1/jobs/{id}/report/summary.csv
```

## 8. 권장 첫 실데이터 설정

최초에는 전체 노선이 아니라 10~30m 또는 60초 청크 하나를 사용한다.

```json
{
  "surface": {
    "grid_size_m": 0.10,
    "corridor_half_width_m": 3.5,
    "max_input_points": 1000000
  },
  "detection": {
    "pothole_min_depth_m": 0.05,
    "pothole_min_area_m2": 0.05,
    "rut_min_depth_m": 0.025,
    "segment_length_m": 20
  }
}
```

검출이 동작하는 것을 확인한 뒤 평탄 기준면 노이즈에 맞춰 임계값을 낮춘다. 최초 실행부터
2~3cm 포트홀 임계값을 적용하면 Depth 노이즈를 결함으로 오판할 수 있다.

## 9. 로그 확인

```bash
docker compose -f compose.road-condition.yml logs -f road-condition-api
docker compose -f compose.road-condition.yml logs -f road-condition-web
```

실패 작업은 API 상태의 `error`와 `/data/jobs/<job_id>/error.log`에 원인이 기록된다.

## 10. 자주 발생하는 문제

### 웹은 열리지만 API 연결 실패

```bash
docker compose -f compose.road-condition.yml ps
docker compose -f compose.road-condition.yml logs road-condition-api
curl http://127.0.0.1:8081/api/v1/health
```

API healthcheck가 통과해야 웹 컨테이너가 시작된다.

### `mapping bundle is incomplete`

상대 경로의 `data/` 아래에 PLY, `trajectory.json`, `summary.json`이 있는지 확인한다.

```bash
find /data/depth-map-artifacts/route_a/temporal_60sec/chunk_0000/data -maxdepth 1 -type f
```

### `too few road-corridor candidate points`

- trajectory와 점군의 좌표가 같은지 확인한다.
- `corridor_half_width_m`를 확인한다.
- 카메라/trajectory 높이와 candidate local-up 범위를 확인한다.
- 점군 단계가 `removed`인지 확인한다.

### `road surface has too few supported grid cells`

- 격자를 0.05m에서 0.10~0.20m로 키운다.
- `min_points_per_cell`을 무작정 낮추기 전에 점밀도와 관측 수를 확인한다.
- `raw` 점군을 사용한다.

### 브라우저가 느림

웹은 전체 `surface.npz`가 아니라 preview를 받는다. 그래도 느리면 API 설정에서
`preview_max_along_cells`, `preview_max_cross_cells`를 낮춘다.

### 결과가 모두 E등급

현재 점수는 보수적인 내부 MVP 점수다. 공식 등급이 아니며, threshold와 가중치를 실측
holdout으로 보정하기 전에는 절대 평가보다 상대 비교에 사용한다.

## 11. 개발자 로컬 테스트

Docker 없이 Python 검사:

```bash
python -m venv .venv-road-condition
source .venv-road-condition/bin/activate
pip install -r services/road_condition_api/requirements.txt
pip install pytest httpx

PYTHONPATH=.:services/road_condition_api pytest -q tests/road_condition
python -m compileall -q road_condition_core services/road_condition_api/app
node --check services/road_condition_web/app.js
```

API 로컬 실행:

```bash
PYTHONPATH=.:services/road_condition_api \
ROAD_CONDITION_DATA_ROOT=artifacts/road_condition_runtime \
ROAD_CONDITION_WORKSPACE_ROOT=artifacts \
uvicorn app.main:app --host 127.0.0.1 --port 8081 --reload
```

정적 웹은 Nginx proxy가 필요하므로 전체 기능 확인은 Compose 실행을 권장한다.

## 12. Stage 03 pose와 보정 bundle

새 mapping 실행은 기존 파일을 바꾸지 않고 다음 optional 파일을 추가한다.

```text
data/camera_poses.npz
data/analysis_source_manifest.json
```

장착값을 실제 측정하지 않았다면 기본 `--calibration-status unknown`을 유지한다. 숫자 0은
mapping 호환 가정으로만 사용되고 manifest의 calibrated 값은 null이며 분석 결과는
`manual_review_required`다. 측정값이 있을 때만 다음처럼 상태와 높이를 명시한다.

```bash
.venv/bin/python map_rgbd_gps.py DATASET_PATH \
  --output artifacts/calibrated_short \
  --max-frames 20 \
  --calibration-status measured \
  --camera-model-name OAK-D-LR \
  --camera-height-m 1.50 \
  --mount-yaw-deg 0.2 --mount-pitch-deg 14.8 --mount-roll-deg -0.3 \
  --camera-offset-right-m 0.04 \
  --camera-offset-down-m 0.18 \
  --camera-offset-forward-m 0.32
```

승인된 짧은 평탄 기준 mapping bundle에서 거리 band별 noise와 실험 threshold 후보를 만든다.

```bash
PYTHONPATH=. .venv/bin/python scripts/road_condition_calibrate.py \
  artifacts/calibrated_flat_short
```

결과는 기본적으로 `<mapping>/calibration/` 아래 manifest, flat-surface noise JSON,
포트홀/러팅 ground-truth worksheet, threshold recommendation과 HTML report로 기록된다.

## 13. Stage 04 수동 도로 ROI

예시는 [`road_roi.example.geojson`](road_roi.example.geojson)이다. 좌표는 위경도가 아니라
trajectory를 따른 진행거리 `s`와 횡방향 `t` metre다. 파일을 mapping bundle의
`data/road_roi.geojson`에 두면 API가 자동 탐색한다. 다른 이름이면 웹/API의
`road_roi_path`에 bundle 기준 상대 경로를 지정한다.

```json
{
  "source_type": "mapping_bundle",
  "mapping_output_path": "route_a/chunk_0000",
  "road_roi_path": "data/road_roi.geojson",
  "point_cloud_stage": "raw"
}
```

ROI feature에는 `zone_id`, `zone_type`, `chainage_start_m`, `chainage_end_m`, `source`,
`confidence`가 필요하고 lane에는 `lane_id`도 필요하다. exclusion이 항상 최우선이며
shoulder와 unknown은 surface fitting에서 제외된다. ROI가 없으면 기존 corridor 분석으로
자동 fallback한다.

## 14. Stage 05 resumable route 분석

한 mapping chunk를 10m core + 3m halo tile로 분석한다. 출력 디렉터리에 완료 status와 같은
input signature가 있으면 재실행하지 않는다. Parquet writer 때문에 `route` extra가 필요하다.

```bash
.venv/bin/pip install -e '.[route]'
PYTHONPATH=. .venv/bin/python scripts/road_condition_route.py \
  artifacts/route_a/temporal_60sec/chunk_0000 \
  --output artifacts/route_a/road_condition/chunk_0000
```

청크들을 point cloud 재로딩 없이 route 결과로 병합할 때 각 청크의 global chainage offset을
명시한다.

```bash
PYTHONPATH=. .venv/bin/python scripts/road_condition_merge_routes.py \
  --chunk chunk_0000,artifacts/route_a/road_condition/chunk_0000,0 \
  --chunk chunk_0001,artifacts/route_a/road_condition/chunk_0001,58.7 \
  --output artifacts/route_a/road_condition/merged
```

offset은 실제 trajectory seam 검토 후 확정해야 한다. 현재 merge tolerance는 experimental이며
청크 pose 오차가 측정되지 않은 상태에서 자동 승인하지 않는다.

## 15. Stage 06 추가 형상 screening

추가 기능은 기존 결과를 보존하기 위해 기본 비활성화다. API 작업의 `config`에서 필요한 기능만
독립적으로 켠다.

```json
{
  "source_type": "mapping_bundle",
  "mapping_output_path": "route_a/chunk_0000",
  "config": {
    "advanced_geometry": {
      "step_manhole_enabled": true,
      "crossfall_enabled": true,
      "longitudinal_enabled": true,
      "ponding_screening_enabled": true
    }
  }
}
```

단차 결과는 맨홀/구조물 `candidate`, 물 관련 결과는 `ponding_screening_proxy`다. 맨홀 자산
DB와 배수구 위치가 없으므로 자산 확정, 배수 용량, 침수 예측은 제공하지 않는다. 종단 profile의
`roughness_proxy_m`도 표준 IRI가 아니며 실측 비교 장비로 보정하기 전에는 내부 실험값이다.

## 16. Stage 07 route 타일 뷰어

웹의 **노선·날짜·타일 탐색**에서 Stage 05 route 결과 디렉터리를 `/workspace` 기준 상대 경로로
한 줄에 하나씩 입력한다.

```text
route_a/road_condition/chunk_0000
route_a/road_condition/chunk_0001
```

웹은 manifest를 먼저 읽고 선택한 tile JSON만 점진 로드한다. 전체 PLY는 브라우저로 전송하지
않는다. Local ST, 경량 3D evidence, local ENU 지도 보기를 전환할 수 있고 Z 강조는 1×/2×/5×다.
VWorld/Cesium은 runtime key/token과 WGS84 변환이 설정되지 않은 현재 Compose에서 local ENU로
fallback한다.

키보드로 결함 표 행을 선택하려면 Enter/Space를 사용하고, 뷰어에 focus한 뒤 N/P로 다음/이전
결함을 이동한다. 실패 tile은 manifest에 보이지만 완료 산출물처럼 열리지 않는다. route tile은
read-only이고, 완료 단일 job은 Stage 10 검수 panel에서 별도 audit event로 판정한다.

## 17. Stage 08 보고서 v2 재생성

완료된 job의 `result/`에서 HTML, CSV, 결함별 geometry evidence를 결정적으로 다시 만든다.
HTML이 기준 산출물이며 RGB 원본/overlay가 연결되지 않은 결함은 보고서에 `N/A`로 표시된다.

```bash
PYTHONPATH=. .venv/bin/python scripts/road_condition_report.py \
  data/jobs/<job_id>/result
```

PDF가 필요하면 분석 API와 분리된 전용 이미지를 사용한다. 입력 result는 read-only로, 출력
디렉터리만 writable로 마운트한다.

```bash
docker build -f docker/road_condition_report.Dockerfile \
  -t depth-map-road-condition-report .
docker run --rm \
  -v "$PWD/data/jobs/<job_id>/result:/input:ro" \
  -v "$PWD/report-output:/output" \
  depth-map-road-condition-report /input --output /output --pdf
```

`report_manifest.json`에는 알고리즘 버전, 설정 hash, 입력 JSON hash, dataset ID, mapping commit
SHA와 누락 evidence가 기록된다. 값이 없는 추적성 항목은 추정하지 않고 `N/A`로 유지한다.

## 18. Stage 09 RGB 크랙 계약 검증

현재 이미지는 AI inference가 아니라 모델 승인·metric·BEV 후처리 계약을 검증한다. 라벨과
승인 weights/GPU 정보가 없으므로 `ready_for_neural_inference=false`가 정상 상태다.

```bash
docker build -f services/road_condition_crack_worker/Dockerfile \
  -t depth-map-road-condition-crack-contract .
docker run --rm --network none \
  depth-map-road-condition-crack-contract capabilities
```

모델 manifest는 예제를 복사해 모든 `null`을 실측/학습 정보로 채우고, weights와 함께
`/workspace` 아래에 둔다. 입력 mount는 read-only다.

```bash
docker run --rm --network none \
  -v "$PWD/artifacts:/workspace:ro" \
  depth-map-road-condition-crack-contract \
  verify-model --manifest crack/model_manifest.json
```

승인되지 않았거나 정보가 빠졌거나 SHA-256이 다른 모델은 exit code 2로 거부된다. holdout NPZ
평가는 `truth_mask`, `probability` `[N,H,W]`, `route_length_m`, `wet`, `shadow` `[N]`을 요구한다.
선택적으로 `truth_length_m`, `predicted_length_m` `[N]`을 넣는다.

```bash
docker run --rm --network none \
  -v "$PWD/artifacts:/workspace:ro" \
  depth-map-road-condition-crack-contract \
  evaluate-npz /workspace/crack/holdout_predictions.npz
```

metric 정의는 고정됐지만 acceptance threshold는 아직 `N/A`이며 자동 승인은 비활성화다. 이
contract image는 운영 service가 아니므로 현재 2-service Compose에는 넣지 않는다.

## 19. Stage 10 점수 profile과 수동 검수

새 작업은 기본 `internal-geometry-mvp-v1` profile을 사용한다. 이는 기존 계산을 재현하는 내부
실험 profile이며 기관 표준이 아니다.

```json
{
  "source_type": "synthetic",
  "synthetic_profile": "mixed",
  "scoring_profile_id": "internal-geometry-mvp-v1"
}
```

summary와 report에서 profile ID/version/hash/승인 상태를 확인할 수 있다. API config에서 score
weight나 구간 길이를 덮어쓰면 `custom_override_applied=true`가 기록된다.

완료 job의 raw defect와 검수 상태를 조회한다.

```text
GET /api/v1/jobs/{job_id}/defects
GET /api/v1/jobs/{job_id}/reviews
```

승인·거절·재수집은 `after` 없이, 수정은 원 defect 전체를 복사한 `after`와 함께 전송한다.

```json
POST /api/v1/jobs/{job_id}/reviews/{defect_id}
{
  "actor": "local-reviewer",
  "action": "accepted",
  "reason": "geometry evidence checked",
  "expected_version": 0
}
```

동시에 열린 화면의 stale version은 409로 거부된다. 웹은 최신 version을 표시하고 심각도 수정도
같은 endpoint에 기록한다. `defects.json`은 절대 덮어쓰지 않는다. 현재 로그인/권한이 없으므로
actor 문자열은 신원이 검증된 계정이 아니라 audit label이다.

## 20. Stage 11 유지보수 예산 screening v2

웹의 **유지보수 시나리오 v2**에 가용 예산을 입력하면 알려진 예시 단가만으로 우선순위
screening을 실행한다. API 직접 호출 예시는 다음과 같다.

```json
POST /api/v1/jobs/{job_id}/scenarios/v2
{
  "catalog_id": "internal-planning-v1",
  "include_types": ["pothole", "rutting", "bump"],
  "budget_krw": 5000000,
  "comparison_budgets_krw": [2500000, 7500000],
  "goal": "risk_screening_priority"
}
```

응답의 `priced_total_krw`는 패칭/덧씌우기/연삭과 동원비만 포함한다.
`full_total_krw`는 차선 통제·장비 이동·폐기물 비용이 미제공이라 `null`이며, 0원이라는 뜻이
아니다. 카탈로그 단가와 최소 작업 단위는 내부 실험 예시이므로 계약·발주 전에 승인된 실제
단가표로 별도 catalog를 만들어야 한다.

`score_projection`은 비보정 planning estimate이고 실제 예측이 아니다. 반복 조사 자료가 없는
현재 `deterioration.annual_rate`와 `projected_score`는 `null`이다. 기존 강우 screening이 필요한
호환 클라이언트는 legacy `POST /api/v1/jobs/{job_id}/scenarios`를 계속 사용할 수 있다.

## 21. Stage 12 RoadInventory-MMS contract 평가

운영 기본값은 비활성화다. 인증과 object storage가 설정되지 않은 상태에서 활성화하지 않는다.
격리된 로컬 계약 시험에서만 다음 환경값을 사용한다.

```dotenv
ROAD_CONDITION_RIMMS_CONTRACT_INGRESS_ENABLED=true
```

```text
POST /api/v1/integrations/rimms/jobs
Idempotency-Key: rimms-2026-001-attempt-1
{
  "contract_version": "road-condition-rimms-request-v1",
  "expected_result_contract_version": "road-condition-rimms-result-v1",
  "external_job_id": "rimms-2026-001",
  "survey_id": "survey-20260827-001",
  "route_id": "route-a",
  "lane_id": null,
  "mapping_bundle_uri": "s3://bucket/survey-001/mapping/",
  "raw_dataset_uri": "s3://bucket/survey-001/raw/",
  "road_roi_uri": "s3://bucket/survey-001/road_roi.geojson",
  "config_profile_id": "internal-geometry-mvp-v1",
  "callback_url": null
}
```

같은 header와 같은 body의 재시도는 새 작업을 만들지 않고
`idempotency_replayed=true`를 반환한다. 상태는
`GET /api/v1/integrations/rimms/jobs/{external_job_id}`로 polling한다. 현재 정상 응답은
`awaiting_connector_configuration`이며 completed 결과를 만들지 않는다. callback URL, signed
query URI, 파일 본문 업로드는 거부된다.
