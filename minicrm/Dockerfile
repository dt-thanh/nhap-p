# Image RIÊNG của Mini CRM. Không dùng chung image với backend, và đó là chủ đích:
# dùng chung nghĩa là mọi phụ thuộc của backend (prophet, langgraph, rq) đi theo
# vào một ứng dụng không cần chúng, và hai vòng đời triển khai dính vào nhau.
FROM python:3.11-slim AS builder

USER root

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# ---- Runtime ----
FROM python:3.11-slim

USER root

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONPATH= \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 curl \
    && rm -rf /var/lib/apt/lists/* \
    && (id -u crmuser >/dev/null 2>&1 || useradd -m -u 1000 crmuser)

COPY --from=builder /opt/venv /opt/venv
COPY . .

RUN sed -i 's/\r$//' /app/entrypoint.sh \
    && chmod +x /app/entrypoint.sh \
    && chown -R crmuser /app
USER crmuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
