FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# gosu: drop from root to the target uid/gid in the entrypoint after fixing
# mount ownership (~1.5 MB, no other system libs needed — all Python deps ship
# manylinux wheels: asyncpg, psycopg[binary], pillow, pillow-heif…).
RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000

# Entrypoint runs as root: chowns /config & /downloads, sets umask, drops to
# PUID:PGID (default 99:100), then exec's the CMD/command below.
ENTRYPOINT ["/entrypoint.sh"]

# Single worker: the in-process iCloud session + APScheduler assume one process.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
