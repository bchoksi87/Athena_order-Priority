import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { describeError, isApiError } from "@/api/client";
import { useAuth } from "@/app/auth";
import { homeForRole } from "@/app/nav";
import { ThemeToggle } from "@/components/ThemeToggle";
import { DEMO_ACCOUNTS, DEMO_TOUR, isDemoMode, resetDemoData } from "@/demo";
import { ROLE_LABELS, STORAGE_KEYS } from "@/lib/constants";
import { readStorage, writeStorage } from "@/lib/useLocalStorage";

import "./LoginPage.css";

const APP_ENV = (import.meta.env.VITE_APP_ENV ?? "dev").toUpperCase();

function loginErrorMessage(err: unknown): string {
  if (isApiError(err)) {
    if (err.isNetwork) return "Cannot reach the PPSE API. Check that the backend is running and the /api proxy is configured.";
    if (err.status === 401) return "Invalid username or password.";
    if (err.status === 403) return "This account is deactivated. Ask an administrator to re-activate it.";
    if (err.status === 422) return "Enter both a username and a password.";
    if (err.status >= 500) return "The API returned a server error. Try again in a moment.";
  }
  return describeError(err);
}

/** DEMO MODE: the six seeded accounts with one-click sign-in, the "what to try" script and the reset action. */
function DemoPanel({ busy, onSignIn }: { busy: boolean; onSignIn: (username: string, password: string) => void }) {
  return (
    <aside className="login-demo" data-testid="demo-panel" aria-label="Demo mode">
      <div className="login-demo-head">
        <span className="badge badge-demo">DEMO</span>
        <strong>No backend, real engine output</strong>
      </div>
      <p className="text-sm text-muted">
        Every API call is answered in the page from schedules, scores and explanations captured from the real PPSE backend
        (small synthetic plant). Changes you make stay in this browser.
      </p>
      <div className="login-demo-accounts" role="list">
        {DEMO_ACCOUNTS.map((acc) => (
          <button
            key={acc.username}
            type="button"
            role="listitem"
            className="btn login-demo-account"
            data-testid={`demo-login-${acc.username}`}
            disabled={busy}
            title={`${acc.username} / ${acc.password}`}
            onClick={() => onSignIn(acc.username, acc.password)}
          >
            <span className="login-demo-account-name">
              <strong>{ROLE_LABELS[acc.role]}</strong>
              <span className="mono text-faint">{acc.username}</span>
            </span>
            <span className="text-faint text-xs">{acc.blurb}</span>
          </button>
        ))}
      </div>
      <div className="login-demo-tour">
        <div className="label">What to try</div>
        <ol>
          {DEMO_TOUR.map((step) => (
            <li key={step.title}>
              <strong>{step.title}</strong> — {step.text}
            </li>
          ))}
        </ol>
      </div>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <span className="login-foot">Captured with backend/scripts/capture_demo_data.py</span>
        <button
          type="button"
          className="btn btn-sm"
          data-testid="reset-demo-data"
          onClick={() => {
            if (window.confirm("Reset the demo data? Every change made in this browser is dropped and the page reloads.")) void resetDemoData();
          }}
        >
          Reset demo data
        </button>
      </div>
    </aside>
  );
}

export default function LoginPage() {
  const { status, user, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const remembered = readStorage<string>(STORAGE_KEYS.rememberedUsername, "");
  const [username, setUsername] = useState(remembered);
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(remembered !== "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [capsLock, setCapsLock] = useState(false);
  const demo = isDemoMode();

  if (status === "authenticated" && user) {
    return <Navigate to={homeForRole(user.role)} replace />;
  }

  const from = (location.state as { from?: string } | null)?.from;

  const signIn = async (name: string, secret: string) => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const u = await login(name, secret);
      writeStorage(STORAGE_KEYS.rememberedUsername, remember ? name : null);
      navigate(from && from !== "/login" ? from : homeForRole(u.role), { replace: true });
    } catch (err) {
      setError(loginErrorMessage(err));
      setPassword("");
    } finally {
      setBusy(false);
    }
  };

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    void signIn(username.trim(), password);
  };

  return (
    <div className={`login${demo ? " login-with-demo" : ""}`}>
      <div className="login-layout">
      <form className="login-card" onSubmit={onSubmit} aria-busy={busy} data-testid="login-form">
        <div className="login-brand">
          <span className="login-brand-mark">P</span>
          <div>
            <h1>PPSE</h1>
            <div className="text-muted text-sm">Production Priority &amp; Scheduling Engine</div>
          </div>
          <span className="badge badge-env" style={{ marginLeft: "auto" }}>
            {APP_ENV}
          </span>
        </div>
        {status === "loading" ? <div className="login-note">Restoring your previous session…</div> : null}
        {from && from !== "/login" ? <div className="login-note">Sign in to continue to <span className="mono">{from}</span>.</div> : null}
        {error ? (
          <div className="login-error" role="alert">
            {error}
          </div>
        ) : null}
        <label className="field">
          <span className="label">Username</span>
          <input
            className="input"
            value={username}
            onChange={(e) => {
              setUsername(e.target.value);
              setError(null);
            }}
            autoComplete="username" autoFocus={remembered === ""} required aria-invalid={error ? true : undefined} />
        </label>
        <label className="field">
          <span className="label">Password</span>
          <input
            className="input"
            type="password"
            value={password}
            onChange={(e) => {
              setPassword(e.target.value);
              setError(null);
            }}
            onKeyUp={(e) => setCapsLock(typeof e.getModifierState === "function" && e.getModifierState("CapsLock"))}
            autoComplete="current-password"
            autoFocus={remembered !== ""}
            required
            aria-invalid={error ? true : undefined}
          />
          {capsLock ? <span className="login-hint tone-at-risk">Caps Lock is on</span> : null}
        </label>
        <label className="row text-sm">
          <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
          Remember my username on this device
        </label>
        <button type="submit" className="btn btn-primary" disabled={busy || !username.trim() || !password}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <span className="login-foot">Read-only ERP integration · JWT session · roles per DESIGN_CONTRACT §9</span>
          <ThemeToggle />
        </div>
      </form>
      {demo ? <DemoPanel busy={busy} onSignIn={(name, secret) => void signIn(name, secret)} /> : null}
      </div>
    </div>
  );
}
