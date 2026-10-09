# AI Value Auditor: one container (FastAPI + static UI). Runs on EC2 or ECS Fargate.
# Data: either build with ./data present (it is copied in), or set AUDITOR_DATA_S3_URI at runtime.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8080 AUDITOR_STATE_DIR=/app/state

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

# .dockerignore keeps secrets, the REPH brief/zip and local state out of the image
COPY . .
RUN useradd --create-home --uid 10001 app && mkdir -p /app/state /app/data && chown -R app /app
USER app

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=4)"

# One worker on purpose: audit jobs and their progress live in process memory.
CMD ["sh", "-c", "python scripts/fetch_data.py && exec uvicorn web.server:app --host 0.0.0.0 --port ${PORT} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
