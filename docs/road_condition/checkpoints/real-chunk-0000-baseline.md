# Real chunk 0000 checkpoint — runtime cleanup and baseline

## 기준

- Branch: `feat/road-condition-real-chunk-0000-hardening`
- Base SHA: `109679aae70e5dad1e3392772d4977ff549d6888`
- Mapping bundle:
  `artifacts/2026-08-19_09-51-22_hardlinked/temporal_60sec/chunk_0000`
- Preserved API job: `631cb61ed4c24257b98bff56e27d6b29`
- Preserved route result:
  `artifacts/2026-08-19_09-51-22_hardlinked/road_condition/chunk_0000`

## Runtime 정리

- 합성 demo job 17개 삭제
- 테스트용 `bundle` mapping job 2개 삭제
- 잘못된 `road_condition/chunk_0000` 입력 실패 job 1개 삭제
- 실제 완료 job과 중복된 stale `running` job 1개 삭제
- `/data/jobs` 사용량: 53 MB → 4.9 MB
- mapping bundle, raw PLY/NPZ와 route 결과는 삭제하지 않음

API가 실행 중인 job 삭제를 거부하므로, 실제 process가 없고 한 시간 이상 갱신되지 않은 stale
job 디렉터리 하나만 exact path로 삭제했다. 삭제한 runtime job 결과는 복구할 수 없다.

## 단일 job 기준선

- original point count: 39,362,186
- analyzed point count: 2,000,000
- valid coverage: 3.8993%
- candidates: 56
- calibration: `unknown`, `manual_review_required=true`
- ROI: 없음, trajectory corridor fallback

전체 경로에서 2백만 점을 표본 추출한 단일 job 결과는 coverage가 너무 낮아 알고리즘 개선
비교 기준으로 사용하지 않는다.

## 10 m route tile 기준선

- state: completed
- tiles: 77/77 completed, 0 failed
- tile valid coverage: min 53.93%, median 97.80%, max 99.94%
- route candidates: 644
- tile result candidates before aggregate ownership: 690
- candidate types before aggregate: pothole 247, rutting 83, bump 360
- pothole maximum depth: 2.44 m
- bump maximum height: 1.99 m
- lateral offset 절댓값 2.5 m 초과 후보: 198/690

타일 분석은 global sampling coverage 문제를 해결했지만 비도로/비현실 표면 후보가 detector에
들어가 false positive가 폭증했다. 다음 알고리즘 체크포인트는 raw point를 지면 필터로 미리
삭제하지 않고, fitted surface cell의 plausible residual gate와 제외 회계를 추가한다.

## 중단 및 수용 기준

- 기존 raw/clean/removed mapping 계약을 변경하지 않는다.
- 포트홀 음수 residual을 high-point 제거 명목으로 삭제하지 않는다.
- 합성 포트홀/러팅/범프 회귀를 유지한다.
- 비현실 높이 후보는 defect가 아니라 excluded surface cell로 회계한다.
- `chunk_0000` route tile의 원시 지지 coverage가 기존보다 낮아지면 중단한다. 분석 가능
  coverage는 제외 셀 수·면적과 정확히 대응하는 경우에만 낮아질 수 있다.
- ground truth가 없으므로 후보 수 감소를 정확도 향상 또는 승인으로 간주하지 않는다.
