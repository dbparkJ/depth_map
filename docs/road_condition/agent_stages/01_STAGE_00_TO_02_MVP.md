# Stage 00 — 기준선, 용어, 결과 계약

## 목표

도로 상태 기능이 기존 mapping과 별도임을 고정하고, 산출물 이름과 정확도 표현을 먼저
버전화한다.

## 사용자에게 물을 질문

1. 분석 결과를 처음 사용할 대상은 내부 연구, 현장 작업자, 지자체 보고 중 무엇인가?
2. 기본 구간 길이는 10m, 20m, 100m 중 무엇인가?
3. 결과 좌표는 local ENU만 필요한가, 위경도 GIS까지 바로 필요한가?
4. 공식 PCI/IRI 요구가 있는가, 내부 상대평가로 시작해도 되는가?

## 권장 기본값

```text
사용 대상: 내부 연구 + 현장 검수
구간 길이: 20m
좌표: local ST + local ENU, 위경도는 후속
점수: internal geometry score
```

## 구현 작업

- `AGENTS.md` 추가
- `docs/road_condition/ARCHITECTURE.md` 추가
- `docs/road_condition/IMPLEMENTATION_STATUS.md` 추가
- 결과 contract format version을 1로 고정
- defect type 초기 enum: `pothole`, `rutting`, `bump`
- metric 단위는 필드명에 `_m`, `_m2`, `_m3`, `_mm`, `_krw`를 포함
- 알 수 없는 config 키는 오류 처리

## 수용 기준

- 용어 제한이 문서와 API capabilities에 모두 표시된다.
- 모든 JSON이 `allow_nan=False`로 기록된다.
- defect와 segment 결과가 동일 입력에서 결정적이다.
- 기존 `rgbd_map` 파일은 수정하지 않는다.

## 검증

```bash
python - <<'PY'
from road_condition_core.config import AnalysisConfig
AnalysisConfig().validate()
try:
    AnalysisConfig.from_overrides({'surface': {'typo': 1}})
except ValueError:
    pass
else:
    raise SystemExit('unknown key was accepted')
PY
```

## 완료 후 질문

```text
Stage 00의 결과 계약과 명칭을 승인합니까?
A) 권장 기본값 승인
B) 구간 길이/용어 수정
C) GIS 위경도 출력을 Stage 03으로 당김
```

---

# Stage 01 — 웹/API 분리와 Docker Compose

## 목표

사용자가 clone 후 한 개의 Compose 명령으로 실행할 수 있게 한다.

## 사용자에게 물을 질문

1. 기본 웹 포트 8080과 API 포트 8081을 사용해도 되는가?
2. 최초 실행에서 합성 데모를 자동 생성할 것인가?
3. 실제 결과 디렉터리를 read-only로 마운트하는 방식에 동의하는가?
4. 최초 MVP에 PostgreSQL/Redis가 꼭 필요한가?

## 권장 기본값

```text
web: 8080
api: 8081
자동 데모: 사용
workspace: read-only bind mount
DB/Redis: 사용하지 않음
worker: 1
```

## 파일

```text
compose.road-condition.yml
.env.road-condition.example
.dockerignore
services/road_condition_api/Dockerfile
services/road_condition_api/requirements.txt
services/road_condition_web/Dockerfile
services/road_condition_web/nginx.conf
scripts/road_condition_smoke.sh
```

## 구현 세부사항

- API와 웹 이미지는 별도 Dockerfile을 사용한다.
- 웹은 `/api/`를 Docker DNS 이름 `road-condition-api:8000`으로 proxy한다.
- API healthcheck 성공 후 웹을 시작한다.
- `/data`는 named volume이다.
- `/workspace`는 `${ROAD_CONDITION_WORKSPACE}` host bind와 `:ro`다.
- API의 CORS는 직접 API 포트 개발 접근만 허용하고 웹은 same-origin proxy를 사용한다.
- 대용량 입력을 HTTP multipart로 업로드하지 않는다.
- Docker가 없는 환경에서는 YAML parse와 정적 검사를 수행하고 Docker build 미실행 사유를
  완료 보고에 명시한다.

## 검증

```bash
docker compose -f compose.road-condition.yml config
docker compose -f compose.road-condition.yml build
docker compose -f compose.road-condition.yml up -d
./scripts/road_condition_smoke.sh
docker compose -f compose.road-condition.yml down
```

## 수용 기준

- 빈 저장소 clone 상태에서 데모가 열린다.
- 웹 8080, API docs 8081에서 접근된다.
- 웹 컨테이너 안에 분석 Python 코드가 없다.
- API 컨테이너 안에 Nginx UI build가 없다.
- workspace 이탈 경로가 실패한다.
- compose down/up 후 named volume 작업 목록이 유지된다.

## 롤백

```bash
git revert <stage-01-checkpoint-sha>
docker compose -f compose.road-condition.yml down -v
```

## 완료 후 질문

```text
Stage 01 컨테이너 구조를 승인합니까?
A) 현재 구조 승인
B) 포트 변경
C) PostgreSQL/Redis를 지금 추가
D) 웹 프레임워크를 React/Vue로 변경
```

DB와 프레임워크 변경은 특별한 이유가 없으면 후속으로 미룬다.

---

# Stage 02 — 합성 fixture와 형상 분석 MVP

## 목표

실데이터가 없어도 설치와 알고리즘 결과를 검증할 수 있는 결정적 합성 도로와 기본 검출기를
구현한다.

## 사용자에게 물을 질문

1. 초기 최우선 결함은 포트홀, 러팅, 범프 순서로 맞는가?
2. 합성 도로 길이는 기본 60m로 충분한가?
3. 최초 grid는 10cm로 시작하고 실데이터 보정 후 줄여도 되는가?

## 권장 기본값

```text
결함: pothole + rutting + bump
합성 길이: 60m
grid: 0.10m
corridor half width: 3.5m
```

## 구현 세부사항

### 합성 도로

- 종단구배
- 횡단경사
- 약한 crown
- 2개 포트홀
- 좌우 wheel-path 러팅
- 1개 범프
- 4회 관측과 Gaussian noise
- 고정 seed

### 표면 생성

1. ENU point를 fused trajectory에 투영한다.
2. along-track `s`, signed cross-track `t`, trajectory-relative local-up을 계산한다.
3. corridor 밖과 높이 후보 범위 밖을 제외한다.
4. grid cell별 dense lower mode를 사용한다.
5. point count와 position spread를 저장한다.
6. 겹치는 12m robust quadratic tile로 reference surface를 생성한다.
7. `residual = observed - reference`를 계산한다.

### 포트홀

- residual이 음수 threshold 아래인 cell
- morphology
- connected component
- 최소 면적
- 최대/P95/평균 깊이
- 면적/체적
- convex hull polygon

### 러팅

- 좌우 wheel band
- longitudinal median filtering
- 최소 깊이와 길이
- 짧은 gap closing
- 좌우 구분

### 범프

- positive residual component
- 높이, 면적, positive volume

## 합성 수용 기준

```text
mixed fixture pothole count >= 2
max pothole depth >= 0.08m
rutting count >= 1
max rut depth >= 0.025m
bump count >= 1
valid coverage >= 95%
flat fixture pothole false positive = 0
flat fixture bump false positive = 0
```

## 검증

```bash
PYTHONPATH=.:services/road_condition_api pytest -q tests/road_condition/test_pipeline.py
```

## 완료 후 질문

```text
Stage 02 geometry MVP를 승인합니까?
A) 승인하고 실데이터 보정으로 진행
B) 포트홀 threshold 변경
C) 합성 truth 오차 metric 추가 후 진행
D) 단차/횡단경사를 먼저 추가
```
