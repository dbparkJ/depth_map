# Stage 03 follow-up checkpoint — real surface plausibility gate

## 기준과 결정

- Branch: `feat/road-condition-real-chunk-0000-hardening`
- Base checkpoint: `34d5b29`
- 입력: `temporal_60sec/chunk_0000/data/cloud_raw_enu.ply`
- 실측 truth와 road ROI가 없으므로 임계값은 승인 기준이 아닌 실험값으로 취급한다.
- 사용자 요청에 따라 raw point나 mapping bundle을 삭제하지 않고, fitted surface 이후의
  비현실 셀만 detector 입력에서 제외한다.

## 구현

- robust reference surface 대비 잔차가 `-0.30 m` 미만 또는 `+0.25 m` 초과인 셀을 제외한다.
- 원시 지지 mask, 분석 가능 mask, 낮은/높은 제외 mask를 분리한다.
- 결과 JSON에 원시 지지율, 분석 가능 지지율, 제외 셀 수·방향·면적을 기록한다.
- `surface.npz`에 세 mask를 함께 저장해 제외 근거를 보존한다.
- ROI가 있을 때는 ROI 밖 셀이 제외 통계에 섞이지 않도록 모든 mask에 동일하게 적용한다.

## 검증

- compileall: 통과
- 도로 상태 전체 pytest: `56 passed`
- 새 합성 회귀: 평탄 도로에 `+65 cm/-65 cm` 패치를 주입해 상·하 제외 셀로 집계되고
  결함 최대값으로 전달되지 않음을 확인
- 짧은 실데이터 smoke: full route 실행의 첫 4개 10 m tile 완료
  - raw supported coverage: `53.93%, 85.63%, 91.14%, 92.10%`
  - usable coverage: `49.75%, 84.05%, 89.74%, 90.62%`
  - excluded cells: `346, 177, 157, 166`
  - 첫 4 tile 최대 포트홀 깊이 `28.6 cm`, 최대 범프 높이 `24.8 cm`

전체 77 tile 실행은 기존 결과를 덮어쓰지 않고 다음 경로에서 계속한다.

`artifacts/2026-08-19_09-51-22_hardlinked/road_condition/chunk_0000_plausibility_v1`

## 알려진 제한

- `-30 cm/+25 cm`는 측량 truth로 보정되지 않은 내부 실험 게이트다.
- 지지율이 매우 낮은 tile은 제외 후 내부 최소 coverage 50%를 밑돌 수 있다.
- ROI가 없으므로 연석 안쪽 실제 도로 폭을 자동 판별하지 못한다.
- 후보 수 감소는 정확도 개선이나 자동 승인으로 해석하지 않는다.
