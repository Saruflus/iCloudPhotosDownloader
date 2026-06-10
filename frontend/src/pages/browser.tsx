import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { API_BASE, TOKENS } from "../config";
import type { Album, Asset } from "../types";

const RAW = ["CR2", "CR3", "NEF", "ARW", "DNG", "RAF", "RW2", "ORF"];
const VIDEO = ["MOV", "MP4", "M4V"];

function badges(a: Asset): { label: string; cls: string }[] {
  const out: { label: string; cls: string }[] = [];
  const mt = (a.media_type || "").toUpperCase();
  if (mt === "HEIC") out.push({ label: "HEIC", cls: "bg-slate-700" });
  if (RAW.includes(mt)) out.push({ label: "RAW", cls: "bg-purple-700" });
  if (VIDEO.includes(mt)) out.push({ label: "VIDEO", cls: "bg-rose-700" });
  if (a.is_live_photo) out.push({ label: "LIVE", cls: "bg-amber-600" });
  if (a.has_edited_version) out.push({ label: "EDIT", cls: "bg-emerald-700" });
  return out;
}

const SAMPLE: Record<string, string> = {
  year: "2024", month: "06", day: "15", mediatype: "HEIC",
  person: "Alice", make: "Apple", model: "iPhone 15 Pro", filename: "IMG_0042",
};

export default function BrowserPage() {
  const nav = useNavigate();
  const [albums, setAlbums] = useState<Album[]>([]);
  const [selectedAlbums, setSelectedAlbums] = useState<Set<string>>(new Set());
  const [activeAlbum, setActiveAlbum] = useState<string | null>(null);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [offset, setOffset] = useState(0);
  const [loadingAssets, setLoadingAssets] = useState(false);
  const [selectedAssets, setSelectedAssets] = useState<Set<string>>(new Set());

  const [filters, setFilters] = useState({ jpeg: true, heic: true, video: true, raw: false });
  const [version, setVersion] = useState("edited");
  const [fanout, setFanout] = useState(true);
  const [force, setForce] = useState(false);
  const [templateStr, setTemplateStr] = useState("{year}/{month}/{album}");
  const [err, setErr] = useState<string | null>(null);
  const [launching, setLaunching] = useState(false);

  useEffect(() => {
    api.albums().then(setAlbums).catch((e) => setErr(e.message));
  }, []);

  const openAlbum = async (name: string) => {
    setActiveAlbum(name);
    setAssets([]);
    setOffset(0);
    setLoadingAssets(true);
    try {
      const a = await api.assets(name, 0, 60);
      setAssets(a);
      setOffset(60);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setLoadingAssets(false);
    }
  };

  const loadMore = async () => {
    if (!activeAlbum) return;
    setLoadingAssets(true);
    try {
      const a = await api.assets(activeAlbum, offset, 60);
      setAssets((p) => [...p, ...a]);
      setOffset((o) => o + 60);
    } finally {
      setLoadingAssets(false);
    }
  };

  const toggle = (set: Set<string>, key: string, fn: (s: Set<string>) => void) => {
    const n = new Set(set);
    n.has(key) ? n.delete(key) : n.add(key);
    fn(n);
  };

  const template = templateStr.split("/").map((s) => s.trim()).filter(Boolean);
  const sampleAlbum = [...selectedAlbums][0] || activeAlbum || "Holidays";
  const preview =
    "/downloads/" +
    template
      .map((seg) =>
        seg.replace(/\{(\w+)\}/g, (_, t) => (t === "album" ? sampleAlbum : SAMPLE[t]) ?? `{${t}}`),
      )
      .join("/") +
    "/IMG_0042.HEIC";

  const canStart = selectedAlbums.size > 0 || selectedAssets.size > 0;

  const start = async () => {
    setErr(null);
    setLaunching(true);
    try {
      const job = await api.createJob({
        selected_albums: [...selectedAlbums],
        selected_asset_ids: [...selectedAssets],
        folder_structure: template,
        include_raw: filters.raw,
        include_jpeg: filters.jpeg,
        include_heic: filters.heic,
        include_video: filters.video,
        download_version: version,
        album_fanout: fanout,
        force_redownload: force,
      });
      nav(`/jobs?focus=${job.id}`);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setLaunching(false);
    }
  };

  return (
    <div className="flex h-[calc(100vh-49px)]">
      {/* LEFT: albums */}
      <aside className="w-64 shrink-0 border-r bg-white overflow-y-auto">
        <h2 className="px-4 py-2 text-xs font-semibold uppercase text-slate-400">Albums</h2>
        {albums.map((al) => (
          <div
            key={al.name}
            className={`flex items-center gap-2 px-4 py-1.5 text-sm cursor-pointer hover:bg-slate-50 ${
              activeAlbum === al.name ? "bg-blue-50" : ""
            }`}
          >
            <input
              type="checkbox"
              checked={selectedAlbums.has(al.name)}
              onChange={() => toggle(selectedAlbums, al.name, setSelectedAlbums)}
            />
            <span className="flex-1 truncate" onClick={() => openAlbum(al.name)}>
              {al.name}
            </span>
            <span className="text-xs text-slate-400">{al.asset_count ?? "?"}</span>
          </div>
        ))}
      </aside>

      {/* CENTER: assets */}
      <main className="flex-1 overflow-y-auto p-4">
        {!activeAlbum ? (
          <p className="text-slate-400 mt-8 text-center">Click an album to preview its photos.</p>
        ) : (
          <>
            <div className="flex items-center justify-between mb-3">
              <h2 className="font-medium">{activeAlbum}</h2>
              <button
                className="text-xs text-blue-600 hover:underline"
                onClick={() =>
                  setSelectedAssets((prev) => {
                    const all = assets.every((a) => prev.has(a.asset_id));
                    const n = new Set(prev);
                    assets.forEach((a) => (all ? n.delete(a.asset_id) : n.add(a.asset_id)));
                    return n;
                  })
                }
              >
                Select / clear all (loaded)
              </button>
            </div>
            <div className="grid grid-cols-3 sm:grid-cols-4 lg:grid-cols-6 gap-2">
              {assets.map((a) => (
                <button
                  key={a.asset_id}
                  onClick={() => toggle(selectedAssets, a.asset_id, setSelectedAssets)}
                  className={`relative aspect-square rounded overflow-hidden border-2 ${
                    selectedAssets.has(a.asset_id) ? "border-blue-500" : "border-transparent"
                  }`}
                >
                  <img src={API_BASE + a.thumbnail_url} loading="lazy" className="w-full h-full object-cover bg-slate-200" />
                  <div className="absolute top-1 left-1 flex flex-wrap gap-0.5">
                    {badges(a).map((b) => (
                      <span key={b.label} className={`text-[9px] text-white px-1 rounded ${b.cls}`}>
                        {b.label}
                      </span>
                    ))}
                  </div>
                  {selectedAssets.has(a.asset_id) && (
                    <div className="absolute inset-0 bg-blue-500/20" />
                  )}
                </button>
              ))}
            </div>
            <div className="text-center mt-4">
              <button
                onClick={loadMore}
                disabled={loadingAssets}
                className="text-sm text-blue-600 hover:underline disabled:opacity-50"
              >
                {loadingAssets ? "Loading…" : "Load more"}
              </button>
            </div>
          </>
        )}
      </main>

      {/* RIGHT: config + launch */}
      <aside className="w-80 shrink-0 border-l bg-white overflow-y-auto p-4 text-sm">
        <h2 className="font-semibold mb-3">Download settings</h2>

        <fieldset className="mb-4">
          <legend className="text-xs font-semibold uppercase text-slate-400 mb-1">Formats</legend>
          {([["jpeg", "JPEG"], ["heic", "HEIC"], ["video", "Video"], ["raw", "RAW (large)"]] as const).map(
            ([k, label]) => (
              <label key={k} className="flex items-center gap-2 py-0.5">
                <input
                  type="checkbox"
                  checked={filters[k]}
                  onChange={(e) => setFilters((f) => ({ ...f, [k]: e.target.checked }))}
                />
                {label}
              </label>
            ),
          )}
        </fieldset>

        <label className="block mb-3">
          <span className="text-xs font-semibold uppercase text-slate-400">Version</span>
          <select
            className="w-full border rounded px-2 py-1 mt-1"
            value={version}
            onChange={(e) => setVersion(e.target.value)}
          >
            <option value="edited">Edited (fallback original)</option>
            <option value="original">Original</option>
            <option value="both">Both</option>
          </select>
        </label>

        <label className="flex items-center gap-2 mb-1">
          <input type="checkbox" checked={fanout} onChange={(e) => setFanout(e.target.checked)} />
          One copy per album
        </label>
        {fanout && templateStr.includes("{album}") && (
          <p className="text-xs text-amber-600 mb-2">
            ⚠ Photos in several albums are duplicated (more disk used).
          </p>
        )}
        <label className="flex items-center gap-2 mb-4">
          <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />
          Force re-download
        </label>

        <div className="mb-2">
          <span className="text-xs font-semibold uppercase text-slate-400">Folder template</span>
          <div className="flex flex-wrap gap-1 my-1">
            {["{year}/{month}/{album}", "{year}/{album}", "{album}"].map((p) => (
              <button
                key={p}
                onClick={() => setTemplateStr(p)}
                className="text-xs bg-slate-100 hover:bg-slate-200 rounded px-1.5 py-0.5"
              >
                {p}
              </button>
            ))}
          </div>
          <div className="flex flex-wrap gap-1 my-1">
            {TOKENS.map((t) => (
              <button
                key={t.id}
                title={t.example}
                onClick={() => setTemplateStr((s) => (s ? `${s}/{${t.id}}` : `{${t.id}}`))}
                className="text-xs bg-blue-50 text-blue-700 hover:bg-blue-100 rounded px-1.5 py-0.5"
              >
                {t.label}
              </button>
            ))}
          </div>
          <input
            className="w-full border rounded px-2 py-1 font-mono text-xs"
            value={templateStr}
            onChange={(e) => setTemplateStr(e.target.value)}
          />
          <p className="text-[11px] text-slate-500 mt-1 break-all">→ {preview}</p>
        </div>

        {err && <p className="text-xs text-red-600 my-2">{err}</p>}

        <button
          onClick={start}
          disabled={!canStart || launching || template.length === 0}
          className="w-full bg-blue-600 text-white rounded py-2 mt-2 hover:bg-blue-700 disabled:opacity-50"
        >
          {launching ? "Starting…" : "Start download"}
        </button>
        <p className="text-[11px] text-slate-500 mt-2">
          {selectedAlbums.size} album(s)
          {selectedAssets.size > 0 ? `, ${selectedAssets.size} specific photo(s)` : " (whole albums)"}
        </p>
      </aside>
    </div>
  );
}
