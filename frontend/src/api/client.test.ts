import { describe, expect, it, vi } from "vitest";

import { ApiClient, ApiError, buildQuery, toPaged, unwrapList } from "./client";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

describe("ApiClient", () => {
  it("builds query strings and drops empty values", () => {
    expect(buildQuery({ a: 1, b: "x", c: null, d: undefined, e: "", f: [1, 2] })).toBe("?a=1&b=x&f=1&f=2");
    expect(buildQuery(undefined)).toBe("");
  });

  it("sends the bearer token and parses JSON", async () => {
    const fetchImpl = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
      const headers = init?.headers as Record<string, string>;
      expect(headers.Authorization).toBe("Bearer tok");
      return jsonResponse(200, { ok: true });
    });
    const client = new ApiClient({ baseUrl: "/api/v1", token: "tok", fetchImpl });
    await expect(client.get<{ ok: boolean }>("/orders", { limit: 5 })).resolves.toEqual({ ok: true });
    expect(fetchImpl.mock.calls[0]?.[0]).toBe("/api/v1/orders?limit=5");
  });

  it("normalises errors and calls onUnauthorized on 401", async () => {
    const onUnauthorized = vi.fn();
    const client = new ApiClient({
      baseUrl: "/api/v1",
      token: null,
      onUnauthorized,
      fetchImpl: async () => jsonResponse(401, { error: "unauthenticated", message: "token expired", details: {} }),
    });
    const err = await client.get("/auth/me").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).code).toBe("unauthenticated");
    expect((err as ApiError).message).toBe("token expired");
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it("maps FastAPI detail payloads and network failures", async () => {
    const client = new ApiClient({
      baseUrl: "/api/v1",
      fetchImpl: async () => jsonResponse(422, { detail: [{ loc: ["body"], msg: "bad" }] }),
    });
    const err = (await client.post("/x", {}).catch((e: unknown) => e)) as ApiError;
    expect(err.code).toBe("validation_error");

    const offline = new ApiClient({
      baseUrl: "/api/v1",
      fetchImpl: async () => {
        throw new TypeError("Failed to fetch");
      },
    });
    const netErr = (await offline.get("/x").catch((e: unknown) => e)) as ApiError;
    expect(netErr.isNetwork).toBe(true);
  });

  it("unwraps bare arrays and page envelopes", () => {
    expect(unwrapList([1, 2])).toEqual([1, 2]);
    expect(unwrapList({ items: [3], meta: { total: 1, offset: 0, limit: 1, has_more: false } })).toEqual([3]);
    expect(toPaged([1, 2, 3]).meta.total).toBe(3);
  });
});
