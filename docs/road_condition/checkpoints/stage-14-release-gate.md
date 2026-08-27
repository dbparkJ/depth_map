# Stage 14 checkpoint — validation and v1 release gate

## 기준과 결정

- Branch: `feat/road-condition-stage-14-release-gate`
- Base SHA: `d1b317dd0891087b6fc59cef6f23227fe64ef1da`
- 실제 release dataset/holdout/acceptance threshold/approver: 미제공
- 결정: v1 tag/release artifact를 생성하지 않고 fail-closed readiness gate를 구현
- 상태: `BLOCKED`, synthetic-only release 금지

## 구현

- 9개 필수 dataset subset과 tuning/holdout 분리 manifest
- 15개 최종 metric value/unit/acceptance/evidence 필드
- 7개 릴리스 금지 조건의 evidence gate
- 9개 release artifact path/SHA와 명시적 approver gate
- canonical manifest SHA, 결정적 blocker 목록, exit code 2 CLI
- release readiness와 해제 절차 문서

## 검증 결과

- static: Python compileall, 컨테이너 Node syntax, Compose YAML/config, diff check PASS
- unit/API regression: `53 passed` (`54.07s`), release gate 집중 `3 passed`
- release gate: exit code 2, `BLOCKED`, `release_allowed=false`, blocker 42개 PASS
- readiness manifest SHA-256: `e05565ad7ba8f5f1a62b12ce89e0ba63a457c4f8ea7ef66d535ee5e48876a0fa`
- synthetic Docker smoke: PASS, mixed 168,000 points, coverage 1.0, geometry defect 7개
- Docker: API/web healthcheck와 quickstart smoke PASS (Stage 12 service images, 서비스 코드 동일)
- real smoke: SKIPPED, 승인된 짧은 실데이터 경로 미제공
- peak RSS/web render/전체 노선 wall time: N/A, release metric으로 추정하지 않음

## 알려진 제한

- v1은 릴리스되지 않았고 release manifest/image tag/SBOM을 만들지 않았다.
- 합성 데이터는 holdout, 실제 노선 accuracy, adverse condition을 대신하지 않는다.
- 승인된 짧은 실데이터 경로가 없어 real smoke를 실행하지 않는다.
- peak RSS, full-route OOM, web render, 운영 성공률 metric이 없다.
- Stage 13 운영 보안 gate도 미충족이다.
