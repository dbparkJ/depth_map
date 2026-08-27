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
