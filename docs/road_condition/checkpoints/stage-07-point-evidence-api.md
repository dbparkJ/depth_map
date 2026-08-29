# Stage 07 checkpoint — read-only point evidence API

## 기준

- Branch: `feat/road-condition-official-3d-evidence`
- Base checkpoint: `bb21f89`
- `/workspace` read-only mount와 상대 경로 guard를 유지한다.
- 전체 PLY, mapping browser sample, 임의 workspace 파일은 HTTP로 제공하지 않는다.

## 구현

- route manifest 응답에 tile별 evidence point/byte/mask/bbox와 availability 계약을 추가한다.
- evidence manifest 응답에서는 source file hash를 제거한다.
- 선택 route와 정확한 `tile-NNNNNN` 조합으로만 `.rcev` 하나를 조회할 수 있다.
- manifest artifact가 `tiles/<동일 tile ID>.rcev`와 다르면 거부한다.
- symlink/path escape, 1 MiB 초과, 잘못된 magic/version/stride/size를 거부한다.
- binary 응답에 vendor media type, ETag, immutable cache, `nosniff` header를 적용한다.

## Endpoint

```text
GET /api/v1/route-datasets/evidence/manifest?path=<workspace-relative-route>
GET /api/v1/route-datasets/evidence/tile?path=<workspace-relative-route>&tile_id=tile-000000
```

## 검증

- Python compileall: 통과
- route manifest가 point evidence availability와 tile summary를 반환: 통과
- 선택 evidence tile의 `RCEV` binary와 media type: 통과
- workspace escape, tile ID traversal, artifact mismatch, size/header 위조 거부: 통과
- 기존 JSON tile API와 failed tile `409` 계약: 유지
- public gateway: GET 허용, POST/DELETE `405` 정책 유지

## 알려진 제한

- Range request 최적화는 tile 최대 1 MiB 계약에서 우선 필요하지 않아 구현하지 않았다.
- evidence가 생성되지 않은 route는 기존 JSON preview만 제공한다.
- 인증이 없는 Quick Tunnel은 조회 권한 분리를 제공하지 않는다.
