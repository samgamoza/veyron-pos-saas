# Veyron POS — production image.
# Plain Flask/gunicorn; portable to Proxmox (docker host), Render, Fly, Railway,
# Cloud Run, ECS, or any Linux VM.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WEB_CONCURRENCY=3

WORKDIR /app

# curl is used by the container healthcheck.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

# Install deps first so layer caching survives app-code edits.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run unprivileged. /data holds product images and backups (mount a volume here).
RUN useradd --create-home --uid 10001 veyron \
 && mkdir -p /data/backups /data/images/products \
 && chown -R veyron:veyron /app /data
USER veyron

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

# --preload creates the app (and runs init_db) ONCE in the master process before
# forking, avoiding concurrent first-boot schema creation across workers.
CMD ["sh", "-c", "gunicorn wsgi:app -b 0.0.0.0:8000 --workers ${WEB_CONCURRENCY:-3} --preload --access-logfile - --error-logfile -"]
