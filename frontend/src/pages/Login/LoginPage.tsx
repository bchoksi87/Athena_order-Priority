import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { describeError } from "@/api/client";
import { useAuth } from "@/app/auth";
import { homeForRole } from "@/app/nav";
import { ThemeToggle } from "@/components/ThemeToggle";

import "./LoginPage.css";

export default function LoginPage() {
  const { status, user, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (status === "authenticated" && user) {
    return <Navigate to={homeForRole(user.role)} replace />;
  }

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const u = await login(username.trim(), password);
      const from = (location.state as { from?: string } | null)?.from;
      navigate(from && from !== "/login" ? from : homeForRole(u.role), { replace: true });
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login">
      <form className="login-card" onSubmit={onSubmit}>
        <div className="login-brand">
          <span className="login-brand-mark">P</span>
          <div>
            <h1>PPSE</h1>
            <div className="text-muted text-sm">Production Priority &amp; Scheduling Engine</div>
          </div>
        </div>
        {error ? (
          <div className="login-error" role="alert">
            {error}
          </div>
        ) : null}
        <label className="field">
          <span className="label">Username</span>
          <input className="input" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" autoFocus required />
        </label>
        <label className="field">
          <span className="label">Password</span>
          <input
            className="input"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
        </label>
        <button type="submit" className="btn btn-primary" disabled={busy || !username || !password}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <span className="login-foot">Read-only ERP integration · JWT session</span>
          <ThemeToggle />
        </div>
      </form>
    </div>
  );
}
