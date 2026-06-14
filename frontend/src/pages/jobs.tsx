import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api";
import { useJobSocket } from "../hooks/use-job-socket";
import { estimate, formatDuration, formatRate } from "../lib/eta";
import type { Job } from "../types";

const STATUS_CLS: Record<string, string> = {
  pending: "bg-slate-200 text-slate-700",
  running: "bg-blue-100 text-blue-700",
  completed: "bg-emerald-100 text-emerald-700",
  failed: "bg-red-100 text-red-700",
  cancelled: "bg-amber-100 text-amber-700",
};

function Badge({ status }: { status: string }) {
  return (
    <span className={`text-xs px-2 py-0.5 rounded ${STATUS_CLS[status] || "bg-slate-200"}`}>
      {status}
    </span>
  );
}

function LivePanel({ job, onChange }: { job: Job; onChange: () => void }) {
  const { progress, logs, done, connected } = useJobSocket(job.id);
  const logRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    logRef.current?.scrollTo(0, logRef.current.scrollHeight);
  }, [logs]);
  useEffect(() => {
    if (done) onChange();
  }, [done]); // refresh the list when the job finishes

  const downloaded = progress?.downloaded ?? job.downloaded_count;
  const skipped = progress?.skipped ?? job.skipped_count;
  const failed = progress?.failed ?? job.failed_count;
  const total = progress?.total ?? job.total_assets;
  const processed = downloaded + skipped + failed;
  const pct = total ? Math.round((processed / total) * 100) : 0;

  // ETA from a reference point taken once the first real progress arrives —
  // smooths the bursty per-file events into a steady rate.
  const ref = useRef<{ t: number; processed: number } | null>(null);
  if (ref.current === null && processed > 0) {
    ref.current = { t: Date.now(), processed };
  }
  const elapsedSec = ref.current ? (Date.now() - ref.current.t) / 1000 : 0;
  const { ratePerMin, etaSec } = estimate(
    ref.current ? processed - ref.current.processed : 0,
    Math.max(0, total - processed),
    elapsedSec,
  );

  return (
    <div className="mt-3 border-t pt-3">
      <div className="flex items-center gap-2 text-xs text-slate-500 mb-1">
        <span className={`w-2 h-2 rounded-full ${connected ? "bg-emerald-500" : "bg-slate-300"}`} />
        {connected ? "live" : "disconnected"}
        {progress?.current_file && <span className="truncate">· {progress.current_file}</span>}
      </div>
      <div className="w-full h-2 bg-slate-200 rounded overflow-hidden">
        <div className="h-full bg-blue-500 transition-all" style={{ width: `${pct}%` }} />
      </div>
      <div className="flex gap-4 text-xs mt-1 text-slate-600">
        <span>⬇ {downloaded}</span>
        <span>⏭ {skipped}</span>
        <span className={failed ? "text-red-600" : ""}>✕ {failed}</span>
        <span className="ml-auto">{processed}/{total}</span>
      </div>
      {!done && processed > 0 && total > processed && (ratePerMin || etaSec != null) && (
        <div className="flex gap-3 text-[11px] text-slate-500 mt-0.5">
          {ratePerMin != null && <span>{formatRate(ratePerMin)}</span>}
          {etaSec != null && <span className="ml-auto">ETA {formatDuration(etaSec)}</span>}
        </div>
      )}
      <div ref={logRef} className="mt-2 h-32 overflow-y-auto bg-slate-900 text-slate-100 text-[11px] font-mono rounded p-2">
        {logs.length === 0 && <span className="text-slate-500">waiting for log…</span>}
        {logs.map((l, i) => (
          <div key={i} className={l.level === "error" ? "text-red-400" : ""}>
            {l.message}
          </div>
        ))}
      </div>
    </div>
  );
}

export default function JobsPage() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [params] = useSearchParams();
  const [openId, setOpenId] = useState<number | null>(
    params.get("focus") ? Number(params.get("focus")) : null,
  );

  const refresh = () => api.jobs().then(setJobs).catch(() => {});
  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 4000);
    return () => clearInterval(t);
  }, []);

  const cancel = async (id: number) => {
    await api.cancelJob(id);
    refresh();
  };
  const retry = async (id: number) => {
    const job = await api.retryFailed(id);
    setOpenId(job.id);
    refresh();
  };

  return (
    <div className="max-w-3xl mx-auto p-6">
      <h1 className="text-lg font-semibold mb-4">Jobs</h1>
      {jobs.length === 0 && <p className="text-slate-400">No jobs yet.</p>}
      <div className="space-y-3">
        {jobs.map((job) => {
          const open = openId === job.id;
          const live = job.status === "running" || job.status === "pending";
          return (
            <div key={job.id} className="bg-white border rounded-lg p-4 shadow-sm">
              <div className="flex items-center gap-3">
                <span className="font-medium">#{job.id}</span>
                <Badge status={job.status} />
                <span className="text-sm text-slate-500 truncate">
                  {job.selected_albums.join(", ") || `${job.selected_asset_ids.length} photos`}
                </span>
                <span className="ml-auto text-xs text-slate-500">
                  ⬇{job.downloaded_count} ⏭{job.skipped_count} ✕{job.failed_count} /{job.total_assets}
                </span>
              </div>
              <div className="flex gap-3 mt-2 text-xs">
                <button onClick={() => setOpenId(open ? null : job.id)} className="text-blue-600 hover:underline">
                  {open ? "Hide" : "Details"}
                </button>
                {live && (
                  <button onClick={() => cancel(job.id)} className="text-red-600 hover:underline">
                    Cancel
                  </button>
                )}
                {job.failed_count > 0 && (
                  <button onClick={() => retry(job.id)} className="text-amber-600 hover:underline">
                    Retry failed
                  </button>
                )}
              </div>
              {open && (live ? <LivePanel job={job} onChange={refresh} /> : (
                <div className="mt-3 border-t pt-3 text-xs text-slate-500">
                  Template: <code>{job.folder_structure.join(" / ") || "(flat)"}</code> · version{" "}
                  {job.download_version} · {job.album_fanout ? "fanout" : "first album"}
                </div>
              ))}
            </div>
          );
        })}
      </div>
    </div>
  );
}
