# Stage 11 checkpoint — maintenance and deterioration scenario v2

## 기준과 결정

- Branch: `feat/road-condition-stage-11-maintenance-v2`
- Base SHA: `9a6d6264dc15902b3d498991289ae5f3e5838f4f`
- 공법/실단가표: 미제공, 기존 MVP 가격을 versioned internal planning 예시로 이관
- 최소 작업 단위: 포트홀 0.25㎡, 러팅 1.0㎡, 범프 0.25㎡ experimental 기본값
- 추가 비용: 차선 통제·장비 이동·폐기물은 `N/A`, 0원으로 처리하지 않음
- 반복 조사: 미제공, 연간 열화율과 열화 projection은 `N/A`
- 예산 목표: 결정적 risk-screening priority, 최적화 주장 없음

## 구현

- `internal-planning-v1@1.0.0` YAML 공법/단가 catalog와 strict path/schema guard
- 공법별 최소 작업량, 동원비, defect별 추천과 부분 비용
- severity/형상 기반 deterministic budget screening과 복수 예산 비교
- 알려진 비용 `priced_total_krw`와 미확정 전체 비용 `full_total_krw=null` 분리
- 비보정 전후 internal score planning estimate와 명시적 `not_prediction` 상태
- 반복 조사 부재에 따른 deterioration rate/projected score `null`
- legacy `/scenarios` 보존 및 새 `/scenarios/v2` API
- 웹 예산 screening과 N/A/비보정 한계 표시

## 검증 결과

- Python compileall, 컨테이너 Node syntax, Compose YAML/config, `git diff --check` 통과
- 전체 단위/회귀 `46 passed` (`35.74s`), 유지보수/API 집중 검증 `6 passed`
- 최소 작업량, null 비용, catalog path guard, 빈 type 선택 검증 통과
- 55만원 fixture에서 high pothole만 선정하고 알려진 비용이 예산 이하임을 검증
- 두 비교 예산의 결정적 선정 수와 deterioration/전체 비용 `null` 검증
- API image `bd1336510ff9`, web image `84e9783e12aa` non-root build 통과
- Compose 18080/18081 healthcheck와 synthetic mixed smoke 통과
- 웹 reverse proxy의 v2 요청에서 60만원 예산, 2건 선정, 알려진 비용 584,000원 확인
- 같은 응답에서 전체 비용/열화율 `null`, catalog/version/hash와 `not_prediction` 확인
- API 로그에 HTTP 오류와 traceback 없음

## 알려진 제한

- 실제 발주 단가, 공급자 견적, 계약 최소 작업 단위가 없다.
- 차선 통제, 장비 이동, 폐기물 비용이 없어 전체 비용을 계산하지 않는다.
- 추천 우선순위는 안전·민원·교통량 자료가 없는 내부 geometry screening이다.
- 전후 점수는 planning heuristic이며 현장 보수 효과 예측이 아니다.
- 공간 정합된 반복 조사 자료가 없어 열화율을 추정하지 않는다.
