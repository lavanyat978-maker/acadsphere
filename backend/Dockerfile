FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    STORAGE_LOCAL_PATH=/data/storage \
    DATABASE_URL=sqlite:////data/app.db

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd --system --uid 10001 --home-dir /app praxis \
    && mkdir -p /data/storage \
    && chown -R praxis:praxis /app /data

USER praxis

EXPOSE 8000

HEALTHCHECK --interval=20s --timeout=4s --start-period=15s --retries=5 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

# No --proxy-headers: uvicorn would trust any X-Forwarded-For. The app reads the
# header itself and honours only TRUSTED_PROXY_COUNT hops (see core/runtime.py).
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-proxy-headers"]
