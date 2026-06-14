import { describe, expect, it } from "vitest";
import { sortAssets } from "./sort";
import type { Asset } from "../types";

const mk = (id: string, created_at: string | null): Asset => ({
  asset_id: id,
  filename: `${id}.jpg`,
  media_type: "JPG",
  media_category: "JPEG",
  file_size: 1,
  created_at,
  is_live_photo: false,
  has_edited_version: false,
  has_raw_version: false,
  thumbnail_url: `/api/assets/${id}/thumbnail`,
});

const A = mk("a", "2024-06-15T10:00:00Z");
const B = mk("b", "2023-01-01T00:00:00Z");
const C = mk("c", "2025-12-31T23:59:00Z");
const U = mk("u", null);
const LIST = [A, B, C, U];

const ids = (l: Asset[]) => l.map((a) => a.asset_id);

describe("sortAssets", () => {
  it("'album' keeps iCloud order and the same array", () => {
    expect(sortAssets(LIST, "album")).toBe(LIST);
  });

  it("newest first, undated last", () => {
    expect(ids(sortAssets(LIST, "newest"))).toEqual(["c", "a", "b", "u"]);
  });

  it("oldest first, undated last", () => {
    expect(ids(sortAssets(LIST, "oldest"))).toEqual(["b", "a", "c", "u"]);
  });

  it("does not mutate the input", () => {
    const copy = [...LIST];
    sortAssets(LIST, "newest");
    expect(LIST).toEqual(copy);
  });
});
