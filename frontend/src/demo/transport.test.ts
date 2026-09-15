/**
 * The in-page demo backend through its `fetch`: auth and role rules, order listing, the live
 * mutations (expedite, configuration), the plan state machine and what-if preset matching.
 */
import { beforeAll, describe, expect, it } from "vitest";

import type { PriorityProfile, SimulateBody } from "@/api/types";

import { loadDemoDataset } from "./demoData";
import { CLOSED_STATUSES } from "./orders";
import { DemoStore } from "./store";
import { createDemoFetch, createRuntime, type DemoRuntime } from "./transport";
import type { DemoData } from "./types";

interface Reply<T = Record<string, unknown>> {
  status: number;
  body: T;
}

interface Api {
  runtime: DemoRuntime;
  call: <T = Record<string, unknown>>(method: string, path: string, options?: { token?: string | null; body?: unknown }) => Promise<Reply<T>>;
  login: (username: string, password: string) => Promise<string>;
}

let dataset: DemoData;

beforeAll(async () => {
  dataset = await loadDemoDataset();
});

/** A fresh runtime on a private copy of the dataset, clock frozen five minutes after the capture, no storage. */
function makeApi(): Api {
  const capturedAt = new Date(dataset.meta.captured_at).getTime();
  const store = new DemoStore(structuredClone(dataset), { storage: null, shiftDays: 0, now: () => new Date(capturedAt + 5 * 60_000) });
  const runtime = createRuntime(store);
  const fetchImpl = createDemoFetch({ runtime });
  const call = async <T,>(method: string, path: string, options: { token?: string | null; body?: unknown } = {}): Promise<Reply<T>> => {
    const headers: Record<string, string> = { Accept: "application/json" };
    if (options.token) headers.Authorization = `Bearer ${options.token}`;
    if (options.body !== undefined) headers["Content-Type"] = "application/json";
    const response = await fetchImpl(`/api/v1${path}`, { method, headers, body: options.body === undefined ? undefined : JSON.stringify(options.body) });
    const body = response.status === 204 ? null : await response.json();
    return { status: response.status, body: body as T };
  };
  const login = async (username: string, password: string) => {
    const res = await call<{ access_token: string }>("POST", "/auth/login", { body: { username, password } });
    expect(res.status).toBe(200);
    return res.body.access_token;
  };
  return { runtime, call, login };
}

interface ErrorEnvelope {
  error: string;
  message: string;
  details: Record<string, unknown>;
}

interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
  has_more: boolean;
}

interface Row {
  order: { order_id: string; customer_id: string; order_status: string; due_date: string | null };
  priority: { score: number; rank: number | null; risk_level: string; blocked: boolean } | null;
}

describe("demo transport: authentication and roles", () => {
  it("logs in with the seeded credentials and answers /auth/me for the token", async () => {
    const api = makeApi();
    const res = await api.call<{ access_token: string; token_type: string; expires_in: number; user: { role: string; username: string } }>("POST", "/auth/login", {
      body: { username: "manager", password: "manager123" },
    });
    expect(res.status).toBe(200);
    expect(res.body.access_token).toBe("demo.production_manager");
    expect(res.body.token_type).toBe("bearer");
    expect(res.body.expires_in).toBe(dataset.auth.expires_in ?? 28_800);
    expect(res.body.user.role).toBe("production_manager");

    const me = await api.call<{ username: string; role: string }>("GET", "/auth/me", { token: res.body.access_token });
    expect(me.status).toBe(200);
    expect(me.body.username).toBe("manager");
  });

  it("rejects a wrong password and a missing token with 401 and the error envelope", async () => {
    const api = makeApi();
    const bad = await api.call<ErrorEnvelope>("POST", "/auth/login", { body: { username: "manager", password: "nope" } });
    expect(bad.status).toBe(401);
    expect(bad.body).toEqual({ error: "unauthenticated", message: "invalid username or password", details: {} });

    const anonymous = await api.call<ErrorEnvelope>("GET", "/orders");
    expect(anonymous.status).toBe(401);
    expect(anonymous.body.error).toBe("unauthenticated");
    expect(anonymous.body.message).toBe("missing bearer token");

    const garbage = await api.call<ErrorEnvelope>("GET", "/orders", { token: "demo.nobody" });
    expect(garbage.status).toBe(401);

    const missingField = await api.call<ErrorEnvelope>("POST", "/auth/login", { body: { username: "manager" } });
    expect(missingField.status).toBe(422);
    expect(missingField.body.error).toBe("validation_error");
  });

  it("applies the role rules: executives read but never write, operators cannot read the audit log", async () => {
    const api = makeApi();
    const executive = await api.login("executive", "executive123");
    const kpis = await api.call("GET", "/analytics/kpis", { token: executive });
    expect(kpis.status).toBe(200);

    const forbidden = await api.call<ErrorEnvelope>("POST", `/orders/${firstOpenOrder()}/expedite`, { token: executive, body: { reason: "x" } });
    expect(forbidden.status).toBe(403);
    expect(forbidden.body).toEqual({
      error: "forbidden",
      message: "role 'executive' is not allowed; requires 'production_manager' or higher",
      details: { required_role: "production_manager", actual_role: "executive" },
    });

    const operator = await api.login("operator", "operator123");
    const audit = await api.call<ErrorEnvelope>("GET", "/audit", { token: operator });
    expect(audit.status).toBe(403);
    expect(audit.body.message).toBe("role 'operator' is not allowed; requires 'production_manager' or higher");

    const supervisorRead = await api.call<ErrorEnvelope>("GET", "/priority/configuration", { token: await api.login("supervisor", "supervisor123") });
    expect(supervisorRead.status).toBe(403);
    expect(supervisorRead.body.message).toBe("role 'supervisor' may not read this resource; requires 'planner' or higher");

    const planner = await api.login("planner", "planner123");
    const users = await api.call<ErrorEnvelope>("GET", "/users", { token: planner });
    expect(users.status).toBe(403);

    const unknown = await api.call<ErrorEnvelope>("GET", "/orders/NOPE-1", { token: planner });
    expect(unknown.status).toBe(404);
    expect(unknown.body.error).toBe("not_found");
  });
});

function firstOpenOrder(): string {
  const open = Object.values(dataset.orders.detail).find((d) => d.priority !== null && d.priority.rank !== null && !d.priority.blocked);
  if (!open) throw new Error("no scored order in the dataset");
  return open.order.order_id;
}

describe("demo transport: order listing", () => {
  it("lists open orders by priority (desc) with PageResponse paging", async () => {
    const api = makeApi();
    const token = await api.login("planner", "planner123");
    const page1 = await api.call<Page<Row>>("GET", "/orders", { token });
    expect(page1.status).toBe(200);
    expect(page1.body.page).toBe(1);
    expect(page1.body.page_size).toBe(50);
    expect(page1.body.items).toHaveLength(50);
    expect(page1.body.has_more).toBe(true);
    const openCount = Object.values(dataset.orders.detail).filter((d) => !CLOSED_STATUSES.includes(d.order.order_status) && d.order.pending_quantity > 0).length;
    expect(page1.body.total).toBe(openCount);
    expect(page1.body.pages).toBe(Math.ceil(openCount / 50));
    const scores = page1.body.items.map((r) => r.priority?.score ?? -1);
    expect([...scores].sort((a, b) => b - a)).toEqual(scores);

    const page2 = await api.call<Page<Row>>("GET", "/orders?page=2&page_size=20", { token });
    expect(page2.body.items).toHaveLength(20);
    expect(page2.body.page).toBe(2);
    expect(page2.body.items[0]?.order.order_id).not.toBe(page1.body.items[0]?.order.order_id);

    const all = await api.call<Page<Row>>("GET", "/orders?open_only=false&page_size=500", { token });
    expect(all.body.total).toBe(Object.keys(dataset.orders.detail).length);
    expect(all.body.items.some((r) => r.order.order_status === "completed")).toBe(true);
  });

  it("supports every sort key and the filters", async () => {
    const api = makeApi();
    const token = await api.login("planner", "planner123");
    const byDue = await api.call<Page<Row>>("GET", "/orders?sort=due_date&order=asc&page_size=500", { token });
    const dues = byDue.body.items.map((r) => r.order.due_date).filter((d): d is string => d !== null);
    expect([...dues].sort()).toEqual(dues);

    const byRank = await api.call<Page<Row>>("GET", "/orders?sort=rank&page_size=500", { token });
    const ranks = byRank.body.items.map((r) => r.priority?.rank ?? Number.MAX_SAFE_INTEGER);
    expect([...ranks].sort((a, b) => a - b)).toEqual(ranks);

    const byId = await api.call<Page<Row>>("GET", "/orders?sort=order_id&order=desc&page_size=500", { token });
    const ids = byId.body.items.map((r) => r.order.order_id);
    expect([...ids].sort().reverse()).toEqual(ids);

    const customer = Object.values(dataset.orders.detail)[0]?.order.customer_id ?? "";
    const byCustomer = await api.call<Page<Row>>("GET", `/orders?customer_id=${customer}&open_only=false`, { token });
    expect(byCustomer.body.total).toBeGreaterThan(0);
    expect(byCustomer.body.items.every((r) => r.order.customer_id === customer)).toBe(true);

    const highRisk = await api.call<Page<Row>>("GET", "/orders?risk=high&page_size=500", { token });
    expect(highRisk.body.items.every((r) => r.priority?.risk_level === "high")).toBe(true);

    const statuses = await api.call<Page<Row>>("GET", "/orders?status=released&status=in_production&open_only=false&page_size=500", { token });
    expect(statuses.body.total).toBeGreaterThan(0);
    expect(statuses.body.items.every((r) => r.order.order_status === "released" || r.order.order_status === "in_production")).toBe(true);

    const needle = firstOpenOrder().slice(-8);
    const search = await api.call<Page<Row>>("GET", `/orders?search=${needle}`, { token });
    expect(search.body.items.some((r) => r.order.order_id.includes(needle))).toBe(true);

    const badSort = await api.call<ErrorEnvelope>("GET", "/orders?sort=nope", { token });
    expect(badSort.status).toBe(422);
    const badStatus = await api.call<ErrorEnvelope>("GET", "/orders?status=bogus", { token });
    expect(badStatus.status).toBe(422);
  });
});

interface Explanation {
  order_id: string;
  score: number;
  rank: number | null;
  explanation: string;
  lines: Array<{ kind: string; key: string; points: number; reason: string; source_id?: string | null }>;
}

describe("demo transport: expedite", () => {
  it("requires a reason, writes an expedite adjustment and an audit row, and re-ranks the queue", async () => {
    const api = makeApi();
    const manager = await api.login("manager", "manager123");
    const planner = await api.login("planner", "planner123");
    // Pick a scored, unblocked order well down the queue so the +30 boost visibly moves it
    // (a blocked order would stay under the profile's blocked_order_cap, exactly like the engine).
    const queue = await api.call<Page<Row>>("GET", "/orders?sort=rank&page_size=500", { token: planner });
    const target = queue.body.items.find((r) => r.priority !== null && !r.priority.blocked && (r.priority.rank ?? 0) > 100 && r.priority.score < 60);
    if (!target) throw new Error("no suitable order");
    const before = await api.call<Explanation>("GET", `/orders/${target.order.order_id}/explanation`, { token: planner });
    expect(before.status).toBe(200);

    const noReason = await api.call<ErrorEnvelope>("POST", `/orders/${target.order.order_id}/expedite`, { token: manager, body: { boost_points: 30 } });
    expect(noReason.status).toBe(422);
    expect(noReason.body.error).toBe("validation_error");
    expect(JSON.stringify(noReason.body.details)).toContain("reason");

    const plannerTry = await api.call<ErrorEnvelope>("POST", `/orders/${target.order.order_id}/expedite`, { token: planner, body: { reason: "customer escalation" } });
    expect(plannerTry.status).toBe(403);

    const created = await api.call<{ expedite_id: string; order_id: string; boost_points: number; active: boolean }>("POST", `/orders/${target.order.order_id}/expedite`, {
      token: manager,
      body: { reason: "customer escalation", boost_points: 30, duration_hours: 24 },
    });
    expect(created.status).toBe(201);
    expect(created.body.order_id).toBe(target.order.order_id);
    expect(created.body.boost_points).toBe(30);
    expect(created.body.active).toBe(true);

    const after = await api.call<Explanation>("GET", `/orders/${target.order.order_id}/explanation`, { token: planner });
    const adjustment = after.body.lines.find((l) => l.kind === "adjustment" && l.key === "expedite");
    expect(adjustment).toBeDefined();
    expect(adjustment?.points).toBe(30);
    expect(adjustment?.source_id).toBe(created.body.expedite_id);
    expect(before.body.lines.some((l) => l.key === "expedite")).toBe(false);
    expect(after.body.score).toBeCloseTo(Math.min(100, before.body.score + 30), 6);
    expect((after.body.rank ?? Infinity) < (before.body.rank ?? Infinity)).toBe(true);
    expect(after.body.explanation).toContain("+30 — Expedite");

    const queueAfter = await api.call<Page<Row>>("GET", "/orders?sort=rank&page_size=500", { token: planner });
    const position = queueAfter.body.items.findIndex((r) => r.order.order_id === target.order.order_id);
    expect(position).toBe((after.body.rank ?? 0) - 1);
    const ranksAfter = queueAfter.body.items.map((r) => r.priority?.rank);
    expect(ranksAfter.slice(0, 10)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);

    const audit = await api.call<Page<{ action: string; entity_id: string; reason: string | null; user_id: string }>>("GET", "/audit", { token: manager });
    expect(audit.status).toBe(200);
    expect(audit.body.items[0]).toMatchObject({ action: "expedite.create", entity_id: target.order.order_id, reason: "customer escalation" });

    const detail = await api.call<{ expedites: Array<{ expedite_id: string }>; adjustments: Array<{ kind: string }> }>("GET", `/orders/${target.order.order_id}`, { token: planner });
    expect(detail.body.expedites.map((e) => e.expedite_id)).toContain(created.body.expedite_id);

    const cancelled = await api.call<{ active: boolean }>("DELETE", `/expedites/${created.body.expedite_id}?reason=${encodeURIComponent("resolved")}`, { token: manager });
    expect(cancelled.status).toBe(200);
    expect(cancelled.body.active).toBe(false);
    const restored = await api.call<Explanation>("GET", `/orders/${target.order.order_id}/explanation`, { token: planner });
    expect(restored.body.score).toBeCloseTo(before.body.score, 6);
  });
});

describe("demo transport: priority configuration", () => {
  it("re-scores every order on PUT and previews the top-N moves with the engine's sentence", async () => {
    const api = makeApi();
    const admin = await api.login("admin", "admin123");
    const planner = await api.login("planner", "planner123");
    const current = await api.call<{ profile: PriorityProfile; version: { version: number } }>("GET", "/priority/configuration", { token: admin });
    expect(current.status).toBe(200);
    const orderId = firstOpenOrder();
    const before = await api.call<{ priority: { score: number; profile_version: number } }>("GET", `/orders/${orderId}`, { token: planner });

    // Preview the captured candidate (Due Date Urgency 25% -> 40%): same sentence as the real engine.
    const captured = dataset.configuration.preview;
    const preview = await api.call<{ summary: string; entered_top_n: unknown[]; left_top_n: unknown[]; top_n: number; orders_evaluated: number; weight_changes: unknown[] }>("POST", "/priority/configuration/preview", {
      token: planner,
      body: captured.request,
    });
    expect(preview.status).toBe(200);
    expect(preview.body.summary).toBe(captured.response.summary);
    expect(preview.body.top_n).toBe(captured.response.top_n);
    expect(preview.body.orders_evaluated).toBe(captured.response.orders_evaluated);
    expect(preview.body.entered_top_n).toEqual(captured.response.entered_top_n);
    expect(preview.body.left_top_n).toEqual(captured.response.left_top_n);

    const unchanged = await api.call<ErrorEnvelope>("PUT", "/priority/configuration", { token: admin, body: { profile: current.body.profile, reason: "no change" } });
    expect(unchanged.status).toBe(409);

    const plannerPut = await api.call<ErrorEnvelope>("PUT", "/priority/configuration", { token: planner, body: { profile: captured.request.profile, reason: "x" } });
    expect(plannerPut.status).toBe(403);

    const saved = await api.call<{ version: { version: number; is_active: boolean }; previous_version: number | null; changed_new: Record<string, unknown>; config: { priority_profile: PriorityProfile } }>("PUT", "/priority/configuration", {
      token: admin,
      body: { profile: captured.request.profile, reason: "favour due dates" },
    });
    expect(saved.status).toBe(200);
    expect(saved.body.version.version).toBe(current.body.version.version + 1);
    expect(saved.body.version.is_active).toBe(true);
    expect(saved.body.previous_version).toBe(current.body.version.version);
    expect(Object.keys(saved.body.changed_new).some((k) => k.startsWith("priority_profile.weights"))).toBe(true);
    const reread = await api.call<{ profile: PriorityProfile; version: { version: number }; weights_pct: Record<string, number> }>("GET", "/priority/configuration", { token: admin });
    expect(reread.body.version.version).toBe(saved.body.version.version);
    expect(reread.body.profile.weights.find((w) => w.key === "due_date_urgency")?.weight).toBe(40);

    const after = await api.call<{ priority: { score: number; profile_version: number } }>("GET", `/orders/${orderId}`, { token: planner });
    expect(after.body.priority.profile_version).toBe(saved.body.version.version);
    expect(after.body.priority.score).not.toBeCloseTo(before.body.priority.score, 6);

    const versions = await api.call<Array<{ version: number; is_active: boolean }>>("GET", "/priority/configuration/versions", { token: planner });
    expect(versions.body.find((v) => v.is_active)?.version).toBe(saved.body.version.version);

    // Rollback: activating the previous version re-scores back to the captured numbers.
    const rollback = await api.call<{ version: number; is_active: boolean }>("POST", `/priority/configuration/versions/${current.body.version.version}/activate`, {
      token: admin,
      body: { reason: "roll back" },
    });
    expect(rollback.status).toBe(200);
    const restored = await api.call<{ priority: { score: number } }>("GET", `/orders/${orderId}`, { token: planner });
    expect(restored.body.priority.score).toBeCloseTo(before.body.priority.score, 6);

    const audit = await api.call<Page<{ action: string }>>("GET", "/audit?page_size=5", { token: admin });
    expect(audit.body.items.map((a) => a.action).slice(0, 2)).toEqual(["config.activate_version", "config.update_priority_profile"]);
  });
});

interface VersionInfo {
  version_number: number;
  status: string;
}

describe("demo transport: plan state machine", () => {
  it("approves and publishes a draft, supersedes the published plan and rejects invalid transitions with 409", async () => {
    const api = makeApi();
    const manager = await api.login("manager", "manager123");
    const planner = await api.login("planner", "planner123");
    const versions = await api.call<Page<VersionInfo>>("GET", "/schedule/versions?page_size=50", { token: planner });
    const published = versions.body.items.find((v) => v.status === "published");
    const drafts = versions.body.items.filter((v) => v.status === "draft").map((v) => v.version_number).sort((a, b) => a - b);
    const rejected = versions.body.items.find((v) => v.status === "rejected");
    expect(published).toBeDefined();
    expect(drafts.length).toBeGreaterThanOrEqual(2);
    const [draftA, draftB] = drafts as [number, number];

    const plannerApprove = await api.call<ErrorEnvelope>("POST", "/schedule/approve", { token: planner, body: { version: draftA, reason: "looks good" } });
    expect(plannerApprove.status).toBe(403);
    const noReason = await api.call<ErrorEnvelope>("POST", "/schedule/approve", { token: manager, body: { version: draftA } });
    expect(noReason.status).toBe(422);

    const publishDraft = await api.call<ErrorEnvelope>("POST", "/schedule/publish", { token: manager, body: { version: draftA, reason: "skip approval" } });
    expect(publishDraft.status).toBe(409);
    expect(publishDraft.body.error).toBe("conflict");

    const approved = await api.call<{ status: string; version_number: number; approved_by: string | null }>("POST", "/schedule/approve", { token: manager, body: { version: draftA, reason: "looks good" } });
    expect(approved.status).toBe(200);
    expect(approved.body.status).toBe("approved");
    expect(approved.body.approved_by).toBeTruthy();

    const again = await api.call<ErrorEnvelope>("POST", "/schedule/approve", { token: manager, body: { version: draftA, reason: "again" } });
    expect(again.status).toBe(409);

    const publishedRes = await api.call<{ version: { status: string; version_number: number }; receipt: { status: string; message: string } | null; superseded: number }>("POST", "/schedule/publish", {
      token: manager,
      body: { version: draftA, reason: "go live" },
    });
    expect(publishedRes.status).toBe(200);
    expect(publishedRes.body.version.status).toBe("published");
    expect(publishedRes.body.receipt?.status).toBe("skipped_read_only");
    expect(publishedRes.body.superseded).toBe(1);

    const afterPublish = await api.call<Page<VersionInfo>>("GET", "/schedule/versions?page_size=50", { token: planner });
    const byNumber = new Map(afterPublish.body.items.map((v) => [v.version_number, v.status]));
    expect(byNumber.get(draftA)).toBe("published");
    expect(byNumber.get(published?.version_number ?? -1)).toBe("superseded");
    const current = await api.call<{ version: { version_number: number; status: string } }>("GET", "/schedule", { token: planner });
    expect(current.body.version.version_number).toBe(draftA);

    const approveSuperseded = await api.call<ErrorEnvelope>("POST", "/schedule/approve", { token: manager, body: { version: published?.version_number, reason: "x" } });
    expect(approveSuperseded.status).toBe(409);
    if (rejected) {
      const publishRejected = await api.call<ErrorEnvelope>("POST", "/schedule/publish", { token: manager, body: { version: rejected.version_number, reason: "x" } });
      expect(publishRejected.status).toBe(409);
    }

    const rejectB = await api.call<{ status: string }>("POST", "/schedule/reject", { token: manager, body: { version: draftB, reason: "not this one" } });
    expect(rejectB.status).toBe(200);
    expect(rejectB.body.status).toBe("rejected");
    const approveRejected = await api.call<ErrorEnvelope>("POST", "/schedule/reject", { token: manager, body: { version: draftB, reason: "twice" } });
    expect(approveRejected.status).toBe(409);

    const missing = await api.call<ErrorEnvelope>("POST", "/schedule/approve", { token: manager, body: { version: 999, reason: "x" } });
    expect(missing.status).toBe(404);

    const generated = await api.call<{ version: { version_number: number; status: string } }>("POST", "/schedule/generate", { token: planner, body: { note: "fresh draft" } });
    expect(generated.status).toBe(201);
    expect(generated.body.version.status).toBe("draft");
    expect(generated.body.version.version_number).toBe(Math.max(...afterPublish.body.items.map((v) => v.version_number)) + 1);

    const audit = await api.call<Page<{ action: string }>>("GET", "/audit?page_size=10", { token: manager });
    const actions = audit.body.items.map((a) => a.action);
    expect(actions).toContain("schedule.approved");
    expect(actions).toContain("schedule.published");
    expect(actions).toContain("schedule.rejected");
    expect(actions).toContain("schedule.generated");
  });
});

describe("demo transport: what-if simulation", () => {
  it("answers each scenario kind from the matching preset and explains the limits with 422", async () => {
    const api = makeApi();
    const planner = await api.login("planner", "planner123");
    const presets = dataset.simulations.presets ?? [];
    expect(presets.length).toBeGreaterThan(0);

    const body: SimulateBody = { scenarios: [{ kind: "machine_down", machine_id: "MC-CNC3-02", duration_hours: 4, reason: "test" } as SimulateBody["scenarios"][number]], top_n: 10 };
    const res = await api.call<{ summary: string; simulation_id: string; top_n: number; scenarios: Array<{ kind: string }>; top_orders?: unknown[]; orders?: unknown[] }>("POST", "/schedule/simulate", { token: planner, body });
    expect(res.status).toBe(200);
    expect(res.body.summary.startsWith("Demo preset (kind machine_down): ")).toBe(true);
    expect(res.body.top_n).toBe(10);
    expect(res.body.simulation_id).toMatch(/^sim_/);

    // Order-based kinds pick the preset sharing the most ids; a different id still gets the kind's preset.
    const holdPreset = presets.find((p) => p.kind === "hold_orders");
    if (holdPreset) {
      const hold = await api.call<{ summary: string }>("POST", "/schedule/simulate", { token: planner, body: { scenarios: [{ kind: "hold_orders", order_ids: [firstOpenOrder()], reason: "x" } as SimulateBody["scenarios"][number]] } });
      expect(hold.status).toBe(200);
      expect(hold.body.summary.startsWith("Demo preset (kind hold_orders): ")).toBe(true);
    }

    const unknownKind = await api.call<ErrorEnvelope>("POST", "/schedule/simulate", { token: planner, body: { scenarios: [{ kind: "teleport" }] } });
    expect(unknownKind.status).toBe(422);

    const two = await api.call<ErrorEnvelope>("POST", "/schedule/simulate", {
      token: planner,
      body: { scenarios: [{ kind: "machine_down", machine_id: "MC-CNC3-02", duration_hours: 4 }, { kind: "extra_working_day", day: "2026-09-19" }] },
    });
    expect(two.status).toBe(422);
    expect(two.body.message).toContain("preset");

    const empty = await api.call<ErrorEnvelope>("POST", "/schedule/simulate", { token: planner, body: { scenarios: [] } });
    expect(empty.status).toBe(422);

    const operator = await api.login("operator", "operator123");
    const forbidden = await api.call<ErrorEnvelope>("POST", "/schedule/simulate", { token: operator, body });
    expect(forbidden.status).toBe(403);

    const audit = await api.call<Page<{ action: string }>>("GET", "/audit?page_size=5", { token: await api.login("manager", "manager123") });
    expect(audit.body.items.some((a) => a.action === "simulation.run")).toBe(true);
  });
});

describe("demo transport: alerts and persistence", () => {
  it("acknowledges an alert once and keeps mutations in the journal", async () => {
    const api = makeApi();
    const supervisor = await api.login("supervisor", "supervisor123");
    const list = await api.call<Page<{ alert_id: string; acknowledged: boolean }>>("GET", "/alerts?page_size=5", { token: supervisor });
    expect(list.status).toBe(200);
    const alert = list.body.items.find((a) => !a.acknowledged);
    if (!alert) throw new Error("no unacknowledged alert");
    const ack = await api.call<{ acknowledged: boolean; acknowledged_by: string | null }>("POST", `/alerts/${alert.alert_id}/acknowledge`, { token: supervisor, body: { note: "seen" } });
    expect(ack.status).toBe(200);
    expect(ack.body.acknowledged).toBe(true);
    const twice = await api.call<ErrorEnvelope>("POST", `/alerts/${alert.alert_id}/acknowledge`, { token: supervisor, body: {} });
    expect(twice.status).toBe(409);
    expect(api.runtime.store.hasMutations()).toBe(true);
    const summary = await api.call<{ unacknowledged: number; total: number }>("GET", "/alerts/summary", { token: supervisor });
    expect(summary.status).toBe(200);
  });
});
