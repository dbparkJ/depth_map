# Road-condition operations readiness gate

기준일: 2026-08-27

이 문서는 Stage 13 분산 실행·운영 보안의 전환 판단 기록이다. 현재 시스템을 운영 준비 완료로
승인하는 문서가 아니다.

## 1. 분산 queue 전환 조건

| 전환 조건 | 현재 증거 | 판정 |
|---|---|---|
| 단일 작업이 운영 SLA 초과 | 운영 SLA와 대표 노선 wall time 미제공 | N/A |
| 여러 장치 동시 업로드 | 장치 수·동시성 요구 미제공 | N/A |
| API 재시작 시 running 복구 필요 | 운영 복구 목표 미승인 | N/A |
| 여러 worker host 필요 | host/GPU topology 미제공 | N/A |
| 조직별 권한 필요 | tenant/조직 모델 미제공 | N/A |

전환 조건이 하나도 증명되지 않았으므로 파일 queue를 PostgreSQL/Redis/RabbitMQ로 교체하지
않는다. 현재 `compose.road-condition.yml`의 API와 web 두 서비스 경계도 유지한다.

## 2. 운영 필수 항목 준비도

| 항목 | 상태 | 현재 경계 또는 필요한 입력 |
|---|---|---|
| authn/authz | 미구현 | IdP, token/session 방식, 역할 matrix 필요 |
| tenant isolation | 미구현 | tenant 식별자와 data ownership 정책 필요 |
| job quota | 미구현 | tenant별 동시성·용량·기간 한도 필요 |
| input checksum | 부분 | mapping/calibration/report hash는 있으나 모든 URI object 검증 전 |
| image/model version | 부분 | geometry/report image 추적 가능, crack model 미승인 |
| structured logs | 미구현 | 개인정보 redaction과 log schema 필요 |
| timing/peak RSS metric | 미구현 | 대표 데이터와 운영 예산 필요 |
| tracing/correlation ID | 미구현 | tracing backend와 retention 필요 |
| backup/restore | 미구현 | RPO/RTO와 저장소 필요 |
| retention | 미구현 | 작업/원본/리포트 보존 기간 필요 |
| PII/location policy | 미구현 | 위치정보 등급·접근/파기 정책 필요 |
| TLS/reverse proxy | 미구현 | 운영 hostname/certificate termination 필요 |

`/workspace:ro`, non-root API/report 이미지, 상대 경로 guard, raw prediction 불변성, RIMMS ingress
기본 비활성화는 개발 환경의 방어선이다. 이것만으로 운영 보안을 충족하지 않는다.

## 3. Stage 13 수용 기준 판정

| 수용 기준 | 판정 | 이유 |
|---|---|---|
| worker kill 후 재시도 | NOT RUN | 분산 worker/queue를 전환하지 않음 |
| idempotency 중복 방지 | PARTIAL PASS | RIMMS contract ingress만 key/body 충돌 검증 |
| 결과 checksum | PARTIAL PASS | report/profile/catalog hash, 전체 object E2E checksum 전 |
| migration rollback | N/A | DB migration 없음 |
| 보안 scan | NOT RUN | 승인 scanner/정책/예외 기준 미제공 |
| load test | NOT RUN | workload와 SLA 미제공 |

Stage 13은 `DEFERRED_GATE_NOT_MET`이다. 운영 stack 완료나 production-ready로 표시하지 않는다.

## 4. 전환 승인에 필요한 자료

1. 대표 노선 크기, 장치 수, 동시 job 수, 일일 처리량
2. queue/run latency와 성공률 SLA, CPU/RAM/GPU 예산
3. IdP/auth protocol, 역할·tenant·조직 schema
4. object storage 종류, credential 방식, network zone
5. RPO/RTO, retention, location/PII 접근·파기 정책
6. observability backend와 log redaction 정책
7. 보안 scanner, 취약점 severity 차단 기준, 예외 승인자
8. load/kill/retry/restore 시험 환경과 승인자

위 자료가 승인되면 별도 Stage 13 구현 브랜치에서 migration, worker kill retry, checksum, 보안
scan, load/restore를 각각 재현 가능한 증거로 남긴다.
