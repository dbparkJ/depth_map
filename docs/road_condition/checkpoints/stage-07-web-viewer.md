# Stage 07 checkpoint — progressive web evidence viewer

## 기준과 결정

- Branch: `feat/road-condition-stage-07-web-viewer`
- Base SHA: `08f98add89479bec3e8e17e49411851d9ee9160c`
- 기본 지도: offline local ENU
- VWorld/Cesium: adapter 경계 제공, key/token과 WGS84 설정 전 명시적 fallback
- 앱 경계: 기존 viewer와 합치지 않고 road-condition web 별도 유지
- 모바일: responsive read-only review 지원
- 작업자 수정·승인: Stage 10까지 보류

## 구현

- 완료 job/날짜 선택과 여러 route 결과 경로 입력
- `/workspace` 상대 경로, tile ID, artifact allowlist, 25MB JSON 상한
- route manifest 선로드 후 선택 tile JSON 한 개만 메모리에 유지
- Local ST residual, segment grade, low coverage, ROI와 defect layer
- confidence/quality filter와 추가 형상 screening layer
- downsampled preview 기반 경량 3D evidence, 1×/2×/5× Z 강조
- local ENU 지도와 VWorld/Cesium adapter 상태/fallback
- API defect ID를 그대로 사용하는 click/table selection
- 선택 지점 횡·종단 profile, RGB evidence N/A, summary before/after 비교
- 키보드 행 선택과 N/P 이동, WebGL context loss/reload 안내 계약

## 검증 결과

- 정적 검사: Python compileall, `app.js`/`viewer_core.js` node syntax, Compose config 통과
- 단위/회귀: `153 passed, 1 warning` (`39.09s`)
- route API: path escape, JSON symlink escape, tile ID/artifact allowlist, failed tile 차단 통과
- 순수 JS: path 순서, tile sequence, confidence/quality filter, adapter, perspective contract 통과
- 웹 정적 계약: keyboard focus, context loss 안내, progressive endpoint, Docker asset 포함 확인
- Docker: build와 기존 synthetic mixed smoke 통과
- 임시 read-only 합성 route: 2 tile manifest, 선택 tile 65 surface rows/결함 JSON 통과
- headless Chrome: 자동 job 완료 후 동적 DOM `17,331 bytes`, 화면 캡처 `1600×1200` 확인
- 전체 PLY endpoint가 없고 manifest의 host 절대 artifact 경로가 응답에서 제거됨을 확인

## 알려진 제한

- VWorld/Cesium 실 basemap은 key/token과 WGS84 defect GeoJSON이 없어 활성화하지 않았다.
- 3D evidence는 surface preview의 Canvas perspective이며 전체 point cloud viewer가 아니다.
- RGB frame evidence와 survey 간 공간·시간 정합은 아직 없다.
- before/after는 summary 차이만 표시하며 동일 노선 정합을 자동 보장하지 않는다.
