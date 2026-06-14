# Intelligent LLM Gateway — production-shaped single container.
# Runs on Render, Railway, Fly.io, Hugging Face Spaces (Docker), Cloud Run, k8s.
FROM python:3.12-slim AS base

# fail fast, no .pyc, unbuffered logs (12-factor friendly)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000 \
    GATEWAY_AUTODEMO=1 \
    GATEWAY_SIM_LATENCY_SCALE=0.12

WORKDIR /app

# deps first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# app code
COPY . .

# run as an unprivileged user (don't ship root in your containers)
RUN useradd --create-home --uid 10001 appuser && chown -R appuser /app
USER appuser

EXPOSE 8000

# container-native liveness check (Render/Fly/k8s read this; humans read /metrics)
HEALTHCHECK --interval=30s --timeout=4s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,os,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8000')+'/health',timeout=3).status==200 else 1)"

# bind 0.0.0.0 and honour the platform-provided $PORT
CMD ["sh", "-c", "uvicorn gateway.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
