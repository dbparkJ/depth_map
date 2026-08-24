# depth_map 제외 범위 후속 작업 계획

## 1. 목적과 기준선

이 문서는 고밀도 점군 P0/P1과 도로 지도 후처리 작업에서 의도적으로 제외한 기능을 후속
이슈와 작은 PR로 나누기 위한 계획이다. 현재의 점군 프리셋, 관측 메타데이터, Depth 경계,
타일 이웃 필터, local surface, raw/clean/removed 계약, 품질 guard와 재후처리를 기준선으로
삼는다.

이 문서는 구현 승인이 아니다. 각 단계는 선행조건과 수용 기준을 별도 이슈에서 확정한
뒤 진행하며, 궤적 추정과 출력 계약을 한 PR에서 동시에 바꾸지 않는다.

### 현재 기준선에서 완료된 항목

- Radius/statistical 이상점 제거와 타일 경계 처리는 현재 후처리 기준선에 포함됐다.
- confidence 파일 존재 여부와 정렬 경로는 지원하지만 장비별 값 의미·임계값 적용은 아직
  하지 않는다.
- 대표 전체 데이터 1회 실행은 현재 후처리 검증에 포함하되, 여러 프리셋·장비·구간의
  정식 benchmark와 리소스 회귀는 단계 0B에 남긴다.
- 아래 계획은 현재 commit에서 제외된 semantic 모델, 동적 객체 전용 처리, TSDF/nvblox/
  Mesh, IMU, VO/GPS 재설계와 Potree/3D Tiles를 구현 승인 없이 확장하지 않는다.

## 2. 범위 매핑

명세의 `이번 작업에서 제외` 아홉 항목을 모두 다음 단계에 배치한다.

| 제외 항목 | 후속 단계 |
| --- | --- |
| Visual Odometry 알고리즘 교체 | 2A |
| IMU 추가 | 2C |
| GPS 보정 방식 전면 변경 | 2B |
| 루프 클로저 신규 구현 | 3 |
| Potree 서버 구축 | 4B |
| Cesium 3D Tiles 전체 변환 파이프라인 | 4C |
| Mesh 또는 TSDF 재구성 | 5 |
| 다중 데이터셋 대용량 benchmark | 0B |
| UI 전면 재설계 | 6 |

명세 §19의 별도 작업 중 이번 P1에서 구현한 거리·회전 기반 키프레임은 backlog에서
제외한다. 나머지는 다음처럼 포함한다.

| §19 backlog | 후속 단계 |
| --- | --- |
| 동적 객체 제거 | 1B |
| Depth confidence 기반 필터 | 1A |
| 통계적 이상점 제거 | 현재 기준선에서 완료 |
| 프레임 중첩 영역 색상 평균 및 노출 보정 | 1D |
| LAZ 저장 | 4A |
| Potree octree 변환 | 4B |
| Cesium 3D Tiles point cloud 변환 | 4C |
| 타일 단위 스트리밍 | 4D |
| TSDF 또는 surfel 기반 표면 재구성 | 5 |

Potree 서버와 octree 변환, Cesium 전체 파이프라인과 point-cloud 변환, Mesh/TSDF와
TSDF/surfel은 각각 하나의 작업 패키지에서 함께 추적한다.

## 3. 공통 선행조건과 데이터 정책

### 공통 선행조건

- 현재 P0/P1 단위 테스트와 20프레임 이하 스모크 테스트가 통과해야 한다.
- `summary.json`의 점군 통계와 PLY/BIN 포인트 수 계약을 버전화하고 고정한다.
- 카메라 내부 파라미터, RGB–Depth 정렬 여부, 카메라–GNSS 외부표정과 시간 기준을
  데이터셋 manifest에 기록한다.
- 직선, 회전, 정지, 반복 방문, 동적 객체, 저텍스처를 포함한 짧은 대표 구간을 정하고
  현재 구현의 포인트 수, 궤적 잔차, 실행시간과 peak RSS 기준선을 남긴다.
- 정확도 변경은 독립 측량점 또는 최소한 기존 방식과 분리된 holdout 기준으로 평가한다.

### 데이터 및 산출물 정책

- 실제 RGB, Depth, GNSS, IMU, PLY, LAZ, octree, tileset, mesh는 Git에 커밋하지 않는다.
- 로컬 입력 경로와 장비별 비밀값은 `.env` 또는 Git 제외 manifest로 관리한다. Git에는
  익명화한 스키마, 생성 스크립트, 작은 합성 fixture와 해시만 둔다.
- 실데이터 결과는 Git에서 제외된 `artifacts/` 또는 작업별 외부 스토리지에 저장하고,
  명령, commit SHA, 옵션, 데이터셋 ID/해시, 실행시간, peak RSS와 결과 요약을 남긴다.
- 테스트 순서는 정적 확인 → 단위 테스트 → 합성/20프레임 이하 스모크 → 승인된 대표
  구간 → release candidate의 전체 데이터 1회 순서다. 실패 원인 없이 전체 실행을
  반복하지 않는다.
- 전체 실행 전에 예상 디스크, 메모리, 시간 예산과 중단 기준을 기록한다. 임시 산출물의
  보존 기간과 삭제 담당자도 실행 이슈에 명시한다.
- 사람, 차량 번호판, 위치 기록이 포함될 수 있으므로 접근권한과 반출 정책을 데이터셋별로
  확인하고, 외부 서비스 업로드는 별도 승인을 받는다.

## 4. 단계별 작업 계획

### 단계 0A — 평가 하네스와 수용 기준

선행조건: P0/P1 출력 계약과 합성 fixture가 안정되어 있어야 한다.

산출물:

- 동일 입력에서 preset/추정기별 포인트 수, 표면 커버리지, 궤적 오차, 실행시간과 peak
  RSS를 비교하는 재현 가능한 benchmark 명령과 결과 스키마
- 카메라/GNSS 보정값, 데이터 구간과 정답 출처를 기록하는 익명화 manifest
- 실패 프레임, 제거 단계와 메모리 증가 구간을 찾는 요약 리포트

검증:

- 작은 합성 데이터에서 metric 계산을 단위 테스트한다.
- 같은 commit과 설정을 두 번 실행해 포인트 선택과 metric이 결정적인지 확인한다.
- 각 후속 이슈의 정확도·성능 임계치를 구현 전에 기록하고 결과를 본 뒤 완화하지 않는다.

완료조건: 후속 알고리즘 두 개를 같은 데이터와 metric으로 비교할 수 있고, 실행 결과에서
입력/설정/commit을 역추적할 수 있다.

### 단계 0B — 통제된 실제 대용량 전체 실행

선행조건: 단계 0A 완료, 모든 짧은 테스트 통과, 리소스 예산과 실행 승인 확보가 필요하다.

산출물:

- `preview`, `balanced`와 승인된 한 가지 고밀도 설정의 실행 manifest 및 품질 리포트
- 병목별 wall time, peak RSS, 출력 크기, 포인트 제거 통계와 실패 로그
- 다음 최적화의 우선순위를 정하는 결론

검증:

- 먼저 대표 구간 한 번으로 경로, 용량과 중단 기준을 확인한다.
- release candidate별 전체 데이터 실행은 원칙적으로 한 번만 수행한다.
- PLY header, BIN header, summary count 관계와 산출물 열기 검사를 자동화한다.

완료조건: 전체 입력 누락 없이 끝나고 결과가 예산 안에 있으며, 재실행 없이도 실패와
밀도 감소 단계를 판단할 로그가 남는다.

### 단계 1A — Depth confidence 기반 필터

선행조건: 카메라가 confidence 또는 동등한 품질 map을 실제로 기록하는지 확인해야 한다.
없다면 먼저 recorder의 파일 형식과 RGB/Depth 동기화를 정의한다.

산출물: 선택적 confidence 입력, 임계값 CLI, 통과/제거 통계, confidence가 없는 기존
데이터의 동일 동작 보장이다.

검증: 경계·저신뢰·무효 깊이를 담은 합성 영상, confidence 유무 양쪽의 회귀 테스트,
holdout 표면 노이즈 비교를 수행한다.

완료조건: 필터를 켰을 때 노이즈 metric이 사전 임계치만큼 개선되고, 끄거나 입력이 없을
때 기존 결과와 호환된다.

### 단계 1B — 동적 객체 제거

선행조건: 동적 객체가 포함된 라벨 구간과 정적 구조 보존 metric이 필요하다.

산출물: 의미론 mask 또는 다중 프레임 motion 일관성 기반의 선택적 mask 단계, 클래스별
정책, 제거 통계와 시각화 진단이다. 특정 모델 의존성은 optional extra로 격리한다.

검증: 보행자·차량 제거율, 정적 차량/건물의 오제거율, mask가 없는 데이터의 회귀를
측정한다.

완료조건: 사전 합의한 동적 잔상 감소와 정적 표면 보존 기준을 동시에 만족한다.

### 완료 기준선 1C — 통계적 이상점 제거

현재 상태: SciPy/Open3D 선택, radius/statistical 판정, tile+overlap과 사유 통계가
구현됐다. 아래 항목은 새 필터 구현이 아니라 장기 성능 회귀와 backend 동등성 검증이다.

후속 산출물: backend별 대형 benchmark, 타일 밀집도별 peak RSS와 허용 오차 리포트다.

검증: 고립점이 알려진 합성 점군의 정답 테스트, chunk 경계 동일성, 대규모 구간의 peak
RSS 회귀를 확인한다.

완료조건: 현재 합성 경계 테스트에 더해 여러 장비의 holdout에서 얇은 구조 보존과 고립점
감소 기준을 만족하고, 버전 변경 시 결과 차이를 설명할 수 있다.

### 단계 1D — 중첩 색상 평균과 노출 보정

선행조건: 복셀에 기여한 프레임/관측각 정보와 노출 차이가 있는 대표 구간이 필요하다.

산출물: 관측각·깊이·노출 신뢰도 가중 색상 누적, 프레임별 gain 제한, 전후 진단 이미지다.

검증: 같은 표면의 색 분산과 경계 seam을 측정하고 색상 chart 또는 고정 노출 구간에서
색 왜곡을 확인한다.

완료조건: geometry와 포인트 수를 바꾸지 않고 색 분산/seam metric을 개선하며 RGB가
유효 범위에 머문다.

### 단계 2A — Visual Odometry 교체 평가와 전환

선행조건: 단계 0 평가셋, 현재 `OdometryResult` 계약, 저텍스처·회전·정지 실패 구간이
필요하다.

산출물: 기존 SIFT RGB-D 구현과 분리된 estimator 인터페이스, 한 개 이상의 후보 backend,
feature flag와 비교 리포트다. 기본값 변경은 별도 결정으로 남긴다.

검증: 성공률만이 아니라 상대 자세 오차, GPS와 독립된 holdout 오차, 런타임과 실패
fallback을 비교한다.

완료조건: 후보가 사전 수용 기준을 만족하고 기존 estimator를 즉시 복구할 수 있으며,
핵심 궤적 API를 깨지 않는다.

### 단계 2B — GPS 보정 방식 재설계

선행조건: ellipsoid/MSL datum, GNSS 품질·공분산, 카메라 레버암, 시간 오프셋과 독립
기준점이 정리되어야 한다.

산출물: 품질별 robust weighting/outlier gate, 시간 오프셋 추정 또는 보정, 수평·수직을
분리한 residual과 기존 방식 선택 flag이다.

검증: RTK fixed/float/일반 fix와 GNSS 단절 합성 케이스, 측량점 오차, 잘못된 datum을
탐지하는 테스트를 수행한다.

완료조건: holdout 절대 오차가 개선되고 불량 GNSS에서 발산하지 않으며, 이전 GPS 모드를
명시적으로 재현할 수 있다.

### 단계 2C — IMU 추가

선행조건: 센서 축/단위, bias, 중력 방향, 카메라–IMU 외부표정과 hardware/host clock
동기화가 문서화되어야 한다. 불명확한 `imu.csv`를 추정해 사용하지 않는다.

산출물: 버전 있는 IMU 입력 스키마, calibration 도구, preintegration 또는 자세 prior,
IMU 사용 여부/품질 통계와 IMU-free fallback이다.

검증: 정지 구간 bias, 일정 회전 합성 데이터, 시간 오프셋 주입, IMU 누락/손상 회귀와
급회전 holdout을 평가한다.

완료조건: IMU 사용이 명시적으로 opt-in이고 급회전/저텍스처 metric을 개선하며,
IMU-free 현재 경로의 결과와 테스트를 보존한다.

### 단계 3 — 루프 클로저와 전역 일관성

선행조건: 단계 2에서 프레임 간 자세와 공분산이 안정되고 반복 방문 데이터와 negative
place pair가 준비되어야 한다.

산출물: place candidate 검색, RGB-D 기하 검증, robust loop edge, pose graph 재최적화,
잘못된 closure를 추적하는 진단 파일이다.

검증: 합성 loop, 실제 반복 방문, 유사하지만 다른 장소의 false-positive test, closure 전후
절대/상대 drift와 지도 double-wall을 비교한다.

완료조건: false closure 허용치를 넘지 않으면서 loop drift를 줄이고, closure를 끄면 기존
궤적이 재현된다.

### 단계 4A — LAZ 저장

선행조건: 좌표계/원점 metadata와 색상 필드 계약, 선택할 LAS/LAZ 버전과 optional
dependency 검토가 필요하다.

산출물: streaming LAZ writer, ENU 원점/CRS metadata, CLI와 round-trip 검사 도구다.

검증: PLY 대비 좌표·RGB 허용 오차, 점 수 일치, 대용량 chunk 메모리와 CloudCompare
호환을 확인한다.

완료조건: 전체 배열 복제 없이 저장되고 표준 도구에서 위치·색·점 수가 보존된다.

### 단계 4B — Potree octree와 서버

선행조건: 단계 0B 규모 자료, LAZ/PLY 입력 계약, 목표 브라우저와 배포 환경이 필요하다.

산출물: 재현 가능한 octree 변환 명령, LOD metadata, 정적/서버 배포 설정, CORS·range
request·cache 정책과 운영 문서다.

검증: 작은 fixture의 hierarchy/count, 대표 구간의 first-paint 시간, 이동 중 peak memory,
LOD 전환과 재시작/캐시 동작을 측정한다.

완료조건: 현재 단일 BIN보다 큰 자료를 예산 안에서 점진 로드하고 변환 결과를 원본
commit과 manifest로 추적할 수 있다.

### 단계 4C — Cesium 3D Tiles 변환 파이프라인

선행조건: ENU→ECEF 변환, vertical datum, tile bounding volume과 목표 3D Tiles 버전을
설계하고 Potree와 동일한 평가 자료를 준비해야 한다.

산출물: chunk형 point-cloud tileset 변환기, 좌표 metadata, LOD/error 설정, resume 가능한
manifest와 무결성 검사기다.

검증: 원점·bbox·점 수, 지구고정 좌표 정합, tile 누락, first-paint/peak memory와 VWorld/
Cesium 표시 위치를 확인한다.

완료조건: 전체 변환을 중단 후 재개할 수 있고, 누락/중복 점 없이 지도 위치와 LOD 예산을
만족한다.

### 단계 4D — 타일 단위 스트리밍 통합

선행조건: 4B/4C PoC 결과로 주 전달 형식 또는 두 형식의 명확한 용도를 결정해야 한다.

산출물: 공통 dataset manifest, 진행/취소/재시도, 캐시 제한, 관측 영역 우선순위와 서버
관측성 metric이다.

검증: 느린 네트워크, 실패/재시도, 빠른 카메라 이동, cache eviction과 동시 사용자를
재현하는 통합 테스트를 수행한다.

완료조건: 전체 점군을 메모리에 올리지 않고 탐색할 수 있고 실패 tile이 화면 전체 로드를
막지 않으며 운영 metric으로 병목을 확인할 수 있다.

### 단계 5 — TSDF/surfel 및 Mesh 재구성

선행조건: 단계 2/3의 자세 정확도와 depth confidence가 안정되고, 동적 객체 mask 및
메모리/디스크 예산이 있어야 한다.

산출물: chunk형 TSDF 또는 surfel PoC, 선택 근거, mesh/texture export, 해상도·truncation
설정과 품질 리포트다.

검증: 평면/모서리 합성 scene, watertight 여부가 필요한 영역과 열린 도로 scene을 분리해
surface error, hole, ghosting, peak RSS를 평가한다.

완료조건: point cloud보다 유의미한 표면 품질을 보이는 사용 사례가 확인되고, 자세가 나쁜
구간을 자동 표시하거나 제외하며, 원본 PLY 경로를 유지한다.

### 단계 6 — UI 전면 재설계

선행조건: 최종 전달 형식, summary schema, Potree/3D Tiles 선택과 서버 API가 안정되어야
한다. 그전에는 현재 viewer의 최소 호환 수정만 허용한다.

산출물: 사용자 작업 흐름과 wireframe, 대용량 streaming layer, PLY/웹/LOD 통계 패널,
필터·오류·진행 상태, 접근성/모바일 기준과 배포 문서다.

검증: 작은 BIN과 대용량 tile 양쪽의 브라우저 E2E, 느린 네트워크, 키보드 접근성,
WebGL context loss와 메모리 회귀를 확인한다.

완료조건: 사용자가 원본/표시/LOD 수를 혼동하지 않고 로드 실패를 복구할 수 있으며,
지원 브라우저의 성능·접근성 예산을 만족한다.

## 5. 권장 이슈와 PR 분할

1. 평가 schema·fixture·manifest
2. 통제된 전체 데이터 acceptance run
3. Depth confidence, 동적 mask, 이상점, 색상 보정을 각각 독립 PR
4. VO estimator interface와 후보 backend
5. GPS 재설계
6. IMU 스키마·calibration과 opt-in fusion
7. loop candidate, 기하 검증, graph 적용을 단계별 PR
8. LAZ writer
9. Potree PoC와 운영 서버
10. 3D Tiles PoC와 변환 pipeline
11. streaming 통합
12. TSDF/surfel PoC 후 선택한 reconstruction 구현
13. 출력 계약이 고정된 뒤 viewer 재설계

각 PR은 기본 비활성 feature flag, 단위/합성 테스트, 짧은 스모크 명령, metric 전후 비교,
rollback 방법을 포함한다. 정확도 알고리즘, 저장 형식, 서버와 UI를 하나의 PR에 섞지 않는다.

## 6. 단계 진행 결정

각 단계 시작 전 담당자, 데이터 접근 승인, 계산/저장 예산, 수용 metric과 rollback 경로를
이슈에 기록한다. 단계 종료 시 산출물 링크와 검증 결과를 남기고 다음 단계의 선행조건을
충족했는지 검토한다. Potree와 3D Tiles, TSDF와 surfel처럼 대안이 있는 항목은 PoC 결과를
비교한 결정 기록 없이 전체 구현으로 확대하지 않는다.
