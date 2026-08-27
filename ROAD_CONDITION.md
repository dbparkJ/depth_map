# Depth Map Road Condition Platform

`depth_map` 결과에서 포트홀·러팅·범프를 분석하고 웹에서 확인하는 별도 서비스입니다.
기존 RGB-D/GNSS 매핑 코드는 그대로 유지하며 분석 API와 웹을 Docker Compose로 분리합니다.

## 실행

```bash
docker compose -f compose.road-condition.yml up --build
```

- 웹: `http://127.0.0.1:8080`
- API 문서: `http://127.0.0.1:8081/docs`

데이터 없이도 첫 화면에서 합성 도로 데모가 자동 실행됩니다.

## 실제 결과 연결

```bash
cp .env.road-condition.example .env.road-condition
```

`.env.road-condition`:

```dotenv
ROAD_CONDITION_WORKSPACE=/depth_map/결과가_있는_호스트_디렉터리
```

```bash
docker compose \
  --env-file .env.road-condition \
  -f compose.road-condition.yml \
  up --build
```

웹에는 workspace 기준 상대 경로를 입력합니다.

## 문서

- 사용자 실행: `docs/road_condition/USER_QUICKSTART.md`
- 아키텍처: `docs/road_condition/ARCHITECTURE.md`
- AI-agent 단계별 지침: `docs/road_condition/AI_AGENT_EXECUTION_PLAN.md`
- 완료·미완료 상태: `docs/road_condition/IMPLEMENTATION_STATUS.md`

## 정확도 주의

현재 버전은 geometry-first MVP입니다. 내부 형상 점수와 roughness proxy를 제공하며, 공식 PCI
또는 IRI로 보고하면 안 됩니다. 실제 적용 전 평탄 기준면, 실측 포트홀, 실측 러팅 자료로
보정해야 합니다.
