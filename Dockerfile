# ---- Stage 1: Build ----
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Build deps cho một số wheel (prophet/cmdstanpy, psycopg2)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# Cài vào venv để copy sang runtime image chạy non-root
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# ---- Stage 2: Runtime ----
FROM python:3.11-slim

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# libpq cho psycopg2, curl cho healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 appuser

COPY --from=builder /opt/venv /opt/venv

COPY . .

RUN mkdir -p /app/data /app/uploads && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# Entrypoint chạy migration (nếu RUN_MIGRATIONS=true) rồi exec command bên dưới
ENTRYPOINT ["/app/docker/entrypoint.sh"]

# Mặc định = API + WebSocket. Worker/scheduler override command trong compose.
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
