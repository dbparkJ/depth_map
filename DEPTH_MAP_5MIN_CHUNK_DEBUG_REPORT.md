# high_density_map 5분 청크 구현·검증 보고

## 완료 범위

- Python 3.11 전용 Conda 환경에 Open3D 0.19와 PDAL 2.10 설치
- timestamp 반개구간 기반 `--chunk-duration-seconds`/`--chunk-index`
- 청크 간 공통 dataset-first ENU 원점
- raw/clean/removed 전체 출력과 8단계 capped PLY/BIN/JSON/PNG 디버그 기록
- 기존 `high_density_map` 코스의 첫 300초(3,294프레임) 대표 실행
- 작은 20프레임 조정 후 보존 raw에서 후처리만 1회 재실행

## 대표 결과

최종 raw/clean/removed는 각각 20,000,000 / 18,429,603 / 1,570,397점이며 제거율은
7.851985%다. 581,659개의 local-surface 아래 후보는 clean에서 0개가 됐다. 199개의
10 m trajectory 구간은 모두 유지됐고 top view에서 1.99 km 노선 형상이 연속된 지도
형태로 보인다.

다만 XY occupied-cell coverage는 81.0322%로 90% guard를 통과하지 못했다. Corridor
coverage 92.4317%, high-structure retention 95.5671%, X/Y bbox 유지율
99.6720%/99.8064%는 통과했다. 결과가 완전 통과인 것처럼 표시하지 않고 이 제한을 다음
단계의 선행조건으로 둔다.

## 확인 경로

- 전체 기록: `artifacts/high_density_map_5min_chunk_0000/DEBUG_LOG.md`
- 결과: `artifacts/high_density_map_5min_chunk_0000/data/`
- 최종 단계 진단: `artifacts/high_density_map_5min_chunk_0000/data/debug_stages/`
- 최종 PNG: `artifacts/high_density_map_5min_chunk_0000/diagnostics/`
- 1차 설정 보존본: `data/postprocess_attempts/baseline_r018/` 및
  `diagnostics/postprocess_attempts/baseline_r018/`

## 다음 계획

1. 20프레임 및 별도 60초 holdout에서 radius 최소 이웃/반경과 ground-first 순서를
   비교하고, XY coverage 90% 이상·도로 하부 감소 60% 이상·high structure 85% 이상을
   동시에 만족하는 map-preserve 설정을 고정한다.
2. 품질 metric에서 실제 노이즈로만 채워진 raw 단독 cell과 도로/구조 지지 cell을 분리해
   coverage guard의 의미를 보강하되, 임계값을 결과에 맞춰 사후 완화하지 않는다.
3. 위 설정이 통과한 뒤에만 chunk 1(300–600초)을 별도 output으로 한 번 실행한다.
4. chunk 0/1의 공통 ENU 원점, timestamp 경계 중복 없음, seam과 색상 차이를 검사한다.
5. 전체 배열 결합 대신 streaming LAZ 또는 타일 스트리밍을 우선 구현한다.

전체 데이터 자동 반복과 chunk 1 실행은 이번 세션에서 수행하지 않는다.
