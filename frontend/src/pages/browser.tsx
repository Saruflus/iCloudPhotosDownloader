import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import AlbumChecklist from "../components/album-checklist";
import AssetGrid from "../components/asset-grid";
import DownloadConfig, { DEFAULT_CONFIG, DownloadConfigValue, dateRangeToApi, templateToArray } from "../components/download-config";
import { useAlbums } from "../hooks/use-albums";
import { AssetSort, SORT_OPTIONS, sortAssets } from "../lib/sort";
import type { Asset } from "../types";

export default function BrowserPage() {
  const nav = useNavigate();
  const { albums, error: albumsError } = useAlbums();
  const [selectedAlbums, setSelectedAlbums] = useState<Set<string>>(new Set());
  const [activeAlbum, setActiveAlbum] = useState<string | null>(null);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [offset, setOffset] = useState(0);
  const [loadingAssets, setLoadingAssets] = useState(false);
  const [hasMore, setHasMore] = useState(true);
  const [selectedAssets, setSelectedAssets] = useState<Set<string>>(new Set());
  const [sort, setSort] = useState<AssetSort>("album");

  const [config, setConfig] = useState<DownloadConfigValue>(DEFAULT_CONFIG);
  const [verify, setVerify] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [launching, setLaunching] = useState(false);
  const [preview, setPreview] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);

  const sortedAssets = useMemo(() => sortAssets(assets, sort), [assets, sort]);

  const PAGE = 60;

  const openAlbum = async (name: string) => {
    setActiveAlbum(name);
    setAssets([]);
    setOffset(0);
    setHasMore(true);
    setLoadingAssets(true);
    try {
      const a = await api.assets(name, 0, PAGE);
      setAssets(a);
      setOffset(PAGE);
      setHasMore(a.length === PAGE);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setLoadingAssets(false);
    }
  };

  const loadMore = async () => {
    if (!activeAlbum || loadingAssets || !hasMore) return;
    setLoadingAssets(true);
    try {
      const a = await api.assets(activeAlbum, offset, PAGE);
      setAssets((p) => [...p, ...a]);
      setOffset((o) => o + PAGE);
      setHasMore(a.length === PAGE);
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

  const jobBody = () => ({
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
    job_type: verify ? "verify" : "download",
    ...dateRangeToApi(config),
  });

  const start = async () => {
    setErr(null);
    setLaunching(true);
    try {
      const job = await api.createJob(jobBody());
      nav(`/jobs?focus=${job.id}`);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setLaunching(false);
    }
  };

  const runPreview = async () => {
    setErr(null);
    setPreview(null);
    setPreviewing(true);
    try {
      const p = await api.previewJob(jobBody());
      setPreview(
        `${p.to_download} to download · ${p.already_completed} already done · ${p.matching} matching`,
      );
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setPreviewing(false);
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
      <main className="flex-1 p-4 flex flex-col min-h-0">
        {!activeAlbum ? (
          <p className="text-slate-400 mt-8 text-center">Click an album to preview its photos.</p>
        ) : (
          <>
            <div className="flex items-center justify-between mb-3 gap-3">
              <h2 className="font-medium">{activeAlbum}</h2>
              <div className="flex items-center gap-3">
                <select
                  className="text-xs border rounded px-1.5 py-1"
                  value={sort}
                  onChange={(e) => setSort(e.target.value as AssetSort)}
                >
                  {SORT_OPTIONS.map((o) => (
                    <option key={o.id} value={o.id}>
                      {o.label}
                    </option>
                  ))}
                </select>
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
            </div>
            <AssetGrid
              assets={sortedAssets}
              selected={selectedAssets}
              onToggle={(id) => toggleSet(selectedAssets, id, setSelectedAssets)}
              onEndReached={loadMore}
            />
            <div className="text-center mt-2 text-xs text-slate-500">
              {assets.length} loaded
              {loadingAssets && " · loading…"}
              {!loadingAssets && hasMore && (
                <button onClick={loadMore} className="ml-2 text-blue-600 hover:underline">
                  Load more
                </button>
              )}
              {!hasMore && " · all loaded"}
            </div>
          </>
        )}
      </main>

      {/* RIGHT: config + launch */}
      <aside className="w-80 shrink-0 border-l bg-white overflow-y-auto p-4">
        <h2 className="font-semibold mb-3 text-sm">Download settings</h2>
        <DownloadConfig value={config} onChange={setConfig} sampleAlbum={sampleAlbum} />

        <label className="flex items-center gap-2 text-sm mt-3 pt-3 border-t">
          <input type="checkbox" checked={verify} onChange={(e) => setVerify(e.target.checked)} />
          Verify &amp; repair
        </label>
        {verify && (
          <p className="text-[11px] text-slate-500 mt-1">
            Re-downloads files missing on disk for already-synced photos in the selected albums.
          </p>
        )}

        {(err || albumsError) && <p className="text-xs text-red-600 my-2">{err || albumsError}</p>}

        <button
          onClick={runPreview}
          disabled={!canStart || previewing || template.length === 0}
          className="w-full border border-blue-600 text-blue-600 rounded py-1.5 mt-4 hover:bg-blue-50 disabled:opacity-50 text-sm"
        >
          {previewing ? "Scanning…" : "Preview (dry run)"}
        </button>
        {preview && <p className="text-xs text-slate-600 mt-1.5">{preview}</p>}

        <button
          onClick={start}
          disabled={!canStart || launching || template.length === 0}
          className="w-full bg-blue-600 text-white rounded py-2 mt-2 hover:bg-blue-700 disabled:opacity-50"
        >
          {launching ? "Starting…" : verify ? "Start verify & repair" : "Start download"}
        </button>
        <p className="text-[11px] text-slate-500 mt-2">
          {selectedAlbums.size} album(s)
          {selectedAssets.size > 0 ? `, ${selectedAssets.size} specific photo(s)` : " (whole albums)"}
        </p>
      </aside>
    </div>
  );
}
