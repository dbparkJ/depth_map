# 통합 체크포인트 — 실제 3D evidence, VWorld, 연구·예산 근거

## 기준

- Branch: `feat/road-condition-official-3d-evidence`
- Base checkpoint: `b1be51a`
- 실제 route:
  `2026-08-19_09-51-22_hardlinked/road_condition/chunk_0000_boundary_guard_v4`
- 알고리즘: `road-condition-geometry-mvp-4`
- 외부 공개: Cloudflare Quick Tunnel의 HTTPS GET-only gateway
- 카메라 장착 보정과 실측 truth가 없으므로 결과는 `손상 후보/현장 확인 필요`다.

## 통합된 체크포인트

- 연구 근거: 국내외 논문·공공 매뉴얼, 적용/미적용 임계값과 센서 차이 추적
- 예산 근거: 2026년 하반기 표준시장단가·표준품셈과 나라장터 혼합 총액의 용도 분리
- RCEV v1: 선택한 10 m tile의 실제 RGB/XYZ와 포트홀·러팅·융기 mask
- read-only API: route manifest, 선택 tile JSON, evidence binary, 예산 검토
- 웹: VWorld actual-height point/mask, Local ENU fallback, 행정 담당자 중심 설명
- 분석: 26개 청크 균등 감사 후 높은 비노면 물체 경계 범프 guard

## 실제 데이터 결과

- 전체 입력: 26개 청크, raw point `696,129,845`, trajectory `11,544.63 m`
- 균등 감사: 완료 청크 `24/26`, 완료 주행 tile `68`, 정차성 control 실패 `2`
- 감사 전/후 후보: `1,204 → 999`
  - 포트홀 `444 → 444`
  - 러팅 `110 → 110`
  - 범프 `650 → 445`
- coverage는 전후 tile별 동일하다. 참/거짓 실측이 없으므로 남은 후보의 정확도 개선을
  주장하지 않는다.
- 새 `chunk_0000` route: `77/77`, 실패 0, 후보 `537`, evidence `210,884점 / 2,535,536
  bytes`
- 첫 tile: 후보 7건(포트홀 5, 러팅 2, 범프 0), 실제 evidence `1,394점 / 16,792 bytes`
- 첫 tile 2026 예산 검토: 계산 가능한 공식 공종 부분합 하한 `50,486원`, 전체 공사비
  `N/A`

## 통합 검증

- Python compile: PASS
- browser JavaScript syntax: PASS (`node:22-alpine`)
- road-condition tests: `66 passed`
- Compose config/build/up: PASS, API·web container healthy
- Compose synthetic `mixed` job smoke: PASS, 후보 7건, supported/usable coverage 100%
- API health: `road-condition-geometry-mvp-4`
- local/public route manifest·evidence·budget GET: `200`
- public write 경계: POST `405`
- 1440×1800 headless Chrome screenshot에서 VWorld, 실제 point/mask, 행정 요약, 예산 결과를
  함께 확인했다.

## 성능 관찰값

2026-08-30 현재 장비와 Quick Tunnel에서 한 통합 smoke로 측정한 값이며 SLA가 아니다.

| 항목 | 관찰값 |
|---|---:|
| 첫 RCEV tile | 16,792 bytes / 1,394점 |
| RCEV parse, Node 22, 1,000회 p50 / p95 | 0.019 ms / 0.036 ms |
| 공개 evidence cold download | 911 ms |
| 같은 실행의 warm download 범위 | 43–70 ms |
| 외부 headless Chrome DOM/load | 1,893 ms |
| VWorld + 실제 evidence 확인 가능 | 3,881 ms |
| 예산 결과 확인 가능 | 4,317 ms |

browser 시간은 CDP에서 route point 수, 후보 수, VWorld viewer와 예산 문구가 모두 DOM에
나타난 시점을 사용했다. 네트워크·VWorld 캐시·Quick Tunnel 상태에 따라 달라진다.

## 안전 경계와 제한

- 전체 PLY를 브라우저로 전송하지 않고 선택 tile 하나만 유지한다.
- API는 `/workspace` 아래 상대 경로와 allowlist artifact만 읽는다. Compose workspace mount는
  read-only다.
- 공개 gateway는 조회 GET만 허용하며 분석 실행·삭제·검수 POST/DELETE를 차단한다.
- Quick Tunnel 주소는 재시작 시 바뀔 수 있으며 인증을 대신하지 않는다.
- 표시 mask는 형상 후보 evidence이지 확정 파손 라벨이 아니다.
- 도로 ROI, RGB frame 연결, 카메라 보정, 포트홀·러팅 실측 truth가 없으므로 발주 수량과
  정확도 승인을 할 수 없다.
- `50,486원`은 재료·운반·폐기·교통통제·간접비·이윤·VAT가 빠진 계산 가능 공종 부분합
  하한이며 전체 보수비가 아니다.

## 재현과 롤백

- 화면은 route query로 해당 상대 경로를 지정해 재현한다.
- 이 체크포인트는 독립 commit으로 고정한다.
- 이전 예산 API 체크포인트 확인: `git switch --detach b1be51a`
- 기능 브랜치에서 되돌릴 때는 통합 commit을 `git revert`해 기록을 보존한다.
