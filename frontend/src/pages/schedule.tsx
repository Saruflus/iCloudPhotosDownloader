import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import AlbumChecklist from "../components/album-checklist";
import DownloadConfig, { DEFAULT_CONFIG, DownloadConfigValue, templateToArray } from "../components/download-config";
import type { Album } from "../types";

function buildCron(mode: string, hour: number, minute: number, everyN: number, custom: string): string {
  if (mode === "daily") return `${minute} ${hour} * * *`;
  if (mode === "everyN") return `0 */${everyN} * * *`;
  return custom.trim();
}

function parseCron(c: string) {
  const daily = c.match(/^(\d{1,2})\s+(\d{1,2})\s+\*\s+\*\s+\*$/);
  if (daily) return { mode: "daily", minute: +daily[1], hour: +daily[2], everyN: 6, custom: c };
  const en = c.match(/^0\s+\*\/(\d{1,2})\s+\*\s+\*\s+\*$/);
  if (en) return { mode: "everyN", minute: 0, hour: 2, everyN: +en[1], custom: c };
  return { mode: "custom", minute: 0, hour: 2, everyN: 6, custom: c };
}

function fmt(ts: string | null): string {
  if (!ts) return "—";
  const d = new Date(ts);
  return isNaN(d.getTime()) ? ts : d.toLocaleString();
}

export default function SchedulePage() {
  const [albums, setAlbums] = useState<Album[]>([]);
  const [selectedAlbums, setSelectedAlbums] = useState<Set<string>>(new Set());
  const [config, setConfig] = useState<DownloadConfigValue>(DEFAULT_CONFIG);

  const [mode, setMode] = useState("daily");
  const [hour, setHour] = useState(2);
  const [minute, setMinute] = useState(0);
  const [everyN, setEveryN] = useState(6);
  const [custom, setCustom] = useState("0 2 * * *");

  const [enabled, setEnabled] = useState(true);
  const [lastRun, setLastRun] = useState<string | null>(null);
  const [nextRun, setNextRun] = useState<string | null>(null);

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.albums().then(setAlbums).catch((e) => setErr(e.message));
  }, []);

  useEffect(() => {
    api
      .getSchedule()
      .then((s) => {
        if (!s) return;
        const jc = s.job_config || {};
        setSelectedAlbums(new Set((jc.selected_albums as string[]) || []));
        setConfig({
          filters: {
            jpeg: jc.include_jpeg ?? true,
            heic: jc.include_heic ?? true,
            video: jc.include_video ?? true,
            raw: jc.include_raw ?? false,
          },
          version: (jc.download_version as string) || "edited",
          fanout: jc.album_fanout ?? true,
          force: jc.force_redownload ?? false,
          templateStr: ((jc.folder_structure as string[]) || ["{year}", "{month}", "{album}"]).join("/"),
        });
        setEnabled(s.enabled);
        setLastRun(s.last_run_at);
        setNextRun(s.next_run_at);
        const p = parseCron(s.cron_expression);
        setMode(p.mode);
        setHour(p.hour);
        setMinute(p.minute);
        setEveryN(p.everyN);
        setCustom(p.custom);
      })
      .catch((e) => setErr(e.message))
      .finally(() => setLoading(false));
  }, []);

  const cron = useMemo(() => buildCron(mode, hour, minute, everyN, custom), [mode, hour, minute, everyN, custom]);
  const sampleAlbum = [...selectedAlbums][0] || "Holidays";

  const toggleAlbum = (name: string) =>
    setSelectedAlbums((prev) => {
      const n = new Set(prev);
      n.has(name) ? n.delete(name) : n.add(name);
      return n;
    });

  const save = async () => {
    setErr(null);
    setMsg(null);
    setSaving(true);
    try {
      const s = await api.putSchedule({
        cron_expression: cron,
        enabled,
        job_config: {
          selected_albums: [...selectedAlbums],
          folder_structure: templateToArray(config.templateStr),
          include_raw: config.filters.raw,
          include_jpeg: config.filters.jpeg,
          include_heic: config.filters.heic,
          include_video: config.filters.video,
          download_version: config.version,
          album_fanout: config.fanout,
          force_redownload: config.force,
        },
      });
      setLastRun(s.last_run_at);
      setNextRun(s.next_run_at);
      setMsg("Schedule saved.");
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <div className="p-8 text-slate-500">Loading…</div>;

  const numCls = "w-16 border rounded px-2 py-1";

  return (
    <div className="flex h-[calc(100vh-49px)]">
      {/* LEFT: albums to sync */}
      <aside className="w-64 shrink-0 border-r bg-white">
        <AlbumChecklist albums={albums} selected={selectedAlbums} onToggle={toggleAlbum} />
      </aside>

      {/* CENTER: schedule + config */}
      <main className="flex-1 overflow-y-auto p-6">
        <div className="max-w-2xl">
          <h1 className="text-lg font-semibold mb-4">Schedule</h1>

          <section className="bg-white border rounded-lg p-4 mb-4">
            <h3 className="text-sm font-semibold mb-2">When</h3>
            <div className="flex gap-2 mb-2">
              {[["daily", "Daily"], ["everyN", "Every N hours"], ["custom", "Custom cron"]].map(([m, label]) => (
                <button
                  key={m}
                  onClick={() => setMode(m)}
                  className={`text-sm px-3 py-1 rounded ${mode === m ? "bg-blue-600 text-white" : "bg-slate-100 hover:bg-slate-200"}`}
                >
                  {label}
                </button>
              ))}
            </div>

            {mode === "daily" && (
              <div className="flex items-center gap-2 text-sm">
                at
                <input type="number" min={0} max={23} value={hour} onChange={(e) => setHour(+e.target.value)} className={numCls} />
                h
                <input type="number" min={0} max={59} value={minute} onChange={(e) => setMinute(+e.target.value)} className={numCls} />
              </div>
            )}
            {mode === "everyN" && (
              <div className="flex items-center gap-2 text-sm">
                every
                <input type="number" min={1} max={23} value={everyN} onChange={(e) => setEveryN(+e.target.value)} className={numCls} />
                hour(s)
              </div>
            )}
            {mode === "custom" && (
              <input
                value={custom}
                onChange={(e) => setCustom(e.target.value)}
                placeholder="0 2 * * *"
                className="w-full border rounded px-2 py-1 font-mono text-sm"
              />
            )}
            <p className="text-[11px] text-slate-500 mt-2 font-mono">cron: {cron || "—"}</p>
          </section>

          <section className="bg-white border rounded-lg p-4 mb-4">
            <h3 className="text-sm font-semibold mb-2">What &amp; where</h3>
            <DownloadConfig value={config} onChange={setConfig} sampleAlbum={sampleAlbum} />
          </section>

          <section className="bg-white border rounded-lg p-4">
            <label className="flex items-center gap-2 text-sm mb-3">
              <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
              Enabled
            </label>
            <div className="text-xs text-slate-500 mb-3">
              <div>Last run: {fmt(lastRun)}</div>
              <div>Next run: {enabled ? fmt(nextRun) : "—"}</div>
            </div>
            {err && <p className="text-sm text-red-600 mb-2">{err}</p>}
            {msg && <p className="text-sm text-emerald-600 mb-2">{msg}</p>}
            <button
              onClick={save}
              disabled={saving || selectedAlbums.size === 0 || !cron}
              className="bg-blue-600 text-white rounded px-4 py-2 hover:bg-blue-700 disabled:opacity-50"
            >
              {saving ? "Saving…" : "Save schedule"}
            </button>
            {selectedAlbums.size === 0 && (
              <p className="text-[11px] text-slate-500 mt-2">Select at least one album to sync.</p>
            )}
          </section>
        </div>
      </main>
    </div>
  );
}
