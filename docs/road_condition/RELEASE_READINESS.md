# Road-condition v1 release readiness

기준일: 2026-08-27
판정: **BLOCKED — release prohibited**

`release_readiness/road-condition-v1.yaml`은 릴리스 manifest가 아니라 v1 readiness 입력이다.
`scripts/road_condition_release_gate.py`는 누락값을 추정하지 않고 release exit code 2로 차단한다.

## 현재 차단 범주

- calibration/development/holdout/adverse/flat/severe/rutting/intersection/pose-poor subset 미제공
- parameter tuning 데이터와 최종 평가 데이터 분리 미확인
- 포트홀 precision/recall과 깊이·면적·체적 오차 미측정
- 러팅 깊이 오차, flat false-positive density 미측정
- 승인 crack holdout metric/weights/GPU 미제공
- coverage/low-confidence recall/job success/wall time/peak RSS 정식 benchmark 미측정
- web first meaningful render와 holdout report consistency 미측정
- 전체 데이터 OOM, calibration 확정, 실제 seam 중복, 운영 Docker quickstart 증거 미제공
- CHANGELOG, release manifest, immutable image tags, SBOM, migration/benchmark/runbook 미작성
- 명시적 release approver와 승인 시각 없음

합성 mixed demo의 168,000 points, coverage 1.0, 7개 geometry defect와 Docker smoke는 개발 회귀
증거다. holdout 정확도나 전체 노선 성능을 대신하지 않으므로 release readiness metric에 복사하지
않는다. 승인된 짧은 실데이터 경로도 없어 real smoke는 `SKIPPED`다.

## Gate 실행

```bash
PYTHONPATH=. python scripts/road_condition_release_gate.py \
  release_readiness/road-condition-v1.yaml
```

현재 정상 결과는 `status=BLOCKED`, `release_allowed=false`, exit code 2다. CI에서는 이 exit code를
무시하거나 release job을 우회하지 않는다.

## 해제 절차

1. dataset owner가 subset ID/evidence URI와 tuning/holdout 분리를 승인한다.
2. 고정 threshold로 모든 최종 metric을 산출하고 각각 acceptance 기준 통과를 기록한다.
3. 릴리스 금지 조건 7개를 실제 evidence로 `pass` 처리한다.
4. 릴리스 산출물 9개를 만들고 SHA-256과 immutable image digest를 기록한다.
5. 독립 release approver가 manifest와 evidence를 검토한다.
6. gate가 0을 반환한 동일 commit에서 Docker build/smoke, SBOM/security scan을 재실행한다.

그 전에는 tag, v1 release manifest, 공식 PCI/IRI 명칭을 생성하지 않는다.
