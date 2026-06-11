import { useState } from "react";
import { filterAlbums } from "../lib/album-filter";
import type { Album } from "../types";

/** Album list with multi-select checkboxes and a name search box.
 *  `onOpen` (optional) is called when the name is clicked (Browser uses it to
 *  load the asset grid; Schedule omits it). */
export default function AlbumChecklist({
  albums,
  selected,
  onToggle,
  activeAlbum,
  onOpen,
}: {
  albums: Album[];
  selected: Set<string>;
  onToggle: (name: string) => void;
  activeAlbum?: string | null;
  onOpen?: (name: string) => void;
}) {
  const [q, setQ] = useState("");
  const filtered = filterAlbums(albums, q);

  return (
    <div className="flex flex-col h-full">
      <div className="p-2 border-b">
        <input
          className="w-full border rounded px-2 py-1 text-sm"
          placeholder="Search albums…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
      </div>
      <div className="flex-1 overflow-y-auto">
        {filtered.map((al) => (
          <div
            key={al.name}
            className={`flex items-center gap-2 px-3 py-1.5 text-sm hover:bg-slate-50 ${
              activeAlbum === al.name ? "bg-blue-50" : ""
            }`}
          >
            <input type="checkbox" checked={selected.has(al.name)} onChange={() => onToggle(al.name)} />
            <span
              className={`flex-1 truncate ${onOpen ? "cursor-pointer" : ""}`}
              onClick={() => onOpen?.(al.name)}
            >
              {al.name}
            </span>
            <span className="text-xs text-slate-400">{al.asset_count ?? "?"}</span>
          </div>
        ))}
        {filtered.length === 0 && <p className="text-xs text-slate-400 p-3">No album matches “{q}”.</p>}
      </div>
    </div>
  );
}
