import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { API_BASE } from "../config";
import AlbumChecklist from "../components/album-checklist";
import DownloadConfig, { DEFAULT_CONFIG, DownloadConfigValue, templateToArray } from "../components/download-config";
import type { Album, Asset } from "../types";

const RAW = ["CR2", "CR3", "NEF", "ARW", "DNG", "RAF", "RW2", "ORF"];
const VIDEO = ["MOV", "MP4", "M4V"];

function badges(a: Asset): { label: string; cls: string }[] {
  const out: { label: string; cls: string }[] = [];
  const mt = (a.media_type || "").toUpperCase();
  if (mt === "HEIC") out.push({ label: "HEIC", cls: "bg-slate-700" });
  if (RAW.includes(mt)) out.push({ label: "RAW", cls: "bg-purple-700" });
  // RAW companion (resOriginalAlt) on a non-RAW primary, e.g. JPEG+RAW pairs.
  else if (a.has_raw_version) out.push({ label: "RAW", cls: "bg-purple-700" });
  if (VIDEO.includes(mt)) out.push({ label: "VIDEO", cls: "bg-rose-700" });
  if (a.is_live_photo) out.push({ label: "LIVE", cls: "bg-amber-600" });
  if (a.has_edited_version) out.push({ label: "EDIT", cls: "bg-emerald-700" });
  return out;
}

export default function BrowserPage() {
  const nav = useNavigate();
  const [albums, setAlbums] = useState<Album[]>([]);
  const [selectedAlbums, setSelectedAlbums] = useState<Set<string>>(new Set());
  const [activeAlbum, setActiveAlbum] = useState<string | null>(null);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [offset, setOffset] = useState(0);
  const [loadingAssets, setLoadingAssets] = useState(false);
  const [selectedAssets, setSelectedAssets] = useState<Set<string>>(new Set());

  const [config, setConfig] = useState<DownloadConfigValue>(DEFAULT_CONFIG);
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

  const toggleSet = (set: Set<string>, key: string, fn: (s: Set<string>) => void) => {
    const n = new Set(set);
    n.has(key) ? n.delete(key) : n.add(key);
    fn(n);
  };

  const sampleAlbum = [...selectedAlbums][0] || activeAlbum || "Holidays";
  const template = templateToArray(config.templateStr);
  const canStart = selectedAlbums.size > 0 || selectedAssets.size > 0;

  const start = async () => {
    setErr(null);
    setLaunching(true);
    try {
      const job = await api.createJob({
        selected_albums: [...selectedAlbums],
        selected_asset_ids: [...selectedAssets],
        folder_structure: template,
        include_raw: config.filters.raw,
        include_jpeg: config.filters.jpeg,
        include_heic: config.filters.heic,
        include_video: config.filters.video,
        download_version: config.version,
        album_fanout: config.fanout,
        force_redownload: config.force,
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
      <aside className="w-64 shrink-0 border-r bg-white">
        <AlbumChecklist
          albums={albums}
          selected={selectedAlbums}
          onToggle={(name) => toggleSet(selectedAlbums, name, setSelectedAlbums)}
          activeAlbum={activeAlbum}
          onOpen={openAlbum}
        />
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
                  onClick={() => toggleSet(selectedAssets, a.asset_id, setSelectedAssets)}
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
                  {selectedAssets.has(a.asset_id) && <div className="absolute inset-0 bg-blue-500/20" />}
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
      <aside className="w-80 shrink-0 border-l bg-white overflow-y-auto p-4">
        <h2 className="font-semibold mb-3 text-sm">Download settings</h2>
        <DownloadConfig value={config} onChange={setConfig} sampleAlbum={sampleAlbum} />

        {err && <p className="text-xs text-red-600 my-2">{err}</p>}

        <button
          onClick={start}
          disabled={!canStart || launching || template.length === 0}
          className="w-full bg-blue-600 text-white rounded py-2 mt-4 hover:bg-blue-700 disabled:opacity-50"
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
