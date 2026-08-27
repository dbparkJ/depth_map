# Stage 03 — 실데이터 보정과 camera pose 계약

## 목표

합성 성공을 실제 측정 가능성으로 착각하지 않도록 장치·장착·Depth·pose 오차를 계측하고,
분석에 필요한 frame pose/provenance를 보존한다.

## 시작 전 반드시 사용자에게 물을 질문

1. 사용할 장치 정확한 모델은 무엇인가?
2. RGB와 Depth 해상도, FPS, 정렬 방식은 무엇인가?
3. 카메라 렌즈 중심 높이는 노면 기준 몇 m인가?
4. 카메라 mount yaw/pitch/roll 실측값은 무엇인가?
5. GNSS 안테나에서 카메라 중심까지 right/down/forward offset은 무엇인가?
6. 평탄한 아스팔트 기준 구간이 있는가?
7. 실측 포트홀은 몇 개이며 깊이·장축·단축·면적을 어떻게 측정했는가?
8. 좌우 러팅 실측 구간이 있는가?
9. 건조/습윤, 햇빛/그늘 조건별 자료가 있는가?
10. 최초 실데이터 경로와 분석 허용 구간은 어디인가?

답이 없으면 다음 기본값을 사용하지 말고 `unknown`으로 기록한다. unknown calibration 상태에서는
결과에 `manual_review_required`를 추가한다.

## 필요한 새 산출물

```text
data/camera_poses.npz
  format_version                 uint16 scalar
  frame_index                    int32[N]
  source_frame_index             int32[N]
  timestamp_monotonic_ns         int64[N]
  T_enu_camera                   float64[N,4,4]
  pose_quality_score             float32[N]
  pose_method_code               uint8[N]
  rgb_intrinsics                 float64[3,3]
  depth_intrinsics               float64[3,3]
  rgb_to_depth_transform         float64[4,4]
  camera_to_gnss_transform       float64[4,4]
```

메모리 문제 시 calibration matrix는 manifest JSON에 한 번만 기록한다.

```text
data/analysis_source_manifest.json
```

필수 필드:

```json
{
  "format_version": 1,
  "dataset_id": "...",
  "mapping_commit_sha": "...",
  "camera_model": "...",
  "camera_height_m": null,
  "mount_yaw_deg": null,
  "mount_pitch_deg": null,
  "mount_roll_deg": null,
  "camera_offset_right_m": null,
  "camera_offset_down_m": null,
  "camera_offset_forward_m": null,
  "rgb_depth_alignment": "...",
  "timestamp_basis": "monotonic_ns",
  "calibration_status": "unknown|measured|estimated"
}
```

## 구현 작업

1. 기존 trajectory에 orientation이 어디까지 보존되는지 조사한다.
2. 현재 pose representation을 4×4 matrix로 내보내는 adapter를 추가한다.
3. 기존 출력 파일을 변경하지 않고 새 optional file만 추가한다.
4. analysis backend가 pose file 유무를 capabilities와 quality에 기록하도록 한다.
5. frame reprojection mode는 feature flag로 추가한다.
6. PLY-only mode는 계속 지원하되 정밀 측정 제한을 명시한다.
7. 평탄 기준면에서 거리 band별 residual MAD/RMSE를 계산한다.
8. threshold recommendation report를 생성한다.

## 보정 결과 파일

```text
calibration/
  manifest.json
  flat_surface_noise.json
  pothole_ground_truth.csv
  rut_ground_truth.csv
  threshold_recommendation.json
  calibration_report.html
```

## 수용 기준

- 기존 `summary.json`, PLY, trajectory 계약이 깨지지 않는다.
- pose matrix와 frame index 길이가 일치한다.
- 모든 transform의 축 정의가 문서화된다.
- 평탄 기준면 false positive가 합의한 한도 이하다.
- 최소 검출 깊이는 고정 상수가 아니라 측정 noise에서 계산된다.
- 실측과 자동 포트홀 깊이 오차가 표에 기록된다.

최초 권장 목표이며 데이터 확인 후 구현 전에 확정한다.

```text
flat false-positive area ratio <= 0.5%
pothole max-depth absolute error <= max(0.02m, 20%)
rut-depth absolute error <= max(0.015m, 25%)
```

결과 확인 후 목표를 낮추지 않는다. 데이터 한계면 실패로 기록하고 장치/장착/알고리즘 개선을
선택한다.

## 테스트 명령

```bash
PYTHONPATH=. pytest -q tests/road_condition/test_pose_export.py
PYTHONPATH=.:services/road_condition_api pytest -q tests/road_condition
```

실데이터 smoke 명령은 데이터 경로와 frame 범위를 완료 보고에 그대로 남긴다.

## 완료 후 질문

```text
실데이터 보정 결과를 승인합니까?
A) 권장 threshold로 Stage 04 진행
B) 장착 보정부터 다시 수행
C) PLY-only MVP를 유지하고 정밀 측정 보류
D) 추가 holdout 구간으로 재검증
```
