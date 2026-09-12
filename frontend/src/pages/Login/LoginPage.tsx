import { useEffect, useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { describeError, isApiError } from "@/api/client";
import { useAuth } from "@/app/auth";
import { homeForRole } from "@/app/nav";
import { ThemeToggle } from "@/components/ThemeToggle";
import { STORAGE_KEYS } from "@/lib/constants";
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

  useEffect(() => {
    if (error) setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- clear the error when the user edits the form
  }, [username, password]);

  if (status === "authenticated" && user) {
    return <Navigate to={homeForRole(user.role)} replace />;
  }

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const u = await login(username.trim(), password);
      writeStorage(STORAGE_KEYS.rememberedUsername, remember ? username.trim() : null);
      const from = (location.state as { from?: string } | null)?.from;
      navigate(from && from !== "/login" ? from : homeForRole(u.role), { replace: true });
    } catch (err) {
      setError(loginErrorMessage(err));
      setPassword("");
    } finally {
      setBusy(false);
    }
  };

  const from = (location.state as { from?: string } | null)?.from;

  return (
    <div className="login">
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
          <input className="input" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" autoFocus={remembered === ""} required aria-invalid={error ? true : undefined} />
        </label>
        <label className="field">
          <span className="label">Password</span>
          <input
            className="input"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
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
    </div>
  );
}
