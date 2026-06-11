import { useCallback, useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { api } from "./api";
import Nav from "./components/nav";
import AuthPage from "./pages/auth";
import BrowserPage from "./pages/browser";
import JobsPage from "./pages/jobs";
import SchedulePage from "./pages/schedule";
import type { AuthStatus } from "./types";

export default function App() {
  const [status, setStatus] = useState<AuthStatus | null>(null);

  const refresh = useCallback(() => {
    api
      .authStatus()
      .then(setStatus)
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
          refresh();
        }}
      />
      <Routes>
        <Route
          path="/auth"
          element={authed ? <Navigate to="/" replace /> : <AuthPage status={status} onAuthed={refresh} />}
        />
        <Route path="/" element={authed ? <BrowserPage /> : <Navigate to="/auth" replace />} />
        <Route path="/jobs" element={authed ? <JobsPage /> : <Navigate to="/auth" replace />} />
        <Route path="/schedule" element={authed ? <SchedulePage /> : <Navigate to="/auth" replace />} />
        <Route path="*" element={<Navigate to={authed ? "/" : "/auth"} replace />} />
      </Routes>
    </div>
  );
}
