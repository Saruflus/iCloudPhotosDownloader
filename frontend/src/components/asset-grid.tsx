import { useEffect, useRef, useState } from "react";
import { FixedSizeGrid } from "react-window";
import type { GridChildComponentProps, GridOnItemsRenderedProps } from "react-window";
import { API_BASE } from "../config";
import { badges } from "../lib/badges";
import type { Asset } from "../types";

const MIN_TILE = 140; // px — tiles grow to fill, never shrink below this

/** Virtualized photo grid (Lot 3): only visible tiles are in the DOM, so
 *  1k+ albums scroll smoothly. Calls `onEndReached` near the bottom so the
 *  page can auto-load the next batch. */
export default function AssetGrid({
  assets,
  selected,
  onToggle,
  onEndReached,
}: {
  assets: Asset[];
  selected: Set<string>;
  onToggle: (id: string) => void;
  onEndReached?: () => void;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  // jsdom (tests) has no ResizeObserver — these defaults keep it rendering.
  const [size, setSize] = useState({ width: 800, height: 600 });

  useEffect(() => {
    const el = wrapRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      const r = entries[0].contentRect;
      if (r.width > 0 && r.height > 0) setSize({ width: r.width, height: r.height });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const columnCount = Math.max(2, Math.min(8, Math.floor(size.width / MIN_TILE)));
  const cell = Math.floor(size.width / columnCount);
  const rowCount = Math.ceil(assets.length / columnCount);

  const handleRendered = ({ visibleRowStopIndex }: GridOnItemsRenderedProps) => {
    if (onEndReached && rowCount > 0 && visibleRowStopIndex >= rowCount - 2) {
      onEndReached();
    }
  };

  const Cell = ({ columnIndex, rowIndex, style }: GridChildComponentProps) => {
    const a = assets[rowIndex * columnCount + columnIndex];
    if (!a) return null;
    const sel = selected.has(a.asset_id);
    return (
      <div style={style} className="p-1">
        <button
          onClick={() => onToggle(a.asset_id)}
          className={`relative w-full h-full rounded overflow-hidden border-2 ${
            sel ? "border-blue-500" : "border-transparent"
          }`}
        >
          <img
            src={API_BASE + a.thumbnail_url}
            loading="lazy"
            className="w-full h-full object-cover bg-slate-200"
          />
          <div className="absolute top-1 left-1 flex flex-wrap gap-0.5">
            {badges(a).map((b) => (
              <span key={b.label} className={`text-[9px] text-white px-1 rounded ${b.cls}`}>
                {b.label}
              </span>
            ))}
          </div>
          {sel && <div className="absolute inset-0 bg-blue-500/20" />}
        </button>
      </div>
    );
  };

  return (
    <div ref={wrapRef} className="flex-1 min-h-0">
      <FixedSizeGrid
        columnCount={columnCount}
        rowCount={rowCount}
        columnWidth={cell}
        rowHeight={cell}
        width={size.width}
        height={size.height}
        onItemsRendered={handleRendered}
        itemKey={({ columnIndex, rowIndex }) =>
          assets[rowIndex * columnCount + columnIndex]?.asset_id ?? `${rowIndex}:${columnIndex}`
        }
      >
        {Cell}
      </FixedSizeGrid>
    </div>
  );
}
