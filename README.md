# IMU-free RGB-D + GPS 3D mapping

OAK-D-LR의 RGB/정렬 깊이와 GNSS만 사용해 컬러 점군을 로컬 ENU 좌표로 만들고,
VWorld WebGL 3D 지도 위에서 비교하는 도구입니다. `imu.csv`, `imu_events.csv`,
`external_imu.csv`는 로더의 입력 목록에 없으며 열지 않습니다.

## 설치

```bash
cd depth_map
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

기본 후처리는 SciPy만으로 동작합니다. Open3D 이웃 백엔드를 함께 쓰려면 선택적으로
다음을 설치합니다. Open3D가 없어도 `auto`는 SciPy로 폴백합니다.

```bash
.venv/bin/pip install -e '.[postprocess]'
```

Open3D와 PDAL 비교 파이프라인을 함께 재현하려면 기존 `.venv`를 바꾸지 않고 전용
Conda 환경을 사용합니다. `environment-postprocess.yml`은 Python 3.11, Open3D 0.19와
PDAL 2.x를 고정합니다. 프로젝트 테스트 의존성은 환경 생성 후 설치합니다.

```bash
conda env create -f environment-postprocess.yml
conda run -n depth-map-postprocess python -m pip install -e '.[test]'
conda run -n depth-map-postprocess python -c \
  "import open3d as o3d; print(o3d.__version__)"
conda run -n depth-map-postprocess pdal --version
```

이 환경에서 실행할 때는 `conda run --no-capture-output -n
depth-map-postprocess python ...` 형태를 사용해야 후처리 자식 프로세스도 같은 PDAL
실행 파일을 찾습니다.

`.env`에 VWorld 키를 지정합니다. 키와 허용 도메인의 조합은 VWorld 개발자 설정과
일치해야 합니다.

```dotenv
VWORLD_API_KEY=발급받은_키
VWORLD_DOMAIN=http://127.0.0.1:8000
```

`.env`와 실제 데이터 및 PLY/BIN 같은 대용량 산출물은 Git에 추가하지 않습니다.

## 점군 밀도 프리셋

기본 프리셋은 일반 지도 확인용 `balanced`입니다. 프리셋은 프레임·픽셀 샘플링,
복셀 크기, 프레임별/최종 포인트 상한과 ROI를 한 번에 정합니다.

| 설정 | `preview` | `balanced` (기본) | `dense` |
| --- | ---: | ---: | ---: |
| 프레임 간격 | 10 | 2 | 1 |
| 픽셀 간격 | 10 | 4 | 2 |
| 복셀 크기 | 0.25 m | 0.10 m | 0.05 m |
| PLY 최대 점 수 | 1,000,000 | 5,000,000 | 20,000,000 |
| 웹 최대 점 수 | 300,000 | 700,000 | 800,000 |
| 프레임별 최대 점 수 | 5,000 | 20,000 | 50,000 |
| 세로 ROI | 0.15–0.90 | 0.10–0.98 | 0.05–0.98 |

개별 옵션을 함께 주면 그 값만 프리셋보다 우선합니다. 예를 들어 다음 명령은
`balanced`의 나머지 설정은 유지하면서 복셀 크기만 7 cm로 바꿉니다.

```bash
.venv/bin/python map_rgbd_gps.py DATASET_PATH \
  --output artifacts/balanced_7cm_map \
  --pose-mode hybrid \
  --cloud-preset balanced \
  --voxel-size-m 0.07
```

해석된 최종값은 `summary.json`의 `parameters.resolved_cloud_config`에 기록됩니다.
`--per-frame-max-points 0`은 프레임별 상한만 끄며, `--max-points`는 복셀 집계가
끝난 PLY 점군의 최종 상한으로만 사용됩니다.

## 점군 후처리

기본 `road-map` 후처리는 3D 투영 전 Depth 경계 검사와 복셀 관측 통계를 사용하고,
복셀화된 원본에 타일 단위 radius/statistical outlier, trajectory corridor의 local
surface 저점 필터, 밝은 저신뢰 결합 규칙을 순서대로 적용합니다.

| 프리셋 | 용도 |
| --- | --- |
| `off` | 후처리 전후 호환 비교. Depth 경계 필터도 끕니다. |
| `conservative` | 지주·표지판·가드레일 같은 얇은 구조 보존 우선 |
| `road-map` | 도로 지도용 기본 권장값 |
| `aggressive` | 제거가 거의 되지 않은 결과를 한 번 비교할 때 사용 |
| `road-map-temporal` | pose/depth/temporal/coarse support 기반 방사형 고스트 제거 |

```bash
.venv/bin/python map_rgbd_gps.py DATASET_PATH \
  --output artifacts/road_map_clean \
  --pose-mode hybrid \
  --cloud-preset dense \
  --postprocess-preset road-map
```

`road-map`에서는 raw·removed·진단 출력을 기본 보존합니다. `--no-keep-raw-cloud`,
`--no-save-removed-cloud`, `--no-write-postprocess-diagnostics`로 각각 명시적으로 끌 수
있습니다. 임계값 override는 `--radius-outlier-radius-m`,
`--postprocess-tile-size-m`, `--road-corridor-half-width-m` 등을 `--help`에서 확인합니다.

필터를 순서대로 검토해야 하는 실행에는 `--write-debug-stages`를 추가합니다. 각 단계의
전체 점 수와 제거 회계는 JSON에 기록하고, PLY/BIN/PNG는 기본 최대 500,000점의 결정적
샘플로 저장합니다. 정식 `cloud_raw_enu.ply`, `cloud_clean_enu.ply`,
`cloud_removed_enu.ply`는 이 상한과 무관하게 기존 전체 출력 계약을 유지합니다.
진단 상한은 `--debug-stage-max-points`로 조정할 수 있습니다.

방사형 ray/curtain 또는 원거리 양자화 띠에는 `road-map-temporal`을 사용합니다. 출력용
fine voxel과 별개로 15/25 cm support grid에서 source frame 중복을 제거하고, camera
baseline 또는 시간 간격을 만족한 독립 view를 셉니다. `occluded_or_unknown`은 삭제 근거가
아니며 이웃 depth가 후보 위치를 자유 공간으로 관측한 경우만 contradiction으로 셉니다.

```bash
conda run --no-capture-output -n depth-map-postprocess \
  python map_rgbd_gps.py DATASET_PATH \
  --output artifacts/road_map_temporal_smoke \
  --pose-mode hybrid --cloud-preset dense --max-frames 120 \
  --postprocess-preset road-map-temporal \
  --far-depth-policy adaptive \
  --support-voxel-size-m 0.15 --support-far-voxel-size-m 0.25 \
  --temporal-window-seconds 0.25 --pose-cloud-policy interpolate \
  --map-envelope-mode soft --no-auto-postprocess-fallback
```

Confidence map의 값 방향은 장치별로 다르므로 threshold를 주려면
`--depth-confidence-order lower-is-better|higher-is-better`도 함께 확인해야 합니다.
confidence가 없거나 threshold가 지정되지 않으면 비활성 사유가 frame report에 남습니다.

품질 guard가 coverage/구조물 과삭제를 감지하면 동일 raw에서 `conservative`를, 제거가
사실상 없으면 `aggressive`를 최대 한 번만 시험하고 더 안전한 결과를 선택합니다. RGB-D,
VO, GPS와 raw 점군을 다시 만들지 않으므로 전체 매핑 실행은 반복되지 않습니다.

### 후처리만 다시 실행

보존된 raw PLY·numeric NPZ·trajectory·summary에서 정제 단계만 다시 실행할 수 있습니다.
이 명령은 `trajectory.json`, `trajectory.csv`, `odometry.csv`와 raw 파일을 수정하지 않습니다.

```bash
.venv/bin/python postprocess_cloud.py \
  --output artifacts/road_map_clean \
  --postprocess-preset conservative \
  --no-auto-postprocess-fallback
```

Depth 품질, pose gate, temporal reprojection과 coarse support는 투영/융합 단계이므로
postprocess-only 실행에서 복구할 수 없습니다. format v2 provenance가 없는 기존 bundle에
`road-map-temporal`을 적용하면 report에 `legacy_compatibility_no_coarse_provenance`와
비파괴 compatibility mode를 기록합니다. 새 `cloud_raw_enu.ply`는 이 prefilter 뒤,
local ground와 ROR/SOR 전의 `fused_prefiltered_raw`입니다.

### 백엔드와 흰색 점 정책

`--neighbor-backend auto`는 Open3D가 import되면 이를 선택하고, 없으면
`scipy.spatial.cKDTree`를 사용합니다. `--ground-backend auto`의 기준 결과는 저장소 내부
local surface이며, PDAL 실행 파일이 없으면 비교를 생략하고 보고서에 기록합니다. 명시한
Open3D/PDAL을 사용할 수 없으면 조용히 설정을 바꾸지 않고 시작 전에 오류를 냅니다.

밝은 저채도 색은 단독 삭제 조건이 아닙니다. 프레임 지지가 낮으면서 radius/statistical
고립, 높은 위치 분산 또는 local surface 아래 조건이 함께 있을 때만 제거 사유를
추가합니다. 반복 관측된 흰 차선·표지판·차량·콘크리트는 색만으로 삭제하지 않습니다.

## 지도 생성

### 5분 시간 청크

대형 고밀도 입력은 전체 노선을 한 프로세스에서 반복하지 않고, 호출 한 번에 시간 청크
하나만 생성합니다. 경계는 데이터셋의 첫 synchronized RGB-D timestamp를 기준으로 한
반개구간 `[start, end)`이므로 인접 청크가 겹치지 않습니다. 모든 청크는 데이터셋 첫
프레임의 공통 ENU 원점을 사용해 이후 결합할 수 있습니다.

기존 `high_density_map` 코스의 첫 5분을 보수적으로 정제하고 단계별 진단까지 남기는
명령은 다음과 같습니다.

```bash
conda run --no-capture-output -n depth-map-postprocess \
  python map_rgbd_gps.py \
  /home/geon_lab/AI_PARK/2026_camera_lidar_calibration/safe_gard_test/data/2026-08-19_10-16-33_raw \
  --output artifacts/high_density_map_5min_chunk_0000 \
  --pose-mode hybrid \
  --cloud-preset dense \
  --min-depth-m 0.7 \
  --chunk-duration-seconds 300 \
  --chunk-index 0 \
  --postprocess-preset conservative \
  --radius-outlier-radius-m 0.30 \
  --statistical-std-ratio 3.5 \
  --below-ground-tolerance-m 0.20 \
  --neighbor-backend open3d \
  --ground-backend auto \
  --write-debug-stages \
  --debug-stage-max-points 500000 \
  --no-auto-postprocess-fallback
```

이 명령으로 검증한 chunk 0은 raw 20,000,000점, clean 18,429,603점이며 도로 하부
후보 581,659점을 모두 제거했다. 노선 199개 구간은 전부 유지됐지만 XY occupied-cell
coverage는 81.03%라 90% 품질 guard는 아직 통과하지 못했다. 실제 수치와 다음 조정 계획은
`DEPTH_MAP_5MIN_CHUNK_DEBUG_REPORT.md`에 기록했다.

다음 구간은 output과 `--chunk-index`를 함께 `0001`/`1`로 바꿔 별도 호출합니다. 한 번에
전체 인덱스를 도는 자동 루프는 제공하지 않습니다. 먼저 현재 청크의
`data/debug_stages/index.json`과 진단 이미지를 승인한 뒤 다음 청크를 실행하십시오.
`--chunk-duration-seconds`는 `--start-frame` 또는 `--max-frames`와 함께 사용할 수 없습니다.

### 전체 데이터를 60초 temporal 청크로 처리

`2026-08-19_10-16-33_raw`은 동기화 RGB-D 13,283프레임, timestamp span 1,208.353초이므로
60초 반개구간 청크가 총 21개(`chunk-index` 0–20)입니다. 아래 루프는 메모리 사용이
겹치지 않도록 한 번에 한 청크만 순차 실행하며, 중간 청크가 실패하면 즉시 중단합니다.

```bash
set -euo pipefail

DATASET_PATH=/home/geon_lab/AI_PARK/2026_camera_lidar_calibration/safe_gard_test/data/2026-08-19_10-16-33_raw

for CHUNK_INDEX in $(seq 0 20); do
  CHUNK_TAG=$(printf '%04d' "${CHUNK_INDEX}")

  conda run --no-capture-output -n depth-map-postprocess \
    python map_rgbd_gps.py \
    "${DATASET_PATH}" \
    --output "artifacts/ultra_density_map_60sec_chunk_${CHUNK_TAG}_temporal_v1" \
    --pose-mode hybrid \
    --cloud-preset dense \
    --cloud-frame-stride 1 \
    --pixel-stride 1 \
    --voxel-size-m 0.03 \
    --per-frame-max-points 0 \
    --max-points 40000000 \
    --browser-max-points 1000000 \
    --min-depth-m 0.7 \
    --chunk-duration-seconds 60 \
    --chunk-index "${CHUNK_INDEX}" \
    --postprocess-preset road-map-temporal \
    --neighbor-backend scipy \
    --ground-backend local \
    --write-debug-stages \
    --debug-stage-max-points 500000 \
    --no-auto-postprocess-fallback
done
```

각 디렉터리에는 해당 구간의 `data/cloud_clean_enu.ply`가 별도로 생성됩니다. 모든 청크는
데이터셋 첫 프레임의 공통 ENU 원점을 사용하지만, 위 명령은 청크 PLY를 마지막에 하나로
병합하지 않습니다. 검증한 첫 60초 청크 하나는 1시간 3분 18초, peak RSS 17.43 GiB가
걸렸으므로 21개 전체 순차 실행은 장시간 작업입니다. 마지막 `chunk-index 20`은 약
8.35초 구간이라 앞선 청크보다 짧습니다.

일반적인 주행 구간은 다음처럼 생성합니다.

```bash
.venv/bin/python map_rgbd_gps.py DATASET_PATH \
  --output artifacts/balanced_map \
  --pose-mode hybrid \
  --cloud-preset balanced
```

빠르게 GPS 배치와 지도 로딩만 확인하려면 `preview`와 GPS 자세를 사용할 수 있습니다.

```bash
.venv/bin/python map_rgbd_gps.py DATASET_PATH \
  --output artifacts/gps_preview \
  --pose-mode gps \
  --cloud-preset preview
```

CloudCompare 또는 Open3D에서 확인할 고밀도 PLY는 `dense`로 생성합니다.

```bash
.venv/bin/python map_rgbd_gps.py DATASET_PATH \
  --output artifacts/dense_map \
  --pose-mode hybrid \
  --cloud-preset dense \
  --postprocess-preset road-map
```

프레임별 제한을 끄는 시험은 메모리 사용량이 크게 늘 수 있으므로 짧은 구간에서만
실행합니다.

```bash
.venv/bin/python map_rgbd_gps.py DATASET_PATH \
  --output artifacts/no_frame_cap_test \
  --pose-mode hybrid \
  --max-frames 100 \
  --cloud-preset balanced \
  --per-frame-max-points 0
```

### 거리·회전·시간 기반 키프레임

세 키프레임 옵션이 모두 생략되면 프리셋 또는 `--cloud-frame-stride`의 고정 프레임
간격을 사용합니다. 하나라도 지정하면 고정 간격을 완전히 대체하며, 지정한 양수
조건 중 하나라도 만족하는 프레임을 선택합니다. 조건은 마지막으로 선택한 키프레임과
비교하고 첫 프레임과 마지막 프레임은 중복 없이 포함합니다.

```bash
.venv/bin/python map_rgbd_gps.py DATASET_PATH \
  --output artifacts/keyframe_map \
  --pose-mode hybrid \
  --cloud-preset balanced \
  --cloud-keyframe-distance-m 0.20 \
  --cloud-keyframe-angle-deg 1.0 \
  --cloud-keyframe-max-dt-s 0.50
```

부분 지정도 가능합니다. 예를 들어 거리만 지정하면 회전과 시간 조건은 비활성이고
거리 조건만 사용합니다.

### 카메라 장착값

카메라 장착각과 GNSS 안테나 대비 카메라 위치를 알면 다음 값을 지정합니다. 각도는
카메라 기준으로 yaw 오른쪽(+), pitch 아래(+), roll 영상 시계방향(+)입니다. 오프셋은
GNSS 안테나에서 카메라 중심으로 향하는 벡터를 카메라의 right/down/forward 축으로
표현한 값입니다.

```bash
--mount-yaw-deg 0 --mount-pitch-deg 0 --mount-roll-deg 0 \
--camera-offset-right-m 0 --camera-offset-down-m 0 --camera-offset-forward-m 0
```

## 밀도와 메모리 동작

각 프레임의 유효 깊이 점은 먼저 NumPy로 로컬 복셀 집계한 뒤 XYZ/RGB 합과 XYZ 제곱합,
관측 수, 서로 다른 프레임 수와 관측 깊이를 연속 배열 run으로 병합합니다. 위치 분산은
`sqrt(var_x + var_y + var_z)`로 정의합니다. 같은 크기 단계의 run만 정렬·축약하는 방식이라
모든 원시 프레임 점을 하나의 거대한 배열로 모으지 않습니다. 복셀 대표 XYZ/RGB는
첫 점이 아니라 전체 누적 평균이며, 같은 입력과 설정은 결정적인 결과를 만듭니다.

최종 PLY 상한과 웹 상한을 적용할 때는 입력 배열 순서에 따른 `linspace` 대신 3D 공간
grid를 이용한 결정적 균등 샘플링을 사용합니다. 점유 cell에서 먼저 점을 고른 뒤 공간
전체에 round-robin으로 잔여 점을 배분하므로 한 주행 구간에 치우치는 현상을 줄입니다.

`dense`는 최대 2천만 PLY 점을 허용하는 오프라인 프리셋이라 처리시간과 메모리가 크게
늘 수 있습니다. `--per-frame-max-points 0`, 작은 복셀 또는 큰 `--max-points`를 함께
사용할 때는 먼저 짧은 구간으로 확인하십시오. 웹 뷰어는 점을 개별 Cesium primitive로
추가하므로 백만 점을 넘는 `--browser-max-points`에는 긴 로딩과 브라우저 메모리 압박
경고가 표시됩니다.

이웃 필터는 20 m core tile과 overlap query로 처리하고 각 점은 core에서 정확히 한 번
판정합니다. 그래도 타일 안의 점 수, NPZ 압축과 진단 이미지 생성에 따라 실행시간과
peak RSS가 달라집니다. 대형 입력은 같은 설정을 반복 실행하기 전에 report와 실패
단계를 먼저 확인하십시오.

## 3D 지도에서 확인

```bash
.venv/bin/python serve_map.py \
  --output artifacts/balanced_map \
  --port 8000 --open
```

브라우저에서 `http://127.0.0.1:8000/viewer/`를 엽니다. VWorld WebGL 3D가
초기화되지 않으면 Cesium + OpenStreetMap으로 자동 전환하며, 키가 유효하면 VWorld
위성 WMTS도 겹쳐 표시합니다. Cesium 폴백은 타원체 지도라 실제 3D 지형고가 없으며
수평 정합 확인용입니다. `?engine=cesium`을 붙이면 폴백을 강제로 시험할 수 있습니다.

뷰어는 정제 점군을 먼저 불러오고 원본·제거점은 체크할 때 처음 로드합니다. 정제/원본/
제거점과 융합/GPS trajectory를 독립적으로 전환할 수 있으며 제거점 색 범례가 사유를
나타냅니다. 웹 수가 작아도 PLY 점이 사라진 것은 아닙니다. 전체 밀도와 표면 품질은
CloudCompare에서 `data/cloud_clean_enu.ply`와 raw/removed를 함께 확인하십시오. 레이어와
동/북/높이 오프셋은 표시만 바꾸며 산출물에는 영향을 주지 않습니다.

## 산출물과 진단

- `data/cloud_raw_enu.ply`: format v2 depth/pose/temporal/coarse prefilter 후 fused raw
- `data/cloud_clean_enu.ply`: 선택된 후처리의 정제 점군
- `data/cloud_removed_enu.ply`: 대표 제거 사유 색을 입힌 제거점
- `data/cloud_enu.ply`: `cloud_clean_enu.ply`와 동일한 하위 호환 출력
- `data/cloud_raw_metadata.npz`: raw와 정렬된 관측 수·프레임 수·위치 분산·깊이
- `data/removed_points_metadata.npz`: 제거점 원본 RGB·사유 bitmask·대표 사유
- `data/points_raw.bin`, `points_clean.bin`, `points_removed.bin`: 공간 균등 웹 샘플
- `data/points.bin`: `points_clean.bin`과 동일한 하위 호환 출력
- `data/postprocess_report.json`: 제거 사유, 품질 guard, 의존성, 선택 결과
- `data/postprocess_stages.json`: 단계별 입력·출력·제거 수와 소요시간
- `data/postprocess_parameters.json`: 실제 해석된 프리셋·override·백엔드
- `data/trajectory.csv`: 프레임별 융합/GPS/그래프 전 GPS 보조 VO 위치와 GNSS 품질
- `data/odometry.csv`: 매칭·inlier·재투영 오차·GPS 게이트 사유를 포함한 간선 진단
- `data/pose_frame_quality.csv`: frame별 pose 품질, cloud 사용/보간/제외 사유와 projection 집계
- `data/depth_frame_quality.json`: depth 분포, far peak/threshold, confidence 및 단계별 기여량
- `data/cloud_provenance_sample.npz`: source frame/depth/pose/support/temporal compact sample
- `data/prefilter_removed_sample.npz`: cap 전 제거점의 결정적 sample(제거 발생 시)
- `data/trajectory.geojson`: 세 궤적의 WGS84 LineString
- `data/summary.json`: 해석된 설정, 점군 단계별 제거 통계와 품질 요약
- `data/accuracy_report.json`: VO 채택률과 GPS 대비 잔차 통계
- `diagnostics/top_before_after.png`, `side_before_after.png`: raw/clean/removed 비교
- `diagnostics/removed_reason_top.png`: 제거 사유 top view
- `diagnostics/frame_reject_heatmap.png`, `problem_frame_montage.png`: frame gate 진단
- `diagnostics/representative_tiles.json`, `run_summary.txt`: 대표 타일과 실행 요약
- `data/debug_stages/index.json`: 단계 순서, 전체 count, 샘플 상한과 제거 회계 검증
- `data/debug_stages/NN_stage/`: 단계별 survivor/removal 샘플 PLY·BIN과 `stage.json`
- `diagnostics/debug_stages/NN_stage/`: 모든 단계가 같은 축을 쓰는 top/side 비교 PNG
- `diagnostics/pdal_clean_comparison.ply`: PDAL이 있을 때 생성하는 binary 비교 결과
- `viewer/`: VWorld/Cesium 브라우저 뷰어

`summary.json`에서는 항상 `point_count == ply_point_count == clean_point_count`이고
`raw_point_count == clean_point_count + removed_point_count`입니다.
`browser_point_count <= clean_point_count`이며 각 BIN 헤더 count는 대응하는
`*_browser_point_count`와 같아야 합니다. 다음 명령으로 해석된 설정과 후보 픽셀,
유효/무효 깊이, 프레임별 상한, 복셀 병합, 최종 상한에서 줄어든 수를 확인합니다.

```bash
jq '.cloud, .parameters.resolved_cloud_config, .parameters.resolved_postprocess_config' \
  artifacts/balanced_map/data/summary.json
```

### 좌표계가 포함된 LAS 변환

로컬 ENU PLY를 측량/GIS 도구에서 바로 읽을 수 있는 LAS 1.4 point format 7로
변환하려면 PDAL이 포함된 후처리 환경에서 다음을 실행합니다. 원점의 경위도로 WGS 84
UTM zone을 자동 선택하고, 이 데이터의 경우 EPSG:32652가 LAS에 기록됩니다. X/Y는 UTM
metre, Z는 WGS 84 타원체고 metre이며 원본 8-bit RGB 값은 LAS RGB 필드에 보존합니다.

```bash
conda run --no-capture-output -n depth-map-postprocess \
  python convert_cloud_to_las.py \
  --output artifacts/ultra_density_map_60sec_chunk_0000 \
  --stage clean
```

기본 결과는 `data/cloud_clean_epsg32652.las`이고, 재현 가능한 PDAL pipeline과 좌표계,
점 수, 경계 검증값은 각각 같은 위치의 `.pdal.json`, `.report.json`에 저장됩니다.
`--stage raw|removed`, `--target-crs`, `--las`, `--scale-m`으로 입력 단계와 출력을 바꿀
수 있습니다. 기존 LAS를 명시적으로 교체할 때만 `--overwrite`를 사용하십시오.

건물·수목·차량·부유점을 제외하고 지면만 LAS로 만들려면 `--ground-only`를 추가합니다.
ELM으로 낮은 고립 노이즈를 먼저 제외하고 SMRF가 지면을 `Classification=2`로 분류한
뒤 해당 점만 기록합니다. 결과 파일명에는 `_ground_`가 붙으므로 전체 LAS를 덮어쓰지
않습니다.

```bash
conda run --no-capture-output -n depth-map-postprocess \
  python convert_cloud_to_las.py \
  --output artifacts/ultra_density_map_60sec_chunk_0000_temporal_v1 \
  --stage clean --ground-only
```

기본값은 `--ground-cell-m 0.50`, `--ground-scalar 1.20`, `--ground-slope 0.15`,
`--ground-threshold-m 0.20`, `--ground-window-m 8.0`입니다. 지면 누락이 많으면
`--ground-threshold-m`을 조금 높이고, 비지면이 많이 남으면 낮춰 조정합니다. 일반 LAS
변환은 streaming이지만 SMRF 지면 추출은 전체 점군 이웃 연산이므로 입력이 클수록 실행
메모리가 더 필요합니다.

통계가 비정상적으로 작다면 `sampled_frame_count`와 `decoded_frame_count`를 먼저 보고,
그다음 `candidate_pixel_sample_count`, `valid_depth_sample_count_before_voxel`,
`discarded_by_per_frame_cap`, `discarded_by_voxel`, `discarded_by_final_cap` 순서로 어느
단계에서 밀도가 줄었는지 확인합니다.

## 정확도 해석

`gps_constraint_residual_horizontal_m`은 GPS가 최적화 제약으로 들어간 뒤의 내부
잔차입니다. 따라서 독립적인 절대 정확도 지표가 아닙니다. `pre_graph_gps_aided`도
GPS 방위각, Essential 스케일과 실패 폴백을 사용합니다. 실제 정합은 VWorld의 도로
경계, 건물, 위성영상 또는 별도 측량 기준점과 점군을 비교해 판단해야 합니다.

IMU를 빼면 급격한 회전·정지·텍스처 부족 구간에서 자세가 약해질 수 있습니다.
카메라–GNSS 외부표정과 레버암을 측정해 위 옵션에 넣는 것이 절대 정합 개선의 중요한
선행 작업입니다. 이번 밀도 작업에서 제외한 기능의 단계별 계획은
[`DEPTH_MAP_FOLLOW_UP_PLAN.md`](DEPTH_MAP_FOLLOW_UP_PLAN.md)에 정리했습니다.

## 테스트

```bash
.venv/bin/python -m pytest -q
.venv/bin/python map_rgbd_gps.py --help
.venv/bin/python postprocess_cloud.py --help
conda run -n depth-map-postprocess python convert_cloud_to_las.py --help
```

실데이터 스모크 테스트는 입력이 유효할 때 `--max-frames 20` 이하로 한 번만 실행하고,
전체 데이터 처리는 별도 검증 작업에서 명시적으로 계획해 수행합니다.
