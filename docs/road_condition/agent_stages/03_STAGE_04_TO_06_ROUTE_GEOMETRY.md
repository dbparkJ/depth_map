# Stage 04 — 도로 ROI, 차로 경계, 도로 좌표계

## 목표

차량 궤적 주변 전체를 도로로 간주하지 않고 실제 포장면과 차로를 구분한다.

## 사용자 질문

1. 노선별 차로 수와 차로 폭을 알고 있는가?
2. 도로 중심선 SHP/GeoJSON이 있는가?
3. 차선/연석을 RGB AI로 찾을 것인가, 작업자가 ROI를 그릴 것인가?
4. 갓길과 보도는 분석 대상인가?
5. 교차로와 회차 구간을 어떻게 구분할 것인가?

## 권장 기본값

```text
1차: 수동 road_roi.geojson + trajectory fallback
2차: RGB lane/curb segmentation
갓길: 별도 surface class
교차로: separate zone
```

## 파일 계약

```text
road_roi.geojson
  road polygon
  lane polygon
  shoulder polygon
  exclusion polygon
```

properties:

```json
{
  "zone_id": "lane-L1-0001",
  "zone_type": "lane",
  "lane_id": "L1",
  "chainage_start_m": 0,
  "chainage_end_m": 120,
  "source": "manual|centerline_buffer|rgb_ai",
  "confidence": 1.0
}
```

## 구현 작업

- ST grid cell에 zone/lane ID 부여
- exclusion polygon 우선 적용
- 각 defect에 lane_id와 road_zone 추가
- 차로별 segment metric 생성
- ROI coverage와 unknown area 기록
- ROI가 없을 때 기존 corridor fallback 유지
- UI에서 ROI와 unknown area 표시

## 수용 기준

- 보도·수목·차량 표면이 도로면 fitting에 들어오지 않는다.
- lane ID가 결함과 segment에 보존된다.
- ROI 경계에서 reference surface가 불연속이 되지 않는다.
- ROI가 없는 기존 API 요청은 계속 동작한다.

## 완료 후 질문

```text
도로 ROI 결과를 승인합니까?
A) 수동 ROI workflow 승인
B) 차선 AI를 바로 추가
C) 도로 중심선 buffer만 사용
D) 갓길/보도를 분석 대상에 포함
```

---

# Stage 05 — 타일, 청크 halo, 전체 노선 병합

## 목표

60초 청크와 10m 분석 타일 경계에서 결함이 잘리거나 중복되는 문제를 해결한다.

## 사용자 질문

1. 최종 노선 결과의 기본 tile 길이는 10m, 20m 중 무엇인가?
2. 청크 간 trajectory 오차가 어느 정도인가?
3. 전체 노선 결과를 하나의 파일로 합칠지 dataset manifest로 유지할지?
4. 중단 후 재개와 부분 재처리가 필요한가?

## 권장 기본값

```text
analysis core tile: 10m
halo: 앞뒤 3m
segment report: 20m
storage: tile manifest + partitioned Parquet/GeoJSON
resume: 필수
```

## 핵심 규칙

```text
입력 범위 = core + halo
표면 fitting = core + halo
결함 검출 = core + halo
최종 소유권 = defect centroid가 속한 core
인접 tile merge = geometry overlap + chainage + type
```

## 병합 조건 초기안

- 동일 `defect_type`
- chainage 차이 허용
- polygon IoU 또는 최소 거리
- 심각도와 metric 차이가 허용 범위 내
- source frame/time overlap
- merge 후 원본 defect ID를 `merged_from`에 보존

## 산출물

```text
route_manifest.json
route_segments.parquet
route_defects.parquet
route_defects.geojson
tiles/<tile_id>/...
```

## 수용 기준

- 경계에 배치한 합성 포트홀은 하나만 결과에 남는다.
- tile 실행 순서를 바꿔도 병합 결과가 같다.
- 완료 tile은 재실행하지 않는다.
- 실패 tile이 전체 dataset을 무효화하지 않는다.
- 전체 point cloud를 한 번에 RAM에 올리지 않는다.

## 테스트

- 포트홀이 tile 경계 중앙
- 포트홀이 청크 경계 중앙
- 동일 결함의 좌표가 약간 어긋난 두 청크
- 서로 가까운 독립 포트홀 두 개
- 마지막 짧은 청크

## 완료 후 질문

```text
청크 병합 정책을 승인합니까?
A) centroid ownership + 3m halo 승인
B) halo 변경
C) 전체 merged PLY도 생성
D) manifest만 만들고 defect merge 보류
```

---

# Stage 06 — 형상 검출 고도화

## 6.1 단차·맨홀

구현:

- 국소 gradient와 step edge
- 원형/사각 구조 후보
- RGB object detector와 선택적 결합
- 접근 방향별 높이 차이

metric:

```text
step_height_m
approach_slope_percent
edge_length_m
```

## 6.2 횡단경사

- reference surface의 `dz/dt`
- lane별 median/percentile
- 좌우 기울기와 crown 위치
- 교차로 제외

## 6.3 종단경사·roughness

- wheel path profile
- pose drift 저주파와 노면 고주파 분리
- 현재 `roughness_proxy` 유지
- 표준 장비와 보정 후에만 `estimated_iri` 추가

## 6.4 물고임 screening

- DEM depression fill
- 잠재 저류 깊이/면적/체적
- drain 위치가 없으면 drainage capacity 계산 금지

## 사용자 질문

1. 다음 우선순위는 맨홀 단차, 횡단경사, 물고임 중 무엇인가?
2. 맨홀 위치/자산 DB가 있는가?
3. 실제 배수구 위치가 있는가?
4. roughness 비교 장비 자료가 있는가?

## 수용 기준

각 detector는 독립 feature flag와 독립 테스트를 가진다. 하나가 실패해도 기존 포트홀 결과가
바뀌지 않아야 한다.
