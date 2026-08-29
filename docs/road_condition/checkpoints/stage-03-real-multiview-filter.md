# Stage 03 follow-up checkpoint — real multi-view transient filter

## 기준과 결정

- Branch: `feat/road-condition-real-chunk-0000-hardening`
- Base checkpoint: `be240c6`
- 입력: `temporal_60sec/chunk_0000/data/cloud_raw_enu.ply`와
  `cloud_raw_metadata.npz`
- 고정 폭 corridor만으로는 차량·벽·보도 같은 비도로 물체를 충분히 구분할 수 없어,
  mapping 단계가 기록한 독립 관측 시점 수를 1차 transient evidence로 사용한다.
- 실측 truth와 road ROI가 없으므로 후보는 자동 승인하지 않고 수동 검수 대상으로 유지한다.

## 구현

- `independent_view_count >= 2`인 점만 표면 격자와 결함 검출에 전달한다.
- 해당 metadata가 없는 이전 mapping bundle은 기존 동작을 유지하고, 결과 제한사항에
  필터 미적용을 기록한다.
- 결과 품질 정보에 입력·유지·제외 점 수, 유지율, 최소 독립 시점 수를 기록한다.
- route CLI와 API는 필요한 metadata 배열만 선택적으로 읽어 대용량 NPZ 로딩 비용을 줄인다.
- 웹에서 다중시점 제외 수를 품질 판단 근거로 보여주고, `route` query parameter로 실제 route
  결과를 바로 열 수 있게 한다.

## 검증

- Python compileall: 통과
- JavaScript syntax check: 통과
- 도로 상태 전체 pytest: `58 passed`
- 합성 회귀: 단일 시점 `+18 cm` 일시 물체가 제외되고 bump 후보가 생성되지 않음을 확인
- metadata가 없는 합성 입력의 하위 호환 동작 확인
- 실제 `chunk_0000` 전체 route 실행:
  - 완료 tile: `77/77`, 실패 `0`
  - 후보: `610 -> 589` (`pothole 218`, `rutting 87`, `bump 284`)
  - halo 포함 분석 점: `62,258,263 -> 53,748,327`, 제외 `8,509,936` (`13.67%`)
  - usable coverage min/p50/max: `59.31% / 96.53% / 99.71%`
  - plausibility 제외 셀: low `27`, high `7,970`

실행 결과는 Git에 포함하지 않고 다음 경로에 보존한다.

`artifacts/2026-08-19_09-51-22_hardlinked/road_condition/chunk_0000_multiview_v2`

## 해석과 알려진 제한

- 후보 수가 `21`개 감소했지만 정확도 향상이나 자동 승인 근거로 해석하지 않는다.
- 최대 포트홀 깊이는 `28.62 cm -> 22.96 cm`로 낮아졌지만, 최대 러팅 깊이는
  `10.02 cm -> 15.38 cm`로 높아졌다. 차선 ROI와 실측 truth 없이 임계값을 추가 조정하지 않는다.
- 반복 관측된 차량이나 고정 구조물은 독립 시점 수만으로 제거되지 않을 수 있다.
- 다음 정확도 개선 우선순위는 RGB/trajectory 기반 실제 도로 ROI와 현장 실측 대조다.
