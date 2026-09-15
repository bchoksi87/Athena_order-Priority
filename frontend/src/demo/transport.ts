/**
 * `createDemoFetch()` — a drop-in `fetch` for the API client that answers every `/api/v1`
 * request in the page: it parses method, path, query and body, checks the bearer token
 * and the role rules, dispatches to the route table and returns a real `Response` with
 * the same status codes and error envelope as the FastAPI backend.
 */
import { resolveBaseUrl } from "@/api/client";
import type { UserInfo } from "@/api/types";
import { ROLE_RANK } from "@/lib/constants";

import { ActiveConfig } from "./config";
import { DemoApiError, forbiddenRead, forbiddenWrite, notFound, unauthenticated } from "./errors";
import { OrderModel } from "./orders";
import { PlanModel } from "./plan";
import { matchRoute, type Access, type DemoModels, type RouteResult } from "./routes";
import { DemoStore, type DemoStoreOptions } from "./store";
import { SystemModel } from "./system";
import { Query } from "./util";

export const API_MARKER = "/api/v1";

/** The loaded store plus its read models; one per page. */
export interface DemoRuntime {
  store: DemoStore;
  models: DemoModels;
}

export interface DemoRequest {
  method: string;
  path: string;
  search: string;
  token: string | null;
  body: unknown;
}

export function createModels(store: DemoStore): DemoModels {
  const config = new ActiveConfig(store);
  const plan = new PlanModel(store, config);
  const orders = new OrderModel(store, config, plan);
  const system = new SystemModel(store, plan, orders);
  return { config, plan, orders, system };
}

export function createRuntime(store: DemoStore): DemoRuntime {
  return { store, models: createModels(store) };
}

/** Strips the API base (`/api/v1`, or whatever VITE_API_BASE_URL says) from a request URL. */
export function routePath(url: URL, base: string = resolveBaseUrl()): string {
  const pathname = url.pathname;
  const marker = pathname.indexOf(API_MARKER);
  let rest: string;
  if (marker >= 0) rest = pathname.slice(marker + API_MARKER.length);
  else {
    const normalised = base.replace(/^\.\//, "/").replace(/\/+$/, "");
    const idx = normalised ? pathname.indexOf(normalised) : -1;
    rest = idx >= 0 ? pathname.slice(idx + normalised.length) : pathname;
  }
  rest = rest.replace(/\/+$/, "");
  return rest.startsWith("/") ? rest : `/${rest}`;
}

function headerValue(init: RequestInit | undefined, input: RequestInfo | URL, name: string): string | null {
  const source = init?.headers ?? (typeof Request !== "undefined" && input instanceof Request ? input.headers : undefined);
  if (!source) return null;
  return new Headers(source).get(name);
}

export function parseRequest(input: RequestInfo | URL, init?: RequestInit): DemoRequest {
  const rawUrl = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
  const url = new URL(rawUrl, "http://demo.local/");
  const method = (init?.method ?? (typeof Request !== "undefined" && input instanceof Request ? input.method : "GET")).toUpperCase();
  const auth = headerValue(init, input, "authorization");
  const token = auth && auth.toLowerCase().startsWith("bearer ") ? auth.slice(7).trim() : null;
  let body: unknown = null;
  if (typeof init?.body === "string" && init.body !== "") {
    try {
      body = JSON.parse(init.body);
    } catch {
      throw new DemoApiError(422, "validation_error", "request validation failed", { errors: [{ loc: ["body"], msg: "JSON decode error", type: "json_invalid" }] });
    }
  }
  return { method, path: routePath(url), search: url.search, token, body };
}

function authorise(store: DemoStore, access: Access, token: string | null): UserInfo | null {
  if (access.kind === "public") return token ? store.userForToken(token) : null;
  if (!token) throw unauthenticated("missing bearer token");
  const user = store.userForToken(token);
  if (!user) throw unauthenticated("invalid token");
  if (access.kind === "read") {
    if (user.role !== "executive" && ROLE_RANK[user.role] < ROLE_RANK[access.min]) throw forbiddenRead(user.role, access.min);
  } else if (access.kind === "write") {
    if (ROLE_RANK[user.role] < ROLE_RANK[access.min]) throw forbiddenWrite(user.role, access.min);
  }
  return user;
}

/** Runs one request against the runtime (no HTTP involved); errors become their envelope. */
export function dispatch(runtime: DemoRuntime, request: DemoRequest): RouteResult {
  runtime.store.requestCount += 1;
  try {
    const match = matchRoute(request.method, request.path);
    if (!match) throw notFound("Not Found");
    const user = authorise(runtime.store, match.route.access, request.token);
    return match.route.handler({
      store: runtime.store,
      models: runtime.models,
      user,
      params: match.params,
      query: new Query(new URLSearchParams(request.search)),
      body: request.body,
      method: request.method,
      path: request.path,
    });
  } catch (err) {
    if (err instanceof DemoApiError) return { status: err.status, body: err.toJSON() };
    console.error("[demo] handler failed", request.method, request.path, err);
    return { status: 500, body: { error: "internal_error", message: err instanceof Error ? err.message : "internal server error", details: {} } };
  }
}

export function toResponse(result: RouteResult): Response {
  if (result.status === 204) return new Response(null, { status: 204 });
  return new Response(JSON.stringify(result.body), { status: result.status, headers: { "content-type": "application/json" } });
}

let runtimePromise: Promise<DemoRuntime> | null = null;

/** The page-wide runtime (loaded once, on the first API call). */
export function getDemoRuntime(options: DemoStoreOptions = {}): Promise<DemoRuntime> {
  if (!runtimePromise) runtimePromise = DemoStore.load(options).then(createRuntime);
  return runtimePromise;
}

/** For tests: use a pre-built runtime instead of loading the dataset chunk. */
export function setDemoRuntime(runtime: DemoRuntime | null): void {
  runtimePromise = runtime ? Promise.resolve(runtime) : null;
}

export interface DemoFetchOptions {
  runtime?: DemoRuntime | Promise<DemoRuntime>;
  /** Simulated latency in milliseconds (default: none). */
  latencyMs?: number;
}

/** A `fetch` implementation for `new ApiClient({ fetchImpl })` backed by the in-page demo backend. */
export function createDemoFetch(options: DemoFetchOptions = {}): typeof fetch {
  const runtime = options.runtime ? Promise.resolve(options.runtime) : null;
  return async (input, init) => {
    const rt = await (runtime ?? getDemoRuntime());
    let request: DemoRequest;
    try {
      request = parseRequest(input, init);
    } catch (err) {
      if (err instanceof DemoApiError) return toResponse({ status: err.status, body: err.toJSON() });
      throw err;
    }
    if (options.latencyMs) await new Promise((resolve) => setTimeout(resolve, options.latencyMs));
    return toResponse(dispatch(rt, request));
  };
}
