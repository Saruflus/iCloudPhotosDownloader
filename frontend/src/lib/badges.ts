import type { Asset } from "../types";

export interface Badge {
  label: string;
  cls: string;
}

// Mirrors backend classify_media (app/core/paths.py) — used only as a fallback
// when the backend predates `media_category`. The backend field is the source
// of truth; do not extend these lists independently.
const RAW_EXTS = new Set([
  "CR2", "CR3", "NEF", "ARW", "DNG", "RAF", "RW2", "ORF",
  "PEF", "SRW", "SR2", "3FR", "ERF", "KDC", "NRW", "RAW",
]);
const VIDEO_EXTS = new Set(["MOV", "MP4", "M4V", "AVI", "HEVC", "3GP", "3G2", "MPG", "MPEG"]);
const HEIC_EXTS = new Set(["HEIC", "HEIF"]);
const JPEG_EXTS = new Set(["JPG", "JPEG"]);

/** Uppercase category for an asset: prefers backend media_category, falls back
 *  to deriving it from the media_type extension (older backend). */
export function assetCategory(a: Pick<Asset, "media_category" | "media_type">): string {
  if (a.media_category) return a.media_category.toUpperCase();
  const ext = (a.media_type || "").toUpperCase();
  if (RAW_EXTS.has(ext)) return "RAW";
  if (VIDEO_EXTS.has(ext)) return "VIDEO";
  if (HEIC_EXTS.has(ext)) return "HEIC";
  if (JPEG_EXTS.has(ext)) return "JPEG";
  return ext;
}

/** Grid badges for one asset. At most one RAW badge (primary RAW or RAW companion). */
export function badges(a: Asset): Badge[] {
  const out: Badge[] = [];
  const cat = assetCategory(a);
  if (cat === "HEIC") out.push({ label: "HEIC", cls: "bg-slate-700" });
  if (cat === "RAW" || a.has_raw_version) out.push({ label: "RAW", cls: "bg-purple-700" });
  if (cat === "VIDEO") out.push({ label: "VIDEO", cls: "bg-rose-700" });
  if (a.is_live_photo) out.push({ label: "LIVE", cls: "bg-amber-600" });
  if (a.has_edited_version) out.push({ label: "EDIT", cls: "bg-emerald-700" });
  return out;
}
