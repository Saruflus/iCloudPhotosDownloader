# Roadmap

Planned improvements. Effort: **S** small · **M** medium · **L** large.

## Lot 1 — Quick wins (UX, low effort)
- [x] **RAW badge** for photos that have a RAW companion (`resOriginalAlt`) — add a
      `has_raw_version` flag to the asset metadata + purple badge in the grid. **S**
- [x] **Album search bar** (client-side filter over album names). **S**
- [x] **Thumbnail HTTP cache headers** on `/api/assets/{id}/thumbnail` → browser
      caches, instant re-display. **S**
- [x] **Album display sorting** sort the thumbs by date. **M**
- [x] **Async album counts** — don't block the album list on `len(album)`; load
      counts lazily in the background. **M**

## Lot 2 — Features
- [x] **Schedule page** in the UI (the scheduler engine already runs and can be used using CLI; only the webUI is missing). **M**
- [x] **Config/settings page** (concurrency, timezone, API secret, paths). **M**
- [x] **Shared albums** (pyicloud `shared_streams`). **M**
- [x] **Date-range filter** (only download photos in a capture-date window). **M**
- [x] **Dry-run / preview** ("X to download / Y already downloaded") before launch. **M**

## Lot 3 — Big-sync comfort & performance
- [x] **Virtualized asset grid** (react-window) for huge albums (1k+). **M**
- [x] **Parallel thumbnail prefetch** on album open (next-page warm-up). **M**
- [x] **Disk thumbnail cache** (survives restart, beyond Redis TTL). **M**
- [x] **Persistent PhotoAsset cache** — thumbnail endpoint falls back to a direct
      fetch-by-id, so no 404 after a restart before re-browsing. **S/M**
- [x] **Apple throttling handling** (global backoff) on large syncs. **M**

## Lot 4 — Robustness / ops
- [x] **Notifications** (ntfy / Discord / email) on job done/failed — and
      especially on `needs_2fa`, for unattended scheduled jobs. **M**
- [x] **Link jobs ↔ assets in DB** (`last_job_id`) → precise "retry failed". **M**
- [x] **Healthchecks** in compose + `depends_on: condition: service_healthy`. **S**
- [x] **Multiple schedules** instead of a single one. **M**
- [x] **Verify/repair mode** — re-download files missing on disk. **M**

## Later / nice-to-have
- [ ] **Persons/faces** → enable the `{person}` token (decode the `people` field). **L**
      _(deferred: pyicloud doesn't reliably expose face tags — needs a separate
      CloudKit people-zone query; see note in `services/icloud.py`.)_
- [x] `/api/tokens` endpoint (frontend fetches the token list, falls back to a static palette). **S**
- [x] **ETA / throughput** in the progress panel. **S**
- [ ] **Responsive mobile** layout + **dark mode**. **M**
- [x] **"Session expired" banner** in the UI. **S**
- [x] Frontend tests (vitest + testing-library). **M**
