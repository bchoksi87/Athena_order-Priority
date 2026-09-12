import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useApiClient } from "@/api/context";
import { STORAGE_KEYS } from "@/lib/constants";

import { AuthProvider, roleCanRead, useAuth } from "./auth";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

const user = { user_id: "u1", username: "planner", role: "planner" as const, display_name: "Pat Planner" };

function Probe() {
  const auth = useAuth();
  const client = useApiClient();
  return (
    <div>
      <span data-testid="status">{auth.status}</span>
      <span data-testid="user">{auth.user?.username ?? ""}</span>
      <button type="button" onClick={() => void auth.login("planner", "pw")}>
        login
      </button>
      <button type="button" onClick={() => void client.get("/orders").catch(() => undefined)}>
        call
      </button>
    </div>
  );
}

describe("AuthProvider", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    localStorage.clear();
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("starts anonymous and stores the session after login", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { access_token: "tok", token_type: "bearer", expires_in: 3600, role: "planner", user }));
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    expect(screen.getByTestId("status")).toHaveTextContent("anonymous");
    await act(async () => {
      screen.getByText("login").click();
    });
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("authenticated"));
    expect(screen.getByTestId("user")).toHaveTextContent("planner");
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEYS.auth) ?? "{}") as { token?: string };
    expect(stored.token).toBe("tok");

    // Subsequent requests carry the bearer token.
    fetchMock.mockResolvedValueOnce(jsonResponse(200, []));
    await act(async () => {
      screen.getByText("call").click();
    });
    const init = fetchMock.mock.calls[1]?.[1];
    expect((init?.headers as Record<string, string>).Authorization).toBe("Bearer tok");
  });

  it("restores a stored session via /auth/me and logs out on 401", async () => {
    localStorage.setItem(STORAGE_KEYS.auth, JSON.stringify({ token: "old", user, expires_at: new Date(Date.now() + 60_000).toISOString() }));
    fetchMock.mockResolvedValueOnce(jsonResponse(200, user));
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    expect(screen.getByTestId("status")).toHaveTextContent("loading");
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("authenticated"));
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/v1/auth/me");

    fetchMock.mockResolvedValueOnce(jsonResponse(401, { error: "unauthenticated", message: "expired", details: {} }));
    await act(async () => {
      screen.getByText("call").click();
    });
    await waitFor(() => expect(screen.getByTestId("status")).toHaveTextContent("anonymous"));
    expect(localStorage.getItem(STORAGE_KEYS.auth)).toBeNull();
  });

  it("drops an expired stored session without calling the API", () => {
    localStorage.setItem(STORAGE_KEYS.auth, JSON.stringify({ token: "old", user, expires_at: new Date(Date.now() - 1000).toISOString() }));
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    expect(screen.getByTestId("status")).toHaveTextContent("anonymous");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("grants executives read access but no write role", () => {
    expect(roleCanRead("executive", "production_manager")).toBe(true);
    expect(roleCanRead("operator", "planner")).toBe(false);
    expect(roleCanRead("admin", "planner")).toBe(true);
  });
});
