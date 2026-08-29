# Stage 11 follow-up checkpoint — route용 2026 예산 검토 GET

## 목적

Cloudflare 공개 gateway는 안전상 POST/DELETE를 차단한다. 기존 유지보수 v2 endpoint가 job용
POST뿐이면 실제 route 결과를 외부에서 보는 사용자는 후보별 예산 검토를 실행할 수 없다.

## 구현

- `GET /api/v1/route-datasets/budget-report`
  - `/workspace` 아래 route 상대 경로
  - strict `tile-NNNNNN` ID
  - 선택한 완료 tile의 `summary.json`, `defects.json`만 읽음
  - query budget 범위 `0~1조원`
  - 기본 `kr-molit-2026h2-reference` catalog만 사용
- 응답은 job v2와 동일하게 다음을 분리한다.
  - 공식 공종 중 계산 가능한 부분합 하한
  - 예산 내/보류/단가 미산정 후보
  - full project estimate `N/A`
  - 미산정 재료·운반·폐기·교통통제·간접비·VAT
  - 2026 CODIL 근거와 나라장터 혼합 총액 context
- 웹은 route tile을 열 때 위 GET을 자동 호출하고, 예산 입력을 바꾸어 다시 조회할 수 있다.
- job 화면은 기존 POST v2 계약을 유지한다.

## 검증

- API fixture: 0.1㎡ 포트홀의 공식 계산 가능 부분합 `876원`, 전체 공사비 `N/A`
- 실제 `chunk_0000_boundary_guard_v4/tile-000000`: 계산 가능한 공종 부분합 하한
  `50,486원`, 예산 내 계산 가능 후보 7건, 단가 미산정 후보 0건, 전체 공사비 `N/A`
- 전체 road-condition tests: `66 passed`
- Python compile, browser JavaScript syntax, Compose 2-service contract 통과
- local/public manifest·evidence·budget GET `200`
- public gateway는 GET을 허용하고 기존 POST `405` 경계를 유지한다.
- 1440×1800 headless browser에서 VWorld, 실제 수집점·손상 mask, 예산 결과가 함께 표시됨을
  확인했다.

## 제한

- 후보 mask 면적은 발주용 확정 물량이 아니다.
- 금액이 작게 보여도 최소 출동비·재료·장비·통제·간접비가 빠진 하한 부분합이다.
- 나라장터 혼합 총액을 포트홀 ㎡ 단가로 전용하지 않는다.
