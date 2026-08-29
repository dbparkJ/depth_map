# Stage 07 checkpoint — lightweight real RGB point evidence

## 기준과 결정

- Branch: `feat/road-condition-official-3d-evidence`
- Base checkpoint: `cffa1db`
- 분석 입력 계약은 계속 raw PLY + raw metadata다.
- 웹 시각 문맥에는 mapping이 이미 만든 공간 균등 `points_clean.bin`을 사용한다. clean browser
  sample이 없는 이전 bundle만 `points_raw.bin`으로 fallback한다.
- 전체 청크 점을 전송하지 않고 선택 route tile의 실제 도로 표면 점만 파생 evidence로 만든다.

## 구현

- 기존 `RGBD v1` browser point header/count/stride/size를 엄격하게 검증해 읽는다.
- 각 browser point를 trajectory에 투영하고 분석 `surface.npz`의 supported surface에서
  `±15 cm` band만 evidence로 유지한다.
- route tile core에 앞뒤 `1 m` 문맥을 포함한다.
- tile의 geometry candidate polygon과 point의 `(s,t)`를 대조해 defect class/index를 붙인다.
- `RCEV v1`은 ENU XYZ를 tile bbox 기준 uint16으로 양자화하고 actual RGB, defect class,
  tile-local defect index를 12 byte record에 저장한다.
- tile당 최대 60,000점이며 mask point와 context point를 결정적으로 나눠 보존한다.
- 결과는 route output의 `evidence/manifest.json`과 `evidence/tiles/*.rcev`에 기록한다.
- 산출물에는 source hash, tile별 hash, byte, bbox, 양자화 오차, mask point와 defect mapping을
  남긴다.

## 검증

- Python compileall: 통과
- RCEV encode/decode, RGB/mask 보존, malformed RGBD/RCEV 회귀: 통과
- 전체 road-condition pytest: 통과
- 실제 `chunk_0000_multiview_v2` 77 tile 변환:
  - source: clean browser spatial sample `1,000,000`점
  - evidence: `210,884`점, 총 `2,535,536 bytes`
  - tile point min/p50/max: `1,394 / 2,509 / 5,470`
  - 최대 tile payload: `65,704 bytes`
  - mask point: `40,181`
  - point가 직접 겹친 tile-local 후보: `268/634`
  - 최대 양자화 오차: `0.021 cm`
  - wall time: `14.56 s`
  - peak RSS: `287.6 MiB`

실데이터 RCEV와 manifest는 대용량 파생 데이터이므로 Git에 포함하지 않는다.

## 알려진 제한

- 작은 candidate polygon은 1,000,000점 spatial sample에도 직접 겹치는 point가 없을 수 있다.
  웹은 candidate polygon mask도 함께 표시해 이를 숨기지 않는다.
- clean sample은 보기용 문맥이며 분석 알고리즘 입력으로 사용하지 않는다.
- evidence surface band `±15 cm`는 시각화 범위이고 결함 판정 threshold가 아니다.
- RCEV는 이번 route tile PoC 계약이며 전 노선 LOD 표준을 확정한 것은 아니다.
