# Stage 13 follow-up checkpoint — Cloudflare read-only external tunnel

## 기준과 결정

- Branch: `feat/road-condition-real-chunk-0000-hardening`
- Base checkpoint: `b56ca43`
- 로컬 확인 주소: `http://127.0.0.1:18080`
- Cloudflare 계정 인증서와 tunnel token이 없는 환경이므로 즉시 확인용 Quick Tunnel을 사용한다.
- 인증 없는 공개 주소가 분석 실행·삭제·검수 변경 기능을 노출하지 않도록 별도 read-only
  gateway를 둔다.

## 구현

- 기존 `compose.road-condition.yml`의 두 서비스 계약은 바꾸지 않는다.
- 별도 `compose.road-condition-public.yml`이 기존 내부 Docker network에 연결된다.
- public gateway는 모든 경로에서 `GET/HEAD`만 허용하고 그 외 method는 `405`로 차단한다.
- gateway host port는 `127.0.0.1:18082`에만 bind하고 Cloudflare Tunnel만 외부 ingress로 쓴다.
- Quick Tunnel 시작 스크립트가 기본 서비스와 공개 gateway를 기동하고 외부 URL을 출력한다.
- 검색 엔진 수집 방지 header와 기본 보안 header를 적용한다.
- HTTPS 공개 화면에서는 VWorld SDK의 domain에 현재 browser origin을 전달한다. 이 값으로 VWorld의
  하위 SDK와 지도 tile도 HTTPS로 로드되어 mixed-content 차단을 피한다.

## 검증

- 기본 Compose 서비스 집합: `road-condition-api`, `road-condition-web` 유지
- 기본/public Compose config와 Nginx healthcheck: 통과
- local public gateway health: HTTP `200`
- Cloudflare HTTPS root/health: HTTP `200`
- local/Cloudflare gateway의 job 생성 POST: HTTP `405`
- Cloudflare HTTPS route manifest: `completed`, tile `77/77`, 후보 `589`
- 외부 headless Chrome WebGL 검증:
  - VWorld viewer와 Cesium canvas 생성
  - ENU 결함을 EPSG:4326으로 변환
  - 첫 route tile 후보 `10`개를 VWorld data source에 표시
- Python compileall, JavaScript syntax, 도로 상태 pytest: 통과

## 보안과 제한

- Quick Tunnel URL을 아는 사람은 분석 결과를 조회할 수 있다. 민감 데이터 공개 용도가 아니다.
- Quick Tunnel은 임시 주소이며 container가 다시 만들어지면 주소가 바뀔 수 있다.
- 고정 hostname, 사용자 로그인, 접근 로그 정책은 Cloudflare 계정에서 named tunnel과 Access를
  구성해야 한다.
- VWorld 요청에는 접속 중인 실제 origin을 전달한다. named tunnel로 hostname을 고정할 때는 해당
  hostname이 VWorld API key의 허용 URI에도 등록되어 있는지 다시 확인한다.
