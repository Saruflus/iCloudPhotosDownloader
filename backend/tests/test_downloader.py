#!/usr/bin/env python3
"""Downloader tests (D5–D9) — fake store/publisher/iCloud + real temp filesystem.

Run:  cd backend && PYTHONPATH=. python tests/test_downloader.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from app.core import paths as P
from app.core.paths import AssetContext
from app.services.downloader import Downloader, JobSpec, Outcome, StoredAsset
from app.services.icloud import DownloadedFile

PASSED = 0


def check(label: str, cond: bool) -> None:
    global PASSED
    assert cond, f"FAILED: {label}"
    PASSED += 1
    print(f"  ✓ {label}")


def make_jpeg(path: Path, dt: bytes = b"2021:07:04 12:34:56", color=(120, 30, 30)) -> None:
    from PIL import Image
    import piexif

    Image.new("RGB", (8, 8), color).save(
        path, "jpeg",
        exif=piexif.dump({"Exif": {piexif.ExifIFD.DateTimeOriginal: dt}}),
    )


class FakePhoto:
    def __init__(self, id, filename, is_live=False, created=None):
        self.id = id
        self.filename = filename
        self.is_live_photo = is_live
        self.created = created


class FakeICloud:
    def __init__(self, with_edited=False, with_live=False, fail=False):
        self.with_edited = with_edited
        self.with_live = with_live
        self.fail = fail
        self.calls = 0

    def download_asset(self, photo, version, tmp_dir):
        self.calls += 1
        if self.fail:
            raise RuntimeError("simulated iCloud failure")
        tmp = Path(tmp_dir)
        out = []
        want_original = version in ("original", "both") or (version == "edited" and not self.with_edited)
        if want_original:
            p = tmp / f"{photo.id}.original.jpg"
            make_jpeg(p)
            out.append(DownloadedFile(path=p, kind="original", size=p.stat().st_size, ext=".jpg"))
        if self.with_edited and version in ("edited", "both"):
            p = tmp / f"{photo.id}.edited.jpg"
            make_jpeg(p, color=(0, 200, 0))
            out.append(DownloadedFile(path=p, kind="edited", size=p.stat().st_size, ext=".jpg"))
        if self.with_live:
            p = tmp / f"{photo.id}.live_video.MOV"
            p.write_bytes(b"\x00\x00\x00\x18ftypmp4")
            out.append(DownloadedFile(path=p, kind="live_video", size=p.stat().st_size, ext=".MOV"))
        return out


class FakeStore:
    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.path_index: dict[str, str] = {}

    def get(self, asset_id):
        r = self.rows.get(asset_id)
        if not r:
            return None
        return StoredAsset(
            status=r["status"], files=r.get("files", []), filename=r.get("filename"),
            media_type=r.get("media_type"), created_at_icloud=r.get("created"),
            exif=r.get("exif", {}),
        )

    def begin(self, asset_id, *, filename, media_type, is_live, source_version, job_id=None):
        self.rows[asset_id] = {"status": "downloading", "filename": filename,
                               "media_type": media_type, "files": [],
                               "last_job_id": job_id}

    def complete(self, asset_id, *, files, exif, original_path, file_size,
                 created_at_icloud, source_version, is_live):
        self.rows[asset_id].update(status="completed", files=files, exif=exif,
                                   original_path=original_path)
        for f in files:
            self.path_index[f["path"]] = asset_id

    def append_files(self, asset_id, files):
        self.rows[asset_id]["files"] = self.rows[asset_id].get("files", []) + files
        for f in files:
            self.path_index[f["path"]] = asset_id

    def fail(self, asset_id, *, error):
        self.rows.setdefault(asset_id, {}).update(status="failed", error=error)

    def path_owner(self, path):
        return self.path_index.get(path)


class FakePublisher:
    def __init__(self):
        self.events = []

    def publish(self, job_id, event):
        self.events.append((job_id, event))


def no_part_files(base: Path) -> bool:
    return not any(p.suffix == ".part" for p in base.rglob("*"))


def run() -> None:
    print("== basic download (EXIF-driven date) ==")
    base = Path(tempfile.mkdtemp(prefix="dl_basic_"))
    store, pub, ic = FakeStore(), FakePublisher(), FakeICloud()
    dl = Downloader(ic, store, base, pub, tz_name="UTC")
    job = JobSpec(template=["{year}", "{album}"], download_version="original")
    out = dl.download_asset(FakePhoto("A1", "IMG_0001.jpg"), ["Holidays"], job, job_id=7)
    expected = base / "2021" / "Holidays" / "IMG_0001.jpg"
    check("outcome downloaded", out == Outcome.downloaded)
    check("file at EXIF-derived year path", expected.is_file())
    check("row completed", store.rows["A1"]["status"] == "completed")
    check("files recorded", store.rows["A1"]["files"][0]["path"] == str(expected))
    check("progress/log published", any(e["type"] == "log" for _, e in pub.events))
    check("no .part leftovers", no_part_files(base))
    check("tmp dir cleaned", not (base / ".icloud-tmp").exists() or not any((base / ".icloud-tmp").iterdir()))

    print("== skip already-completed ==")
    store2, ic2 = FakeStore(), FakeICloud()
    store2.rows["A1"] = {"status": "completed", "files": [{"path": "x", "kind": "original", "album": None}]}
    dl2 = Downloader(ic2, store2, Path(tempfile.mkdtemp()), tz_name="UTC")
    out = dl2.download_asset(FakePhoto("A1", "IMG.jpg"), ["Holidays"], JobSpec(template=["{year}"]))
    check("skipped", out == Outcome.skipped)
    check("iCloud not called on skip", ic2.calls == 0)

    print("== album fanout (D8) ==")
    base = Path(tempfile.mkdtemp(prefix="dl_fan_"))
    store, ic = FakeStore(), FakeICloud()
    dl = Downloader(ic, store, base, tz_name="UTC")
    dl.download_asset(FakePhoto("A2", "IMG.jpg"), ["A", "B"],
                      JobSpec(template=["{album}"], download_version="original", album_fanout=True))
    check("copy in album A", (base / "A" / "IMG.jpg").is_file())
    check("copy in album B", (base / "B" / "IMG.jpg").is_file())
    check("two files recorded", len(store.rows["A2"]["files"]) == 2)
    check("no .part leftovers (fanout)", no_part_files(base))

    print("== both versions (edited suffix, D6) ==")
    base = Path(tempfile.mkdtemp(prefix="dl_both_"))
    store, ic = FakeStore(), FakeICloud(with_edited=True)
    dl = Downloader(ic, store, base, tz_name="UTC")
    dl.download_asset(FakePhoto("A3", "IMG_9.jpg"), ["Hol"],
                      JobSpec(template=["{album}"], download_version="both"))
    check("original written", (base / "Hol" / "IMG_9.jpg").is_file())
    check("edited written with suffix", (base / "Hol" / "IMG_9_edited.jpg").is_file())
    kinds = {f["kind"] for f in store.rows["A3"]["files"]}
    check("both kinds recorded", kinds == {"original", "edited"})

    print("== collision disambiguation (D7) ==")
    base = Path(tempfile.mkdtemp(prefix="dl_col_"))
    store, ic = FakeStore(), FakeICloud()
    job = JobSpec(template=["{album}"], download_version="original")
    ctx = AssetContext(filename="IMG.jpg", media_type="JPEG", albums=["Hol"])
    clash = str(P.final_path(str(base), job.template, ctx, "Hol", "IMG.jpg"))
    store.path_index[clash] = "SOMEONE_ELSE"  # path owned by a different asset
    dl = Downloader(ic, store, base, tz_name="UTC")
    dl.download_asset(FakePhoto("A4", "IMG.jpg"), ["Hol"], job)
    written = store.rows["A4"]["files"][0]["path"]
    check("collision → suffixed path", written != clash and "~" in Path(written).name)
    check("suffixed file exists", Path(written).is_file())

    print("== live photo (paired video) ==")
    base = Path(tempfile.mkdtemp(prefix="dl_live_"))
    store, ic = FakeStore(), FakeICloud(with_live=True)
    dl = Downloader(ic, store, base, tz_name="UTC")
    dl.download_asset(FakePhoto("A5", "IMG_L.jpg", is_live=True), ["Hol"],
                      JobSpec(template=["{album}"], download_version="original"))
    check("still written", (base / "Hol" / "IMG_L.jpg").is_file())
    check("live video written", (base / "Hol" / "IMG_L_live.MOV").is_file())

    print("== failure handling ==")
    base = Path(tempfile.mkdtemp(prefix="dl_err_"))
    store, pub, ic = FakeStore(), FakePublisher(), FakeICloud(fail=True)
    dl = Downloader(ic, store, base, pub, tz_name="UTC")
    out = dl.download_asset(FakePhoto("A6", "IMG.jpg"), ["Hol"], JobSpec(template=["{album}"]), job_id=9)
    check("outcome failed", out == Outcome.failed)
    check("row marked failed", store.rows["A6"]["status"] == "failed")
    check("error log published", any(e.get("level") == "error" for _, e in pub.events))
    check("no .part leftovers on failure", no_part_files(base))

    print("== fanout reconciliation (later album, D8) ==")
    base = Path(tempfile.mkdtemp(prefix="dl_recon_"))
    store, ic = FakeStore(), FakeICloud()
    # asset already downloaded into album A; album B is new
    srcfile = base / "A" / "IMG.jpg"
    srcfile.parent.mkdir(parents=True)
    srcfile.write_bytes(b"jpegdata")
    store.rows["A7"] = {"status": "completed",
                        "files": [{"path": str(srcfile), "kind": "original", "album": "A", "size": 8}]}
    dl = Downloader(ic, store, base, tz_name="UTC")
    out = dl.download_asset(FakePhoto("A7", "IMG.jpg"), ["A", "B"],
                            JobSpec(template=["{album}"], album_fanout=True))
    check("still skipped (no re-download)", out == Outcome.skipped and ic.calls == 0)
    check("reconciled copy into B", (base / "B" / "IMG.jpg").is_file())
    albums_now = {f["album"] for f in store.rows["A7"]["files"]}
    check("album B appended", albums_now == {"A", "B"})

    print(f"\nALL {PASSED} CHECKS PASSED ✓")


if __name__ == "__main__":
    try:
        run()
    except AssertionError as e:
        print(f"\n{e}")
        sys.exit(1)
