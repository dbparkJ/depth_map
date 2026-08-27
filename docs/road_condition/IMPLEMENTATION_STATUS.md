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
- [~] 유지보수 score recovery와 강우 계산은 planning/screening proxy다.
- [~] HTML report는 구현했으나 PDF와 결함별 RGB evidence 전이다.
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

- [ ] `road_roi.geojson`
- [ ] lane/shoulder/exclusion zone
- [ ] 10m core + halo tile
- [ ] 청크 경계 defect deduplication
- [ ] route manifest
- [ ] Parquet partition
- [ ] resume와 partial failure

### P1: 추가 형상 분석

- [ ] 맨홀·단차
- [ ] 횡단경사
- [ ] 종단경사
- [ ] 물고임 depression screening
- [ ] calibrated roughness/IRI 비교

### P1: UI와 리포트

- [ ] VWorld/Cesium 지도
- [ ] 위경도 defect GeoJSON
- [ ] RGB evidence
- [ ] 종단 profile
- [ ] low-confidence/recollection layer
- [ ] PDF report
- [ ] before/after survey

### P2: RGB AI

- [ ] road mask
- [ ] crack segmentation
- [ ] BEV multi-frame fusion
- [ ] 길이·폭·면적
- [ ] crack type
- [ ] 패칭·박리·블리딩
- [ ] GPU worker Dockerfile

### P2: 업무 시스템

- [ ] scoring profile YAML
- [ ] 작업자 승인·수정·거절
- [ ] audit log
- [ ] 보수공법 catalog
- [ ] 실제 단가 version
- [ ] 예산 최적화
- [ ] `RoadInventory-MMS` API
- [ ] object storage URI 계약

### P3: 운영

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

## 4. 현재 테스트 결과

로컬 정적·Python 검증 기준:

```text
road-condition tests: 6 passed
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
