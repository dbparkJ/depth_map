# AGENTS.md

## 1. 적용 범위

이 파일은 저장소 전체에 적용한다. 도로 상태 분석 관련 작업은 반드시
`docs/road_condition/AI_AGENT_EXECUTION_PLAN.md`를 먼저 읽고, 그 문서의 단계와 승인 게이트를
따른다. 기존 RGB-D/GNSS 매핑, 후처리, LAS 변환과 새 도로 상태 분석은 서로 다른 제품 경계다.

## 2. 절대 규칙

1. 기존 `rgbd_map/`, `map_rgbd_gps.py`, `postprocess_cloud.py`, `convert_cloud_to_las.py`의
   출력 계약이나 기본값을 도로 상태 기능 때문에 임의로 바꾸지 않는다.
2. 포트홀 분석 입력으로 `ground-only LAS`만 사용하지 않는다. 지면 필터가 실제 함몰을 제거할
   수 있으므로 기본 입력은 `cloud_raw_enu.ply`와 `cloud_raw_metadata.npz`다.
3. 웹과 분석 백엔드를 결합하지 않는다.
   - 분석 코어: `road_condition_core/`
   - HTTP API: `services/road_condition_api/`
   - 웹 시뮬레이터: `services/road_condition_web/`
4. 모든 서비스는 `compose.road-condition.yml`로 함께 실행되어야 한다.
5. 공식 검증 전에는 다음 명칭을 사용하지 않는다.
   - `PCI` 대신 `internal geometry score`
   - `IRI` 대신 `roughness proxy`
   - `침수 예측` 대신 `물고임/저류 screening proxy`
6. 실제 전체 노선 실행 전에 합성 테스트와 짧은 실데이터 smoke test를 먼저 수행한다.
7. 대용량 PLY, RGB, Depth, LAS, NPZ 결과를 Git에 커밋하지 않는다.
8. 사용자 경로를 API에서 직접 열 때는 반드시 설정된 `/workspace` 아래 상대 경로만 허용한다.
9. API 컨테이너의 `/workspace` 마운트는 읽기 전용이어야 한다.
10. 한 단계에 알고리즘, 저장 형식, UI 전면 개편을 섞지 않는다.

## 3. 단계별 진행 프로토콜

AI-agent는 한 번에 하나의 단계만 완료한다. 각 단계에서 다음 순서를 지킨다.

1. 현재 브랜치와 기준 commit SHA를 기록한다.
2. 단계의 선행조건을 확인한다.
3. 문서에 명시된 필수 질문을 사용자에게 제시한다.
4. 사용자가 답하지 않았으나 "기본값으로 진행" 또는 전체 진행을 명시한 경우 문서의 기본값을
   적용하고 결정 기록을 남긴다.
5. 최소 변경으로 구현한다.
6. 정적 검사 → 단위 테스트 → 합성 smoke → 승인된 짧은 실데이터 순서로 검증한다.
7. 산출물, 명령, 테스트 결과, 알려진 제한을 체크포인트 문서와 commit 메시지에 남긴다.
8. `checkpoint/road-condition-stage-XX-<name>` 형태로 되돌릴 수 있는 commit을 만든다.
9. 다음 단계로 넘어가기 전에 사용자에게 아래 형식으로 묻는다.

```text
[단계 XX 완료]
- 구현: ...
- 테스트: ...
- 변경 파일: ...
- 알려진 제한: ...
- 체크포인트 commit: ...

다음 단계에서 결정할 항목:
1. ...
2. ...

A) 권장 기본값으로 진행
B) 값을 지정해 진행
C) 현재 단계 수정
D) 여기서 중단
```

## 4. 브랜치와 커밋

- 기능 브랜치: `feat/road-condition-<stage-or-topic>`
- 문서: `docs: ...`
- 분석 코어: `feat(road-condition-core): ...`
- API: `feat(road-condition-api): ...`
- 웹: `feat(road-condition-web): ...`
- Docker: `build(road-condition): ...`
- 테스트: `test(road-condition): ...`

각 단계 종료 commit은 독립적으로 checkout 가능한 상태여야 하며 테스트가 실패한 상태를
체크포인트로 표시하지 않는다.

## 5. 기본 검증 명령

```bash
python -m compileall -q road_condition_core services/road_condition_api/app
node --check services/road_condition_web/app.js
PYTHONPATH=.:services/road_condition_api pytest -q tests/road_condition
python - <<'PY'
import yaml
with open('compose.road-condition.yml', encoding='utf-8') as stream:
    payload = yaml.safe_load(stream)
assert set(payload['services']) == {'road-condition-api', 'road-condition-web'}
PY
```

Docker가 있는 환경에서는 추가로 실행한다.

```bash
docker compose -f compose.road-condition.yml config
docker compose -f compose.road-condition.yml build
docker compose -f compose.road-condition.yml up -d
./scripts/road_condition_smoke.sh
docker compose -f compose.road-condition.yml down
```

## 6. 데이터와 정확도

실데이터 알고리즘 변경에는 반드시 다음 정보가 있어야 한다.

- 장치와 카메라 모델
- RGB/Depth 해상도와 동기화 방식
- 카메라 높이와 pitch/roll/yaw
- GNSS 안테나와 카메라 사이 lever arm
- 노면까지의 유효 거리 분포
- 평탄 기준면의 Z MAD/RMSE
- 실측 포트홀 깊이·면적·체적
- 실측 좌우 러팅 깊이
- 건조/습윤, 햇빛/그늘 조건

이 값이 없으면 임계값은 실험값으로 명시하고 자동 승인하지 않는다.

## 7. 현재 MVP 경계

현재 MVP는 다음을 제공한다.

- 합성 도로 데모
- 기존 `depth_map` 결과 디렉터리 읽기
- 로컬 도로 좌표 `(s, t)` 표면 격자
- robust quadratic reference surface
- 포트홀, 러팅, 범프 형상 검출
- 구간별 내부 형상 점수
- GeoJSON/JSON/NPZ/HTML 산출물
- FastAPI 파일 기반 작업 큐
- Nginx 정적 웹 시뮬레이터
- Docker Compose

다음은 구현 완료로 간주하지 않는다.

- RGB 크랙 segmentation
- 정식 PCI/IRI
- 실제 배수망을 포함한 침수 모델
- PDF 보고서
- 인증/권한/다중 조직
- Redis/Celery/PostgreSQL 기반 분산 실행
- 전체 청크 stitching과 중복 결함 병합
- `RoadInventory-MMS` 운영 연동
