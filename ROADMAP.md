# Roadmap

Planned improvements. Effort: **S** small · **M** medium · **L** large.

## Lot 1 — Quick wins (UX, low effort)
- [ ] **RAW badge** for photos that have a RAW companion (`resOriginalAlt`) — add a
      `has_raw_version` flag to the asset metadata + purple badge in the grid. **S**
- [ ] **Album search bar** (client-side filter over album names). **S**
- [ ] **Thumbnail HTTP cache headers** on `/api/assets/{id}/thumbnail` → browser
      caches, instant re-display. **S**
- [ ] **Async album counts** — don't block the album list on `len(album)` (39
      queries); load counts lazily / in the background. **M**

## Lot 2 — Features
- [ ] **Schedule page** in the UI (the scheduler engine already runs; only the
      screen is missing). **M**
- [ ] **Config/settings page** (concurrency, timezone, API secret, paths). **M**
- [ ] **Shared albums** (pyicloud `shared_streams`). **M**
- [ ] **Date-range filter** (only download photos after a date). **M**
- [ ] **Dry-run / preview** ("X to download / Y already present") before launch. **M**

## Lot 3 — Big-sync comfort & performance
- [ ] **Virtualized asset grid** (react-window) for huge albums (22k+). **M**
- [ ] **Parallel thumbnail prefetch** on album open. **M**
- [ ] **Disk thumbnail cache** (survives restart, beyond Redis TTL). **M**
- [ ] **Persistent PhotoAsset cache** (no thumbnail 404 after a restart before
      re-browsing). **S/M**
- [ ] **Apple throttling handling** (global backoff) on large syncs. **M**

## Lot 4 — Robustness / ops
- [ ] **Notifications** (ntfy / Discord / email) on job done/failed — and
      especially on `needs_2fa`, for unattended scheduled jobs. **M**
- [ ] **Link jobs ↔ assets in DB** (`job_id`) → real per-job breakdown + precise
      "retry failed". **M**
- [ ] **Healthchecks** in compose + `depends_on: condition: service_healthy`. **S**
- [ ] **Multiple schedules** instead of a single one. **M**
- [ ] **Verify/repair mode** — re-download files missing on disk. **M**

## Later / nice-to-have
- [ ] **Persons/faces** → enable the `{person}` token (decode the `people` field). **L**
- [ ] `/api/tokens` endpoint (frontend currently hardcodes the token list). **S**
- [ ] **ETA / throughput** in the progress panel. **S**
- [ ] **Responsive mobile** layout + **dark mode**. **M**
- [ ] **"Session expired" banner** in the UI. **S**
- [ ] Frontend tests. **M**
