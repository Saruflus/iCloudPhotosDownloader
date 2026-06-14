import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import AlbumChecklist from "../components/album-checklist";
import DownloadConfig, { DEFAULT_CONFIG, DownloadConfigValue, dateRangeToApi, templateToArray } from "../components/download-config";
import { useAlbums } from "../hooks/use-albums";
import { buildCron, parseCron } from "../lib/cron";
import type { Schedule, ScheduleBody } from "../types";

function fmt(ts: string | null): string {
  if (!ts) return "—";
  const d = new Date(ts);
  return isNaN(d.getTime()) ? ts : d.toLocaleString();
}

function configFromJob(jc: Record<string, any>): DownloadConfigValue {
  return {
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
    dateFrom: ((jc.date_from as string) || "").slice(0, 10),
    dateTo: ((jc.date_to as string) || "").slice(0, 10),
  };
}

export default function SchedulePage() {
  const { albums } = useAlbums();
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [editingId, setEditingId] = useState<number | null>(null); // null = new

  const [selectedAlbums, setSelectedAlbums] = useState<Set<string>>(new Set());
  const [config, setConfig] = useState<DownloadConfigValue>(DEFAULT_CONFIG);

  const [mode, setMode] = useState("daily");
  const [hour, setHour] = useState(2);
  const [minute, setMinute] = useState(0);
  const [everyN, setEveryN] = useState(6);
  const [custom, setCustom] = useState("0 2 * * *");
  const [enabled, setEnabled] = useState(true);

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const reload = () =>
    api.listSchedules().then(setSchedules).catch((e) => setErr(e.message));

  useEffect(() => {
    reload().finally(() => setLoading(false));
  }, []);

  const newSchedule = () => {
    setEditingId(null);
    setSelectedAlbums(new Set());
    setConfig(DEFAULT_CONFIG);
    setMode("daily");
    setHour(2);
    setMinute(0);
    setEveryN(6);
    setCustom("0 2 * * *");
    setEnabled(true);
    setMsg(null);
    setErr(null);
  };

  const editSchedule = (s: Schedule) => {
    const jc = s.job_config || {};
    setEditingId(s.id);
    setSelectedAlbums(new Set((jc.selected_albums as string[]) || []));
    setConfig(configFromJob(jc));
    setEnabled(s.enabled);
    const p = parseCron(s.cron_expression);
    setMode(p.mode);
    setHour(p.hour);
    setMinute(p.minute);
    setEveryN(p.everyN);
    setCustom(p.custom);
    setMsg(null);
    setErr(null);
  };

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
    const body: ScheduleBody = {
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
        ...dateRangeToApi(config),
      },
    };
    try {
      const saved = editingId == null
        ? await api.createSchedule(body)
        : await api.updateSchedule(editingId, body);
      await reload();
      setEditingId(saved.id);
      setMsg("Schedule saved.");
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const toggle = async (s: Schedule) => {
    try {
      await api.toggleScheduleById(s.id, !s.enabled);
      await reload();
      if (editingId === s.id) setEnabled(!s.enabled);
    } catch (e) {
      setErr((e as Error).message);
    }
  };

  const remove = async (s: Schedule) => {
    try {
      await api.deleteSchedule(s.id);
      await reload();
      if (editingId === s.id) newSchedule();
    } catch (e) {
      setErr((e as Error).message);
    }
  };

  if (loading) return <div className="p-8 text-slate-500">Loading…</div>;

  const numCls = "w-16 border rounded px-2 py-1";
  const summarize = (s: Schedule) => {
    const albumsN = ((s.job_config?.selected_albums as string[]) || []).length;
    return `${s.cron_expression} · ${albumsN} album(s)`;
  };

  return (
    <div className="flex h-[calc(100vh-49px)]">
      {/* LEFT: albums to sync for the schedule being edited */}
      <aside className="w-64 shrink-0 border-r bg-white">
        <AlbumChecklist albums={albums} selected={selectedAlbums} onToggle={toggleAlbum} />
      </aside>

      {/* CENTER: list of schedules + editor */}
      <main className="flex-1 overflow-y-auto p-6">
        <div className="max-w-2xl">
          <div className="flex items-center justify-between mb-4">
            <h1 className="text-lg font-semibold">Schedules</h1>
            <button
              onClick={newSchedule}
              className={`text-sm px-3 py-1 rounded ${editingId === null ? "bg-blue-600 text-white" : "bg-slate-100 hover:bg-slate-200"}`}
            >
              + New schedule
            </button>
          </div>

          {/* existing schedules */}
          {schedules.length > 0 && (
            <div className="mb-4 space-y-2">
              {schedules.map((s) => (
                <div
                  key={s.id}
                  className={`flex items-center gap-3 bg-white border rounded-lg px-3 py-2 ${
                    editingId === s.id ? "ring-2 ring-blue-400" : ""
                  }`}
                >
                  <button onClick={() => editSchedule(s)} className="flex-1 text-left">
                    <div className="text-sm font-mono">{summarize(s)}</div>
                    <div className="text-[11px] text-slate-500">
                      next {s.enabled ? fmt(s.next_run_at) : "—"} · last {fmt(s.last_run_at)}
                    </div>
                  </button>
                  <label className="flex items-center gap-1 text-xs text-slate-600">
                    <input type="checkbox" checked={s.enabled} onChange={() => toggle(s)} />
                    on
                  </label>
                  <button onClick={() => remove(s)} className="text-xs text-slate-400 hover:text-red-600">
                    delete
                  </button>
                </div>
              ))}
            </div>
          )}

          <h2 className="text-sm font-semibold text-slate-500 mb-2">
            {editingId == null ? "New schedule" : `Editing schedule #${editingId}`}
          </h2>

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
            {err && <p className="text-sm text-red-600 mb-2">{err}</p>}
            {msg && <p className="text-sm text-emerald-600 mb-2">{msg}</p>}
            <button
              onClick={save}
              disabled={saving || selectedAlbums.size === 0 || !cron}
              className="bg-blue-600 text-white rounded px-4 py-2 hover:bg-blue-700 disabled:opacity-50"
            >
              {saving ? "Saving…" : editingId == null ? "Create schedule" : "Save changes"}
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
