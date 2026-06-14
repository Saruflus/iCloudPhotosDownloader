import { useEffect, useState } from "react";
import { api } from "../api";
import type { AppSettings } from "../types";

const FIELDS = [
  {
    key: "download_concurrency" as const,
    label: "Download concurrency",
    hint: "Parallel downloads per job (1–16). Applies from the next job.",
    type: "number",
  },
  {
    key: "max_retries" as const,
    label: "Max retries",
    hint: "Per-asset retry attempts on failure (0–10).",
    type: "number",
  },
  {
    key: "local_timezone" as const,
    label: "Local timezone",
    hint: "Used for {year}/{month}/{day} folder tokens, e.g. Europe/Paris.",
    type: "text",
  },
  {
    key: "thumbnail_cache_ttl" as const,
    label: "Thumbnail cache TTL (s)",
    hint: "How long thumbnails stay cached (Redis + browser).",
    type: "number",
  },
];

export default function SettingsPage() {
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const load = (s: AppSettings) => {
    setSettings(s);
    setDraft({
      download_concurrency: String(s.download_concurrency),
      max_retries: String(s.max_retries),
      local_timezone: s.local_timezone,
      thumbnail_cache_ttl: String(s.thumbnail_cache_ttl),
    });
  };

  useEffect(() => {
    api.getSettings().then(load).catch((e) => setErr(e.message));
  }, []);

  const save = async () => {
    setErr(null);
    setMsg(null);
    setSaving(true);
    try {
      const body: Record<string, unknown> = {};
      for (const f of FIELDS) {
        const v = draft[f.key];
        body[f.key] = f.type === "number" ? Number(v) : v;
      }
      load(await api.putSettings(body));
      setMsg("Settings saved. They apply from the next job start.");
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const reset = async (key: string) => {
    setErr(null);
    setMsg(null);
    try {
      load(await api.resetSetting(key));
      setMsg(`${key} reset to its .env default.`);
    } catch (e) {
      setErr((e as Error).message);
    }
  };

  if (!settings) return <div className="p-8 text-slate-500">{err || "Loading…"}</div>;

  return (
    <main className="max-w-2xl mx-auto p-6">
      <h1 className="text-lg font-semibold mb-4">Settings</h1>

      <section className="bg-white border rounded-lg p-4 mb-4">
        <h3 className="text-sm font-semibold mb-3">Runtime settings</h3>
        {FIELDS.map((f) => (
          <div key={f.key} className="mb-4">
            <label className="block text-xs font-semibold uppercase text-slate-400">
              {f.label}
              {settings.overridden.includes(f.key) && (
                <button
                  onClick={() => reset(f.key)}
                  className="ml-2 normal-case font-normal text-blue-600 hover:underline"
                  title="Remove the override and return to the .env value"
                >
                  overridden — reset
                </button>
              )}
            </label>
            <input
              type={f.type}
              value={draft[f.key] ?? ""}
              onChange={(e) => setDraft((d) => ({ ...d, [f.key]: e.target.value }))}
              className="w-full border rounded px-2 py-1 mt-1 text-sm"
            />
            <p className="text-[11px] text-slate-500 mt-0.5">{f.hint}</p>
          </div>
        ))}
        {err && <p className="text-sm text-red-600 mb-2">{err}</p>}
        {msg && <p className="text-sm text-emerald-600 mb-2">{msg}</p>}
        <button
          onClick={save}
          disabled={saving}
          className="bg-blue-600 text-white rounded px-4 py-2 hover:bg-blue-700 disabled:opacity-50"
        >
          {saving ? "Saving…" : "Save settings"}
        </button>
      </section>

      <section className="bg-white border rounded-lg p-4 text-sm mb-4">
        <h3 className="text-sm font-semibold mb-2">Notifications</h3>
        {settings.notify_channels.length === 0 ? (
          <p className="text-xs text-slate-500">
            No channels configured. Set <code>NTFY_URL</code>, <code>DISCORD_WEBHOOK_URL</code>, or{" "}
            <code>SMTP_*</code> in <code>.env</code> to get alerts on job done/failed and on{" "}
            <code>needs_2fa</code> for scheduled syncs.
          </p>
        ) : (
          <div className="text-xs">
            <div className="mb-1">
              Active:{" "}
              {settings.notify_channels.map((c) => (
                <span key={c} className="inline-block bg-emerald-100 text-emerald-700 rounded px-1.5 py-0.5 mr-1">
                  {c}
                </span>
              ))}
            </div>
            <p className="text-slate-500">
              On failure: {settings.notify_on_failure ? "yes" : "no"} · On success:{" "}
              {settings.notify_on_success ? "yes" : "no"} · always alerts on expired session (2FA).
            </p>
          </div>
        )}
      </section>

      <section className="bg-white border rounded-lg p-4 text-sm">
        <h3 className="text-sm font-semibold mb-2">Environment (read-only)</h3>
        <dl className="grid grid-cols-[auto,1fr] gap-x-4 gap-y-1 text-xs">
          <dt className="text-slate-400">Download path</dt>
          <dd className="font-mono">{settings.download_base_path}</dd>
          <dt className="text-slate-400">iCloud config dir</dt>
          <dd className="font-mono">{settings.icloud_config_dir}</dd>
          <dt className="text-slate-400">API secret</dt>
          <dd>{settings.api_secret_set ? "set" : "not set"}</dd>
        </dl>
        <p className="text-[11px] text-slate-500 mt-2">
          These come from <code>.env</code> / the compose file and need a container restart to change.
        </p>
      </section>
    </main>
  );
}
