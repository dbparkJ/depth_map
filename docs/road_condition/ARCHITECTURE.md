# 도로 상태 분석 플랫폼 아키텍처

## 1. 목표

`depth_map`이 생성한 RGB-D/GNSS 매핑 결과를 이용해 도로면 형상 결함을 분석하고, 사용자가
Docker Compose 한 번으로 웹 시뮬레이터와 백엔드를 실행할 수 있도록 한다. 기존 매핑
파이프라인은 수집·정합·점군 생성에 집중하고, 도로 상태 시스템은 분석·시각화·리포트에
집중한다.

현재 구현은 **geometry-first MVP**다. 포트홀, 러팅, 범프를 형상 기반으로 분석한다. 크랙,
패칭, 박리, 블리딩은 RGB 모델 단계에서 추가한다.

## 2. 컨테이너 경계

```text
┌──────────────────────────────────────────────────────────────────────┐
│                         Docker Compose                               │
│                                                                      │
│  ┌────────────────────────┐       HTTP /api        ┌──────────────┐ │
│  │ road-condition-web     │ ─────────────────────▶ │ road-        │ │
│  │ Nginx + HTML/JS/CSS    │                        │ condition-api│ │
│  │ :80 / host :8080       │ ◀───────────────────── │ FastAPI      │ │
│  └────────────────────────┘        JSON/GeoJSON     │ :8000       │ │
│                                                   └──────┬───────┘ │
│                                                          │         │
│                          ┌───────────────────────────────┼───────┐ │
│                          │ road_condition_core           │       │ │
│                          │ projection / surface /        │       │ │
│                          │ detectors / score / report    │       │ │
│                          └───────────────────────────────┼───────┘ │
│                                                          │         │
│                     read-only                             │ read/write
│  host artifacts ─────────────▶ /workspace      volume ───▶ /data   │
└──────────────────────────────────────────────────────────────────────┘
```

### `road-condition-web`

- 정적 HTML/CSS/JavaScript만 제공한다.
- 분석 알고리즘을 포함하지 않는다.
- `/api/*`를 `road-condition-api`로 reverse proxy한다.
- 로컬 도로 좌표 `(s,t)` 기반 residual heatmap과 결함 polygon을 렌더링한다.
- v2 가용 예산을 API에 전송하며 legacy 단가·강우 screening 계약도 보존한다.

### `road-condition-api`

- FastAPI 기반 HTTP 어댑터다.
- 사용자 입력 검증, 작업 상태 저장, 분석 실행, 결과 조회만 담당한다.
- 실제 알고리즘은 `road_condition_core`를 호출한다.
- 대용량 입력은 API 업로드가 아니라 `/workspace` 볼륨의 상대 경로로 읽는다.
- `/workspace`는 읽기 전용이다.
- `/data`는 작업 상태와 결과를 저장하는 named volume이다.

### `road_condition_core`

- HTTP, Docker, 브라우저에 의존하지 않는 Python 분석 라이브러리다.
- 합성 도로 생성기와 실제 `depth_map` 결과 loader를 모두 제공한다.
- 동일 입력과 설정에 대해 결정적 결과를 목표로 한다.
- 공식 표준이 아닌 MVP metric에는 이름으로 제한을 드러낸다.

## 3. 소스 디렉터리

```text
road_condition_core/
  config.py          설정 dataclass, override 검증
  models.py          surface/defect/segment 결과 모델
  io.py              depth_map PLY/trajectory/summary loader
  geometry.py        ENU→(s,t), surface grid, reference surface
  detectors.py       pothole/rutting/bump 검출
  pipeline.py        전체 분석, 점수, 산출물 기록
  maintenance.py     legacy 보수 수량·단가·강우 screening
  maintenance_v2.py  versioned 공법 catalog·예산 우선순위·planning estimate
  report.py          HTML 리포트
  synthetic.py       Docker 데모 및 회귀 fixture

services/road_condition_api/
  Dockerfile
  requirements.txt
  app/
    main.py           FastAPI factory, endpoint, worker 실행
    schemas.py        HTTP request schema
    store.py          파일 기반 job store

services/road_condition_web/
  Dockerfile
  nginx.conf
  index.html
  app.js
  style.css

compose.road-condition.yml
.env.road-condition.example
scripts/road_condition_smoke.sh
tests/road_condition/
docs/road_condition/
```

## 4. 입력 계약

### 4.1 합성 입력

웹과 Docker 설치 확인을 위한 기본 입력이다.

```json
{
  "source_type": "synthetic",
  "synthetic_profile": "mixed"
}
```

지원 프로파일:

- `flat`
- `potholes`
- `rutting`
- `mixed`

### 4.2 실제 `depth_map` 입력

Compose에서 호스트 디렉터리를 `/workspace`로 마운트한다. API에는 `/workspace` 내부의
**상대 경로**만 전송한다.

```json
{
  "source_type": "mapping_bundle",
  "mapping_output_path": "route_a/temporal_60sec/chunk_0000",
  "point_cloud_stage": "raw"
}
```

필수 파일:

```text
<output>/data/cloud_raw_enu.ply
<output>/data/cloud_raw_metadata.npz    # raw일 때 선택적 메타데이터
<output>/data/trajectory.json
<output>/data/summary.json
<output>/data/camera_poses.npz           # Stage 03 이후 optional frame pose
<output>/data/analysis_source_manifest.json # Stage 03 이후 optional calibration provenance
<output>/data/road_roi.geojson          # Stage 04 이후 optional local-ST ROI
```

`clean` 또는 `removed`를 지정하면 해당 PLY를 읽는다. 포트홀 보존을 위해 기본값은 `raw`다.

`camera_poses.npz`가 없으면 기존 bundle은 계속 PLY-only로 분석한다. 이 경우 결과 quality에
`ply_only_pose_unavailable`을 남기며 프레임 단위 정밀도 주장을 하지 않는다. 보정 상태가
`unknown` 또는 `estimated`이면 `manual_review_required`다. `T_enu_camera`는 optical camera
XYZ(right/down/forward)를 local ENU로 옮기는 4×4 행렬이며 모든 변환은
`T_target_source` 열벡터 규칙을 사용한다.

`road_roi.geojson`은 `format_version=1`, `coordinate_system=local_road_ST_metres`인
FeatureCollection이다. `road`, `lane`, `shoulder`, `exclusion` Polygon/MultiPolygon을
허용하며 우선순위는 exclusion > lane > shoulder > road다. 분석 surface에는 road/lane만
포함하고 shoulder는 별도 class로 보존한다. ROI가 없으면 기존 trajectory corridor를 그대로
사용한다.

### 4.3 PLY 제한

MVP loader는 저장소가 기록하는 고정 binary little-endian XYZ/RGB 구조만 허용한다.
임의의 PLY property 순서를 추측하지 않는다. 잘못된 형식을 조용히 읽는 것보다 명시적으로
실패하는 정책이다.

## 5. 분석 흐름

```text
point cloud + fused trajectory
        │
        ▼
입력 포인트 상한 적용
        │
        ▼
trajectory polyline 투영
ENU XYZ → along-track s / signed cross-track t / local-up
        │
        ▼
도로 corridor 필터
        │
        ▼
(s,t) cell별 dense lower mode
observed surface + point count + position spread
        │
        ▼
겹치는 robust quadratic tile fitting
reference surface
        │
        ▼
residual = observed - reference
        │
        ├─ negative component → pothole
        ├─ wheel-path longitudinal depression → rutting
        └─ positive component → bump
        │
        ▼
20m segment metric + internal geometry score
        │
        ▼
JSON / GeoJSON / NPZ / HTML
```

## 6. 왜 기존 ground filter 뒤에서 분석하지 않는가

기존 후처리는 지도의 노이즈를 줄이는 것이 목적이다. local surface 아래쪽 점을 제거하거나
SMRF로 ground class만 남기는 과정에서 실제 포트홀 내부점이 제거될 수 있다. 도로 상태
분석은 다음 원칙을 따른다.

1. `cloud_raw_enu.ply`를 기본 사용한다.
2. 점별 position spread와 관측 지지가 있으면 confidence에 반영한다.
3. 분석용 reference surface는 지도 정제용 제거 기준과 분리한다.
4. 포트홀 후보를 먼저 삭제하지 않는다.
5. 최종 결함은 형상 크기와 불확실도를 함께 기록한다.

## 7. 결과 계약

작업 결과 디렉터리:

```text
/data/jobs/<job_id>/
  request.json
  status.json
  error.log                  # 실패 시
  result/
    summary.json
    defects.json
    defects.local.geojson
    defects.enu.geojson
    segments.json
    surface_preview.json
    surface.npz
    report.html
    report/                    # Stage 08 self-contained evidence package
      report.html              # source of truth
      report_manifest.json
      summary.csv
      segments.csv
      defects.csv
      report.pdf               # optional, separate Chromium renderer
      figures/
      evidence/<defect_id>/
```

### `summary.json`

주요 구역:

- `format_version`
- `algorithm_version`
- `source`
- `parameters`
- `quality`
- `coverage`
- `results`
- `scores`
- `limitations`

### `defects.json`

공통 필드:

```json
{
  "defect_id": "pothole-0001",
  "defect_type": "pothole",
  "severity": "high",
  "confidence": 0.91,
  "chainage_m": 12.0,
  "lateral_offset_m": -0.4,
  "local_polygon_st_m": [[...]],
  "metrics": {
    "max_depth_m": 0.11,
    "p95_depth_m": 0.10,
    "area_m2": 0.7,
    "volume_m3": 0.04
  },
  "quality_flags": [],
  "source": "geometry"
}
```

### `defects.local.geojson`

웹 시뮬레이터용 `(s,t)` polygon이다. 일반 지도 좌표계가 아니므로
`coordinate_system=local_road_ST_metres`를 명시한다.

### `defects.enu.geojson`

`(s,t)` polygon을 trajectory에 따라 ENU XY로 되돌린다. 위경도 GeoJSON이 아니다.
`origin` metadata와 함께 후속 GIS 변환에 사용한다.

### `surface_preview.json`

브라우저용 downsample 결과다. 전체 표면은 `surface.npz`에 저장한다.

## 8. HTTP API

| Method | Path | 설명 |
|---|---|---|
| GET | `/api/v1/health` | 서비스 상태 |
| GET | `/api/v1/capabilities` | 구현·계획 기능과 기본 설정 |
| POST | `/api/v1/jobs` | 분석 작업 생성 |
| POST | `/api/v1/demo?profile=mixed` | 합성 작업 생성 |
| GET | `/api/v1/jobs` | 최근 작업 |
| GET | `/api/v1/jobs/{id}` | 상태 조회 |
| GET | `/api/v1/jobs/{id}/summary` | 요약 |
| GET | `/api/v1/jobs/{id}/defects` | 결함 JSON |
| GET | `/api/v1/jobs/{id}/defects.local.geojson` | 로컬 polygon |
| GET | `/api/v1/jobs/{id}/defects.enu.geojson` | ENU polygon |
| GET | `/api/v1/jobs/{id}/segments` | 구간 metric |
| GET | `/api/v1/jobs/{id}/surface` | 웹 surface preview |
| GET | `/api/v1/jobs/{id}/report` | report v2 HTML로 redirect |
| GET | `/api/v1/jobs/{id}/report/{asset_path}` | report v2의 허용된 정적 산출물 |
| POST | `/api/v1/jobs/{id}/scenarios` | 유지보수·강우 screening |
| DELETE | `/api/v1/jobs/{id}` | 완료/실패 작업 삭제 |

작업은 파일 기반 queue와 `ThreadPoolExecutor`로 실행한다. MVP 기본 worker 수는 1이다. 이는
한 호스트에서 대용량 점군 작업의 메모리 중첩을 피하기 위한 선택이다.

## 9. 설정 override

```json
{
  "source_type": "mapping_bundle",
  "mapping_output_path": "route_a/chunk_0000",
  "config": {
    "surface": {
      "grid_size_m": 0.05,
      "corridor_half_width_m": 3.5,
      "max_input_points": 2000000
    },
    "detection": {
      "pothole_min_depth_m": 0.035,
      "pothole_min_area_m2": 0.035,
      "rut_min_depth_m": 0.020,
      "segment_length_m": 20
    }
  }
}
```

알 수 없는 설정 키는 오류로 처리한다. 오타를 조용히 무시하지 않는다.

## 10. 보안 경계

MVP에서도 다음은 필수다.

- 절대 경로 입력 금지
- `..`로 workspace 이탈 금지
- `/workspace` read-only
- API에서 shell command 실행 금지
- PLY header와 file size 검증
- NPZ `allow_pickle=False`
- 대용량 입력 포인트 상한
- 실행 오류는 job별 `error.log`에 저장
- running job 삭제 금지

운영 단계에서 추가할 항목:

- 인증과 조직별 권한
- 작업 quota
- presigned object storage URL
- 악성 압축 파일 및 업로드 검사
- audit log
- reverse proxy TLS
- rate limit

## 11. 확장 경계

### RGB 크랙 모델

별도 GPU worker로 분리한다.

```text
rgb frame → road mask → crack segmentation → depth projection → BEV merge
```

결과는 현재 `defects.json` schema에 `source=rgb_ai`로 추가한다. geometry API 프로세스 안에
PyTorch를 강제로 포함하지 않는다.

### 분산 작업

파일 queue가 한계를 보이면 다음 순서로 교체한다.

1. object storage
2. PostgreSQL job metadata
3. Redis/RabbitMQ queue
4. geometry CPU worker
5. crack GPU worker
6. report worker

웹 API 계약을 유지하고 worker 구현만 교체한다.

### `RoadInventory-MMS`

`RoadInventory-MMS`는 작업 생성, 노선·자산 연결, 작업자 검수, 보수계획, 이력 관리 역할을
맡는다. 이 저장소는 분석 bundle을 만들고 결과 URI를 반환한다. 대용량 파일을 서버 간 JSON
body로 전달하지 않는다.

## 12. 성능 전략

현재 MVP는 최대 포인트를 결정적으로 샘플링하고 `(s,t)` 격자로 집계한다. 후속 단계에서는
다음을 적용한다.

- 청크 core + halo
- 10m 타일 streaming
- 청크 경계 결함 deduplication
- Zarr/Parquet/object storage
- surface preview와 full surface 분리
- worker별 peak RSS 기록
- 동일 설정 결과 hash

전체 노선보다 먼저 20프레임, 10m, 60초 청크 순으로 확대한다.

## 13. 타일·청크 route 계약

Stage 05 route 분석은 기본 10m core와 앞뒤 3m halo를 사용한다. fitting과 detector는
core+halo에서 실행하지만 결함 centroid가 들어간 core 하나만 최종 소유한다. tile별
`status.json`과 input signature가 같으면 완료 tile을 건너뛰며 실패 tile은 route manifest를
`partial`로 만들 뿐 완료 tile을 무효화하지 않는다.

```text
route_result/
  route_manifest.json
  route_defects.geojson
  route_defects.parquet
  route_segments.parquet
  tiles/tile-000000/status.json
  tiles/tile-000000/result/defects.parquet
  tiles/tile-000000/result/segments.parquet
  tiles/tile-000000/result/...
```

60초 청크는 각각 독립 처리한 뒤 명시적 global chainage offset으로 결과 파일만 병합한다.
병합 중에는 point cloud를 다시 열지 않는다. 초기 중복 조건은 동일 defect type,
chainage/lateral/polygon 거리와 metric 상대차이며 원본 ID는 `merged_from`에 보존한다. 실제
청크 seam 오차가 unknown이므로 현재 허용치는 experimental이다.

## 14. 추가 형상 screening 경계

Stage 06 detector는 기존 포트홀·러팅·범프 및 내부 형상 점수와 분리된 opt-in 모듈이다. 네
feature flag는 기본 `false`이고 detector별 예외를 격리한다. 따라서 하나가 실패해도 기존
geometry defect와 점수 계산은 계속된다.

| 기능 | 입력/metric | 제품 경계 |
|---|---|---|
| 단차·맨홀 후보 | residual gradient, `step_height_m`, `approach_slope_percent`, `edge_length_m` | 자산 DB/RGB 확인 전 후보 |
| 횡단경사 | reference surface `dz/dt`, median/p05/p95, crown offset | 교차로 ROI 없으면 manual review |
| 종단경사 | trajectory Z를 복원한 reference surface `dz/ds`, 기존 `roughness_proxy_m` | 표준 IRI 아님 |
| 물고임 screening | priority-flood DEM fill, 잠재 깊이·면적·체적 | 배수구/용량/강우 없는 screening proxy |

물고임 기능은 폐쇄 함몰 기하만 계산한다. 배수구 위치가 없는 현재 입력으로 drainage capacity나
침수 예측을 만들지 않는다. 모든 Stage 06 threshold는 실측 holdout으로 보정되기 전까지
experimental이며 자동 승인하지 않는다.

## 15. Stage 07 웹 점진 로드 계약

웹 앱은 기존 `viewer/`와 결합하지 않고 `services/road_condition_web/`에 유지한다. API는
`/workspace` 아래 상대 route 결과만 다음 read-only endpoint로 노출한다.

```text
GET /api/v1/route-datasets/manifest?path=<relative-route-result>
GET /api/v1/route-datasets/tile?path=...&tile_id=tile-000000&artifact=surface
```

manifest 응답은 host 절대 경로와 내부 artifact 경로를 제거한다. tile ID는 고정 형식,
artifact는 JSON allowlist, 파일 크기는 25MB 상한으로 검증한다. 브라우저는 여러 60초 청크의
manifest만 먼저 읽고 선택한 완료 tile의 summary/surface/defect/segment GeoJSON·JSON만
메모리에 유지한다. PLY endpoint는 제공하지 않는다.

기본 지도는 외부 네트워크가 필요 없는 local ENU evidence renderer다. VWorld/Cesium은 adapter
선택과 명확한 fallback을 제공하지만 API key/token과 WGS84 변환 설정 전에는 basemap을
활성화하지 않는다. 경량 3D evidence는 downsampled surface preview만 사용하며 1×/2×/5× Z
강조를 지원한다. route tile은 계속 read-only이고 완료 job의 수동 검수는 Stage 10 audit API를
통해서만 별도 저장한다.

## 16. Stage 08 보고서 v2 계약

보고서의 기준 데이터는 기존 `summary.json`, `segments.json`, `defects.json`이다. HTML을
source of truth로 두고 CSV와 결함별 evidence를 같은 입력에서 결정적으로 재생성한다. PDF는
분석 API 프로세스가 직접 Chromium을 실행하지 않도록 별도 report 이미지/CLI에서만 선택적으로
렌더링한다.

보고서에는 `algorithm_version`, 설정 SHA-256, 입력 JSON SHA-256, dataset ID, mapping commit
SHA를 남긴다. 제공되지 않은 항목과 RGB 원본/overlay는 빈 파일을 만들지 않고 `N/A`와
`missing` 목록으로 명시한다. 내부 형상 점수와 roughness proxy는 공식 PCI/IRI가 아니며,
low-confidence 결함과 수집 재검토 필요 상태를 숨기지 않는다.

API는 job result 내부 `report/`만 읽고 HTML/CSV/JSON/PNG/JPEG/SVG/PDF allowlist와 symlink
confinement를 적용한다. 원본 mapping workspace는 계속 read-only이며 보고서 manifest에는 host
절대 경로를 노출하지 않는다.

## 17. Stage 09 RGB 크랙 계약과 승인 게이트

RGB 크랙 코드는 `services/road_condition_crack_worker/`에 분리하며 geometry API 이미지에는
PyTorch나 모델 weights를 넣지 않는다. 입력 흐름은 road mask, depth validity, pose validity를
모두 통과한 픽셀의 `(s,t)` projection과 segmentation probability만 BEV에 누적한다. pose가
유효하지 않은 frame은 fail-closed로 제외한다.

모델 manifest에는 라벨 형식, 클래스, 최소 폭, RGB/노면 픽셀 해상도, wet/shadow/night 포함
여부, 학습·추론 GPU와 framework, weights SHA-256, holdout 결과와 명시적 승인자를 요구한다.
manifest와 weights는 `/workspace` 아래 상대 경로만 허용하고 read-only로 검증한다.

고정된 `road-condition-crack-holdout-v1`은 다음 metric을 요구한다.

- pixel precision/recall/F1
- IoU matching 기반 instance recall
- matched instance 길이 절대오차
- unmatched prediction 100m당 개수
- wet/shadow subset pixel F1

현재는 라벨, 픽셀 해상도, GPU, weights, 승인 threshold가 모두 미제공이다. 따라서 protocol의
자동 승인은 꺼져 있고 neural inference adapter도 구성하지 않았다. 제공되는 Dockerfile은
manifest/metric/BEV 후처리를 검증하는 contract image이며 운영 crack service가 아니다. 승인된
모델 runtime이 추가되면 이 worker도 `compose.road-condition.yml`에서 함께 실행되도록 한 뒤
서비스 완료로 전환한다.

후처리 defect는 `source=rgb_ai`, model name/version/weights hash와
`rgb_ai_experimental_unvalidated` flag를 갖는다. 작업자 수정 시 top-level prediction을 덮어쓰지
않고 `original_prediction`, 그 SHA-256, revision actor/time/reason/patch를 모두 보존한다.

## 18. Stage 10 점수 profile과 검수 workflow

점수 규칙은 `scoring_profiles/<profile_id>.yaml`에서 로드한다. 기본
`internal-geometry-mvp-v1@1.0.0`은 기존 `AnalysisConfig.score`와 20m 구간을 그대로 재현하는
experimental profile이다. 기관 source document, effective date, severity 기준표가 없으므로
standard 명칭과 자동 승인을 허용하지 않는다.

profile에는 distress type, severity/density 정의, score weights, missing metric 정책, 차로 평가,
승인 상태와 hash가 포함된다. API의 명시적 config override는 허용하되 결과에
`custom_override_applied=true`를 남긴다. summary, HTML/CSV/PDF report와 review event가 같은
profile ID/version/hash를 기록한다. `validated_standard`가 아닌 profile이 standard naming을
켜면 로더가 거부한다.

완료 job을 처음 검수할 때 `result/defects.json`의 canonical SHA-256과 각 raw prediction 사본을
`review_bundle.json`에 기록한다. 상태는 `pending`, `accepted`, `modified`, `rejected`,
`needs_recollection`이며, 변경은 다음 endpoint로만 추가한다.

```text
GET  /api/v1/jobs/{job_id}/reviews
POST /api/v1/jobs/{job_id}/reviews/{defect_id}
```

수정은 전체 `after` defect와 expected version을 요구한다. event는 before/after, actor, action,
UTC time, reason, profile, 이전/새 version을 보존한다. raw prediction hash가 달라지면 검수를
중단하고 stale version은 HTTP 409로 거부한다. 현재 인증은 없으므로 actor는 신원 보장이 없는
audit label이며, route tile 검수와 관리자 2단계 승인은 후속 범위다.

## 19. Stage 11 유지보수·열화 시나리오 v2

기존 `/scenarios` 응답과 기본 단가는 호환성을 위해 그대로 유지한다. v2는 별도
`/scenarios/v2` endpoint와 `maintenance_catalogs/<catalog_id>.yaml`을 사용한다. 기본
`internal-planning-v1@1.0.0`은 기존 MVP 단가를 버전만 부여해 이관한 내부 planning 예시이며,
실제 견적이나 승인 단가표가 아니다. source document와 effective date가 없고 승인 상태는
`experimental`이다.

공법 추천은 포트홀 패칭, 러팅 덧씌우기, 범프 연삭만 지원한다. 포트홀 0.25㎡, 러팅 1.0㎡,
범프 0.25㎡의 최소 작업량은 실측/계약 근거가 없는 experimental 기본값이다. 동원비는 알려진
비용에 한 번만 더한다. 차선 통제, 장비 이동, 폐기물 비용은 입력되지 않았으므로 `null`/`N/A`로
남기며 `full_total_krw`도 `null`이다. 따라서 `priced_total_krw`는 전체 사업비가 아니라 알려진
비용의 부분 합계다.

예산 제한 처리는 severity와 형상 metric으로 만든 내부 risk proxy를 정렬하는 결정적 greedy
screening이다. 수학적 최적화나 안전 위험 모델로 주장하지 않는다. 전후 내부 형상 점수는 선정된
후보 비율에 따른 `uncalibrated_planning_estimate_not_prediction`이며 실제 보수 효과 예측이
아니다. 공간 정합된 반복 조사 자료가 없어 연간 열화율과 열화 후 점수는 모두
`N/A_no_repeated_survey`다.

## 20. Stage 12 RoadInventory-MMS contract ingress

인증 방식, object storage, callback/polling 선택과 업무 ID schema가 제공되지 않았으므로 운영
connector는 만들지 않았다. 기본 `ROAD_CONDITION_RIMMS_CONTRACT_INGRESS_ENABLED=false`이며
endpoint는 HTTP 503으로 닫힌다. 격리된 contract 평가에서만 명시적으로 켤 수 있고, 켜더라도
외부 URI를 읽거나 네트워크 callback을 호출하지 않는다. 수락된 작업 상태는
`awaiting_connector_configuration`이다.

요청은 `road-condition-rimms-request-v1`, 기대 결과는
`road-condition-rimms-result-v1`과 정확히 일치해야 한다. 다른 version은 HTTP 422로 실패한다.
body에는 최대 2,048자의 `s3`, `gs`, `az`, `https` URI 참조만 허용하며 파일 내용, userinfo,
query, fragment와 `file://`은 거부한다. 이는 URI 형식 계약이지 해당 storage 지원 선언이 아니다.

`Idempotency-Key` header는 필수다. 원문은 저장하지 않고 SHA-256만 저장하며, 같은 키와 같은
canonical request 재시도는 기존 external job을 반환한다. 같은 키의 다른 요청 또는 같은
external job ID의 다른 키는 HTTP 409다. callback URL은 인증/재시도 정책이 정해지기 전까지
fail-closed로 거부하므로 delivery 재시도가 없으며, client는 GET polling만 사용한다.

source of truth의 임시 경계는 다음과 같다.

- 조사·노선·차로 식별자: `RoadInventory-MMS`
- 변경 전 raw 분석 prediction: road-condition analysis service
- 작업자 검수 결과의 최종 소유권과 동기화 방향: `N/A_direction_not_agreed`

운영 완료 조건은 인증, object connector/credential, source-of-truth 합의, callback을 선택할 경우
서명 검증과 retry/dead-letter 정책을 별도 승인하는 것이다.

## 21. Stage 13 분산 운영 전환 gate

운영 SLA, 동시 장치 수, 다중 worker host, 조직/tenant 요구가 제공되지 않아 분산 queue 전환
조건이 충족되지 않았다. 파일 queue와 2-service Compose는 그대로 유지하며 PostgreSQL, broker,
worker, auth/TLS를 부분적으로 추가하지 않는다. 준비도와 미충족 수용 기준은
`docs/road_condition/OPERATIONS_READINESS.md`에 기록한다. 현재 상태는
`DEFERRED_GATE_NOT_MET`이며 production-ready가 아니다.
