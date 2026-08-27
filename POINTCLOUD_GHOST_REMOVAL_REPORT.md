# Point-cloud ghost removal implementation report

주된 원인은 **원거리 depth ray/quantization과 depth discontinuity**였고, pose 품질 저하가 일부
복제면을 키웠으며 기존 ±7 m ground 적용 범위가 하방 꼬리를 남겼다. 새 full run에서는 투영 전
depth quality가 473,837,061개 표본을 제외하고 temporal consistency가 19,870,689개 후보를
제외했으며, fusion 뒤 support/ground/ROR/SOR가 1,373,195개 voxel을 추가로 제외했다. 최종
trusted anchor는 99.944%, trusted core cell은 99.397%, trajectory 구간은 37/37 보존됐다.
새 결과가 단순 crop이 아닌 근거는 soft envelope의 실제 제거가 0점이고, 120-frame 동일 입력
A/B에서 near-range point-to-plane p50이 0.129 m에서 0.081 m로 개선됐으며 full temporal
결과가 0.054 m를 기록했다는 점이다. 단, 이 residual은 내부 인접-frame 진단이지 독립 측량
정확도가 아니다.

## 구현 범위

파이프라인 순서를 다음과 같이 바꿨다.

```text
pose/frame QA
→ shared per-frame depth quality (PnP + dense)
→ projective temporal consistency
→ near/far coarse multi-view support
→ 3 cm fine fusion
→ supported ground + soft map envelope
→ mild ROR/SOR
→ final spatial cap/export
```

주요 변경은 다음과 같다.

- `depth_quality.py`: confidence 방향/누락/shape 검증, adaptive far peak, inverse-depth local
  consistency, far-only 경계 erosion과 speckle 처리, frame report를 구현했다.
- `depth_consistency.py`: support, occluded-or-unknown, free-space contradiction을 분리했다.
- `frame_quality.py`: pose frame score, keep/skip/interpolate, position+SLERP 보간,
  `pose_frame_quality.csv`, heatmap/montage를 구현했다.
- `pointcloud.py`: source frame/time/depth/pose provenance, ±1/±2 sliding depth cache,
  temporal prefilter, 15/25 cm coarse support와 독립 baseline/time view 수, pre-cap filtering,
  metadata-aligned cap을 구현했다.
- `ground_surface.py`: seed/apply corridor를 8 m/30 m로 분리하고
  measured/interpolated/fallback/unknown provenance와 uncertainty를 추가했다.
- `trajectory_geometry.py`: 곡선 polyline의 along/cross/local-up과 endpoint buffer를 구현했다.
- `postprocess.py`: temporal/support/pose reason, trusted-reference guard, 실제 soft envelope,
  reliable ground hard reject와 마지막 ROR/SOR polish를 구현했다.
- `postprocess_io.py`: numeric provenance format v2, `fused_prefiltered_raw` 의미와 legacy
  compatibility mode를 구현했다.
- `registration_quality.py`: source-frame voxel 기반 인접 frame overlap과 range-stratified
  3-NN point-to-plane 진단, frame-pair별 기록을 구현했다.
- CLI, debug stage writer, README를 새 preset과 산출물 계약에 맞게 갱신했다.

## `road-map-temporal` full-run resolved 설정

| 항목 | 값 |
| --- | ---: |
| fine voxel | 0.03 m |
| far policy | adaptive, soft 20.0 m, hard 28.8 m |
| edge domain/tolerance | inverse-depth, radius 2 px, abs 0.12 m, rel 0.02 |
| near/far support voxel | 0.15 / 0.25 m |
| independent support | 2회, baseline 0.4 m 또는 time 0.5 s |
| temporal window/tolerance | 0.25 s, abs 0.15 m, rel 0.02 |
| pose cloud policy | interpolate; 24 inliers, ratio 0.2, reprojection 2.5 px |
| ground seed/apply | 8 / 30 m |
| soft envelope | core 10 m, soft 25 m, endpoint buffer 30 m |
| final ROR/SOR | 0.18 m / 2 neighbors, 16 neighbors / 3.5 sigma |

Confidence map은 데이터셋에 없어 filter가 비활성화됐고 그 사실이 frame report에 기록됐다.

## 실제 데이터 검증

기준은 commit `69a8b6106f921833b44d7552f5b2104171f8b08c`, branch
`feat/pointcloud-postprocessing`, chunk 0의 659 frame/59.93 s/약 365 m 구간이다. 기존
`artifacts/ultra_density_map_60sec_chunk_0000`은 읽기 전용으로 유지했고 새 결과는
`artifacts/ultra_density_map_60sec_chunk_0000_temporal_v1`에 썼다.

### Frame/depth/fusion 회계

| 단계 | count |
| --- | ---: |
| 전체 / cloud 사용 frame | 659 / 640 |
| original / interpolated / skipped-long-bracket | 613 / 27 / 19 |
| candidate pixel samples | 1,371,340,800 |
| valid depth samples | 785,301,933 |
| depth-quality rejected | 473,837,061 |
| adaptive far rejected | 1,987,073 |
| temporal tests / supports / contradictions | 592,359,710 / 575,301,406 / 17,058,304 |
| temporal rejected before fusion | 19,870,689 |
| fine unique voxels | 25,973,351 |
| coarse-support prefilter rejected | 607,642 |
| fused prefiltered raw | 25,365,709 |
| final-cap discarded | 0 |
| clean / post-fusion removed | 24,600,156 / 765,553 |

Post-fusion primary removal은 ground 351,143, statistical 254,338, poor-pose 114,193,
radius 43,779, far-untrusted 2,100점이다. 중복 reason을 포함한 ground 후보는 475,242점이며
measured/interpolated ground cell은 19,878/21,071, unknown cell은 0이었다. soft envelope는
평가됐지만 제거한 점은 0점이다.

### Full PLY baseline 비교

아래 값은 browser sample이 아니라 전체 PLY를 trajectory polyline에 투영해 계산했다.

| 지표 | 기존 conservative clean | temporal clean | 기존 대비 감소 |
| --- | ---: | ---: | ---: |
| total points | 38,661,643 | 24,600,156 | 36.37% |
| `h < -5 m` | 1,064,196 | 5,484 | 99.48% |
| `h < -10 m` | 296,234 | 0 | 100% |
| `cross > 15 m && h < -5 m` | 434,787 | 4,875 | 98.88% |
| `cross > 20 m && h < -5 m` | 213,929 | 0 | 100% |
| white + `cross > 15 m && h < -5 m` | 179,673 | 0 | 100% |
| four 29 m quantized peaks, single view | 1,576,347 | 0 | 100% |
| trajectory-supported 10 m segments | 37/37 | 37/37 | preserved |

새 fused raw에서 clean으로의 감소도 `h<-5 m` 96.17%, `h<-10 m` 100%, 외곽 하방
tail 92.09%, 엄격한 흰색 외곽 하방 tail 100%였다. 전체 제거율은 원인 판정에 사용하지
않았고 trusted anchor/core guard로 구조 보존을 판정했다.

### 정합 진단

120-frame conservative/temporal A/B에서 overlap은 0.437→0.455, 0–10 m
point-to-plane p50은 0.129→0.081 m(37.1% 개선), p95는 0.769→0.630 m(18.1% 개선)였다.
full temporal의 30개 인접 frame-pair 표본은 overlap 0.612, p50 0.054 m, p95 0.494 m다.
full pre-graph horizontal GPS-aided residual p50/p95도 기준 19.20/21.14 m에서
9.73/10.57 m로 감소했다. 반면 optical-vs-GPS heading p95는 6.52°에서 17.45°로
악화됐고 PnP 수는 640→416으로 줄었다. 공통 depth gate가 PnP의 불확실한 feature를
essential 경로로 넘긴 영향이며, 0.494 m p95와 함께 실제 extrinsic/time calibration이
필요하다는 신호다. GPS constraint residual과 이 등록 진단은 독립 absolute accuracy
증명이 아니다.

### 구조 guard와 운영 수치

| 지표 | 결과 |
| --- | ---: |
| trusted anchor retention | 99.944% |
| trusted core occupied-cell retention | 99.397% |
| high structure retention | 96.846% |
| trajectory segment retention | 37/37 |
| quality guard | passed |
| wall time | 1:03:18 |
| peak RSS | 18,277,552 KiB (17.43 GiB) |
| swap | 0 |
| output size | 1.7 GiB |

XY coverage 87.14%와 bright-isolated reduction 13.49%는 warning이다. 기존 raw extent가
고스트를 포함하므로 hard failure로 쓰지 않았고 trusted 구조 guard는 통과했다.

## 산출물

- `data/summary.json`, `postprocess_report.json`, `postprocess_stages.json`
- `data/runtime_benchmark.json`
- `data/pose_frame_quality.csv`, `depth_frame_quality.json`, `registration_quality.json`
- `data/cloud_provenance_sample.npz`, `prefilter_removed_sample.npz`
- `data/full_ab_comparison.json`
- `data/debug_stages/index.json`과 단계별 fixed-view sample
- `diagnostics/frame_reject_heatmap.png`, `problem_frame_montage.png`
- `diagnostics/full_ab_top_same_bounds.png`
- `diagnostics/full_ab_side_same_bounds.png`
- `diagnostics/full_ab_cross_section_same_bounds.png`

## 재현 명령

120-frame smoke:

```bash
conda run --no-capture-output -n depth-map-postprocess \
  python map_rgbd_gps.py DATASET_PATH \
  --output artifacts/ghost_removal_temporal_smoke_120_v1 \
  --pose-mode hybrid --cloud-preset balanced --max-frames 120 \
  --cloud-frame-stride 1 --pixel-stride 4 --voxel-size-m 0.10 \
  --per-frame-max-points 0 --max-points 3000000 --browser-max-points 300000 \
  --min-depth-m 0.7 --postprocess-preset road-map-temporal \
  --neighbor-backend scipy --ground-backend local \
  --write-debug-stages --debug-stage-max-points 300000 \
  --no-auto-postprocess-fallback
```

60-second full:

```bash
conda run --no-capture-output -n depth-map-postprocess \
  python map_rgbd_gps.py DATASET_PATH \
  --output artifacts/ultra_density_map_60sec_chunk_0000_temporal_v1 \
  --pose-mode hybrid --cloud-preset dense --cloud-frame-stride 1 \
  --pixel-stride 1 --voxel-size-m 0.03 --per-frame-max-points 0 \
  --max-points 40000000 --browser-max-points 1000000 --min-depth-m 0.7 \
  --chunk-duration-seconds 60 --chunk-index 0 \
  --postprocess-preset road-map-temporal \
  --neighbor-backend scipy --ground-backend local \
  --write-debug-stages --debug-stage-max-points 500000 \
  --no-auto-postprocess-fallback
```

## 테스트와 남은 한계

`.venv/bin/python -m pytest -q` 결과는 `109 passed`다. confidence 방향/누락/shape,
adaptive far spike/plane, temporal 세 상태, fine/coarse voxel 분리, 같은-frame dedup,
baseline/time 독립 view, dense ray curtain, supported wall, curved polyline/endpoint,
pose interpolation, cap metadata alignment와 기존 전체 회귀를 포함한다.

문서의 20-frame road-map 스모크도 최종 코드에서 별도 실행했다. SciPy/local 경로는
raw 4,369점, clean 3,895점, removed 474점으로 4.28초와 peak RSS 257 MiB를 기록했다.
Open3D 0.19.0 + ground `auto` 경로는 clean 3,769점, removed 600점으로 6.77초와
peak RSS 329 MiB를 기록했고, PDAL 2.10.2 비교 파이프라인도 정상 완료됐다. PDAL PLY에는
원본 index가 보장되지 않아 동일한 full local guard를 재현할 수 없으므로 비교 산출물로만
남기고 deterministic local 결과를 선택했다. Preview가 20개 입력 중 3개 cloud frame만
사용해 두 스모크의 XY coverage는 90% 아래였지만, 이는 파일/백엔드 경로 확인용이며
659-frame 대표 실행의 trusted guard는 통과했다.

남은 핵심 한계는 다음과 같다.

- mount roll/pitch/yaw, GNSS-camera lever arm, RGB-D/GNSS timestamp offset가 모두 0 가정이다.
- confidence map이 없어 confidence 기반 제거는 검증하지 못했다.
- full near-range p95 0.494 m와 heading p95 17.45°는 목표보다 크다. 측정된 extrinsic/time
  calibration 없이 aggressive ICP를 적용하면 정상 구조를 pose 오류에 맞춰 왜곡할 수 있어
  자동 SE(3) refinement는 넣지 않았다.
- 등록 수치는 내부 adjacent-frame 일관성이다. surveyed control point 또는 독립 LiDAR 기준이
  있어야 absolute accuracy를 주장할 수 있다.
- soft envelope 밖의 강한 multi-view 구조는 의도적으로 보존한다. 완전한 road-only crop이
  필요하면 별도 명시 모드를 사용해야 한다.
