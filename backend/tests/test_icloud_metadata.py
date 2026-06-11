#!/usr/bin/env python3
"""Asset metadata detector tests — `_has_raw` / `_has_edited`.

These guard the grid badges. `_has_raw` reads the RAW discriminator
(`resOriginalAltRes`) straight off the *master* record, mirroring how
`_has_edited` reads `resJPEGFullRes` off the *asset* record — instead of going
through `photo.versions` (which eagerly builds every rendition and would mask a
single build failure as "no RAW", silently hiding the badge).

Run:  cd backend && PYTHONPATH=. python tests/test_icloud_metadata.py
"""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/icloud_sync")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from app.services.icloud import ICloudService  # noqa: E402

PASSED = 0


def check(label: str, cond: bool) -> None:
    global PASSED
    assert cond, f"FAILED: {label}"
    PASSED += 1
    print(f"  ✓ {label}")


# --- fakes mirroring pyicloud's CK record shapes -----------------------------
class _FakeFields:
    def __init__(self, d: dict) -> None:
        self._d = dict(d)

    def get_value(self, k: str):
        return self._d.get(k)


class _FakeAssetRecord:
    """Typed-record stand-in: supports `.fields.get_value(...)` (asset record)."""

    def __init__(self, d: dict) -> None:
        self.fields = _FakeFields(d)


def _master(raw: bool) -> dict:
    """Legacy-dict master record, as `record_field_value` accepts."""
    fields = {}
    if raw:
        fields["resOriginalAltRes"] = {"value": {"downloadURL": "https://x", "size": 1234}}
    return {"fields": fields}


class FakePhoto:
    def __init__(self, *, raw: bool = False, edited: bool = False) -> None:
        self._master_record = _master(raw)
        self._asset_record = _FakeAssetRecord(
            {"resJPEGFullRes": {"value": {"size": 1}}} if edited else {}
        )


def run() -> None:
    # RAW companion present / absent (resOriginalAltRes on the master record).
    check("has_raw True when resOriginalAltRes present", ICloudService._has_raw(FakePhoto(raw=True)) is True)
    check("has_raw False when absent", ICloudService._has_raw(FakePhoto(raw=False)) is False)

    class NoMaster:
        pass

    check("has_raw False when no master record", ICloudService._has_raw(NoMaster()) is False)

    # Regression for the reported bug: a throwing `.versions` must NOT suppress
    # the badge — the detector reads the record field directly and never touches
    # `.versions`. (The old implementation returned False here.)
    class BoomVersions:
        _master_record = _master(raw=True)

        @property
        def versions(self):
            raise RuntimeError("rendition build failed")

    check("has_raw ignores a broken .versions", ICloudService._has_raw(BoomVersions()) is True)

    # EDIT tag regression guard (unchanged behavior).
    check("has_edited True when resJPEGFullRes present", ICloudService._has_edited(FakePhoto(edited=True)) is True)
    check("has_edited False when absent", ICloudService._has_edited(FakePhoto(edited=False)) is False)

    print(f"\nALL {PASSED} CHECKS PASSED ✓")


if __name__ == "__main__":
    run()
