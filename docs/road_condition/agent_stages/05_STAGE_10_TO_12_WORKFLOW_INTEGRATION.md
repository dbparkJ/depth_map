# Stage 10 — 점수 profile과 검수 workflow

## 목표

내부 MVP 점수를 발주처별 설정 가능한 profile로 분리하고, 자동 검출을 작업자가 승인·수정할
수 있게 한다.

## 사용자 질문

1. 점수 기준을 어느 기관/발주처에 맞출 것인가?
2. 구간 길이와 차로별 평가 여부는?
3. 심각도 기준표가 있는가?
4. 자동 승인 가능한 confidence threshold는?
5. 검수 역할은 관리자/작업자 두 단계가 필요한가?

## 설정

```text
scoring_profiles/<profile_id>.yaml
```

profile 필드:

- version
- source document
- effective date
- distress types
- severity thresholds
- density calculation
- weights/deduct rules
- missing metric policy
- approval status

## 검수 상태

```text
pending
accepted
modified
rejected
needs_recollection
```

수정 이력:

```json
{
  "event_id": "...",
  "defect_id": "...",
  "actor": "...",
  "action": "modified",
  "before": {},
  "after": {},
  "created_at": "...",
  "reason": "..."
}
```

## 수용 기준

- raw prediction을 덮어쓰지 않는다.
- profile version이 report에 표시된다.
- 미측정 항목은 N/A다.
- 표준 명칭은 실제 계산 절차를 검증한 profile에서만 사용한다.

---

# Stage 11 — 유지보수·열화 시나리오 v2

## 사용자 질문

1. 보수공법 목록과 실제 단가표가 있는가?
2. 포트홀 패칭 최소 작업 단위는?
3. 차선 통제·장비 이동·폐기물 비용을 포함할 것인가?
4. 반복 조사 자료가 있는가?
5. 예산 최적화 목표는 위험 최소, 점수 최대, 민원 우선 중 무엇인가?

## 구현

- 공법 catalog
- 단가 version
- 최소 주문/동원 비용
- 결함→공법 recommendation rule
- 예산 제한 우선순위
- 전후 score projection
- 반복 조사 열화 rate
- scenario comparison

## 금지

근거가 없는 단순 선형 점수 회복을 실제 예측으로 표시하지 않는다. 현재 MVP의 회복값은 planning
estimate로만 유지한다.

---

# Stage 12 — RoadInventory-MMS 연동

## 목표

분석 서비스와 업무 시스템을 URI 기반 비동기 작업으로 연결한다.

## 사용자 질문

1. `RoadInventory-MMS`의 현재 인증 방식은?
2. object storage가 있는가?
3. job callback과 polling 중 무엇을 사용할 것인가?
4. 노선/차로/조사 ID schema는?
5. defect 수정 결과를 어느 시스템이 source of truth로 가질 것인가?

## 요청 예시

```json
{
  "external_job_id": "rimms-2026-001",
  "survey_id": "survey-20260827-001",
  "route_id": "route-a",
  "mapping_bundle_uri": "s3://bucket/survey-001/mapping/",
  "raw_dataset_uri": "s3://bucket/survey-001/raw/",
  "road_roi_uri": "s3://bucket/survey-001/road_roi.geojson",
  "config_profile_id": "geometry-v2",
  "callback_url": "https://.../callbacks/road-condition"
}
```

## 결과 예시

```json
{
  "external_job_id": "rimms-2026-001",
  "state": "completed",
  "result_manifest_uri": "s3://bucket/.../route_manifest.json",
  "summary_uri": "s3://bucket/.../summary.json",
  "report_uri": "s3://bucket/.../report.pdf"
}
```

## 수용 기준

- 대용량 파일을 JSON body로 전송하지 않는다.
- 요청은 idempotency key를 가진다.
- callback 재시도가 중복 작업을 만들지 않는다.
- 결과 contract version mismatch를 명시적으로 실패 처리한다.
- source of truth와 수정 동기화 방향이 문서화된다.
