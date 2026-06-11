import { NavLink } from "react-router-dom";

const linkCls = (active: boolean) =>
  `text-sm ${active ? "text-blue-600 font-medium" : "text-slate-600 hover:text-slate-900"}`;

export default function Nav({ authed, onLogout }: { authed: boolean; onLogout: () => void }) {
  return (
    <nav className="flex items-center gap-5 px-6 py-3 bg-white border-b shadow-sm">
      <span className="font-semibold text-slate-800">iCloud → NAS</span>
      {authed && (
        <>
          <NavLink to="/" end className={({ isActive }) => linkCls(isActive)}>
            Browse
          </NavLink>
          <NavLink to="/jobs" className={({ isActive }) => linkCls(isActive)}>
            Jobs
          </NavLink>
          <NavLink to="/schedule" className={({ isActive }) => linkCls(isActive)}>
            Schedule
          </NavLink>
          <button
            onClick={onLogout}
            className="ml-auto text-sm text-slate-500 hover:text-red-600"
          >
            Logout
          </button>
        </>
      )}
    </nav>
  );
}
