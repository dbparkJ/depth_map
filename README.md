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

`.env`에 VWorld 키를 지정합니다. 키와 허용 도메인의 조합은 VWorld 개발자 설정과
일치해야 합니다.

```dotenv
VWORLD_API_KEY=발급받은_키
VWORLD_DOMAIN=http://127.0.0.1:8000
```

현재 프로젝트의 `.env`에는 요청받은 공개 키가 설정되어 있으며 Git에서는 제외됩니다.

## 전체 지도 생성

```bash
.venv/bin/python map_rgbd_gps.py \
  /home/geon_lab/AI_PARK/2026_camera_lidar_calibration/safe_gard_test/data/2026-08-19_10-51-09_raw \
  --output artifacts/2026-08-19_10-51-09_imu_free_map \
  --pose-mode hybrid
```

빠르게 GPS 배치와 지도 로딩만 확인하려면 시각 오도메트리를 생략할 수 있습니다.

```bash
.venv/bin/python map_rgbd_gps.py DATASET \
  --output artifacts/gps_only --pose-mode gps
```

카메라 장착각과 GNSS 안테나 대비 카메라 위치를 알면 다음 값을 지정하면 됩니다.
각도는 카메라 기준으로 yaw 오른쪽(+), pitch 아래(+), roll 영상 시계방향(+)입니다.
오프셋은 GNSS 안테나에서 카메라 중심으로 향하는 벡터를 카메라의
right/down/forward 축으로 표현한 값입니다.

```bash
--mount-yaw-deg 0 --mount-pitch-deg 0 --mount-roll-deg 0 \
--camera-offset-right-m 0 --camera-offset-down-m 0 --camera-offset-forward-m 0
```

## 3D 지도에서 확인

```bash
.venv/bin/python serve_map.py \
  --output artifacts/2026-08-19_10-51-09_imu_free_map \
  --port 8000 --open
```

브라우저에서 `http://127.0.0.1:8000/viewer/`를 엽니다. VWorld WebGL 3D가
초기화되지 않으면 Cesium + OpenStreetMap으로 자동 전환하며, 키가 유효하면 VWorld
위성 WMTS도 겹쳐 표시합니다. Cesium 폴백은 타원체 지도라 실제 3D 지형고가 없으며
수평 정합 확인용입니다. `?engine=cesium`을 붙이면 폴백을 강제로 시험할 수 있습니다.

뷰어에서는 컬러 점군, 융합 궤적, GPS 궤적, 원시 VO 궤적과 잔차 벡터를 각각
켜고 끌 수 있습니다. 동/북/높이 오프셋 슬라이더는 정합 차이를 계측하기 위한
수동 미세조정 도구이며 원본 산출물을 바꾸지 않습니다.

## 산출물

- `data/cloud_enu.ply`: 원점 정보가 헤더에 기록된 로컬 ENU 컬러 점군(기본 최대 100만 점)
- `data/points.bin`: 웹 표시를 위해 별도 축소한 점군(기본 최대 30만 점)
- `data/trajectory.csv`: 프레임별 융합/GPS/그래프 전 GPS 보조 VO 위치와 GNSS 품질
- `data/odometry.csv`: 매칭·inlier·재투영 오차·GPS 게이트 사유를 포함한 간선 진단
- `data/trajectory.geojson`: 세 궤적의 WGS84 LineString
- `data/summary.json`: 입력 목록, 파라미터, 점군 범위, 품질 요약
- `data/accuracy_report.json`: VO 채택률과 GPS 대비 잔차 통계
- `viewer/`: VWorld/Cesium 브라우저 뷰어

## 정확도 해석

`gps_constraint_residual_horizontal_m`은 GPS가 최적화 제약으로 들어간 뒤의 내부
잔차입니다. 따라서 독립적인 절대 정확도 지표가 아닙니다. `pre_graph_gps_aided`도
GPS 방위각, Essential 스케일과 실패 폴백을 사용합니다. 실제 정합은 VWorld의 도로
경계, 건물, 위성영상 또는 별도 측량 기준점과 점군을 비교해 판단해야 합니다.

IMU를 빼면 급격한 회전·정지·텍스처 부족 구간에서 자세가 약해질 수 있습니다.
코드는 RGB-D PnP가 실패하거나 GPS 이동과 크게 모순되는 간선을 GPS 보간으로
대체합니다. 카메라–GNSS 외부표정과 레버암을 측정해 위 옵션에 넣는 것이 절대
정합을 개선하는 가장 중요한 후속 작업입니다.

## 테스트

```bash
.venv/bin/python -m pytest -q
```
