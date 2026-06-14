import { describe, expect, it } from "vitest";
import { assetCategory, badges } from "./badges";
import type { Asset } from "../types";

function asset(over: Partial<Asset>): Asset {
  return {
    asset_id: "A1",
    filename: "IMG_0001.HEIC",
    media_type: "HEIC",
    media_category: "HEIC",
    file_size: 1,
    created_at: null,
    is_live_photo: false,
    has_edited_version: false,
    has_raw_version: false,
    thumbnail_url: "/api/assets/A1/thumbnail",
    ...over,
  };
}

const labels = (a: Asset) => badges(a).map((b) => b.label);

describe("assetCategory", () => {
  it("prefers the backend media_category", () => {
    expect(assetCategory({ media_category: "RAW", media_type: "RAF" })).toBe("RAW");
    expect(assetCategory({ media_category: "Video", media_type: "MOV" })).toBe("VIDEO");
  });

  it("falls back to extension when media_category missing (older backend)", () => {
    for (const ext of ["RAF", "CR2", "CR3", "ARW", "NEF", "DNG", "PEF", "SRW", "3FR"]) {
      expect(assetCategory({ media_category: null, media_type: ext })).toBe("RAW");
    }
    expect(assetCategory({ media_category: null, media_type: "MOV" })).toBe("VIDEO");
    expect(assetCategory({ media_category: null, media_type: "jpg" })).toBe("JPEG");
    expect(assetCategory({ media_category: null, media_type: null })).toBe("");
  });
});

describe("badges", () => {
  it("RAW badge for RAW-primary files (Fuji/Canon/Sony) — the reported bug", () => {
    expect(labels(asset({ media_type: "RAF", media_category: "RAW" }))).toContain("RAW");
    expect(labels(asset({ media_type: "CR3", media_category: "RAW" }))).toContain("RAW");
    expect(labels(asset({ media_type: "ARW", media_category: "RAW" }))).toContain("RAW");
    // and via fallback against an old backend that sends no media_category:
    expect(labels(asset({ media_type: "RAF", media_category: null }))).toContain("RAW");
  });

  it("RAW badge for JPEG+RAW companion pairs", () => {
    expect(labels(asset({ media_type: "JPG", media_category: "JPEG", has_raw_version: true }))).toContain("RAW");
  });

  it("never duplicates the RAW badge (RAW primary that also flags companion)", () => {
    const ls = labels(asset({ media_type: "RAF", media_category: "RAW", has_raw_version: true }));
    expect(ls.filter((l) => l === "RAW")).toHaveLength(1);
  });

  it("VIDEO badge uses the category (was broken: 'Video' vs extension list)", () => {
    expect(labels(asset({ media_type: "MOV", media_category: "Video" }))).toContain("VIDEO");
    expect(labels(asset({ media_type: "MP4", media_category: "Video" }))).toContain("VIDEO");
  });

  it("HEIC / LIVE / EDIT badges", () => {
    const ls = labels(asset({ is_live_photo: true, has_edited_version: true }));
    expect(ls).toEqual(["HEIC", "LIVE", "EDIT"]);
  });

  it("plain JPEG gets no badge", () => {
    expect(labels(asset({ media_type: "JPG", media_category: "JPEG" }))).toEqual([]);
  });
});
