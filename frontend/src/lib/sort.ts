import type { Asset } from "../types";

export type AssetSort = "album" | "newest" | "oldest";

export const SORT_OPTIONS: { id: AssetSort; label: string }[] = [
  { id: "album", label: "Album order" },
  { id: "newest", label: "Newest first" },
  { id: "oldest", label: "Oldest first" },
];

/** Sort loaded assets by capture date. Undated assets always sink to the end;
 *  "album" keeps iCloud's native order. Pure — returns a new array. */
export function sortAssets(assets: Asset[], sort: AssetSort): Asset[] {
  if (sort === "album") return assets;
  const ts = (a: Asset) => (a.created_at ? Date.parse(a.created_at) : NaN);
  return [...assets].sort((x, y) => {
    const tx = ts(x);
    const ty = ts(y);
    if (Number.isNaN(tx) && Number.isNaN(ty)) return 0;
    if (Number.isNaN(tx)) return 1;
    if (Number.isNaN(ty)) return -1;
    return sort === "newest" ? ty - tx : tx - ty;
  });
}
