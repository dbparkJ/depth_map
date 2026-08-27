# Stage 09 checkpoint — RGB crack contract and holdout gate

## 기준과 결정

- Branch: `feat/road-condition-stage-09-rgb-crack-contract`
- Base SHA: `43469496d0b959fe4a381d2b2b6d9a556f4008c0`
- 라벨 형식/클래스/최소 폭/RGB·노면 해상도: 미제공, `unknown`
- wet/shadow/night subset 구성: 미제공, `unknown`
- 학습·추론 GPU/framework: 미제공, `unknown`
- 정책: 모델/수치를 추정하지 않고 자동 승인 비활성화
- 서비스 경계: geometry API에 PyTorch를 추가하지 않음

## 구현

- `/workspace` 상대 model manifest와 weights SHA-256 fail-closed 검증
- dataset/label/resolution/condition/GPU/framework/holdout/승인자 필수 계약
- route·survey-date 분리와 pixel precision/recall/F1 고정
- instance recall, 길이 절대오차, 100m당 오탐, wet/shadow subset metric 고정
- road/depth/pose mask를 모두 적용하는 projected pixel BEV probability 누적
- threshold, thinning, chamfer width 기반 unvalidated crack candidate 후처리
- `source=rgb_ai` defect와 model provenance, experimental/manual-review flag
- immutable original prediction SHA와 revision actor/time/reason/patch 보존
- non-root contract Docker image와 fail-closed capabilities/CLI

## 검증 결과

- 정적 검사: Python compileall, 컨테이너 Node syntax, Compose YAML/config, `git diff --check` 통과
- 전체 단위/회귀: `40 passed` (`57.67s`), contract/API 집중 검증 `9 passed`
- 합성 직선: deterministic BEV candidate, 길이·폭·면적·방향·audit 계약 통과
- 합성 holdout: pixel/instance/length/100m FP/wet/shadow metric 수치 통과
- path escape, weights hash mismatch, protocol hash mismatch, unapproved manifest 거부 통과
- contract Docker: image `7d964c63b0ac`, non-root UID 10001, network none, read-only root/workspace 통과
- Docker NPZ holdout: instance recall 1.0, false positive 1.0/100m, 자동 승인 disabled 확인
- 기존 Compose: 18080/18081에서 build, synthetic mixed smoke, API 오류 로그 없음
- API capabilities: neural inference `not_configured`, geometry API PyTorch `false` 확인

## 알려진 제한

- neural segmentation adapter와 학습/승인 weights가 없어 RGB AI 추론은 수행하지 않는다.
- road mask model과 원 RGB-D pixel→ST camera projection은 아직 없다.
- thinning/chamfer 길이·폭은 합성 계약값이며 실측 accuracy가 아니다.
- crack class는 `unclassified`; 패칭·박리·블리딩도 미구현이다.
- GPU runtime이 정해지지 않아 운영 worker/Compose service로 활성화하지 않았다.
