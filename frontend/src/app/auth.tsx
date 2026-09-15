/**
 * Authentication context. The JWT lives in React state (memory); a copy of
 * the session {token, user, expires_at} is persisted to localStorage so a
 * page reload restores it (and re-validates it against GET /auth/me).
 */
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { ApiClient, ApiError, resolveBaseUrl } from "@/api/client";
import { ApiClientProvider } from "@/api/context";
import { fetchMe, login as loginRequest } from "@/api/auth";
import type { Role, UserInfo } from "@/api/types";
import { installDemoFetch, isDemoMode } from "@/demo";
import { ROLE_RANK, STORAGE_KEYS } from "@/lib/constants";

export interface StoredSession {
  token: string;
  user: UserInfo;
  expires_at: string;
}

export interface AuthState {
  status: "loading" | "anonymous" | "authenticated";
  user: UserInfo | null;
  token: string | null;
  expiresAt: Date | null;
}

export interface AuthContextValue extends AuthState {
  login: (username: string, password: string) => Promise<UserInfo>;
  logout: () => void;
  hasMinRole: (role: Role) => boolean;
  hasReadAccess: (minRole: Role) => boolean;
}

const AuthContext = createContext<AuthContextValue | null>(null);

function readStoredSession(): StoredSession | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEYS.auth);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<StoredSession>;
    if (!parsed.token || !parsed.user || !parsed.expires_at) return null;
    if (new Date(parsed.expires_at).getTime() <= Date.now()) return null;
    return parsed as StoredSession;
  } catch {
    return null;
  }
}

function writeStoredSession(session: StoredSession | null): void {
  try {
    if (session) localStorage.setItem(STORAGE_KEYS.auth, JSON.stringify(session));
    else localStorage.removeItem(STORAGE_KEYS.auth);
  } catch {
    // Storage may be unavailable (private mode); the in-memory session still works.
  }
}

export function roleAtLeast(role: Role, min: Role): boolean {
  return ROLE_RANK[role] >= ROLE_RANK[min];
}

/** Read endpoints: min role or higher, or the read-only executive role (contract §9). */
export function roleCanRead(role: Role, min: Role): boolean {
  return role === "executive" || roleAtLeast(role, min);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>(() => {
    const stored = readStoredSession();
    return stored
      ? { status: "loading", user: stored.user, token: stored.token, expiresAt: new Date(stored.expires_at) }
      : { status: "anonymous", user: null, token: null, expiresAt: null };
  });
  // One client per provider instance; its token is updated from event handlers, never during render.
  // DEMO MODE (VITE_DEMO_MODE=true): every request is answered in the page by the demo backend.
  const [client] = useState(() => new ApiClient({ baseUrl: resolveBaseUrl(), token: state.token, fetchImpl: isDemoMode() ? installDemoFetch() : undefined }));

  const value = useMemo<AuthContextValue>(() => {
    const logout = () => {
      client.setToken(null);
      writeStoredSession(null);
      setState({ status: "anonymous", user: null, token: null, expiresAt: null });
    };
    const login = async (username: string, password: string) => {
      const res = await loginRequest(client, username, password);
      const expiresAt = new Date(Date.now() + res.expires_in * 1000);
      client.setToken(res.access_token);
      writeStoredSession({ token: res.access_token, user: res.user, expires_at: expiresAt.toISOString() });
      setState({ status: "authenticated", user: res.user, token: res.access_token, expiresAt });
      return res.user;
    };
    return {
      ...state,
      login,
      logout,
      hasMinRole: (role) => (state.user ? roleAtLeast(state.user.role, role) : false),
      hasReadAccess: (min) => (state.user ? roleCanRead(state.user.role, min) : false),
    };
  }, [state, client]);

  const { logout } = value;

  // Any 401 ends the session (only when one exists, so anonymous probes do not loop).
  useEffect(() => {
    client.setUnauthorizedHandler(() => {
      if (client.getToken()) logout();
    });
    return () => client.setUnauthorizedHandler(undefined);
  }, [client, logout]);

  // Validate a restored session once on mount.
  useEffect(() => {
    if (state.status !== "loading") return;
    let cancelled = false;
    fetchMe(client)
      .then((user) => {
        if (cancelled) return;
        setState((prev) => ({ ...prev, status: "authenticated", user }));
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.isNetwork) {
          // Backend unreachable: keep the stored session optimistic; requests will fail visibly.
          setState((prev) => ({ ...prev, status: "authenticated" }));
        } else {
          logout();
        }
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- run once for the initial restore
  }, []);

  return (
    <AuthContext.Provider value={value}>
      <ApiClientProvider value={client}>{children}</ApiClientProvider>
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
