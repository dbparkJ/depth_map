# Stage 12 checkpoint — RoadInventory-MMS integration contract

## 기준과 결정

- Branch: `feat/road-condition-stage-12-rimms-contract`
- Base SHA: `12ad4b3f0ee97a638d5a19685ad11b3a5ba10da2`
- 인증: 미제공, ingress 기본 비활성화
- object storage: 미제공, URI 형식만 검증하고 fetch하지 않음
- 전달 방식: polling 전용, callback URL fail-closed
- 업무 ID schema: 외부 ID를 opaque safe string으로만 보존
- source of truth: 조사/노선은 RIMMS, raw prediction은 분석 서비스, 검수 동기화는 N/A

## 구현

- versioned request/result contract와 URI-only Pydantic schema
- file/userinfo/query/fragment URI 거부, payload field 크기 제한
- 필수 `Idempotency-Key`, 원문 미저장 SHA-256 index
- 같은 key/body replay와 key/body/external-ID 충돌 HTTP 409
- atomic JSON 상태 저장과 polling list/detail endpoint
- callback 비활성, object fetch/외부 네트워크 실행 없음
- 기본 503 fail-closed feature gate와 capability/source-of-truth 공시

## 검증 결과

- Python compileall, 컨테이너 Node syntax, Compose YAML/config, `git diff --check` 통과
- 전체 단위/회귀 `50 passed` (`47.60s`), RIMMS/API 집중 검증 `6 passed`
- 기본 feature gate HTTP 503과 capability `ingress_enabled=false` 확인
- 평가 플래그에서 URI-only 작업 생성, polling, list, 원문 key 미저장 확인
- 같은 key/body replay는 같은 request SHA를 반환하고 새 작업을 만들지 않음
- key 재사용/외부 ID 충돌 HTTP 409, callback·URI·양쪽 version mismatch HTTP 422
- API image `66bcc0dcea57`, web image `84e9783e12aa` non-root build 통과
- Compose 평가 모드에서 web reverse proxy 생성→replay→polling과 callback 422 확인
- Compose 기본 모드 재기동 후 동일 ingress가 HTTP 503임을 확인
- 일반 synthetic mixed smoke 통과, API 로그에 traceback/5xx 없음

## 알려진 제한

- 운영 연동이 아니라 ingress contract와 persistence만 구현됐다.
- 인증, 권한, object storage SDK/credential, URI download/upload가 없다.
- callback 서명, retry/backoff, dead-letter가 없으며 callback 입력 자체를 거부한다.
- accepted contract는 분석 작업을 시작하지 않고 connector 구성을 기다린다.
- reviewed defect의 최종 소유권과 양방향 sync 정책은 합의되지 않았다.
