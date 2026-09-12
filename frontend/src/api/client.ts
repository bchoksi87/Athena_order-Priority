/**
 * Thin fetch wrapper for the PPSE API.
 *
 * - Base URL from VITE_API_BASE_URL (default "/api/v1").
 * - Bearer token supplied by a getter so the token can live in memory.
 * - Every failure is normalised to ApiError {status, code, message, details}.
 * - 401 responses invoke `onUnauthorized` (the auth provider logs out).
 */
import type { ErrorResponse, ListResponse, Paged } from "./types";

export const DEFAULT_API_BASE_URL = "/api/v1";

export type QueryValue = string | number | boolean | null | undefined | Array<string | number | boolean>;
export type QueryParams = Record<string, QueryValue>;

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown>;

  constructor(status: number, code: string, message: string, details: Record<string, unknown> = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }

  get isUnauthorized(): boolean {
    return this.status === 401;
  }

  get isForbidden(): boolean {
    return this.status === 403;
  }

  get isNotFound(): boolean {
    return this.status === 404;
  }

  /** True for transport failures (network down, proxy unreachable). */
  get isNetwork(): boolean {
    return this.status === 0;
  }
}

export function isApiError(err: unknown): err is ApiError {
  return err instanceof ApiError;
}

/** Best-effort human message for any thrown value (used by ErrorState). */
export function describeError(err: unknown): string {
  if (isApiError(err)) return err.message;
  if (err instanceof Error) return err.message;
  return "Unexpected error";
}

export interface RequestOptions {
  query?: QueryParams;
  body?: unknown;
  signal?: AbortSignal;
  headers?: Record<string, string>;
}

export interface ApiClientOptions {
  baseUrl?: string;
  /** Initial bearer token; update later with setToken(). */
  token?: string | null;
  onUnauthorized?: () => void;
  fetchImpl?: typeof fetch;
}

export function resolveBaseUrl(): string {
  const configured = import.meta.env.VITE_API_BASE_URL;
  const base = configured && configured.trim() !== "" ? configured.trim() : DEFAULT_API_BASE_URL;
  return base.replace(/\/+$/, "");
}

/** Serialises query params, dropping null/undefined and expanding arrays as repeated keys. */
export function buildQuery(params: QueryParams | undefined): string {
  if (!params) return "";
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === "") continue;
    if (Array.isArray(value)) {
      for (const v of value) search.append(key, String(v));
    } else {
      search.append(key, String(value));
    }
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

async function parseBody(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") ?? "";
  if (response.status === 204) return null;
  if (contentType.includes("application/json")) {
    try {
      return await response.json();
    } catch {
      return null;
    }
  }
  const text = await response.text();
  return text === "" ? null : text;
}

function toApiError(status: number, payload: unknown): ApiError {
  if (payload && typeof payload === "object") {
    const p = payload as Partial<ErrorResponse> & { detail?: unknown };
    if (typeof p.error === "string" && typeof p.message === "string") {
      return new ApiError(status, p.error, p.message, p.details ?? {});
    }
    // FastAPI validation errors arrive as {detail: [...]}; HTTPException as {detail: "..."}.
    if (p.detail !== undefined) {
      const message = typeof p.detail === "string" ? p.detail : "Request validation failed";
      return new ApiError(status, status === 422 ? "validation_error" : "http_error", message, {
        detail: p.detail,
      });
    }
  }
  const fallback = typeof payload === "string" && payload ? payload : `HTTP ${status}`;
  return new ApiError(status, "http_error", fallback);
}

export class ApiClient {
  private readonly baseUrl: string;
  private token: string | null;
  private onUnauthorized: (() => void) | undefined;
  private readonly fetchImpl: typeof fetch;

  constructor(options: ApiClientOptions) {
    this.baseUrl = (options.baseUrl ?? resolveBaseUrl()).replace(/\/+$/, "");
    this.token = options.token ?? null;
    this.onUnauthorized = options.onUnauthorized;
    this.fetchImpl = options.fetchImpl ?? ((input, init) => fetch(input, init));
  }

  /** Replace the bearer token used by subsequent requests (null = anonymous). */
  setToken(token: string | null): void {
    this.token = token;
  }

  getToken(): string | null {
    return this.token;
  }

  /** Register the callback invoked on any 401 response (the auth provider logs out). */
  setUnauthorizedHandler(handler: (() => void) | undefined): void {
    this.onUnauthorized = handler;
  }

  url(path: string, query?: QueryParams): string {
    const cleanPath = path.startsWith("/") ? path : `/${path}`;
    return `${this.baseUrl}${cleanPath}${buildQuery(query)}`;
  }

  async request<T>(method: string, path: string, options: RequestOptions = {}): Promise<T> {
    const headers: Record<string, string> = { Accept: "application/json", ...options.headers };
    if (this.token) headers.Authorization = `Bearer ${this.token}`;
    let body: string | undefined;
    if (options.body !== undefined) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(options.body);
    }

    let response: Response;
    try {
      response = await this.fetchImpl(this.url(path, options.query), {
        method,
        headers,
        body,
        signal: options.signal,
      });
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") throw err;
      throw new ApiError(0, "network_error", "Cannot reach the PPSE API. Check that the backend is running.");
    }

    const payload = await parseBody(response);
    if (!response.ok) {
      const error = toApiError(response.status, payload);
      if (error.isUnauthorized && this.onUnauthorized) this.onUnauthorized();
      throw error;
    }
    return payload as T;
  }

  get<T>(path: string, query?: QueryParams, signal?: AbortSignal): Promise<T> {
    return this.request<T>("GET", path, { query, signal });
  }

  post<T>(path: string, body?: unknown, query?: QueryParams): Promise<T> {
    return this.request<T>("POST", path, { body, query });
  }

  put<T>(path: string, body: unknown): Promise<T> {
    return this.request<T>("PUT", path, { body });
  }

  delete<T>(path: string, query?: QueryParams): Promise<T> {
    return this.request<T>("DELETE", path, { query });
  }
}

/** Accepts either a bare array or a {items, meta} page and returns the items. */
export function unwrapList<T>(res: ListResponse<T> | null | undefined): T[] {
  if (!res) return [];
  if (Array.isArray(res)) return res;
  return Array.isArray(res.items) ? res.items : [];
}

/** Normalises list responses to a page envelope (client-side paging for bare arrays). */
export function toPaged<T>(res: ListResponse<T> | null | undefined): Paged<T> {
  if (res && !Array.isArray(res) && res.meta) return res;
  const items = unwrapList(res);
  return { items, meta: { total: items.length, offset: 0, limit: items.length, has_more: false } };
}
