# 도로 상태 분석 플랫폼 구현 상태

기준 브랜치: `feat/road-condition-platform-mvp`

## 1. 완료

### 문서와 운영 규칙

- [x] 저장소 전체 `AGENTS.md`
- [x] 단계별 AI-agent 실행 계획
- [x] 서비스 아키텍처
- [x] Docker Compose 사용자 quickstart
- [x] 완료/미완료 상태 분리

### 분석 코어

- [x] nested config와 unknown-key 검증
- [x] `depth_map` binary XYZ/RGB PLY loader
- [x] `trajectory.json` fused loader
- [x] `/workspace` 상대 경로 guard
- [x] ENU→local road `(s,t)` projection
- [x] road corridor filter
- [x] cell별 dense lower-mode surface
- [x] point count와 position spread
- [x] overlapping robust quadratic reference surface
- [x] residual surface
- [x] 포트홀 component 검출
- [x] 포트홀 최대/P95/평균 깊이, 면적, 체적
- [x] 좌우 wheel-path 러팅
- [x] 범프 검출
- [x] 20m 구간 metric
- [x] internal geometry score
- [x] roughness proxy
- [x] 합성 flat/potholes/rutting/mixed fixture

### 결과 계약

- [x] `summary.json`
- [x] `defects.json`
- [x] `segments.json`
- [x] `defects.local.geojson`
- [x] `defects.enu.geojson`
- [x] `surface_preview.json`
- [x] `surface.npz`
- [x] `report.html`
- [x] report v2 HTML/CSV/manifest와 결함별 geometry evidence
- [x] 별도 Chromium 이미지의 optional PDF renderer

### API

- [x] FastAPI factory
- [x] health/capabilities
- [x] 합성/매핑 작업 생성
- [x] 단일 host background worker
- [x] 파일 기반 job 상태
- [x] 결과 조회
- [x] HTML report 조회
- [x] 유지보수 비용·강우 screening endpoint
- [x] 완료/실패 작업 삭제

### 웹

- [x] Nginx reverse proxy
- [x] 첫 실행 합성 데모
- [x] 분석 파라미터 입력
- [x] 작업 polling
- [x] summary dashboard
- [x] residual heatmap
- [x] 포트홀·러팅·범프 overlay
- [x] 결함 선택과 횡단 profile
- [x] 구간 표
- [x] 유지보수 시나리오
- [x] HTML report 링크

### Docker와 테스트

- [x] API Dockerfile
- [x] web Dockerfile
- [x] Compose
- [x] healthcheck
- [x] read-only workspace mount
- [x] smoke script
- [x] core/API 단위 테스트
- [x] Python compile 검사
- [x] JavaScript syntax 검사
- [x] Compose YAML parse 검사

## 2. 부분 완료 또는 실험 상태

- [~] 실제 `depth_map` 결과 loader는 구현했으나 대표 실데이터 acceptance run 전이다.
- [~] position spread는 raw metadata가 있을 때 사용하지만 full provenance 활용 전이다.
- [~] ENU GeoJSON은 local ENU이며 위경도 GeoJSON 변환 전이다.
- [~] legacy 유지보수 score recovery와 강우 계산은 planning/screening proxy다.
- [~] v2 공법/단가 catalog와 예산 우선순위는 구현했으나 실제 단가·반복 조사 보정 전이다.
- [~] HTML/CSV/PDF와 geometry evidence는 구현했으나 실제 결함별 RGB frame 연결 전이다.
- [~] worker는 파일 기반 단일 host 구조이며 crash recovery queue 전이다.

## 3. 미완료 — 다음 우선순위

### P0: 실데이터 정확도

- [x] 카메라 pose 4×4 matrix optional export와 strict loader
- [x] 장착각·lever arm manifest (`unknown`은 null + manual review)
- [~] frame reprojection feature flag와 pose-quality gating; 원 RGB-D pixel 재구성 전
- [x] 평탄 기준면 거리 band별 Z MAD/RMSE 계산 하네스
- [ ] 실측 포트홀 깊이·면적·체적 비교
- [ ] 실측 좌우 러팅 비교
- [x] noise-scaled experimental threshold recommendation report
- [ ] stage timing과 peak RSS diagnostics

Stage 03 코드 계약과 합성 검증은 완료했지만 장치/장착값, 평탄 실데이터, 포트홀·러팅
ground truth가 아직 `unknown`이다. 따라서 실험 threshold는 자동 승인하지 않으며 Stage 03
실데이터 accuracy acceptance는 보류 상태다.

### P1: 도로 ROI와 전체 노선

- [x] local-ST `road_roi.geojson` strict loader와 안전한 bundle 상대 경로
- [x] lane/shoulder/exclusion zone precedence와 surface filtering
- [x] 결함 lane/zone attribution, 차로별 segment, ROI/unknown coverage
- [x] ROI 부재 시 기존 trajectory corridor fallback
- [x] 10m core + 3m halo tile과 centroid ownership
- [x] 청크 경계 deterministic defect deduplication과 `merged_from`
- [x] route/tile manifest
- [x] tile partition과 route defect/segment Parquet + defect GeoJSON
- [x] input signature resume와 partial failure
- [~] 한 mapping chunk씩 streaming; PLY 자체의 spatial streaming loader는 후속 최적화

### P1: 추가 형상 분석

- [x] opt-in 맨홀·단차 geometry candidate와 독립 실패 격리
- [x] opt-in 횡단경사 reference-surface profile
- [x] opt-in 종단경사와 기존 roughness proxy profile
- [x] opt-in DEM depression 물고임 screening proxy
- [ ] calibrated roughness/IRI 비교

추가 형상 기능은 기본 비활성화이며 threshold는 합성 검증만 마친 experimental 값이다. 맨홀
자산 DB, 배수구 위치, roughness 비교 장비가 없으므로 asset identity, drainage capacity, 표준 IRI를
산출하지 않는다.

### P1: UI와 리포트

- [~] local ENU 지도와 VWorld/Cesium adapter 경계; 외부 key/token·WGS84 설정 전 fallback
- [x] route manifest + 선택 tile JSON 점진 로드, 전체 PLY 브라우저 전송 금지
- [x] 경량 3D evidence와 1×/2×/5× Z 강조
- [x] segment grade/low coverage/confidence/quality layer
- [ ] 위경도 defect GeoJSON
- [~] RGB evidence N/A 표시; 실제 frame 연결은 후속
- [x] 선택 결함 횡·종단 residual profile
- [x] low-confidence/recollection filter
- [x] HTML source-of-truth 기반 optional PDF report
- [~] summary 기반 before/after job 비교; survey 정합은 후속

### P2: RGB AI

- [~] road/depth/pose validity mask 입력 계약; road mask model은 미구현
- [ ] crack segmentation model과 승인 weights
- [~] projected pixel probability의 deterministic BEV 누적 계약
- [~] skeleton/chamfer 기반 길이·폭·면적 candidate; 실측 검증 전
- [ ] crack type
- [ ] 패칭·박리·블리딩
- [x] 별도 worker contract image와 model manifest/weights SHA fail-closed gate
- [x] pixel/instance/length/100m FP/wet/shadow holdout metric 정의 고정
- [ ] 승인된 GPU 환경·모델 adapter와 Compose worker

Stage 09 시작 질문의 라벨 형식, 클래스, 최소 폭, RGB/노면 픽셀 해상도, wet/shadow/night 구성,
GPU 환경은 모두 `unknown`이다. 합성 확률맵은 계약 검증에만 사용하며 정확도 acceptance나 모델
승인을 대신하지 않는다.

### P2: 업무 시스템

- [x] version/hash/승인 상태를 갖는 internal scoring profile YAML
- [x] 완료 job 작업자 승인·심각도 수정·거절·재수집 UI/API
- [x] raw prediction 불변 SHA와 before/after/version audit bundle
- [~] actor는 인증 신원이 아닌 audit label; 관리자 2단계 승인 전
- [x] experimental 보수공법 catalog와 최소 작업 단위
- [~] 기존 planning 예시 단가 version; 실제 승인 단가는 미제공
- [~] 결정적 예산 risk screening; 수학적 최적화는 미구현
- [~] 전후 점수 planning estimate; 실제 효과 예측/열화율은 미보정
- [~] `RoadInventory-MMS` polling ingress contract; 인증/실행 connector 전에는 기본 비활성화
- [x] URI-only request, version mismatch, idempotency replay/conflict 계약
- [ ] object storage 실제 connector와 credential
- [ ] callback 서명/재시도 또는 polling 운영 방식 승인
- [ ] reviewed defect source-of-truth와 동기화 방향 합의

### P3: 운영

- [~] Stage 13 전환 조건/운영 필수/수용 기준 readiness matrix; gate 미충족으로 보류
- [ ] PostgreSQL
- [ ] Redis/RabbitMQ
- [ ] 분산 worker
- [ ] 인증/조직 권한
- [ ] quota/rate limit
- [ ] TLS
- [ ] observability
- [ ] backup/restore
- [ ] load test
- [ ] release benchmark

### P4: v1 릴리스 gate

- [x] versioned readiness YAML과 fail-closed machine-readable gate
- [x] dataset/metric/prohibition/artifact/approval 누락 blocker
- [~] synthetic regression/Docker smoke만 통과; real holdout/전체 노선 benchmark 전
- [ ] v1 release 승인, tag, manifest, SBOM, runbook

## 4. 현재 테스트 결과

로컬 정적·Python 검증 기준:

```text
road-condition tests: 53 passed
Python compileall: passed
JavaScript node --check: passed
Compose YAML parsing: passed
Docker image build: 실행 환경에 Docker daemon이 없으면 미검증으로 남김
```

Docker가 있는 환경에서는 아래를 실행하고 이 문서를 갱신한다.

```bash
docker compose -f compose.road-condition.yml config
docker compose -f compose.road-condition.yml build
docker compose -f compose.road-condition.yml up -d
./scripts/road_condition_smoke.sh
docker compose -f compose.road-condition.yml down
```

## 5. 다음 승인 게이트

다음 단계는 **Stage 03: 실데이터 보정과 camera pose 계약**이다. 시작 전에 아래 값이 필요하다.

```text
camera_model:
rgb_resolution:
depth_resolution:
fps:
camera_height_m:
mount_yaw_deg:
mount_pitch_deg:
mount_roll_deg:
camera_offset_right_m:
camera_offset_down_m:
camera_offset_forward_m:
flat_reference_dataset_path:
pothole_ground_truth_available:
rut_ground_truth_available:
first_allowed_smoke_range:
```

값을 아직 모르면 `unknown`으로 기록하고, 먼저 측정 절차와 calibration worksheet를 구현한다.
