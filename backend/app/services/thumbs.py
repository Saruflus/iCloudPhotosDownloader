"""Disk thumbnail cache (Lot 3).

Thumbnails outlive the Redis TTL and container restarts by also living on the
/config volume (small JPEGs, ~10-30 KB each). Lookup order in the API is
Redis → disk → live fetch; writes go to both. Filenames are sha1(asset_id) so
arbitrary record names can't escape the directory.
"""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

LOGGER = logging.getLogger(__name__)


class DiskThumbCache:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, asset_id: str) -> Path:
        digest = hashlib.sha1(asset_id.encode("utf-8")).hexdigest()
        return self.root / digest[:2] / f"{digest}.jpg"  # fan out, max 256 dirs

    def get(self, asset_id: str) -> bytes | None:
        try:
            return self._path(asset_id).read_bytes()
        except OSError:
            return None

    def put(self, asset_id: str, data: bytes) -> None:
        """Atomic write; failures are logged, never raised (cache is best-effort)."""
        path = self._path(asset_id)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            part = path.with_name(path.name + ".part")
            part.write_bytes(data)
            os.replace(part, path)
        except OSError as exc:
            LOGGER.debug("Thumb disk cache write failed for %s: %s", asset_id, exc)
