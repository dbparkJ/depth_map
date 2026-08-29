# Stage 03 follow-up checkpoint — 26개 청크 균등 감사와 높은 물체 경계 guard

## 기준과 중단 조건

- Branch: `feat/road-condition-official-3d-evidence`
- Base checkpoint: `50a420f`
- 입력: `chunk_0000~0025`의 `cloud_raw_enu.ply`와 raw metadata
- 일반 주행 청크: trajectory 25%/50%/75% 지점의 10 m core + 3 m halo
- 40 m 미만 짧은 청크: 중앙 10 m 한 곳, 10 m 미만 정차성 청크: 전체
- 청크별 timeout 15분, peak RSS 10 GiB 초과 시 중단
- 실측 truth와 도로 ROI가 없으므로 후보 수 감소만으로 정확도 개선을 주장하지 않는다.

## 감사 도구

`scripts/road_condition_multichunk_audit.py`는 청크를 하나씩 별도 spawn process에서 읽는다.
작업이 끝날 때마다 JSON/CSV를 원자적으로 갱신하므로 중단 후 완료 청크를 재사용할 수 있다.

각 타일에서 다음을 같은 schema로 기록한다.

- raw/분석/multiview point 수
- supported/usable coverage
- 높음·낮음 plausibility 제외 셀과 제외율
- residual p01/p05/p50/p95/p99
- 포트홀·러팅·범프 수와 최대 깊이/높이
- 도로 통로 바깥 0.75 m edge band 후보 비율
- FHWA/KICT 비교용 25 mm·0.02㎡ 포트홀 설정의 후보 수 차이
- wall time과 청크 프로세스 peak RSS

상세 산출물은 Git 제외 경로에 있다.

- baseline: `artifacts/.../road_condition/multichunk_audit_v1.json/.csv`
- 개선 후: `artifacts/.../road_condition/multichunk_audit_v2_high_boundary_guard.json/.csv`

## baseline 결과

- raw point: `696,129,845`
- trajectory 합계: `11,544.63 m`
- 완료 청크: `24/26`
- 완료 타일: `68`, 실패 control 타일: `2`
- controller wall: `455.5 s`
- 최대 peak RSS: `3,467.0 MiB`
- 정차성 실패:
  - `chunk_0002`: trajectory `0.820 m`
  - `chunk_0003`: trajectory `0.535 m`
  - 둘 다 `road surface has too few supported grid cells`로 기록했으며 주행 결과에서 숨기지 않았다.

68개 완료 타일 분포:

| 항목 | min | p25 | p50 | p75 | max |
|---|---:|---:|---:|---:|---:|
| supported coverage | 35.8% | 89.0% | 95.6% | 97.7% | 99.7% |
| usable coverage | 5.6% | 80.8% | 93.3% | 96.1% | 99.7% |
| supported 중 plausibility 제외 | 0.0% | 0.6% | 2.5% | 6.6% | 84.2% |
| 후보 수/10m | 1 | 14 | 18 | 21 | 47 |
| edge 후보 비율 | 0.0% | 18.0% | 25.0% | 33.3% | 100% |

baseline 후보 합계는 포트홀 `444`, 러팅 `110`, 범프 `650`, 전체 `1,204`다. 68개 타일 중
`47`개에서 최대 범프 높이가 `24 cm` 이상으로 `+25 cm` plausibility 상한 바로 아래에
몰렸다. 이 47개에는 모두 상한을 넘어 이미 제외된 높은 셀이 함께 있었다. 반대로 높은 제외
셀이 없는 4개 타일의 최대 범프 중앙값은 약 `4.9 cm`였다.

## 최소 알고리즘 변경

반복 패턴의 원인은 높은 비노면 물체를 `+25 cm`에서 자른 뒤, 바로 붙어 남은 양(+) 잔차
컴포넌트를 범프로 다시 분할하는 경계 오염이었다. 다음 guard만 추가했다.

1. 기존 `plausibility_excluded_high_mask`는 그대로 유지한다.
2. 범프 threshold를 넘는 연결 컴포넌트 중 excluded-high 셀에 8방향으로 직접 닿은 컴포넌트를
   통째로 범프 detector에서 제외한다.
3. 독립적으로 닫힌 양(+) 형상은 유지한다.
4. 깊은 포트홀을 잘못 지울 가능성을 피하려고 excluded-low에는 같은 억제를 적용하지 않는다.
5. 제거 컴포넌트와 cell 수를 `summary.quality`에 기록한다.

전체 포인트를 버리거나 mapping 결과를 수정하지 않으며, 분석 surface/coverage와 포트홀·러팅
계약도 바꾸지 않는다. algorithm version은 `road-condition-geometry-mvp-4`, method basis는
`road-geometry-evidence-v1 / 1.1.0`이다. route resume signature에도 algorithm version을 넣어
코드 변경 후 이전 타일을 잘못 skip하지 않게 했다.

## 동일 68개 타일 재검증

- 완료/실패 위치: baseline과 동일
- controller wall: `331.5 s` (두 번째 실행의 OS file cache 영향이 있으므로 성능 개선 주장이 아님)
- 최대 peak RSS: `3,466.9 MiB`
- supported/usable/excluded coverage: 타일별 완전 동일
- 포트홀: `444 → 444`
- 러팅: `110 → 110`
- 범프: `650 → 445` (`205` 감소)
- 전체 후보: `1,204 → 999`
- 최대 범프 24 cm 이상 타일: `47 → 12`
- guard가 확인한 raw threshold component: `2,655`, candidate cell `69,158`
- 실제 최종 범프 수가 바뀐 타일: `54/68`

guard raw component 수는 기존 면적·morphology 검사를 통과하기 전 수치이므로 최종 감소 205건과
동일하지 않다. 이 결과는 높은 물체 경계 오염 제거를 지지하지만, 남은 999건의 참/거짓은
판단하지 않는다.

문헌 비교용 25 mm·0.02㎡ 설정은 타일별 후보 수 차이가 `-7~+12`, 중앙값 `0`, 전체 `+50`이었다.
낮은 threshold가 인접 함몰을 합쳐 오히려 후보 개수가 줄어드는 타일도 있었다. truth 없이
기본값을 변경하지 않은 이유다.

## 실제 chunk_0000 새 route/evidence

기존 결과를 보존하고 다음 새 경로에 77개 타일을 생성했다.

`artifacts/2026-08-19_09-51-22_hardlinked/road_condition/chunk_0000_boundary_guard_v4`

- route: `77/77`, 실패 0, `537` route 후보, wall `204.81 s`, peak RSS `3,496,072 KiB`
- 기존 `chunk_0000_multiview_v2`: `589` route 후보
- 새 경량 evidence: `210,884`점, `2,535,536 bytes`, `77/77`
- 첫 tile: 후보 `10 → 7`, 범프 `3 → 0`, guard 제거 raw component `22`
- Cloudflare public manifest/evidence: HTTP `200`
- VWorld에서 새 첫 tile actual point/mask를 headless Chrome으로 확인

## 검증

- 새 audit window/summary/guard 합성 tests 통과
- route algorithm-version resume invalidation test 통과
- 전체 road-condition tests: `66 passed`
- Python compileall: 통과

## 알려진 제한과 다음 행동

- 도로 ROI 부재 때문에 전체 후보의 약 25%가 corridor edge band에 있다. ROI 없이 이를 일괄
  삭제하지 않는다.
- usable coverage 50% 미만 타일은 `chunk_0004/q50`, `chunk_0010/q25`, `chunk_0025/central`이며
  자동 판정 대상이 아니다.
- `chunk_0010/q25`는 supported의 84.2%가 plausibility에서 제외되어 재수집/보정 확인이 우선이다.
- 남은 범프·포트홀·러팅은 실측 truth와 RGB evidence로 검수해야 한다.
- 문헌 임계값은 정의·비교 근거이며 현재 센서의 정확도 승인값이 아니다.
