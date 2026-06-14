import { describe, expect, it } from "vitest";
import { dateRangeToApi, templateToArray, previewPath, DEFAULT_CONFIG } from "./download-config";

describe("templateToArray", () => {
  it("splits on / and trims", () => {
    expect(templateToArray("{year}/{month}/{album}")).toEqual(["{year}", "{month}", "{album}"]);
    expect(templateToArray(" {year} / Photos / {album} ")).toEqual(["{year}", "Photos", "{album}"]);
  });

  it("drops empty segments (doubled or trailing slashes)", () => {
    expect(templateToArray("{year}//{album}/")).toEqual(["{year}", "{album}"]);
    expect(templateToArray("")).toEqual([]);
  });
});

describe("previewPath", () => {
  it("fills tokens with samples and the real album name", () => {
    expect(previewPath("{year}/{month}/{album}", "Vacances")).toBe(
      "/downloads/2024/06/Vacances/IMG_0042.HEIC",
    );
  });

  it("keeps unknown tokens literal", () => {
    expect(previewPath("{nope}/{album}", "X")).toBe("/downloads/{nope}/X/IMG_0042.HEIC");
  });

  it("default config preview is sane", () => {
    expect(previewPath(DEFAULT_CONFIG.templateStr, "Holidays")).toBe(
      "/downloads/2024/06/Holidays/IMG_0042.HEIC",
    );
  });
});

describe("dateRangeToApi", () => {
  it("empty range → nulls (no filter)", () => {
    expect(dateRangeToApi({ dateFrom: "", dateTo: "" })).toEqual({ date_from: null, date_to: null });
  });

  it("from at start of day, to at end of day (inclusive)", () => {
    expect(dateRangeToApi({ dateFrom: "2024-01-01", dateTo: "2024-12-31" })).toEqual({
      date_from: "2024-01-01T00:00:00Z",
      date_to: "2024-12-31T23:59:59Z",
    });
  });

  it("open-ended ranges", () => {
    expect(dateRangeToApi({ dateFrom: "2024-01-01", dateTo: "" }).date_to).toBeNull();
    expect(dateRangeToApi({ dateFrom: "", dateTo: "2024-12-31" }).date_from).toBeNull();
  });
});
