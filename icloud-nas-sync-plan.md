# iCloud → NAS Sync — Full Project Plan

## Context

Build a self-hosted web app that syncs iCloud photos/videos to a NAS (Unraid),
tracking already-downloaded assets by their **iCloud asset ID** in Postgres —
so files can be freely moved on disk without ever triggering re-downloads.

---

## Key Decisions (read this first)

The rest of this document assumes these.

| # | Decision | Choice |
|---|---|---|
| D1 | **Async/sync split** | FastAPI = **async** SQLAlchemy (asyncpg). Celery worker = **sync** SQLAlchemy (psycopg). Two engine factories. All `pyicloud` calls (it is blocking `requests`) are wrapped in `asyncio.to_thread` on the FastAPI side. |
| D2 | **iCloud session storage** | **Single mechanism: the shared `/config` volume** (`pyicloud` cookie directory), mounted into backend, worker, and scheduler. Redis is **not** used for the session — only for pub/sub, locks, and the thumbnail cache. |
| D3 | **Download concurrency** | Bounded parallelism, configurable via `DOWNLOAD_CONCURRENCY` (default `4`). Not unbounded (Apple throttling), not serial (too slow for a full-library first sync). |
| D4 | **Job lock** | **Lease pattern**: short TTL (`60s`) + periodic heartbeat renewal while running, released on exit. |
| D5 | **EXIF extraction** | `pillow-heif` for HEIC, `piexif` for JPEG/TIFF, `exifread` (or `exiftool` if available) for RAW. Prefer pyicloud-provided metadata for dates; read file EXIF only when needed for `{make}`/`{model}`. |
| D6 | **Live Photos / multi-file assets & version** | One `asset_id` can map to **multiple files** (Live Photo image + paired video; and/or edited + original). Modeled via a `files` JSON list on the asset row. `download_version` is a job option — **default `edited`**, or `original`, or `both`. pyicloud's version map omits the edited rendition, so `ICloudService` reads the adjusted full-res field (`resJPEGFull`) from the raw CloudKit record when edits exist, falling back to `original` otherwise — exact field/has-edits signal confirmed via the step-2 spike. When a photo has no adjustments, edited == original, so `both` writes one file (de-duplicated). |
| D7 | **Filename collisions** | The resolver detects target-path collisions and disambiguates with a short asset-id suffix. Silent overwrite is never allowed. |
| D8 | **`{album}` when asset is in N albums** | **Default: one copy per album** (`album_fanout=true`). When a template uses `{album}` and an asset belongs to several albums it is written once per album — the UI **warns** that this multiplies storage. Can be turned off (`album_fanout=false`) to fall back to the first album only. |
| D9 | **Atomic writes** | Stream to a `*.part` temp file, `fsync`, then atomic rename to the final name. On startup, reset any `downloading` rows. |
| D10 | **Scheduler placement** | APScheduler runs in a **dedicated single-instance `scheduler` service** (its own container), not inside multi-worker FastAPI. Avoids duplicate triggers. |
| D11 | **Migrations** | **Alembic**, into a **dedicated `icloud_sync` database**. No blind `create_all` into a shared Postgres. |
| D12 | **iCloud engine** | **Original `pyicloud` library** (modern 2FA: `requires_2fa` / `validate_2fa_code` / `trust_session`), behind `app/services/icloud.py`, persisting the trusted session to the shared `/config` cookie dir. Chosen because current `icloudpd` ships as a CLI-only **binary** (not importable) and `pyicloud-ipd` is frozen at a stale 2SA-era release with dependency conflicts. The `icloudpd` binary is kept as a documented **shell-out fallback** for bulk re-syncs. Everything stays behind the `ICloudService` interface, so the engine remains swappable. See "iCloud engine" below. |
| D13 | **Scale & resilience (10–20k files)** | First full sync is large (10–20k assets) and may run for hours. The job is **resumable** (asset-ID-as-truth → re-running skips `completed`), counters persist per asset, the lease lock (D4) survives long runs via heartbeat, and transient/throttle errors retry with exponential backoff. The set of already-`completed` asset IDs in scope is loaded once at job start to avoid a DB round-trip per asset. |

---

## Environment

- **NAS**: Minisforum N5 running Unraid
- **Photos share**: `/mnt/user/photos` (mounted into the container as `/downloads`)
- **Existing Postgres**: connect via `DATABASE_URL` env var (do NOT spin up a new container). Use a **dedicated database** (`icloud_sync`), not shared tables.
- **Existing Redis**: connect via `REDIS_URL` env var (do NOT spin up a new container)
- **Single user** — no UI authentication (local network only — see Security)
- **Apple ADP**: must be **disabled** on the iCloud account for pyicloud to work

### iCloud engine (decided: original pyicloud library)

We investigated wrapping `icloudpd` (the user's stated preference) and found it no longer works
as a library:

- **`icloudpd` (current, 1.32.x)** ships as a CLI-only **compiled binary** — the PyPI package is
  a shim that subprocess-calls the binary. No importable `PyiCloudService`.
- **`pyicloud-ipd`** (the library icloudpd used to expose) is frozen at **0.10.2**: old **2SA**
  auth (not modern 2FA), pinned-old `certifi`/`keyring`/`tzlocal` that conflict with current deps.
- **Original `pyicloud`** is importable and exposes the **modern 2FA** flow
  (`requires_2fa` / `validate_2fa_code` / `trust_session` / `is_trusted_session`), and integrates
  cleanly with our DB tracking, folder templating, and per-asset WS progress.

**Decision (D12):** use original `pyicloud` behind `ICloudService`; keep the `icloudpd` binary as a
documented shell-out **fallback** for bulk re-syncs if pyicloud auth ever breaks.

**Verified version keys** (`PHOTO_VERSION_LOOKUP`):
`original→resOriginal`, `alternative→resOriginalAlt` (RAW half of a RAW+JPEG pair),
`medium→resJPEGMed`, `thumb→resJPEGThumb`, `original_video→resOriginalVidCompl`,
`sidecar→resSidecar` (Live Photo video). `download(version='original')` returns bytes.

**Edited renditions (D6) — CONFIRMED via the step-2 spike against a real edited photo:**
- The edited full-res rendition is **`resJPEGFull`**, and it lives in the **asset record**
  (`PhotoAsset._asset_record`), *not* the master record. The master only ever holds
  `resOriginal` / `resJPEGMed` / `resJPEGThumb`.
- An asset **is edited iff `resJPEGFullRes` is present in the asset record**
  (`_asset_record.fields.get_value("resJPEGFullRes") is not None`). The `adjustment*` /
  `fullSizeJPEGSource` fields are **not** reliable signals — they appear on unedited assets too.
- `ICloudService` builds the edited resource by calling pyicloud's own
  `build_photo_resource(prefix="resJPEGFull", master_record=photo._asset_record, …)` and streams
  `resource.url` via `api.photos.session`. `original` uses `photo.download_url("original")`
  (master). Live Photo video = `original_video`/`sidecar` (master).
- So `download_version="edited"` → resJPEGFull if edited else resOriginal; `"both"` → both when
  the asset is edited; `"original"` → resOriginal.
- **Format (verified):** the edited rendition keeps the photo's **native container** — a HEIC
  photo's edit comes back as **HEIC, not JPEG** despite the `resJPEGFull` name. Derive each
  file's on-disk extension from its rendition `*FileType`, never assume `.jpg`. For `"both"`,
  the edited file takes an `_edited` suffix (original keeps the plain name) since both share the
  extension. (Confirmed end-to-end: original 3.40 MB vs edited 2.38 MB HEIC for the same asset.)

**Gate status: PASSED.** Auth (cached trusted session, no re-2FA), album listing, dual-record
field access, edited-rendition detection, and thumbnail download all verified against the live
account. Build proceeds to `ICloudService`.

---

## Stack

| Layer | Choice | Notes |
|---|---|---|
| Backend | FastAPI (Python 3.12) | |
| Task queue | Celery + Redis | Worker pool: prefork; DB access is **sync** here (D1) |
| Database | Postgres | **async** (`asyncpg`) in FastAPI, **sync** (`psycopg`) in Celery (D1) |
| Migrations | Alembic | dedicated `icloud_sync` DB (D11) |
| iCloud client | `pyicloud` (original, modern 2FA) | importable; `icloudpd` binary kept as optional shell-out fallback. Isolated behind `ICloudService` (D12) |
| EXIF | `pillow-heif` + `piexif` + `exifread` | format-aware (D5) |
| Scheduler | APScheduler (`AsyncIOScheduler`) | dedicated single-instance service (D10) |
| Real-time | WebSockets (FastAPI) + Redis pub/sub + Redis list for replay | log replay (see WS section) |
| Frontend | React + TypeScript + Vite + TailwindCSS | served by nginx in container |
| Deployment | docker-compose — **4 services**: backend, celery worker, scheduler, frontend | |

---

## Project Structure

```
icloud-nas-sync/
├── docker-compose.yml
├── .env                      # never committed
├── .env.example
│
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── alembic.ini
│   ├── alembic/              # migration scripts
│   └── app/
│       ├── main.py
│       ├── scheduler_main.py # entrypoint for the dedicated scheduler service (D10)
│       ├── cli.py
│       ├── core/
│       │   ├── config.py
│       │   ├── database.py   # exposes BOTH async + sync engine factories (D1)
│       │   ├── redis.py      # pub/sub, locks, thumbnail cache (no session) (D2)
│       │   ├── locks.py      # lease lock w/ heartbeat (D4)
│       │   └── paths.py      # path resolver + sanitizer + collision handling (D7,D8)
│       ├── models/
│       │   └── assets.py
│       ├── services/
│       │   ├── icloud.py     # only place that touches pyicloud (D12)
│       │   ├── exif.py       # format-aware EXIF (D5)
│       │   ├── downloader.py
│       │   └── scheduler.py
│       ├── workers/
│       │   └── tasks.py
│       └── api/
│           ├── auth.py
│           ├── albums.py
│           ├── jobs.py
│           ├── schedule.py
│           ├── tokens.py
│           └── ws.py
│
└── frontend/
    ├── Dockerfile
    ├── nginx.conf            # serves SPA + proxies /api and /ws to backend
    └── src/
        ├── App.tsx
        ├── config.ts         # API/WS base URL (env-injected)
        ├── pages/
        │   ├── Auth.tsx
        │   ├── Browser.tsx
        │   ├── Jobs.tsx
        │   └── Schedule.tsx
        ├── components/
        │   ├── AlbumTree.tsx
        │   ├── AssetGrid.tsx
        │   ├── FolderBuilder.tsx
        │   ├── FilterBar.tsx
        │   ├── ProgressPanel.tsx
        │   └── ScheduleForm.tsx
        └── hooks/
            ├── useWebSocket.ts
            └── useICloud.ts
```

---

## Database Models (Postgres)

> Managed by **Alembic** in the dedicated `icloud_sync` database (D11).

### `downloaded_assets`

Core table. **Asset ID is the source of truth** — never check file existence on disk to
decide whether to download. Once `status = completed`, never re-download unless a job
explicitly sets `force_redownload` (see verify/repair).

| Column | Type | Notes |
|---|---|---|
| `id` | `BIGINT PK` | Auto-increment |
| `asset_id` | `VARCHAR UNIQUE` | iCloud's stable record id — survives file moves |
| `filename` | `VARCHAR` | Original filename |
| `media_type` | `VARCHAR` | `RAW`, `JPEG`, `HEIC`, `MOV`, `MP4`… |
| `is_live_photo` | `BOOL` | true if a paired video component exists (D6) |
| `source_version` | `VARCHAR` | `original` / `edited` — which rendition was fetched (D6) |
| `file_size` | `BIGINT` | Bytes (primary file) |
| `created_at_icloud` | `TIMESTAMP (tz-aware, UTC)` | Shot date from iCloud |
| `albums` | `JSON` | List of album names this asset belongs to |
| `persons` | `JSON` | Tagged faces if exposed by pyicloud, else `[]` |
| `exif_data` | `JSON` | EXIF snapshot at download time (may be partial; see D5) |
| `files` | `JSON` | list of written files: `[{path, kind, album, size}]`. `kind` ∈ `original`/`edited`/`live_video`; `album` enables fanout reconciliation (D6, D8) |
| `original_path` | `VARCHAR` | First/primary file path (informational only) |
| `status` | `ENUM` | `pending / downloading / completed / failed` |
| `downloaded_at` | `TIMESTAMP` | |
| `last_verified_at` | `TIMESTAMP` | set by verify mode |
| `error_message` | `VARCHAR` | If failed |
| `retry_count` | `INT` | See retry policy |

Indexes: `asset_id` (unique), `status`, `media_type`, `created_at_icloud`

### `download_jobs`

One row per user-initiated or scheduled download run.

| Column | Type | Notes |
|---|---|---|
| `id` | `BIGINT PK` | |
| `created_at` | `TIMESTAMP` | |
| `selected_albums` | `JSON` | Album names selected by user |
| `selected_asset_ids` | `JSON` | Specific asset IDs (empty = all in albums) |
| `folder_structure` | `JSON` | e.g. `["{year}", "{month}", "{album}"]` |
| `include_raw` | `BOOL` | Default false |
| `include_jpeg` | `BOOL` | Default true |
| `include_heic` | `BOOL` | Default true |
| `include_video` | `BOOL` | Default true |
| `download_version` | `VARCHAR` | `edited` (default) / `original` / `both` (D6) |
| `album_fanout` | `BOOL` | **default true** — write one copy per album the asset is in; UI warns about storage. Off = first album only (D8) |
| `force_redownload` | `BOOL` | re-fetch even if `completed` (verify/repair) |
| `total_assets` | `INT` | |
| `downloaded_count` | `INT` | |
| `skipped_count` | `INT` | |
| `failed_count` | `INT` | |
| `status` | `VARCHAR` | `pending / running / completed / failed / cancelled` |
| `cancel_requested` | `BOOL` | cooperative cancel flag checked in the loop |
| `celery_task_id` | `VARCHAR` | To monitor/revoke the Celery task |

### `schedules`

| Column | Type | Notes |
|---|---|---|
| `id` | `INT PK` | |
| `cron_expression` | `VARCHAR` | e.g. `0 2 * * *` |
| `job_config` | `JSON` | Same fields as download_jobs (albums, filters, folder_structure, download_version, album_fanout) |
| `enabled` | `BOOL` | |
| `last_run_at` | `TIMESTAMP` | |
| `next_run_at` | `TIMESTAMP` | computed via APScheduler / croniter |

---

## Backend Services

### `app/core/config.py`

Load all config from env via `pydantic-settings`:

- `DATABASE_URL` — base postgres URL. The app derives an **async** (`postgresql+asyncpg://`) and a **sync** (`postgresql+psycopg://`) DSN from it (D1).
- `REDIS_URL` — e.g. `redis://192.168.x.x:6379/0` (pub/sub, locks, thumbnail cache only)
- `DOWNLOAD_BASE_PATH` — e.g. `/downloads`
- `ICLOUD_CONFIG_DIR` — `/config` (`pyicloud` cookie/session dir — the **only** session store, D2)
- `DOWNLOAD_CONCURRENCY` — default `4` (D3)
- `LOCAL_TIMEZONE` — e.g. `Europe/London`; all date-based folder tokens resolve in this tz
- `THUMBNAIL_CACHE_TTL` — seconds, default `604800` (7d)
- `API_SHARED_SECRET` — optional; if set, all `/api` and `/ws` calls require header `X-Sync-Secret` (defense-in-depth on a LAN, see Security)

Available folder-structure tokens:

```python
AVAILABLE_TOKENS = [
    {"id": "year",      "label": "Year",        "example": "2024"},
    {"id": "month",     "label": "Month",       "example": "06"},
    {"id": "day",       "label": "Day",         "example": "15"},
    {"id": "album",     "label": "Album",       "example": "Holidays"},
    {"id": "mediatype", "label": "Media Type",  "example": "RAW"},
    {"id": "person",    "label": "Person",      "example": "Alice"},
    {"id": "make",      "label": "Camera Make", "example": "Apple"},
    {"id": "model",     "label": "Camera Model","example": "iPhone 15 Pro"},
    {"id": "filename",  "label": "Filename",    "example": "IMG_0001"},
]
```

### `app/core/database.py` (D1)

Exposes two factories:
- `async_session()` — `asyncpg` engine, used by FastAPI endpoints.
- `sync_session()` — `psycopg` engine, used by Celery tasks and the scheduler service.

Do not share a single engine across both worlds.

### `app/core/locks.py` (D4)

Lease-based lock:
- `acquire(job_id)` → `SET icloud:sync_lock {job_id} NX EX 60`. If it already holds a *different* job id, abort.
- A background heartbeat (every ~20s) renews the TTL (`EXPIRE 60`) while the job runs.
- `release()` → `DEL icloud:sync_lock`.
- If the worker dies, the lock self-expires within 60s instead of blocking for a fixed long window.

### `app/services/icloud.py`

Wraps the original `pyicloud` library **entirely** (D12). Everything else goes through this
service. `pyicloud` is blocking (`requests`); FastAPI callers must wrap these in
`asyncio.to_thread` (D1).

**Session management (D2):**
- The trusted session lives in `ICLOUD_CONFIG_DIR=/config` (`PyiCloudService(..., cookie_directory=...)`), shared by backend + worker + scheduler. **No session is stored in Redis.**
- `authenticate(apple_id, password)` → constructs `PyiCloudService`; returns `requires_2fa` (pyicloud property). Never persists the password.
- `submit_2fa(code)` → `validate_2fa_code(code)` then `trust_session()` if not `is_trusted_session`; the cookie dir now holds a trusted session.
- On session expiry: set Redis flag `icloud:needs_2fa = "true"` and emit a loud log/notification (the UI/CLI must re-auth — see unattended-2FA note). Initial/interactive auth is best done through `app.cli auth`.

**Methods:**
- `authenticate(apple_id, password) -> requires_2fa: bool`
- `submit_2fa(code) -> success: bool`
- `get_status() -> { authenticated, needs_2fa }`
- `get_albums() -> list[{ name, asset_count }]`  (from `api.photos.albums`)
- `get_assets(album_name, offset, limit) -> list[AssetMetadata]`  (paginated; safe for 20k+ albums, D13)
- `get_asset_thumbnail(asset_id) -> bytes`  *(downloads the `thumb` version; cache-wrapped, see albums API)*
- `download_asset(asset, version: str, tmp_dir: Path) -> list[DownloadedFile]` — resolves the requested rendition(s) and streams each to a `*.part` temp file under `tmp_dir`. `version="original"`→`resOriginal`; `version="edited"`→`resJPEGFull` if present else `resOriginal`; `version="both"`→both when they differ. Live Photos also yield the `sidecar` video. Returns **a list** of `DownloadedFile{tmp_path, kind, size}` with `kind` ∈ `original`/`edited`/`live_video`. If a photo has no adjustments, `edited` == `original` and `both` de-duplicates to one file (D6). The downloader (not this service) decides final placement.

**AssetMetadata:** `asset_id`, `filename`, `media_type`, `file_size`, `created_at` (tz-aware UTC),
`albums`, `persons`, `is_live_photo`, `has_edited_version`.

> **Note on `persons` / face tags:** the iCloud API does not reliably expose face/person data. If
> unavailable, store `[]` and never error. The `{person}` token will resolve to `"Unknown"`.

### `app/services/exif.py` (D5)

Format-aware EXIF reader:
- JPEG/TIFF → `piexif`
- HEIC → `pillow-heif` + Pillow
- RAW (CR2/NEF/ARW/DNG…) → `exifread` (or shell `exiftool` if present)
- Returns a normalized dict; missing fields are omitted, never fatal.
- Date tokens prefer EXIF `DateTimeOriginal`; **fallback to iCloud `created_at`**. All dates
  converted to `LOCAL_TIMEZONE` before extracting year/month/day.

### `app/core/paths.py` (D7, D8, sanitization)

**Path resolver** — given an asset + folder-structure template, build the output path(s):

```
template:  ["{year}", "{month}", "{album}"]
asset:     shot 2024-06-15 (local tz), album "Holidays", file "IMG_0042.HEIC"
result:    /downloads/2024/06/Holidays/IMG_0042.HEIC
```

Token resolution:
- `{year}`/`{month}`/`{day}` → from EXIF `DateTimeOriginal` in `LOCAL_TIMEZONE`, fallback iCloud `created_at`
- `{album}` → with `album_fanout` (default on) the resolver returns **one path per album** the asset belongs to; with fanout off, the first album only (D8)
- `{person}` → first tagged person, fallback `"Unknown"`
- `{mediatype}` → `RAW` / `JPEG` / `HEIC` / `Video`
- `{make}`/`{model}` → from EXIF, fallback `"Unknown"`
- `{filename}` → original filename without extension
- Plain string segments → used as-is (still sanitized)

**Sanitization (hardened):** strip/replace path separators and reserved chars, reject `..`
traversal, trim leading/trailing dots+spaces, cap segment length, normalize unicode, fall back
to a safe placeholder if a segment resolves empty.

**Collision handling (D7):** before writing, if the resolved final path already exists for a
*different* asset_id, append a short asset-id-derived suffix (e.g. `IMG_0042~a1b2c3.HEIC`).
Never silently overwrite a different asset. When `download_version="both"`, the **edited**
rendition gets an `_edited` filename suffix so it sits beside the original without collision
(both share the native extension, e.g. `.HEIC`); extensions come from the rendition `*FileType`.

### `app/services/downloader.py`

**Per-asset download logic:**

1. Query `downloaded_assets` by `asset_id`. If `status = completed` **and not** `force_redownload`:
   - If `album_fanout` is on and the asset's current album set has **new** albums not yet in the row's `files`, copy the existing local file(s) into the missing album path(s) — **no iCloud re-download** — and append to `files`. Otherwise **skip** (increment `skipped_count`). (D8)
2. Upsert row → `status = downloading`.
3. Ask `icloud_service.download_asset(asset, version, tmp_dir)` → it streams each rendition (edited/original/live_video per `download_version`, de-duped if edited==original) to `*.part` temp files and returns descriptors `{tmp_path, kind, size}` (D6).
4. For each returned file × each target album (fanout, D8), resolve the final path via `paths.resolve()` (version suffix e.g. `_original` when `both`; `mkdir -p` parents), apply collision handling (D7), then `fsync` + **atomic rename** the `*.part` into place (D9).
5. Extract EXIF via `exif.py` (D5).
6. Update row → `status = completed`, `exif_data`, `files` (list of `{path, kind, album, size}`), `original_path` (primary file), `downloaded_at`, `source_version` (the job's choice), `is_live_photo`.
7. Publish progress to Redis **and append to the replay log** (see WS).
8. On error → `status = failed`, `error_message`, `retry_count += 1`; remove any `*.part` debris. Transient/throttle errors retry with exponential backoff before being marked failed (D13).

### `app/workers/tasks.py`

Single Celery task `run_download_job(job_id)`:

```
1. Load job (sync session).
2. Acquire lease lock (locks.acquire) + start heartbeat (D4). If held by another job → log + exit.
3. job.status = running.
4. Build asset list:
   - selected_asset_ids non-empty → use those
   - else fetch all assets from selected_albums
5. Apply filters (include_raw/jpeg/heic/video) and download_version.
6. Process assets with bounded concurrency = DOWNLOAD_CONCURRENCY (D3)
   (Celery group/chunks, or a thread pool inside the task):
     - check job.cancel_requested each iteration → stop cleanly if set
     - downloader.download_asset(...)
     - publish progress + append to replay log
     - update job counters (downloaded/skipped/failed)
7. status = completed (or failed if everything failed; cancelled if cancel_requested).
8. Stop heartbeat + release lock (always, even on exception).
```

Worker runs as its own container: `celery -A app.workers.tasks worker --loglevel=info --concurrency=<n>`.

**Retry policy:** within a run, a failed asset retries up to `MAX_RETRIES` (default 3)
with exponential backoff before being marked `failed`. A later job (or a `force_redownload`
/ "retry failed" action) re-attempts assets still in `failed`.

**Cancellation:** `DELETE /api/jobs/{id}` sets `cancel_requested = true` (cooperative)
and revokes the Celery task. The task checks the flag between assets, finishes/aborts the
current `*.part` cleanly, removes partial files, resets any `downloading` rows, releases the lock.

### `app/services/scheduler.py` + `scheduler_main.py` (D10)

APScheduler (`AsyncIOScheduler`, Postgres jobstore) runs in a **dedicated single-instance
`scheduler` container** — not inside multi-worker FastAPI (which would double-fire triggers).

- On startup: load `schedules` where `enabled = true` and register them.
- Each trigger: create a `DownloadJob` row from `schedule.job_config`, call `run_download_job.delay(job_id)`, update `last_run_at`/`next_run_at`.
- `create_or_update_schedule(config)`: upsert + re-register.
- `toggle_schedule(enabled)`: enable/disable without deleting.

> The Schedule API (in the FastAPI process) writes the DB row and signals the scheduler service
> to reload (e.g. via a Redis pub/sub `schedules:reload` message), so the two stay in sync.

### `app/cli.py`

CLI via `typer`. `auth` drives **icloudpd's** 2FA flow and is the **recommended primary auth path** (avoids the unattended-2FA problem):

```bash
docker exec -it icloud-sync-backend python -m app.cli auth     # interactive: Apple ID, pw, 2FA
docker exec -it icloud-sync-backend python -m app.cli sync      # trigger sync (--album optional)
docker exec -it icloud-sync-backend python -m app.cli status    # job status + last sync
```

---

## API Endpoints

> If `API_SHARED_SECRET` is set, every `/api/*` and `/ws/*` request must send `X-Sync-Secret`.

### Auth — `/api/auth`
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/auth/status` | `{ authenticated, needs_2fa }` |
| `POST` | `/api/auth/login` | `{ apple_id, password }` → `{ requires_2fa }`. Password never stored. |
| `POST` | `/api/auth/2fa` | `{ code }` → completes 2FA, persists session to `/config` |
| `POST` | `/api/auth/logout` | Clears the on-disk session |

### Albums — `/api/albums`
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/albums` | `[{ name, asset_count }]` |
| `GET` | `/api/albums/{name}/assets` | Paginated `?offset=0&limit=50` → assets + thumbnail URLs |
| `GET` | `/api/assets/{asset_id}/thumbnail` | Thumbnail bytes — **Redis-cached** (key `thumb:{asset_id}`, TTL `THUMBNAIL_CACHE_TTL`) to avoid hammering iCloud from the grid. pyicloud call wrapped in `to_thread`. |

### Jobs — `/api/jobs`
| Method | Path | Description |
|---|---|---|
| `POST` | `/api/jobs` | Create + launch. Body: `selected_albums`, `selected_asset_ids`, `folder_structure`, `include_raw/jpeg/heic/video`, `download_version`, `album_fanout`, `force_redownload`. **Only creates the row + `run_download_job.delay(id)`** — never downloads in-request. |
| `GET` | `/api/jobs` | List, newest first |
| `GET` | `/api/jobs/{id}` | Detail + per-asset breakdown |
| `DELETE` | `/api/jobs/{id}` | Cancel: set `cancel_requested`, revoke Celery task |
| `POST` | `/api/jobs/{id}/retry-failed` | new job re-attempting this job's `failed` assets |

### Schedule — `/api/schedule`
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/schedule` | Current schedule config |
| `PUT` | `/api/schedule` | `{ cron_expression, job_config, enabled }` → upsert + signal scheduler reload |
| `POST` | `/api/schedule/toggle` | `{ enabled }` |

### Tokens — `/api/tokens`
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/tokens` | Full token list (id, label, example) |

### WebSocket — `/ws/jobs/{job_id}`
**Implementation:** the worker `PUBLISH`es to `icloud:job:{job_id}:progress` **and** `LPUSH`/`LTRIM`s
each log line into `icloud:job:{job_id}:log` (capped at 100). On connect, the WS handler **replays
the stored log first**, then subscribes to live pub/sub — so a refresh/late-join doesn't lose
history. Uses `redis.asyncio`.

Event types:
```json
{ "type": "progress", "downloaded": 42, "skipped": 5, "failed": 0, "total": 150, "current_file": "IMG_0042.HEIC" }
{ "type": "log", "level": "info",  "message": "Downloaded IMG_0042.HEIC → /downloads/2024/06/Holidays/" }
{ "type": "log", "level": "error", "message": "Failed IMG_0043.RAW: timeout" }
{ "type": "done", "status": "completed" }
```

---

## Frontend Pages

### `/auth`
- Shown when `GET /api/auth/status` → `authenticated: false` or `needs_2fa: true`
- Step 1: Apple ID + password. Step 2: 6-digit 2FA + countdown hint.
- Poll status every 60s to catch session expiry.
- Note in UI: initial auth is most reliable via the CLI (`docker exec`).

### `/` — Browser (main)
- **Left `AlbumTree`** — albums from `GET /api/albums`, multi-select checkboxes.
- **Center `AssetGrid`** — thumbnail grid (cached thumbnails), per-asset checkbox, select-all,
  badges (`RAW`/`HEIC`/`Video`/`LIVE`), infinite scroll.
- **Right — config + launch:**
  - `FilterBar` — JPEG/HEIC/Video on by default, RAW off (with large-file note). `download_version` selector — **Edited (default)** / Original / Both. `album_fanout` toggle (**on by default**).
  - `FolderBuilder` — token-chip palette (from `/api/tokens`), drop zone building `/`-separated
    segments, typed plain-text segments, reorder/remove, **live preview** resolved against a
    sample asset. When the `{album}` token is used with fanout on, show a **warning** that
    assets in multiple albums are duplicated (one copy each), increasing storage (D8).
  - **Start Download** — disabled unless something selected → `POST /api/jobs` → redirect `/jobs`.

### `/jobs`
- List newest first: date, albums, status badge, counts.
- Running job → live `ProgressPanel` (WS): progress bar, current file, skipped/failed,
  scrolling log (last 100, **replayed on connect**), Cancel button.
- Completed → expandable per-asset detail. "Retry failed" action.

### `/schedule`
- Cron picker (presets + custom), same `FolderBuilder`/`FilterBar`/`AlbumTree`, enable toggle,
  last-run/next-run timestamps, Save → `PUT /api/schedule`.

---

## Docker Compose (4 services + healthchecks)

```yaml
services:
  backend:
    build: ./backend
    container_name: icloud-sync-backend
    restart: unless-stopped
    ports: ["8000:8000"]
    env_file: .env
    volumes:
      - /mnt/user/photos:/downloads
      - icloud-config:/config        # shared session store (D2)

  celery:
    build: ./backend
    container_name: icloud-sync-celery
    restart: unless-stopped
    command: celery -A app.workers.tasks worker --loglevel=info
    env_file: .env
    volumes:
      - /mnt/user/photos:/downloads
      - icloud-config:/config        # same session (D2)

  scheduler:                          # dedicated single instance (D10)
    build: ./backend
    container_name: icloud-sync-scheduler
    restart: unless-stopped
    command: python -m app.scheduler_main
    env_file: .env
    volumes:
      - icloud-config:/config

  frontend:
    build: ./frontend
    container_name: icloud-sync-frontend
    restart: unless-stopped
    ports: ["3000:80"]

volumes:
  icloud-config:
```

> Postgres and Redis are **external** (existing). Add app-level readiness checks at startup
> (retry-connect to `DATABASE_URL`/`REDIS_URL`) since compose can't `depends_on` them.

`.env` (never commit; provide `.env.example`):
```env
DATABASE_URL=postgresql://user:pass@192.168.x.x:5432/icloud_sync
REDIS_URL=redis://192.168.x.x:6379/0
DOWNLOAD_BASE_PATH=/downloads
ICLOUD_CONFIG_DIR=/config
DOWNLOAD_CONCURRENCY=4
LOCAL_TIMEZONE=Europe/London
THUMBNAIL_CACHE_TTL=604800
# API_SHARED_SECRET=change-me   # optional defense-in-depth
```

---

## Build Order for Claude Code

1. **Alembic + Postgres schema + models** (`models/assets.py`, `core/database.py` dual engines) (D1, D11)
2. **iCloud service** (`services/icloud.py`) wrapping `pyicloud` + **`test_icloud.py` spike** — verify 2FA auth, album listing, and confirm the `resJPEGFull` edited-rendition field on a real edited photo + a sample download. Bake the confirmed field/version mapping into the service. **🚦 GO/NO-GO GATE: do not proceed until this works** (D12, D6)
3. **Auth API** (`api/auth.py`) + curl test
4. **Albums API** (`api/albums.py`) + thumbnail cache
5. **EXIF + path resolver** (`services/exif.py`, `core/paths.py`) — incl. sanitization, collisions, fanout (D5,D7,D8) — unit-test the resolver in isolation
6. **Downloader** (`services/downloader.py`) — atomic writes, Live Photos, multi-file (D6,D9)
7. **Lease lock** (`core/locks.py`) + **Celery task** (`workers/tasks.py`) — concurrency, cancel, retry (D3,D4) + **WS endpoint** with replay (`api/ws.py`)
8. **Jobs API** (`api/jobs.py`)
9. **Scheduler service** (`services/scheduler.py`, `scheduler_main.py`) + Schedule API (D10)
10. **CLI** (`cli.py`) — make `auth` the documented primary login
11. **Frontend** — Auth → Browser → Jobs → Schedule

---

## Critical Implementation Notes

1. **Asset ID is the source of truth** — never stat the disk to decide downloads. Query `downloaded_assets WHERE status='completed'`. The one exception is a job with `force_redownload` (verify/repair).
2. **Single session store = the `/config` volume** (D2). `pyicloud` writes its cookie there; all three containers share it. Do **not** also cache the session in Redis.
3. **Never download in an HTTP handler** — endpoints create the row + `run_download_job.delay(id)`; all download work is in Celery.
4. **WS ↔ Celery via Redis** — worker `PUBLISH`es progress **and** `LPUSH`/`LTRIM`s a 100-line log; WS replays the log on connect then subscribes.
5. **Folder structure = JSON array** of mixed tokens + plain strings; resolver substitutes, sanitizes, joins with `/`, appends filename, **handles collisions** (D7) and **fanout** (D8).
6. **Concurrency control** — bounded `DOWNLOAD_CONCURRENCY` (D3) + **lease lock with heartbeat**, short TTL, always released (D4).
7. **Apple ADP must be disabled** — document prominently; pyicloud cannot read photos with Advanced Data Protection on.
8. **No UI auth, LAN-only** — see Security; optional `API_SHARED_SECRET` for defense-in-depth.
9. **EXIF is format-aware** (D5) — `pillow-heif` (HEIC), `piexif` (JPEG), `exifread`/`exiftool` (RAW). Dates resolved in `LOCAL_TIMEZONE`.
10. **Person/face tags** — store `[]` if the iCloud API doesn't expose them; never error.
11. **Atomic writes (D9)** — `*.part` → fsync → rename. Reset `downloading` rows on startup; clean partials on failure/cancel.
12. **Live Photos / multi-file (D6)** — one asset_id may produce several files; recorded in `files`. `download_version` selects original/edited/both.
13. **Cancellation is cooperative** — set `cancel_requested`, task checks it between assets, cleans up partials, releases lock.
14. **Scheduler is single-instance** (D10) — never run APScheduler in multi-worker FastAPI.
15. **Built on original `pyicloud` (D12)** — modern 2FA, behind `ICloudService`; the `icloudpd` binary is a documented shell-out fallback only. Keep that boundary clean so the engine stays swappable. Edited renditions come from `resJPEGFull` in the raw record (D6).
16. **Large-sync resilience (D13)** — the first sync (10–20k files) may run for hours: it is resumable (skip `completed`), counters persist per asset, the lease lock heartbeats through long runs, completed asset IDs are preloaded once, and transient/throttle errors back off and retry.

---

## Security Notes

- **Do not expose this beyond the LAN.** The app holds a live, full-access iCloud session;
  anything that can reach the Unraid host (incl. a compromised IoT device) could exfiltrate
  the entire library. Bind to the LAN, ideally behind a reverse proxy.
- The Apple password is POSTed in clear over the LAN at login — acceptable only on a trusted
  network. **It is never stored** (only the session cookie is).
- Treat `/config` (the pyicloud session) and `.env` as **secrets** — equivalent to the account
  password. `.env` is git-ignored; ship `.env.example` instead.
- Optional `API_SHARED_SECRET` header check adds a cheap barrier against casual LAN access.
- Thumbnail/asset endpoints have no rate limiting — fine for single-user LAN, but don't expose.

---

## Resolved This Round

- **Engine (D12):** original `pyicloud` library (modern 2FA), behind `ICloudService`; `icloudpd` binary kept as a shell-out fallback. (icloudpd is no longer importable; `pyicloud-ipd` is stale — see "iCloud engine".)
- **Scale (D13):** first sync is large (10–20k files) → resumable, throttle-aware, long-run lock.
- **Version (D6):** default **edited** (hard requirement) — implemented via `resJPEGFull` extraction with original fallback; user can also pick original or both.
- **Album fanout (D8):** **one copy per album** by default, with a UI storage warning.

### Remaining items to confirm during the step-2 spike

- **`resJPEGFull` reality:** confirm the field is present on edited photos in *your* library and that downloading it yields the baked-in edits; confirm the "has-edits" signal (presence of the field vs. an `adjustmentRenderType`). Originals are guaranteed; edited is the open risk.
- **2FA / trusted-session longevity:** CONFIRMED — the trusted session survives restarts in the cookie dir, and `ICloudService.try_restore()` reconstructs an authenticated session **passwordless** (just the remembered Apple ID), so FastAPI/worker/scheduler all auto-restore on boot. This softens the unattended-job caveat: scheduled jobs work without interaction **until Apple expires the trust token** — cadence still unknown (watch for `needs_2fa` flipping true; the only remaining open item).
- **Fanout reconciliation** when an already-downloaded asset is later added to a new album: the plan copies the local file into the new album path without re-downloading (D8). Confirm that's the desired behaviour vs. leaving older assets un-fanned.
