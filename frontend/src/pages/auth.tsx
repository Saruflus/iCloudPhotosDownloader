import { FormEvent, useState } from "react";
import { api } from "../api";
import type { AuthStatus } from "../types";

export default function AuthPage({
  status,
  onAuthed,
}: {
  status: AuthStatus;
  onAuthed: () => void;
}) {
  const [appleId, setAppleId] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [needs2fa, setNeeds2fa] = useState(status.needs_2fa);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const login = async (e: FormEvent) => {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      const r = await api.login(appleId, password);
      setNeeds2fa(r.requires_2fa);
      if (!r.requires_2fa) onAuthed();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const submit2fa = async (e: FormEvent) => {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      await api.submit2fa(code);
      onAuthed();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const input = "w-full border border-slate-300 rounded px-3 py-2 mt-1 focus:outline-none focus:ring-2 focus:ring-blue-400";
  const btn = "w-full bg-blue-600 text-white rounded py-2 mt-4 hover:bg-blue-700 disabled:opacity-50";

  return (
    <div className="max-w-sm mx-auto mt-16 bg-white p-8 rounded-lg shadow-sm border">
      <h1 className="text-lg font-semibold mb-1">Sign in to iCloud</h1>
      <p className="text-sm text-slate-500 mb-5">
        Local network only. Your password is never stored.
      </p>

      {!needs2fa ? (
        <form onSubmit={login}>
          <label className="block text-sm">
            Apple ID
            <input className={input} value={appleId} onChange={(e) => setAppleId(e.target.value)}
              type="email" autoComplete="username" required />
          </label>
          <label className="block text-sm mt-3">
            Password
            <input className={input} value={password} onChange={(e) => setPassword(e.target.value)}
              type="password" autoComplete="current-password" required />
          </label>
          <button className={btn} disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
        </form>
      ) : (
        <form onSubmit={submit2fa}>
          <p className="text-sm text-slate-600 mb-2">
            Enter the 6-digit code from your trusted device.
          </p>
          <input className={`${input} tracking-widest text-center text-lg`} value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
            inputMode="numeric" placeholder="••••••" required />
          <button className={btn} disabled={busy || code.length !== 6}>
            {busy ? "Verifying…" : "Verify"}
          </button>
        </form>
      )}

      {err && <p className="text-sm text-red-600 mt-4">{err}</p>}
      <p className="text-xs text-slate-400 mt-6">
        Tip: the most reliable login is the CLI — <code>docker exec -it icloud-sync-backend python -m app.cli auth</code>
      </p>
    </div>
  );
}
