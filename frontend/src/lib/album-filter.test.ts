import { describe, expect, it } from "vitest";
import { filterAlbums, normalizeText } from "./album-filter";
import type { Album } from "../types";

const mk = (...names: string[]): Album[] => names.map((name) => ({ name, asset_count: null }));

describe("normalizeText", () => {
  it("strips accents, lowercases and trims", () => {
    expect(normalizeText("  Été ")).toBe("ete");
    expect(normalizeText("Noël")).toBe("noel");
    expect(normalizeText("Récents")).toBe("recents");
    expect(normalizeText("Léa")).toBe("lea");
  });

  it("is null/undefined safe", () => {
    expect(normalizeText(undefined as unknown as string)).toBe("");
    expect(normalizeText(null as unknown as string)).toBe("");
  });
});

describe("filterAlbums", () => {
  const albums = mk("Été 2024", "Vacances", "Noël", "Anniversaire Léa", "Récents");

  it("matches accented names from an unaccented query (the reported bug)", () => {
    expect(filterAlbums(albums, "ete").map((a) => a.name)).toEqual(["Été 2024"]);
    expect(filterAlbums(albums, "noel").map((a) => a.name)).toEqual(["Noël"]);
    expect(filterAlbums(albums, "lea").map((a) => a.name)).toEqual(["Anniversaire Léa"]);
  });

  it("is case-insensitive", () => {
    expect(filterAlbums(albums, "RECENT").map((a) => a.name)).toEqual(["Récents"]);
    expect(filterAlbums(albums, "VaCaN").map((a) => a.name)).toEqual(["Vacances"]);
  });

  it("matches substrings anywhere in the name", () => {
    expect(filterAlbums(albums, "lea").map((a) => a.name)).toEqual(["Anniversaire Léa"]);
  });

  it("ignores surrounding whitespace in the query", () => {
    expect(filterAlbums(albums, "  vac  ").map((a) => a.name)).toEqual(["Vacances"]);
  });

  it("returns the full list for an empty/blank query", () => {
    expect(filterAlbums(albums, "")).toHaveLength(5);
    expect(filterAlbums(albums, "   ")).toHaveLength(5);
  });

  it("returns nothing when there is no match", () => {
    expect(filterAlbums(albums, "zzz")).toEqual([]);
  });

  it("does not throw on a null album name", () => {
    const bad = [{ name: null as unknown as string, asset_count: null }, ...mk("Été 2024")];
    expect(filterAlbums(bad, "ete").map((a) => a.name)).toEqual(["Été 2024"]);
  });
});
