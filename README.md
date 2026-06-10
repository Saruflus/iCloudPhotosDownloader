# iCloud → NAS Sync

Self-hosted web app that syncs your **iCloud Photos** to a NAS, tracking every
asset by its **iCloud asset ID** in Postgres — so you can freely move, rename or
reorganize files on disk and they will **never be re-downloaded**.

![CI](https://github.com/Saruflus/icloud-nas-sync/actions/workflows/ci.yml/badge.svg)

---

## Features

- 🔐 **iCloud login with 2FA**; the trusted session is reused — workers and the
  scheduler restore it **passwordless**, so unattended syncs keep running.
- 🗂️ **Browse albums** with thumbnails; select whole albums or individual photos.
- 🎚️ **Filters** (JPEG / HEIC / Video / RAW) and **version** choice — *edited*,
  *original*, or *both*.
- 🧩 **Folder-template builder** — e.g. `{year}/{month}/{album}` — with a live
  path preview. Tokens: year, month, day, album, person, mediatype, make, model,
  filename + free text.
- ♻️ **Asset-ID tracking** — the source of truth is the iCloud asset ID, not the
  file path, so moving/renaming files never triggers a re-download.
- 📺 **Live job progress** over WebSocket (progress bar, current file, log, cancel).
- ⏰ **Cron scheduling**, a **CLI** (`auth` / `sync` / `status`), fully usable headless.
- 🖼️ **Live Photos** (paired video) and **edited renditions** (`resJPEGFull`) handled.

## Stack

FastAPI · Celery · Postgres · Redis · [pyicloud](https://pypi.org/project/pyicloud/) ·
React + Vite + TypeScript + Tailwind · Docker Compose

## Screenshots

_To add: Browser (3-panel), Jobs (live progress)._

---

## Quick start (Docker)

> **Apple ADP (Advanced Data Protection) must be DISABLED** — pyicloud can't read
> photos otherwise. Postgres & Redis are expected to already exist.

```bash
# 1. create a dedicated database on your existing Postgres
psql ... -c "CREATE DATABASE icloud_sync;"

# 2. configure + build
cp .env.example .env            # set DATABASE_URL, REDIS_URL, LOCAL_TIMEZONE
docker compose build

# 3. create the schema + authenticate (interactive 2FA)
docker compose run --rm backend alembic upgrade head
docker compose run --rm -it backend python -m app.cli auth

# 4. start everything
docker compose up -d
```

- Web UI → `http://<host>:3000`
- API / docs → `http://<host>:8000/docs`

### Headless (no UI)

```bash
docker exec -it icloud-sync-backend python -m app.cli sync --album "Holidays"
docker exec -it icloud-sync-backend python -m app.cli status
```

---

## Architecture

```
┌────────────┐   HTTP/WS    ┌──────────────┐
│  Frontend  │ ───────────► │   Backend    │  FastAPI (async)
│  (nginx)   │              │  /api, /ws   │  ── wraps pyicloud (ICloudService)
└────────────┘              └──────┬───────┘
                                   │ enqueue
                            ┌──────▼───────┐   Redis pub/sub → WS progress
                            │    Celery    │   downloads, atomic writes,
                            │   worker     │   EXIF, folder templates
                            └──────┬───────┘
                            ┌──────▼───────┐
                            │  Scheduler   │   APScheduler (cron)
                            └──────────────┘
        Postgres (asset-ID tracking)   ·   Redis (lock, pub/sub, thumb cache)
```

Full design & decision log: [`icloud-nas-sync-plan.md`](icloud-nas-sync-plan.md).
Planned improvements: [`ROADMAP.md`](ROADMAP.md).

---

## Notes for Unraid (hard-won 😅)

- **Mount the pool path directly** (`/mnt/cache/<share>/…`) in the containers,
  **never `/mnt/user/…`** — bind-mounting the shfs/FUSE layer into Docker breaks
  SMB file access.
- Run backend/celery/scheduler as **`user: "99:100"`** (nobody:users) and the
  worker with **`umask 0002`** so downloaded files are owner+group writable and
  movable/editable over SMB.
- Use a **stable DB/Redis address** (the NAS LAN IP + published port, or a
  user-defined network with container names) — the `172.17.x` bridge IPs change
  on reboot.
- Some NAS filesystems **fold filenames to lowercase**; this project keeps all
  source filenames lowercase to stay compatible.

---

## Development

```bash
cd backend
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
# full test suite — no DB / Redis / iCloud account needed (everything is faked):
for t in tests/test_*.py; do PYTHONPATH=. .venv/bin/python "$t"; done

cd ../frontend
npm install && npm run build
```

## Security

**LAN only.** The app holds a live, full-access iCloud session — do not expose it
beyond a trusted network. The Apple password is never stored (only the trusted
session cookie, in the `icloud-config` volume — treat it as a secret). Optionally
set `API_SHARED_SECRET` to require an `X-Sync-Secret` header. `.env` and the
session directory are git-ignored.

## License

[MIT](LICENSE)
