# Stage 03 checkpoint — calibration and camera pose contract

## 기준과 결정

- Branch: `feat/road-condition-stage-03-calibration`
- Base SHA: `9055396ba9fe31143b49d515a1f41fa8f90fde65`
- 사용자 지시: 전체 단계를 권장 기본값으로 진행하고 단계별 커밋 생성
- 장치 모델, RGB/Depth 해상도·FPS, 장착 높이/각도, lever arm, 평탄 기준 경로,
  포트홀/러팅 ground truth, 환경 조건, 최초 smoke 범위: 모두 `unknown`
- 정책: 기존 mapping 숫자 기본값은 바꾸지 않되 calibration manifest의 실측값은 null로 기록
- 자동 승인: 금지; `manual_review_required`
- 최대 보정 입력: 기본 500,000점; 최초 실데이터는 승인된 짧은 평탄 bundle만 허용
- 중단 조건: pose/trajectory 길이 불일치, 비유한 transform, 10,000점 미만 pose-quality
  reprojection support, point-aligned distance metadata 누락

## 구현

- `data/camera_poses.npz` format v1 adapter와 strict loader
- `data/analysis_source_manifest.json` format v1과 optical-camera/local-ENU 축 정의
- 기존 trajectory/PLY/summary 의미를 유지한 additive output
- API capabilities와 result quality에 pose/calibration/PLY-only 상태 노출
- opt-in frame pose round-trip validation과 pose-quality gating
- robust quadratic flat-surface distance-band MAD/RMSE 하네스
- noise-scaled experimental threshold candidate와 calibration HTML/CSV bundle

## 검증

- Python compileall: PASS
- Stage 03 pose/calibration unit: 2 passed
- road-condition regression: 8 passed
- full repository pytest: 132 passed, 1 third-party deprecation warning
- synthetic flat smoke: 30,080 input/analyzed points, 0 defects
- synthetic noise: MAD 0.0013397 m, RMSE 0.0019922 m
- experimental pothole threshold candidate: 0.0119172 m, manual review required
- synthetic calibration wall/peak RSS: 1.95 s / 79,268 KiB
- Compose YAML/config: PASS
- Docker build: PASS (API and web)
- Docker smoke on host ports 18080/18081: PASS
- JavaScript `node --check`: SKIPPED — node executable unavailable
- real short smoke: SKIPPED — allowed range and calibration dataset are unknown
- pothole/rut ground-truth acceptance: SKIPPED — measurements unavailable

## 알려진 제한

- fused PLY에는 원 RGB-D pixel 전체가 없으므로 frame reprojection flag는 대표 source pose의
  ENU↔camera round-trip 검증과 pose-quality gating까지 수행한다.
- 실데이터 flat false-positive area ratio와 포트홀/러팅 오차 목표는 아직 검증하지 않았다.
- 실측값이 들어오기 전 threshold는 실험값이며 자동 승인되지 않는다.

## 롤백

체크포인트의 부모 commit으로 checkout하면 Stage 03 additive 파일과 API quality 확장이 모두
제거되며 기존 Stage 00–02 계약으로 돌아간다.
