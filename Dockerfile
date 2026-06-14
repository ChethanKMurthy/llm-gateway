# Intelligent LLM Gateway — single always-on container.
# Works on Render, Railway, Fly.io, Hugging Face Spaces (Docker), Cloud Run, etc.
FROM python:3.12-slim

WORKDIR /app

# deps first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# app
COPY . .

# self-drive the dashboard for visitors; simulated mode by default (no keys = safe & free).
ENV GATEWAY_AUTODEMO=1 \
    GATEWAY_SIM_LATENCY_SCALE=0.12 \
    PORT=8000

EXPOSE 8000

# bind to 0.0.0.0 and honor the platform-provided $PORT
CMD ["sh", "-c", "uvicorn gateway.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
