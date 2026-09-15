import { useEffect, useState, type FormEvent } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";

import { useAlerts } from "@/api/alerts";
import { useHealth } from "@/api/system";
import { ThemeToggle } from "@/components/ThemeToggle";
import { isDemoMode } from "@/demo/mode";
import { ROLE_LABELS, WRITEBACK_LABELS } from "@/lib/constants";
import { formatDateTime } from "@/lib/time";

import { useAuth } from "./auth";
import { navGroups, routes } from "./nav";
import "./AppShell.css";

const APP_ENV = (import.meta.env.VITE_APP_ENV ?? "dev").toUpperCase();

function useClock(intervalMs = 30_000): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), intervalMs);
    return () => window.clearInterval(id);
  }, [intervalMs]);
  return now;
}

function GlobalSearch() {
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    const term = q.trim();
    if (!term) return;
    // Search stub: an exact order id opens the order; anything else filters the priority queue.
    if (/^[A-Za-z0-9_-]+$/.test(term) && term.length >= 4) {
      navigate(routes.orderDetail(encodeURIComponent(term)));
    } else {
      navigate(`${routes.priorityQueue}?search=${encodeURIComponent(term)}`);
    }
    setQ("");
  };
  return (
    <form className="topbar-search" onSubmit={onSubmit} role="search">
      <input
        className="input"
        placeholder="Search order, part, customer…  (Enter)"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        aria-label="Global search"
      />
    </form>
  );
}

function AlertCounter() {
  const { data } = useAlerts({ acknowledged: false, limit: 1 }, 60_000);
  const count = data?.meta.total ?? 0;
  return (
    <NavLink to={routes.alerts} className="topbar-alerts" title="Open alerts">
      <span>Alerts</span>
      <span className={`topbar-alerts-count${count === 0 ? " zero" : ""}`}>{count > 99 ? "99+" : count}</span>
    </NavLink>
  );
}

/** ERP writeback mode as reported by GET /health (a backend deployment setting, PPSE_WRITEBACK_MODE). */
function WritebackBadge() {
  const { data } = useHealth();
  const mode = data?.writeback_mode ?? "read_only";
  const label = WRITEBACK_LABELS[mode] ?? mode.toUpperCase();
  const title = data
    ? mode === "read_only"
      ? "ERP writeback mode: read only — the ERP is never written"
      : `ERP writeback mode: ${mode} — publishing a schedule goes through the writeback gateway`
    : "ERP writeback mode (waiting for GET /health)";
  return (
    <span className={`badge badge-writeback badge-writeback-${mode}`} title={title} data-testid="writeback-badge">
      {label}
    </span>
  );
}

function HealthBadge() {
  const { data, isError } = useHealth();
  const ok = Boolean(data && data.status === "ok") && !isError;
  return (
    <span className={`badge ${ok ? "badge-health-ok" : "badge-health-bad"}`} title={data ? `API ${data.version}` : "API"}>
      API {ok ? "OK" : "DOWN"}
    </span>
  );
}

/** Application frame: grouped left nav, top bar with badges, routed content in <Outlet/>. */
export function AppShell() {
  const { user, logout, hasMinRole, hasReadAccess } = useAuth();
  const [collapsed, setCollapsed] = useState(false);
  const now = useClock();

  return (
    <div className={`shell${collapsed ? " nav-collapsed" : ""}`}>
      <nav className="nav" aria-label="Primary">
        <button type="button" className="nav-brand btn-ghost" onClick={() => setCollapsed((c) => !c)} title="Toggle navigation">
          <span className="nav-brand-mark">P</span>
          <span className="nav-brand-text">
            <strong>PPSE</strong>
            <span>Control Tower</span>
          </span>
        </button>
        {navGroups.map((group) => {
          const visible = group.items.filter((item) =>
            item.readOnly ? hasReadAccess(item.minRole) : hasMinRole(item.minRole),
          );
          if (visible.length === 0) return null;
          return (
            <div className="nav-group" key={group.label}>
              <div className="nav-group-label">{group.label}</div>
              {visible.map((item) => (
                <NavLink key={item.path} to={item.path} className="nav-link" title={item.label}>
                  <span className="nav-link-short">{item.short}</span>
                  <span className="nav-link-label">{item.label}</span>
                </NavLink>
              ))}
            </div>
          );
        })}
        <div className="nav-footer">v0.1.0</div>
      </nav>

      <header className="topbar">
        <GlobalSearch />
        <div className="topbar-spacer" />
        <span className="topbar-clock">{formatDateTime(now, "EEE dd MMM HH:mm")}</span>
        <div className="topbar-badges">
          <span className="badge badge-env">{APP_ENV}</span>
          {isDemoMode() ? (
            <span className="badge badge-demo" data-testid="demo-badge" title="Demo mode: no backend. Every API call is answered in the page from engine output captured from the real backend; your changes stay in this browser.">
              DEMO
            </span>
          ) : null}
          <WritebackBadge />
          <HealthBadge />
        </div>
        <AlertCounter />
        <ThemeToggle />
        <div className="topbar-user">
          <div className="topbar-user-name">
            <strong>{user?.display_name ?? user?.username ?? "—"}</strong>
            <span>{user ? ROLE_LABELS[user.role] : ""}</span>
          </div>
          <button type="button" className="btn btn-sm" onClick={logout}>
            Sign out
          </button>
        </div>
      </header>

      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}
