# Stage 05 checkpoint — tiles, halo, resume and chunk merge

## 기준과 결정

- Branch: `feat/road-condition-stage-05-chunk-stitching`
- Base SHA: `3b511c3f7b698b42eb78a8618557c8ac16c3320c`
- 기본 core tile: 10m
- 기본 halo: 앞뒤 3m
- report segment: 20m
- 저장: tile manifest + route GeoJSON/Parquet
- ownership: defect centroid가 속한 core
- resume: 필수, input/ROI/pose/config signature 변경 시 재실행
- 청크 trajectory/seam 오차: unknown; merge tolerance는 experimental

## 구현

- deterministic tile planner와 마지막 짧은 tile
- core+halo 분석, centroid core ownership
- 완료 tile skip, 독립 failed tile과 partial route manifest
- 동일 type/chainage/lateral/polygon/metric 기반 deterministic union merge
- `merged_from` 원본 provenance
- 10m core metric을 20m route segment로 집계
- tile별 defect/segment Parquet와 route GeoJSON/Parquet
- 명시적 chainage offset 기반 chunk-result merge; point cloud 재로딩 없음
- host CLI 2개와 API capabilities

## 검증 결과

- 정적 검사: Python compileall, 웹 `node --check`, Compose service/config 검사 통과
- 단위/회귀: `139 passed, 1 warning` (`137.57s`)
- 합성 경계: 20m/14,000점, 2 tile, 경계 pothole 1개만 소유, route Parquet 1행
- resume: 같은 signature의 완료 tile 2개 모두 skip
- merge: 입력 순서 독립, 인접 독립 결함 보존, 두 chunk offset seam은 1개로 병합
- partial: 완료 tile 산출물을 보존하면서 failed tile을 manifest에 기록
- 합성 경계 benchmark: `5.14s`, 최대 RSS `119,084KB`
- Docker: Compose build, 기동, API/웹 synthetic mixed smoke, route capabilities 통과
- 짧은 실데이터: 승인된 장치/ground truth 입력이 없어 미실행

## 알려진 제한

- 현재 CLI는 mapping chunk 하나를 메모리에 로드한 뒤 tile별 분석한다. 전체 노선을 한 번에
  로드하지는 않지만 PLY spatial streaming은 후속이다.
- Parquet는 `pyarrow` route extra가 필요하다.
- 실제 chunk offset과 merge tolerance는 승인된 seam ground truth로 보정하지 않았다.
- 실데이터를 실행하지 않았으므로 현재 threshold와 seam 병합 결과는 자동 승인 대상이 아니다.
