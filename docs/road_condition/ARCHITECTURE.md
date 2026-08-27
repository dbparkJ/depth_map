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
- 유지보수 단가와 강우 screening 값을 API에 전송한다.

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
  maintenance.py     보수 수량·단가·강우 screening
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
```

`clean` 또는 `removed`를 지정하면 해당 PLY를 읽는다. 포트홀 보존을 위해 기본값은 `raw`다.

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
| GET | `/api/v1/jobs/{id}/report` | HTML 리포트 |
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
