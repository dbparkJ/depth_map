# Stage 07 checkpoint — 행정 담당자용 실제 포인트 지도

## 기준

- Branch: `feat/road-condition-official-3d-evidence`
- Base checkpoint: `ed9b746`
- 기본 사용자는 지자체 공무원·발주/행정 담당자이며 분석가는 `전문가 상세`를 열어 기존
  파라미터와 단면을 계속 사용할 수 있다.
- 카메라 보정과 실측 truth가 없으므로 화면은 `확정 손상`이 아니라 `손상 후보`와
  `현장 확인 필요`를 표시한다.

## 구현

- 기본 보기 순서를 `VWorld 지도 + 실제 수집 포인트`로 변경했다.
- `RCEV v1` binary를 브라우저에서 strict decode하고 선택한 10 m tile 하나만 메모리에 둔다.
- actual RGB context point와 다음 고대비 mask를 같은 VWorld/Cesium
  `PointPrimitiveCollection`에 표시한다.
  - 빨강: 포트홀
  - 주황: 러팅
  - 보라: 범프/융기
- 포인트에는 ENU의 실제 Z를 WGS84 ellipsoid height로 변환해 사용한다. mask polygon은 해당
  결함에 연결된 실제 point 평균 높이를 사용하고, 지형과의 z-fighting을 막기 위한 시각적
  `+15 cm` lift를 적용한다. 이 lift는 측정값을 바꾸지 않는다.
- point에는 `disableDepthTestDistance`를 적용해 VWorld 지형 아래로 숨는 문제를 방지했다.
- 첫 tile은 전체 point 중심으로, 선택 후보 변경 시 해당 mask point 중심으로 camera fly-to한다.
- VWorld가 실패하면 같은 actual RGB/mask point를 Local ENU canvas에 그린다.
- 첫 화면을 다음 세 질문으로 바꿨다.
  1. 어디인가
  2. 무엇이 보였나
  3. 무엇을 해야 하나
- 깊이/높이는 mm 대신 기본 cm, 1 m 이상은 m로 표시한다.
- 2026 공식 단가 중 계산 가능한 부분합과 N/A 항목, 판정 연구 근거를 행정 검토 섹션에
  배치했다. 분석 실행·임계값·레이어·단면·전체 표는 `전문가 상세`로 이동했다.

## 검증

- Python web/route/evidence tests: `8 passed`
- JavaScript syntax: `node:22-alpine`에서 `app.js`, `viewer_core.js` 통과
- Node viewer contract: RCEV header/coordinate/RGB/class/index decode 통과
- Docker web image build: 통과
- Cloudflare public GET: page `200`, RCEV tile `200 / 16,792 bytes`, POST `405`
- 실제 `chunk_0000_multiview_v2 / tile-000000` headless Chrome:
  - VWorld 연결
  - 실제 point `1,394`
  - mask point `369`
  - API candidate `10`
  - 첫 화면에서 지도, 판독 상태, 세 질문, 선택 후보를 1440×900 안에 동시 표시
  - screenshot: `/tmp/road-condition-official-vworld-1440x900.png` (검증용 임시 파일, Git 제외)
- 같은 화면을 Cloudflare HTTPS에서도 VWorld basemap과 point/mask가 보이는 상태로 검증했다.

## 안전·성능 경계

- 전체 PLY를 브라우저로 보내지 않는다.
- 한 번에 RCEV tile 하나만 유지하며 context point는 최대 35,000점을 화면에 표본 표시한다.
- mask point는 표본에서 제외하지 않는다.
- VWorld browser key는 domain 제한이 필요한 공개 client key이며 코드에 커밋하지 않는다.

## 알려진 제한

- actual RGB point는 연속 사진이 아니라 depth 기반 3D sample이다.
- 현재 도로 ROI와 장착 보정이 없고 `chunk_0000`의 후보 밀도가 높아 알고리즘 결과는 다음
  26개 청크 균등 감사 전까지 자동 승인하지 않는다.
- 2026 화면 금액은 공식 항목의 계산 가능한 부분합이며 전체 공사비·견적이 아니다.
