# Stage 08 checkpoint — report v2 evidence package

## 기준과 결정

- Branch: `feat/road-condition-stage-08-report-v2`
- Base SHA: `d731c29fe3c8e613c43c7d4798c5b58e13184ada`
- 보고서 용도: 내부 검수용 한글 evidence package
- 고객명·로고: 제공되지 않아 `N/A`
- 기준 산출물: HTML source of truth
- PDF: 분석 API와 분리한 offline Chromium 이미지에서 optional 생성
- RGB evidence: frame 정합 정보가 없어 빈 이미지를 만들지 않고 `N/A`

## 구현

- 기존 summary/segments/defects JSON에서 deterministic report v2 생성
- UTF-8 BOM summary/segments/defects CSV와 JSON 수치 일관성 유지
- residual overview, 결함별 crop, 횡단·종단 SVG profile과 missing metadata
- 알고리즘 버전, config/input SHA-256, dataset ID, mapping commit 추적
- low-confidence와 재수집 필요 상태, 공식 PCI/IRI가 아니라는 제품 경계 표시
- report manifest에서 host 절대 source 경로 제거
- API report trailing-slash redirect와 정적 산출물 allowlist/symlink confinement
- 별도 non-root Chromium + Noto CJK report 이미지와 shell 없는 PDF CLI

## 검증 결과

- 정적 검사: Python compileall, 컨테이너 Node syntax, Compose YAML/config, `git diff --check` 통과
- 단위/회귀: `33 passed` (`53.28s`), report/API 집중 검증 `6 passed`
- 악성 defect ID `../escape`, report asset traversal/symlink confinement 회귀 통과
- JSON/CSV/PDF 점수 `29.7`, dataset/mapping traceability, PDF 한글 텍스트 추출 일치
- report Docker: non-root UID 10001, network none, read-only root/input, Noto Sans CJK KR 확인
- report Docker smoke: 3개 결함 evidence, PDF `261,671 bytes`, `8.15s`, host peak RSS 약 `28MB`
- 기존 Compose: build 후 충돌 없는 18080/18081에서 synthetic mixed smoke 통과
- 웹 proxy의 report v2 HTML/CSV/manifest 조회와 7개 결함 일치, API 오류 로그 없음

## 알려진 제한

- RGB frame ID와 원본/overlay 정합 계약이 없어 geometry evidence만 제공한다.
- 고객명, 프로젝트 로고, 실측 ground truth는 제공되지 않아 `N/A`다.
- PDF는 별도 image의 Chromium을 사용하며 API 요청 중 생성하지 않는다.
- 보고서 수치는 실측 검증 전 internal geometry score와 experimental proxy다.
