/**
 * Test harness: renders a page inside the real providers (auth, query client, toasts, router)
 * with `fetch` mocked at the API-client boundary. Handlers are keyed "METHOD /api/v1/path".
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderResult } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";

import type { Role, UserInfo } from "@/api/types";
import { AuthProvider } from "@/app/auth";
import { ToastProvider } from "@/components/Toast";
import { STORAGE_KEYS } from "@/lib/constants";

export interface MockRequest {
  method: string;
  path: string;
  url: URL;
  body: unknown;
}

export type MockHandlerFn = (req: MockRequest) => unknown;

/** A static JSON body, a MockResponse, or a function of the request (contextually typed). */
export type MockHandler = MockHandlerFn | MockResponse | object | string | number | boolean | null;

export class MockResponse {
  constructor(
    public status: number,
    public body: unknown,
  ) {}
}

export interface MockApi {
  fetch: ReturnType<typeof vi.fn<typeof fetch>>;
  /** Every request seen so far. */
  calls: MockRequest[];
  /** Requests matching a method + path (path is compared without the query string). */
  find: (method: string, path: string) => MockRequest[];
  /** Add or replace handlers after render. */
  set: (routes: Record<string, MockHandler>) => void;
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

/** Installs a fetch mock. Route keys look like "GET /api/v1/orders"; "/api/v1/orders/:id" style params are not needed — use exact paths. */
export function mockApi(routes: Record<string, MockHandler>): MockApi {
  const table = new Map(Object.entries(routes));
  const calls: MockRequest[] = [];
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url, "http://localhost");
    const method = (init?.method ?? "GET").toUpperCase();
    let body: unknown = null;
    if (typeof init?.body === "string" && init.body !== "") {
      try {
        body = JSON.parse(init.body);
      } catch {
        body = init.body;
      }
    }
    const req: MockRequest = { method, path: url.pathname, url, body };
    calls.push(req);
    const handler = table.get(`${method} ${url.pathname}`);
    if (handler === undefined) return jsonResponse(404, { error: "not_found", message: `no mock for ${method} ${url.pathname}`, details: {} });
    const result = typeof handler === "function" ? (handler as MockHandlerFn)(req) : handler;
    if (result instanceof MockResponse) return jsonResponse(result.status, result.body);
    return jsonResponse(200, result);
  });
  vi.stubGlobal("fetch", fetchMock);
  return {
    fetch: fetchMock,
    calls,
    find: (method, path) => calls.filter((c) => c.method === method.toUpperCase() && c.path === path),
    set: (more) => {
      for (const [k, v] of Object.entries(more)) table.set(k, v);
    },
  };
}

export function makeUser(role: Role): UserInfo {
  return { user_id: `usr_${role}`, username: role, role, display_name: `${role} user` };
}

export interface RenderPageOptions {
  role?: Role;
  /** Initial URL (path + query). */
  route?: string;
  /** Route pattern the element is mounted on (for useParams). */
  path?: string;
}

/** Renders `ui` for a signed-in user of `role` at `route`. The session is seeded in localStorage; GET /auth/me must be mocked. */
export function renderPage(ui: ReactElement, { role = "planner", route = "/", path = "*" }: RenderPageOptions = {}): RenderResult {
  const user = makeUser(role);
  localStorage.setItem(STORAGE_KEYS.auth, JSON.stringify({ token: "test-token", user, expires_at: new Date(Date.now() + 3_600_000).toISOString() }));
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: 0 } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <ToastProvider>
          <MemoryRouter initialEntries={[route]}>
            <Routes>
              <Route path={path} element={ui} />
              <Route path="/orders/:orderId" element={<div data-testid="navigated-order" />} />
              <Route path="/machines/:machineId" element={<div data-testid="navigated-machine" />} />
            </Routes>
          </MemoryRouter>
        </ToastProvider>
      </AuthProvider>
    </QueryClientProvider>,
  );
}

/** Standard handler for the session re-validation performed on mount. */
export function authHandlers(role: Role): Record<string, MockHandler> {
  return { "GET /api/v1/auth/me": makeUser(role) };
}
