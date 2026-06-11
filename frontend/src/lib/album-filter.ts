import type { Album } from "../types";

/**
 * Normalize text for searching: strip diacritics (so "ete" matches "Été"),
 * lowercase, and trim. Album names from iCloud are frequently accented
 * (French/other locales), so an accent-sensitive `includes` silently matches
 * nothing — this fixes that.
 */
export function normalizeText(s: string): string {
  return (s ?? "")
    .normalize("NFD") // decompose: "é" -> "e" + combining acute accent
    .replace(/[̀-ͯ]/g, "") // drop combining diacritical marks
    .toLowerCase()
    .trim();
}

/**
 * Filter albums by a (possibly accented, mixed-case) query. An empty/blank
 * query returns the list unchanged. Names that are null/undefined are treated
 * as empty so one bad entry never throws and blanks the whole list.
 */
export function filterAlbums(albums: Album[], query: string): Album[] {
  const needle = normalizeText(query);
  if (!needle) return albums;
  return albums.filter((a) => normalizeText(a.name).includes(needle));
}
