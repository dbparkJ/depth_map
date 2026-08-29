# 현장 공감형 3D evidence와 비전문가 UX 실행 계획

## 기준

- Branch: `feat/road-condition-official-3d-evidence`
- Base SHA: `4c092b7d5cc3d01e58835fd8b0a01231b4c68f90`
- 사용자: 지자체 공무원, 발주·행정·사무 담당자, 현장 검수자
- 기본 입력: ground-only 결과가 아닌 `cloud_raw_enu.ply`와 raw metadata
- 정확도 상태: 카메라 보정과 실측 결함 truth가 없으므로 모든 후보는 `현장 확인 필요`다.

## 현재 문제 진단

현재 첫 화면은 분석 실행 폼, 내부 점수, coverage, residual 용어가 지도보다 먼저 나온다.
1440×1000 화면에서도 VWorld 지도가 첫 화면 아래에 걸려 사용자가 위치와 실제 도로 모습을
바로 볼 수 없다. 결함 polygon만 지도에 표시되어 다음 질문에 답하기 어렵다.

1. 이 위치가 실제로 어떤 도로인가?
2. 수집된 실제 표면에서 어느 부분이 손상 후보인가?
3. 왜 확인이 필요하고 다음 조치는 무엇인가?

기존 `경량 3D evidence`는 surface preview를 강조한 분석용 표현이어서 실제 RGB 점군과 결함
mask를 함께 보는 현장 공감 evidence로는 부족하다.

## 데이터 감사

- `chunk_0000~0025`: mapping bundle `26/26` 완료
- 공통 ENU origin: `1`개
- raw point 합계: `696,129,845`
- trajectory 길이 합계: 약 `11.54 km`
- 각 청크의 `points_raw.bin`: 최대 `400,000`점, 일반적으로 약 `6.4 MB`
- `chunk_0002`, `chunk_0003`: 이동거리 `1 m` 미만의 정차성 구간
- `chunk_0006`, `chunk_0025`: 각각 약 `31.1 m`, `24.3 m`의 짧은 구간
- 기존 실제 분석 기준: `chunk_0000` route tile `77/77`, 후보 `589`, 실측 truth 없음

정차·짧은 청크를 제외하지 않고 negative/control 사례로 포함한다. 주행 청크만으로 결과가 좋아
보이는 편향을 피하기 위해 모든 청크를 감사 대상으로 삼는다.

## 전달 형식 결정

### 검토한 선택지

1. 기존 청크 전체 `points_raw.bin` 직접 로드
   - 장점: 추가 변환 없음
   - 단점: 선택한 10 m 구간에도 청크 전체 5~6.4 MB와 최대 40만 점을 읽고 결함 index가 없다.
2. Potree 또는 정식 Cesium 3D Tiles
   - 장점: 대규모 LOD와 표준 생태계
   - 단점: 변환기·계층·캐시·운영 서버까지 함께 검증해야 하므로 이번 UX 수정의 첫
     체크포인트로는 범위가 크다.
3. route tile 전용 quantized evidence binary
   - 장점: 기존 공간 균등 raw browser sample을 재사용하고 선택 tile만 작게 전송할 수 있으며,
     결함 class/index를 point에 붙일 수 있다.

### 선택

먼저 `route tile 전용 quantized evidence binary`를 구현한다. PoC 성능을 기록한 뒤 전체 노선
LOD가 필요할 때 Potree/3D Tiles와 비교한다. 형식은 원본 PLY를 대체하지 않는 파생 evidence다.

```text
evidence/
  manifest.json
  tiles/tile-000000.rcev

RCEV v1 header
  magic/version/count/stride
  ENU bbox minimum + quantization scale

record
  quantized ENU XYZ uint16 × 3
  actual RGB uint8 × 3
  defect class uint8
  tile-local defect index uint16
```

- 선택 tile 최대 `60,000`점
- 예상 최대 payload: 약 `720 KB + header`
- point는 실제 수집 RGB/XYZ를 보존한다.
- mask는 포트홀/러팅/범프 후보 polygon과 surface band의 교차로 만든다.
- 브라우저는 선택 tile 하나만 유지하고 이전 tile primitive를 제거한다.

## 단계와 체크포인트

### Checkpoint A — 경량 3D evidence 계약

- `RCEV v1` writer/reader/validator
- 실제 RGB browser sample을 분석 tile의 surface band로 자르기
- defect class/index mask 부여
- 합성 fixture와 `chunk_0000` 한 tile smoke
- commit: `feat(road-condition-core): checkpoint/road-condition-stage-07-point-evidence`

수용 기준:

- encode/decode 최대 좌표 오차 `1 cm` 이하
- 실제 RGB 값 보존
- tile당 `60,000`점·`1 MB` 이하
- 전체 PLY를 브라우저/API 메모리에 올리지 않음

### Checkpoint B — read-only evidence API

- `/workspace` 상대 경로만 허용하는 manifest/tile endpoint
- `.rcev`만 allowlist하고 binary streaming
- route manifest에 evidence availability 표시
- public gateway의 GET 허용, POST/DELETE 차단 유지
- commit: `feat(road-condition-api): checkpoint/road-condition-stage-07-point-evidence-api`

### Checkpoint C — 공무원·사무직 기본 화면

- 첫 화면의 60% 이상을 `현장 3D 지도`에 배정
- 실제 RGB 점군과 빨강/주황 손상 mask를 같은 VWorld 장면에 표시
- 결함 선택 시 camera fly-to, 쉬운 설명, cm/㎡, `현장 확인 필요` 조치 표시
- 기본 화면 용어를 `어디인가 / 무엇이 보였나 / 무엇을 해야 하나`로 재구성
- 분석 실행·임계값·coverage·residual은 `전문가 상세`로 이동
- 키보드 접근성과 WebGL 복구 유지
- commit: `feat(road-condition-web): checkpoint/road-condition-stage-07-official-ux`

수용 기준:

- 1440×900 첫 화면에서 지도와 현장 확인 요약이 스크롤 없이 보임
- 사용자가 실제 점군, 손상 mask, 결함 설명을 동시에 확인
- 선택 결함 ID가 API와 동일
- VWorld 실패 시 동일 evidence의 Local ENU fallback 제공

### Checkpoint D — 26개 청크 균등 감사와 최소 알고리즘 개선

- `chunk_0000~0025` 각각에서 trajectory 위치를 순환해 25%/50%/75% 지점의 10 m core를
  한 곳씩 선택한다.
- 정차·짧은 청크는 전체 또는 중앙 구간을 negative/control로 분석한다.
- 청크별 point 수, multiview 제외율, supported/usable coverage, plausibility 제외,
  후보 유형·깊이·높이·횡오프셋을 동일 schema로 기록한다.
- 동일 오탐 패턴이 여러 청크에서 재현되고 합성 회귀를 통과할 때만 최소 변경한다.
- commit: `feat(road-condition-core): checkpoint/road-condition-stage-03-multichunk-audit`

중단·승인 조건:

- 실측 truth가 없으므로 depth/area threshold를 정확도 값으로 자동 조정하지 않는다.
- 도로 ROI가 없는 상태에서 후보 수만 줄어드는 변경은 승인하지 않는다.
- 한 청크만 좋아지고 다른 구간 coverage가 악화되면 기본값을 바꾸지 않는다.
- 예상 실행은 mapping bundle을 한 번에 하나씩 읽고, peak RSS `10 GB` 또는 청크당
  `15분`을 넘으면 중단해 원인을 기록한다.

### Checkpoint E — 통합 검증과 외부 공개

- static/unit/synthetic/real smoke/Compose
- Cloudflare HTTPS에서 실제 route evidence와 VWorld WebGL 확인
- GET-only 공개 경계 재검증
- 성능: evidence byte, download, parse, first-visible 측정
- commit: `test(road-condition): checkpoint/road-condition-official-3d-evidence`

## 명칭과 UX 원칙

- `PCI` 대신 `내부 형상 참고값`
- `IRI` 대신 `승차감 참고값`
- `확정 손상` 대신 `손상 의심 구간` 또는 `현장 확인 필요`
- 첫 화면에는 알고리즘 파라미터를 노출하지 않는다.
- 색만으로 상태를 구분하지 않고 label/icon/text를 함께 쓴다.
- 깊이·높이는 기본 `cm`, 거리·위치는 `m`, 면적은 `㎡`로 표시한다.
- 낮은 신뢰도는 숫자 하나가 아니라 쉬운 이유와 필요한 다음 행동으로 설명한다.

## 알려진 제한

- RGB 점군은 현장 사진과 같은 연속 영상이 아니라 수집된 depth 기반 3D sample이다.
- mask는 현재 geometry candidate의 위치를 설명하는 evidence이며 실제 파손 확정 라벨이 아니다.
- 실제 도로 ROI, 장착 보정, 포트홀·러팅 실측값이 없으므로 공식 정확도 평가는 할 수 없다.
- 정식 전 노선 LOD 배포 형식은 이번 tile PoC 결과 후 Potree/3D Tiles와 비교해 결정한다.
