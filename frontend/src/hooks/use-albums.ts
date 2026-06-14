import { useEffect, useState } from "react";
import { api } from "../api";
import type { Album } from "../types";

const COUNT_WORKERS = 4;

/** Album list with lazily-filled counts.
 *
 * /api/albums now returns instantly without counts (each count is a separate
 * iCloud query). This hook renders the list immediately, then fetches counts a
 * few at a time and merges them in as they arrive.
 */
export function useAlbums(): { albums: Album[]; error: string | null } {
  const [albums, setAlbums] = useState<Album[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .albums()
      .then((list) => {
        if (!alive) return;
        setAlbums(list);
        const queue = list.filter((a) => a.asset_count == null).map((a) => a.name);
        for (let i = 0; i < COUNT_WORKERS; i++) {
          void (async () => {
            while (alive && queue.length > 0) {
              const name = queue.shift()!;
              try {
                const { asset_count } = await api.albumCount(name);
                if (!alive) return;
                setAlbums((prev) =>
                  prev.map((al) => (al.name === name ? { ...al, asset_count } : al)),
                );
              } catch {
                /* count stays "?" — list itself is unaffected */
              }
            }
          })();
        }
      })
      .catch((e) => {
        if (alive) setError((e as Error).message);
      });
    return () => {
      alive = false;
    };
  }, []);

  return { albums, error };
}
