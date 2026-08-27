# Stage 10 checkpoint — scoring profile and review workflow

## 기준과 결정

- Branch: `feat/road-condition-stage-10-review-workflow`
- Base SHA: `1df542544d451d46e93d9c070ecd79d0244332ac`
- 기관/발주처 기준: 미제공, 내부 experimental profile
- 구간/차로: 기존 20m, ROI lane이 있을 때 차로별
- severity 기준표: 미제공, 기존 geometry detector 결과 보존
- 자동 승인 confidence: 미제공, 비활성화
- 역할: 인증 없는 단일 manual reviewer audit label

## 구현

- 기존 점수를 재현하는 `internal-geometry-mvp-v1@1.0.0` YAML
- profile schema/path guard, version/hash/approval/standard naming guard
- profile score/segment 설정과 명시적 custom override 추적
- summary와 legacy/v2 report에 profile ID/version/hash/승인 상태
- raw defects canonical hash와 per-defect immutable prediction 사본
- pending/accepted/modified/rejected/needs_recollection 상태
- before/after/actor/action/time/reason/profile/version event
- optimistic version conflict와 raw prediction 변경 fail-closed
- 완료 job 수동 검수 API와 심각도 수정 웹 panel

## 검증 결과

- 정적 검사: Python compileall, 컨테이너 Node syntax, Compose config, `git diff --check` 통과
- 전체 단위/회귀: `42 passed` (`68.37s`), profile/report/review 집중 검증 `8 passed`
- 기본 profile score config가 기존 `AnalysisConfig.score`와 동일함 확인
- path escape와 experimental standard naming 거부, custom override flag 통과
- report HTML/CSV에 profile `internal-geometry-mvp-v1@1.0.0` 일치
- API 승인→stale 409→수정 event와 before/after/version/profile 일치
- 검수 전후 raw `/defects` 동일, canonical hash guard 통과
- Compose build와 synthetic mixed smoke, 웹 proxy 승인 event, API 오류 로그 없음
- headless Chrome 동적 DOM에서 review panel 확인

## 알려진 제한

- 기관 source document, effective date, severity/deduct 기준이 없어 standard profile이 아니다.
- 인증/권한이 없어 actor 문자열은 신원 보장이 없는 audit label이다.
- 웹 수정은 현재 severity만 제공하며 polygon/metric 편집기는 없다.
- route tile 검수와 관리자 2단계 승인, 다중 API process file lock은 미구현이다.
