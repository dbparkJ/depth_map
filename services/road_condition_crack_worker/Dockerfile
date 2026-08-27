FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app \
    ROAD_CONDITION_WORKSPACE_ROOT=/workspace

WORKDIR /app
COPY services/road_condition_crack_worker/requirements.txt /tmp/requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r /tmp/requirements.txt

COPY services/road_condition_crack_worker/app /app/app
COPY services/road_condition_crack_worker/config /app/config

RUN useradd --create-home --uid 10001 crackworker \
    && mkdir -p /workspace \
    && chown -R crackworker:crackworker /app

USER crackworker
ENTRYPOINT ["python", "-m", "app.main"]
CMD ["capabilities"]
