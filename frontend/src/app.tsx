import { useCallback, useEffect, useRef, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { api } from "./api";
import Nav from "./components/nav";
import AuthPage from "./pages/auth";
import BrowserPage from "./pages/browser";
import JobsPage from "./pages/jobs";
import SchedulePage from "./pages/schedule";
import SettingsPage from "./pages/settings";
import type { AuthStatus } from "./types";

export default function App() {
  const [status, setStatus] = useState<AuthStatus | null>(null);
  // Show a banner only when an established session *expires* mid-use, not on a
  // cold load where the user simply isn't logged in yet.
  const wasAuthed = useRef(false);
  const [expired, setExpired] = useState(false);

  const refresh = useCallback(() => {
    api
      .authStatus()
      .then((s) => {
        if (wasAuthed.current && !s.authenticated) setExpired(true);
        if (s.authenticated) {
          wasAuthed.current = true;
          setExpired(false);
        }
        setStatus(s);
      })
      .catch(() => setStatus({ authenticated: false, needs_2fa: false }));
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 60000); // catch session expiry
    return () => clearInterval(t);
  }, [refresh]);

  if (status === null) {
    return <div className="p-8 text-slate-500">Loading…</div>;
  }
  const authed = status.authenticated;

  return (
    <div className="min-h-screen">
      <Nav
        authed={authed}
        onLogout={async () => {
          await api.logout().catch(() => {});
          wasAuthed.current = false;
          refresh();
        }}
      />
      {expired && !authed && (
        <div className="bg-amber-100 border-b border-amber-300 text-amber-800 text-sm px-6 py-2 flex items-center gap-2">
          <span>⚠ Your iCloud session expired. Please sign in again to continue.</span>
        </div>
      )}
      <Routes>
        <Route
          path="/auth"
          element={authed ? <Navigate to="/" replace /> : <AuthPage status={status} onAuthed={refresh} />}
        />
        <Route path="/" element={authed ? <BrowserPage /> : <Navigate to="/auth" replace />} />
        <Route path="/jobs" element={authed ? <JobsPage /> : <Navigate to="/auth" replace />} />
        <Route path="/schedule" element={authed ? <SchedulePage /> : <Navigate to="/auth" replace />} />
        <Route path="/settings" element={authed ? <SettingsPage /> : <Navigate to="/auth" replace />} />
        <Route path="*" element={<Navigate to={authed ? "/" : "/auth"} replace />} />
      </Routes>
    </div>
  );
}
