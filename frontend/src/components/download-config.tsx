import { useEffect, useState } from "react";
import { api } from "../api";
import { TOKENS } from "../config";

export interface DownloadConfigValue {
  filters: { jpeg: boolean; heic: boolean; video: boolean; raw: boolean };
  version: string;
  fanout: boolean;
  force: boolean;
  templateStr: string;
  dateFrom: string; // "YYYY-MM-DD" or "" (no lower bound)
  dateTo: string; // "YYYY-MM-DD" or "" (no upper bound)
}

export const DEFAULT_CONFIG: DownloadConfigValue = {
  filters: { jpeg: true, heic: true, video: true, raw: false },
  version: "edited",
  fanout: true,
  force: false,
  templateStr: "{year}/{month}/{album}",
  dateFrom: "",
  dateTo: "",
};

/** Date-range fields for the jobs API. The "to" day is inclusive (end of day UTC). */
export function dateRangeToApi(c: Pick<DownloadConfigValue, "dateFrom" | "dateTo">): {
  date_from: string | null;
  date_to: string | null;
} {
  return {
    date_from: c.dateFrom ? `${c.dateFrom}T00:00:00Z` : null,
    date_to: c.dateTo ? `${c.dateTo}T23:59:59Z` : null,
  };
}

export function templateToArray(s: string): string[] {
  return s.split("/").map((x) => x.trim()).filter(Boolean);
}

const SAMPLE: Record<string, string> = {
  year: "2024", month: "06", day: "15", mediatype: "HEIC",
  person: "Alice", make: "Apple", model: "iPhone 15 Pro", filename: "IMG_0042",
};

export function previewPath(templateStr: string, sampleAlbum: string): string {
  return (
    "/downloads/" +
    templateToArray(templateStr)
      .map((seg) => seg.replace(/\{(\w+)\}/g, (_, t) => (t === "album" ? sampleAlbum : SAMPLE[t]) ?? `{${t}}`))
      .join("/") +
    "/IMG_0042.HEIC"
  );
}

/** Shared download settings: formats, version, fanout, force, folder template. */
export default function DownloadConfig({
  value,
  onChange,
  sampleAlbum,
}: {
  value: DownloadConfigValue;
  onChange: (v: DownloadConfigValue) => void;
  sampleAlbum: string;
}) {
  const set = (patch: Partial<DownloadConfigValue>) => onChange({ ...value, ...patch });
  const setFilter = (k: keyof DownloadConfigValue["filters"], on: boolean) =>
    onChange({ ...value, filters: { ...value.filters, [k]: on } });

  // Token chips come from the backend (single source of truth); the static
  // list is only the offline/older-backend fallback.
  const [tokens, setTokens] = useState(TOKENS);
  useEffect(() => {
    api.tokens().then(setTokens).catch(() => {});
  }, []);

  return (
    <div className="text-sm">
      <fieldset className="mb-4">
        <legend className="text-xs font-semibold uppercase text-slate-400 mb-1">Formats</legend>
        {([["jpeg", "JPEG"], ["heic", "HEIC"], ["video", "Video"], ["raw", "RAW (large)"]] as const).map(
          ([k, label]) => (
            <label key={k} className="flex items-center gap-2 py-0.5">
              <input type="checkbox" checked={value.filters[k]} onChange={(e) => setFilter(k, e.target.checked)} />
              {label}
            </label>
          ),
        )}
      </fieldset>

      <label className="block mb-3">
        <span className="text-xs font-semibold uppercase text-slate-400">Version</span>
        <select
          className="w-full border rounded px-2 py-1 mt-1"
          value={value.version}
          onChange={(e) => set({ version: e.target.value })}
        >
          <option value="edited">Edited (fallback original)</option>
          <option value="original">Original</option>
          <option value="both">Both</option>
        </select>
      </label>

      <label className="flex items-center gap-2 mb-1">
        <input type="checkbox" checked={value.fanout} onChange={(e) => set({ fanout: e.target.checked })} />
        One copy per album
      </label>
      {value.fanout && value.templateStr.includes("{album}") && (
        <p className="text-xs text-amber-600 mb-2">⚠ Photos in several albums are duplicated (more disk used).</p>
      )}
      <label className="flex items-center gap-2 mb-4">
        <input type="checkbox" checked={value.force} onChange={(e) => set({ force: e.target.checked })} />
        Force re-download
      </label>

      <fieldset className="mb-4">
        <legend className="text-xs font-semibold uppercase text-slate-400 mb-1">
          Date range <span className="normal-case font-normal">(capture date, optional)</span>
        </legend>
        <div className="flex items-center gap-2">
          <input
            type="date"
            value={value.dateFrom}
            onChange={(e) => set({ dateFrom: e.target.value })}
            className="border rounded px-2 py-1 text-xs"
          />
          →
          <input
            type="date"
            value={value.dateTo}
            onChange={(e) => set({ dateTo: e.target.value })}
            className="border rounded px-2 py-1 text-xs"
          />
        </div>
        {(value.dateFrom || value.dateTo) && (
          <p className="text-[11px] text-slate-500 mt-1">
            Only photos captured {value.dateFrom ? `from ${value.dateFrom}` : ""}
            {value.dateFrom && value.dateTo ? " " : ""}
            {value.dateTo ? `until ${value.dateTo} (inclusive)` : ""}. Undated assets are excluded.
          </p>
        )}
      </fieldset>

      <div>
        <span className="text-xs font-semibold uppercase text-slate-400">Folder template</span>
        <div className="flex flex-wrap gap-1 my-1">
          {["{year}/{month}/{album}", "{year}/{album}", "{album}"].map((p) => (
            <button
              key={p}
              onClick={() => set({ templateStr: p })}
              className="text-xs bg-slate-100 hover:bg-slate-200 rounded px-1.5 py-0.5"
            >
              {p}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap gap-1 my-1">
          {tokens.map((t) => (
            <button
              key={t.id}
              title={t.example}
              onClick={() => set({ templateStr: value.templateStr ? `${value.templateStr}/{${t.id}}` : `{${t.id}}` })}
              className="text-xs bg-blue-50 text-blue-700 hover:bg-blue-100 rounded px-1.5 py-0.5"
            >
              {t.label}
            </button>
          ))}
        </div>
        <input
          className="w-full border rounded px-2 py-1 font-mono text-xs"
          value={value.templateStr}
          onChange={(e) => set({ templateStr: e.target.value })}
        />
        <p className="text-[11px] text-slate-500 mt-1 break-all">→ {previewPath(value.templateStr, sampleAlbum)}</p>
      </div>
    </div>
  );
}
