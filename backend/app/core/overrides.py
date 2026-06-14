"""Runtime setting overrides (Lot 2 settings page).

A whitelisted subset of Settings can be overridden from the UI; values live in
the ``app_settings`` table. Workers re-read them when each job starts (a fresh
runner is built per job), so changes apply without container restarts. Env vars
remain the source for everything else (paths, connections, secret).
"""
from __future__ import annotations

from zoneinfo import ZoneInfo

# key -> (validator, description). Validators raise ValueError on bad input.


def _int_range(lo: int, hi: int):
    def check(v):
        v = int(v)
        if not lo <= v <= hi:
            raise ValueError(f"must be between {lo} and {hi}")
        return v

    return check


def _timezone(v):
    v = str(v)
    try:
        ZoneInfo(v)
    except Exception as exc:  # ZoneInfoNotFoundError is a KeyError, not ValueError
        raise ValueError(f"unknown timezone {v!r}") from exc
    return v


OVERRIDABLE = {
    "download_concurrency": _int_range(1, 16),
    "max_retries": _int_range(0, 10),
    "local_timezone": _timezone,
    "thumbnail_cache_ttl": _int_range(60, 365 * 24 * 3600),
}


def validate_override(key: str, value):
    """Validate one override; returns the normalized value. Raises ValueError."""
    if key not in OVERRIDABLE:
        raise ValueError(f"setting {key!r} is not overridable")
    return OVERRIDABLE[key](value)


def load_overrides_sync(session) -> dict:
    """Read all overrides with a sync session (worker/scheduler side)."""
    from app.models.assets import AppSetting

    out = {}
    for row in session.query(AppSetting).all():
        if row.key in OVERRIDABLE and row.value is not None:
            try:
                out[row.key] = OVERRIDABLE[row.key](row.value)
            except ValueError:
                continue  # ignore a corrupt row rather than break job runs
    return out
