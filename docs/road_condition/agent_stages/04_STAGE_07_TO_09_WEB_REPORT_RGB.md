# Stage 07 — 웹 지도와 3D 시뮬레이터 고도화

## 목표

현재 local ST heatmap을 지도와 3D evidence 중심 검수 도구로 확장한다.

## 사용자 질문

1. VWorld, Cesium, MapLibre 중 기본 지도 엔진은 무엇인가?
2. 기존 `viewer/`와 한 화면으로 합칠 것인가 별도 앱으로 유지할 것인가?
3. 모바일 현장 검수가 필요한가?
4. 작업자 수정·승인 기능이 필요한가?

## 권장 구조

- road-condition web은 별도 앱 유지
- VWorld/Cesium layer adapter 추가
- point cloud는 기존 viewer 또는 tile service로 연동
- defect evidence만 가볍게 로드
- 검수 수정은 Stage 10에서 API와 함께 추가

## UI 기능

- 노선/날짜/job 선택
- 상태등급 segment color
- defect type layer
- residual raster tile
- RGB 증거 이미지
- 종·횡단 profile
- 1×/2×/5× Z exaggeration
- confidence/quality filter
- low coverage layer
- before/after survey comparison

## 수용 기준

- 전체 PLY를 브라우저 메모리에 올리지 않는다.
- 60초 청크 여러 개를 tile로 점진 로드한다.
- defect click 결과와 API defect ID가 동일하다.
- 키보드로 주요 기능에 접근 가능하다.
- WebGL context loss 후 복구 또는 명확한 reload 안내가 있다.

---

# Stage 08 — 리포트 v2와 증거 패키지

## 사용자 질문

1. 최종 보고서 양식은 내부, 지자체, 발주처 중 무엇인가?
2. 회사 로고와 표지 정보가 필요한가?
3. PDF가 필수인가?
4. 결함별 현장 사진을 몇 장 넣을 것인가?
5. 보고서 언어는 한국어만인가?

## 산출물

```text
report/
  report.html
  report.pdf
  summary.csv
  segments.csv
  defects.csv
  figures/
  evidence/<defect_id>/
```

결함 evidence:

```text
rgb_original.jpg
rgb_overlay.jpg
residual_top.png
longitudinal_profile.svg
transverse_profile.svg
metadata.json
```

## 구현 규칙

- HTML을 기준 산출물로 유지
- PDF renderer는 별도 report image 또는 optional dependency
- report에 algorithm version, config hash, mapping commit, dataset ID 포함
- low confidence 결함은 별도 목록
- 미측정 항목은 `N/A`, 0으로 채우지 않음
- 공식 지표가 아니면 표지와 결론에 명시

## 수용 기준

- HTML/PDF 숫자가 JSON과 일치한다.
- 동일 결과에서 report 재생성이 가능하다.
- 한글 font 문제를 Docker build에서 검증한다.
- evidence 누락이 전체 report를 실패시키지 않고 표시된다.

---

# Stage 09 — RGB 크랙 AI

## 시작 전 질문

1. 보유한 크랙 라벨 형식은 무엇인가?
2. 클래스는 이진 크랙인가, 종/횡/망상/블록 분류인가?
3. 최소 검출 폭 요구는 몇 mm인가?
4. 원본 RGB 해상도와 노면 픽셀 해상도는?
5. 야간/우천/그림자 자료가 포함되는가?
6. GPU 학습/추론 환경은 무엇인가?

## 서비스 분리

```text
services/road_condition_crack_worker/
```

geometry API 이미지에 PyTorch를 넣지 않는다.

## 파이프라인

```text
RGB frame
  → road ROI mask
  → crack segmentation probability
  → depth validity mask
  → camera pose projection
  → ST/BEV probability accumulation
  → threshold + skeleton
  → length/width/area/type
  → defect contract
```

## 데이터 계약

```json
{
  "defect_type": "crack",
  "source": "rgb_ai",
  "metrics": {
    "length_m": 12.3,
    "mean_width_mm": 4.2,
    "max_width_mm": 11.0,
    "area_m2": 0.08,
    "orientation_deg": 87.0
  },
  "model": {
    "name": "...",
    "version": "...",
    "weights_sha256": "..."
  }
}
```

## 수용 기준

구현 전에 holdout metric을 고정한다.

- pixel precision/recall/F1
- instance recall
- length error
- false positive per 100m
- wet/shadow subset metric

작업자 수정 결과를 학습 데이터로 재사용할 때 원본 prediction과 수정 이력을 모두 보존한다.
