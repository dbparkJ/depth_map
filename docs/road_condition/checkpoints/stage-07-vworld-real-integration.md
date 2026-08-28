# Stage 07 follow-up checkpoint — VWorld real integration

## 기준과 진단

- Branch: `feat/road-condition-real-chunk-0000-hardening`
- Base checkpoint: `61e8ef1`
- 기존 road-condition web의 `VWorld adapter`는 항상 `ready=false`를 반환하는 placeholder였다.
- 기존 `.env`에는 `VWORLD_API_KEY`가 있지만 Compose web 서비스로 전달되지 않았고
  `VWORLD_DOMAIN`도 설정되지 않았다.
- VWorld 공식 SDK endpoint는 HTTP 200을 반환했다.
- 실제 키를 로그에 출력하지 않고 인증 응답만 검사했다.
  - `domain=127.0.0.1`: `vworldIsValid='true'`
  - `domain=localhost`: `vworldIsValid='true'`
  - domain 생략: `vworldIsValid='false'`, 등록 URI 불일치

## 구현

- Nginx 시작 시 browser runtime config에 VWorld key/domain을 주입한다.
- VWorld의 `document.write` 기반 loader 특성에 맞춰 HTML parsing 중 SDK를 로드한다.
- `defects.enu.geojson`의 원점을 사용해 ENU 좌표를 WGS84/EPSG:4326으로 변환한다.
- VWorld WebGL viewer의 독립 Cesium data source에 결함 polygon을 올린다.
- 키, domain, ENU origin, SDK, WebGL 중 하나라도 실패하면 Local ENU canvas로 fallback하고
  화면에 실패 이유를 남긴다.
- `?view=map&adapter=vworld` query로 지도 화면을 바로 열 수 있다.
- 품질 요약에 원시 지지율과 plausibility 제외 셀 수·면적·방향을 표시한다.

## 검증

- JavaScript syntax check: 통과 (`node:22-alpine`)
- viewer core Node contract: 통과
- Python web contract 및 pipeline tests: 통과
- Compose config/build/up: 통과
- API health: `ok`
- runtime config: key와 `127.0.0.1` domain 주입 확인
- 실제 headless Chrome WebGL 검증:
  - `vworldIsValid=true`
  - VWorld viewer 생성
  - `chunk_0000` 실제 후보 56개를 data source에 표시
  - 화면 상태: `VWorld 연결됨 · EPSG:4326 변환 · 후보 56개 표시`

## 보안과 제한

- VWorld browser key는 클라이언트에 전달되므로 비밀 서버 키로 취급할 수 없다. VWorld 콘솔의
  domain 제한을 반드시 유지한다.
- 실제 후보 위치는 mapping ENU origin과 GNSS/카메라 보정 정확도에 종속된다.
- domain 변경 시 `VWORLD_DOMAIN`과 VWorld 등록 URI를 함께 갱신해야 한다.
- Cesium 독립 adapter는 여전히 runtime token 미구현 placeholder다.
