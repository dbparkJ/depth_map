FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY services/road_condition_api/requirements.txt /tmp/requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r /tmp/requirements.txt

COPY road_condition_core /app/road_condition_core
COPY scoring_profiles /app/scoring_profiles
COPY services/road_condition_api/app /app/app

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data /workspace \
    && chown -R appuser:appuser /app /data /workspace

USER appuser
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
