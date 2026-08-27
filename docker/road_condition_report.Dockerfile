FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN python -m pip install --upgrade pip \
    && python -m pip install "numpy>=2,<3" "Pillow>=10,<13"

COPY road_condition_core/report_v2.py /app/road_condition_core/report_v2.py
COPY scripts/road_condition_report.py /app/scripts/road_condition_report.py

RUN useradd --create-home --uid 10001 reportuser \
    && mkdir -p /input /output \
    && chown -R reportuser:reportuser /app /output

USER reportuser
ENTRYPOINT ["python", "/app/scripts/road_condition_report.py"]
CMD ["--help"]
