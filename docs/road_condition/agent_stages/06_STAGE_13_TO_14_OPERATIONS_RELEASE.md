# Stage 13 — 분산 실행과 운영 보안

## 전환 조건

다음 중 하나를 만족할 때만 파일 queue를 교체한다.

- 단일 작업 처리시간이 운영 SLA를 넘음
- 여러 장치가 동시에 업로드
- API 재시작 시 running 상태 복구 필요
- 여러 worker host 필요
- 조직별 권한 필요

## 권장 서비스

```text
road-condition-api
geometry-worker
crack-worker-gpu
report-worker
PostgreSQL
Redis or RabbitMQ
S3-compatible object storage
reverse proxy / TLS
observability stack
```

## 운영 필수

- authn/authz
- tenant isolation
- job quota
- input checksum
- image/model version
- structured logs
- metrics: queue time, run time, peak RSS, failure stage
- tracing/job correlation ID
- backup/restore
- retention policy
- PII/location access policy

## 수용 기준

- worker kill 후 재시도 가능
- 같은 idempotency key 중복 방지
- 결과 checksum 검증
- migration rollback
- 보안 scan
- load test

---

# Stage 14 — 검증과 v1 릴리스

## 릴리스 데이터셋

최소 다음 subset을 분리한다.

- calibration
- development
- holdout
- adverse weather/shadow
- flat negative
- severe pothole
- rutting
- intersection
- stationary/pose-poor

같은 구간을 parameter tuning과 최종 평가에 동시에 사용하지 않는다.

## 최종 metric

- pothole instance precision/recall
- max-depth MAE
- area error
- volume error
- rut-depth MAE
- flat false-positive density
- crack metric
- coverage
- low-confidence recall
- job success rate
- wall time
- peak RSS
- web first meaningful render
- report consistency

## 릴리스 산출물

```text
CHANGELOG.md
release manifest
Docker image tags
SBOM
migration notes
benchmark report
known limitations
operator runbook
rollback runbook
```

## 릴리스 금지 조건

- holdout 없이 synthetic만 통과
- 공식 명칭 오사용
- 전체 데이터에서 OOM
- report와 JSON 숫자 불일치
- 경계 결함 중복 미해결
- calibration unknown인데 자동 확정
- Docker quickstart 실패

---

# 공통 API·스키마 변경 절차

1. 기존 `format_version` 확인
2. backward-compatible 필드 추가인지 판단
3. 제거/의미 변경이면 version 증가
4. fixture 추가
5. API capabilities 갱신
6. web fallback 추가
7. report fallback 추가
8. migration 문서
9. old result load test

새 필드를 UI 때문에만 임시로 summary에 넣지 않는다. 분석 의미가 있는 field는 core schema에 먼저
추가한다.

---

# 공통 성능·메모리 지침

## 개발 기본 예산

```text
합성 60m: 30초 이내
API worker: 기본 1
입력 포인트 상한: 2,000,000
surface preview: 420 × 140 이하
전체 배열 복제 최소화
```

실제 환경에서 baseline을 측정한 뒤 예산을 확정한다.

## 측정 항목

```text
input file size
input point count
sampled point count
projection time
rasterization time
reference fitting time
detection time
output write time
peak RSS
result size
```

stage별 timing을 `analysis_diagnostics.json`으로 추가하는 작업은 Stage 03에서 수행한다.

---

# 공통 실패 처리

## 작업 상태

```text
queued → running → completed
                 ↘ failed
```

필수 상태 필드:

- job_id
- state
- created_at
- updated_at
- progress
- message
- request
- artifacts
- error

실패 시:

- 사용자 응답에는 예외 type과 안전한 message
- 내부 `error.log`에는 traceback
- partial final artifact를 정상 결과로 노출하지 않음
- 임시 파일은 `.tmp` 후 atomic rename

---

# AI-agent가 단계 시작 때 사용할 질문 템플릿

```text
[Stage XX 시작 전 결정]

이번 단계 목표:
- ...

필수 결정:
1. ...
2. ...
3. ...

권장 기본값:
- ...

응답 방법:
A) 권장 기본값으로 진행
B) 1=..., 2=...로 지정
C) 이 단계 보류
```

사용자가 전체 진행을 이미 명시했고 질문의 답이 기존 대화·설정·데이터에서 명확하면 같은 질문을
반복하지 않는다. 결정 기록만 남기고 진행한다.

---

# AI-agent 완료 보고 템플릿

```text
[Stage XX 완료]

구현:
- ...

검증:
- static: PASS/FAIL
- unit: PASS/FAIL
- synthetic: PASS/FAIL
- real smoke: PASS/FAIL/SKIPPED
- docker: PASS/FAIL/SKIPPED

측정값:
- point count:
- valid coverage:
- defect count:
- wall time:
- peak RSS:

변경 파일:
- ...

체크포인트:
- branch:
- commit:
- rollback:

알려진 제한:
- ...

다음 단계 선택:
A) 권장 기본값으로 진행
B) 설정 변경
C) 현재 단계 수정
D) 중단
```

---

# PR 본문 체크리스트

```markdown
## Scope
- [ ] One stage or clearly separated checkpoint commits
- [ ] Existing mapping defaults unchanged
- [ ] Web/API/core boundary preserved

## Data contract
- [ ] Format version reviewed
- [ ] Units encoded in field names
- [ ] No NaN/Infinity JSON
- [ ] Backward compatibility tested

## Accuracy
- [ ] Synthetic fixture
- [ ] Flat negative fixture
- [ ] Holdout not used for tuning
- [ ] Threshold source documented
- [ ] PCI/IRI naming restrictions respected

## Operations
- [ ] Docker Compose config
- [ ] Healthcheck
- [ ] Read-only workspace
- [ ] Resume/rollback instructions
- [ ] No large artifacts committed

## Tests
- [ ] compileall
- [ ] JavaScript syntax
- [ ] unit tests
- [ ] API tests
- [ ] Docker smoke, or explicit environment reason for skip
```

---

# 현재 브랜치 이후 바로 수행할 권장 순서

현재 MVP 다음 작업은 아래 순서로 한다.

1. **Stage 03A**: 실제 데이터 10~30m 평탄 구간 noise benchmark
2. **Stage 03B**: `camera_poses.npz` 출력 계약
3. **Stage 03C**: 실측 포트홀·러팅 비교 리포트
4. **Stage 04A**: 수동 `road_roi.geojson`
5. **Stage 05A**: 10m core + 3m halo 합성 경계 테스트
6. **Stage 05B**: 60초 청크 두 개 병합
7. **Stage 06A**: 횡단경사
8. **Stage 08A**: 결함 evidence 이미지와 PDF PoC
9. **Stage 09A**: 크랙 데이터 schema와 baseline model
10. **Stage 12A**: `RoadInventory-MMS` job contract

한 단계의 acceptance가 실패하면 다음 단계로 기능을 늘리지 말고 정확도·데이터·성능 원인을 먼저
해결한다.
