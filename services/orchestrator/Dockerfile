# Orchestrator API image (simulation-only).
FROM python:3.12-slim

WORKDIR /app

COPY services/orchestrator/pyproject.toml services/orchestrator/README* ./services/orchestrator/
COPY services/orchestrator/src ./services/orchestrator/src
RUN pip install --no-cache-dir ./services/orchestrator fastapi==0.115.* uvicorn==0.32.*

ENV VOXORA_DATABASE_PATH=/data/voxora.db \
    VOXORA_ALLOW_LIVE_CALLS=false
VOLUME /data
EXPOSE 8000

CMD ["python", "-m", "uvicorn", "voxora.api:create_app_from_env", "--factory", "--host", "0.0.0.0", "--port", "8000"]
