/**
 * Route table of the demo backend: every endpoint of docs/API.md with the same paths,
 * query parameters, request bodies, status codes and role rules (executive = read-only).
 */
import type { AlertSeverity, AlertType, CustomerTier, DataQualityCode, DataQualitySeverity, LockType, MachineStatus, OrderStatus, PriorityProfile, ProcessType, ReadinessState, ReplanTriggerType, RiskLevel, Role, ScheduleStatus, SimulateBody, SyncMode, UserInfo } from "@/api/types";
import { ALERT_SEVERITIES, ALERT_TYPES, DQ_CODES, DQ_SEVERITIES, ORDER_STATUSES, PROCESS_TYPES, READINESS_LABELS, RISK_LEVELS, ROLES } from "@/lib/constants";

import type { ActiveConfig, ConfigSection } from "./config";
import { invalidField, missingField, unauthenticated, validation } from "./errors";
import { SORT_KEYS, type OrderModel, type SortKey } from "./orders";
import { CAPACITY_DIMENSIONS, CAPACITY_PERIODS, DEFAULT_OTD_WINDOW_DAYS, MAX_HORIZON_DAYS, MAX_OTD_WINDOW_DAYS, type PlanModel } from "./plan";
import type { DemoStore } from "./store";
import type { SystemModel } from "./system";
import { bodyObject, optionalDate, optionalNumber, optionalString, requireReason, requiredString, type Query } from "./util";

export interface DemoModels {
  config: ActiveConfig;
  plan: PlanModel;
  orders: OrderModel;
  system: SystemModel;
}

export type Access = { kind: "public" } | { kind: "any" } | { kind: "read"; min: Role } | { kind: "write"; min: Role };

export interface HandlerContext {
  store: DemoStore;
  models: DemoModels;
  user: UserInfo | null;
  params: Record<string, string>;
  query: Query;
  body: unknown;
  method: string;
  path: string;
}

export interface RouteResult {
  status: number;
  body: unknown;
}

export interface Route {
  method: string;
  pattern: string;
  segments: string[];
  access: Access;
  handler: (ctx: HandlerContext) => RouteResult;
}

const PAGE_MAX = 500;
const READINESS_STATES = Object.keys(READINESS_LABELS) as ReadinessState[];
const LOCK_TYPES: LockType[] = ["order", "machine", "sequence", "time_slot"];
const TIERS: CustomerTier[] = ["strategic", "key", "standard", "low"];
const SCHEDULE_STATUSES: ScheduleStatus[] = ["draft", "approved", "published", "superseded", "rejected"];
const REPLAN_TRIGGERS: ReplanTriggerType[] = ["new_order", "order_completed", "machine_down", "machine_up", "material_arrived", "quality_failure", "rework", "production_delay", "customer_priority_change", "manual", "config_change", "scheduled"];

function ok(body: unknown, status = 200): RouteResult {
  return { status, body };
}

function paging(q: Query, defaultSize: number): { page: number; page_size: number } {
  return { page: q.int("page", 1, { min: 1 }), page_size: q.int("page_size", defaultSize, { min: 1, max: PAGE_MAX }) };
}

function requireUser(ctx: HandlerContext): UserInfo {
  if (!ctx.user) throw unauthenticated();
  return ctx.user;
}

function requireBody(ctx: HandlerContext): Record<string, unknown> {
  return bodyObject(ctx.body);
}

function requireEnum<T extends string>(body: Record<string, unknown>, field: string, values: readonly T[]): T {
  const raw = body[field];
  if (raw === undefined || raw === null) throw missingField("body", field);
  if (typeof raw !== "string" || !(values as readonly string[]).includes(raw)) throw invalidField("body", field, `Input should be one of: ${values.join(", ")}`);
  return raw as T;
}

function optionalEnum<T extends string>(body: Record<string, unknown>, field: string, values: readonly T[]): T | null {
  const raw = body[field];
  if (raw === undefined || raw === null) return null;
  if (typeof raw !== "string" || !(values as readonly string[]).includes(raw)) throw invalidField("body", field, `Input should be one of: ${values.join(", ")}`);
  return raw as T;
}

const read = (min: Role): Access => ({ kind: "read", min });
const write = (min: Role): Access => ({ kind: "write", min });
const PUBLIC: Access = { kind: "public" };
const ANY: Access = { kind: "any" };

function route(method: string, pattern: string, access: Access, handler: (ctx: HandlerContext) => RouteResult): Route {
  return { method, pattern, segments: pattern.split("/").filter(Boolean), access, handler };
}

// --------------------------------------------------------------- handlers

const routes: Route[] = [
  // ------------------------------------------------------------------ auth
  route("POST", "/auth/login", PUBLIC, (ctx) => {
    const body = requireBody(ctx);
    const username = requiredString(body, "username");
    const password = requiredString(body, "password");
    const user = ctx.store.authenticate(username, password);
    if (!user) throw unauthenticated("invalid username or password");
    ctx.models.system.recordLogin(user);
    const info = ctx.store.toUserInfo(user);
    return ok({ access_token: ctx.store.tokenFor(user), token_type: "bearer", expires_in: ctx.store.data.auth.expires_in ?? 28_800, role: info.role, user: info });
  }),
  route("GET", "/auth/me", ANY, (ctx) => ok(requireUser(ctx))),

  // ---------------------------------------------------------------- health
  route("GET", "/health", PUBLIC, (ctx) => ok(ctx.models.system.health())),
  route("GET", "/metrics", PUBLIC, (ctx) => ok(ctx.models.system.metrics())),

  // ---------------------------------------------------------------- orders
  route("GET", "/orders", read("operator"), (ctx) => {
    const q = ctx.query;
    const { page, page_size } = paging(q, 50);
    const sort = (q.oneOf("sort", SORT_KEYS) ?? "priority") as SortKey;
    const order = q.oneOf("order", ["asc", "desc"] as const);
    const statuses = q.list("status").map((s) => {
      if (!(ORDER_STATUSES as readonly string[]).includes(s)) throw invalidField("query", "status", `Input should be one of: ${ORDER_STATUSES.join(", ")}`);
      return s as OrderStatus;
    });
    const rows = ctx.models.orders.listRows(
      {
        customer_id: q.str("customer_id"),
        statuses,
        machine_group: q.str("machine_group"),
        process_type: q.oneOf("process_type", PROCESS_TYPES) as ProcessType | undefined,
        machine_id: q.str("machine_id"),
        due_from: q.date("due_from"),
        due_to: q.date("due_to"),
        risk: q.oneOf("risk", RISK_LEVELS) as RiskLevel | undefined,
        readiness: q.oneOf("readiness", READINESS_STATES),
        on_hold: q.bool("on_hold"),
        search: q.str("search"),
        open_only: q.bool("open_only") ?? true,
      },
      sort,
      order === undefined ? null : order === "desc",
    );
    const start = (page - 1) * page_size;
    return ok({ items: rows.slice(start, start + page_size), total: rows.length, page, page_size, pages: Math.ceil(rows.length / page_size), has_more: page * page_size < rows.length });
  }),
  route("GET", "/orders/:order_id", read("operator"), (ctx) => ok(ctx.models.orders.detail(ctx.params.order_id ?? ""))),
  route("GET", "/orders/:order_id/explanation", read("operator"), (ctx) => ok(ctx.models.orders.explanation(ctx.params.order_id ?? ""))),
  route("GET", "/orders/:order_id/machines", read("operator"), (ctx) => ok(ctx.models.orders.machineOptions(ctx.params.order_id ?? ""))),
  route("GET", "/orders/:order_id/overrides", read("operator"), (ctx) => {
    ctx.models.orders.base(ctx.params.order_id ?? "");
    return ok(ctx.store.overridesForOrder(ctx.params.order_id ?? "", true));
  }),
  route("POST", "/orders/:order_id/expedite", write("production_manager"), (ctx) => {
    const body = requireBody(ctx);
    const reason = requireReason(body);
    const boost = optionalNumber(body, "boost_points");
    const duration = optionalNumber(body, "duration_hours");
    if (boost !== null && boost <= 0) throw invalidField("body", "boost_points", "Input should be greater than 0");
    if (duration !== null && duration <= 0) throw invalidField("body", "duration_hours", "Input should be greater than 0");
    return ok(ctx.models.orders.expedite(ctx.params.order_id ?? "", requireUser(ctx), reason, { boost_points: boost, duration_hours: duration, starts_at: optionalDate(body, "starts_at"), expires_at: optionalDate(body, "expires_at") }), 201);
  }),
  route("POST", "/orders/:order_id/hold", write("planner"), (ctx) => {
    const body = requireBody(ctx);
    return ok(ctx.models.orders.hold(ctx.params.order_id ?? "", requireUser(ctx), requireReason(body), optionalDate(body, "expires_at")), 201);
  }),
  route("POST", "/orders/:order_id/release", write("planner"), (ctx) => {
    const body = requireBody(ctx);
    return ok(ctx.models.orders.release(ctx.params.order_id ?? "", requireUser(ctx), requireReason(body)), 201);
  }),
  route("POST", "/orders/:order_id/override-priority", write("production_manager"), (ctx) => {
    const body = requireBody(ctx);
    const reason = requireReason(body);
    const type = requireEnum(body, "type", ["increase", "decrease", "set"] as const);
    const value = optionalNumber(body, "value");
    if (value === null) throw missingField("body", "value");
    if (value < 0 || value > 100) throw invalidField("body", "value", "Input should be between 0 and 100");
    return ok(ctx.models.orders.overridePriority(ctx.params.order_id ?? "", requireUser(ctx), reason, type, value, optionalDate(body, "expires_at")), 201);
  }),
  route("POST", "/orders/:order_id/force-next", write("production_manager"), (ctx) => {
    const body = requireBody(ctx);
    return ok(ctx.models.orders.forceNext(ctx.params.order_id ?? "", requireUser(ctx), requireReason(body), optionalDate(body, "expires_at")), 201);
  }),
  route("POST", "/orders/:order_id/move", write("production_manager"), (ctx) => {
    const body = requireBody(ctx);
    const reason = requireReason(body);
    const target = requiredString(body, "target_machine_id");
    return ok(ctx.models.orders.move(ctx.params.order_id ?? "", requireUser(ctx), reason, target, optionalDate(body, "start_at"), optionalDate(body, "expires_at")), 201);
  }),
  route("POST", "/orders/:order_id/lock-machine", write("production_manager"), (ctx) => {
    const body = requireBody(ctx);
    const reason = requireReason(body);
    const machineId = requiredString(body, "machine_id");
    return ok(ctx.models.orders.lockMachine(ctx.params.order_id ?? "", requireUser(ctx), reason, machineId, optionalDate(body, "expires_at")), 201);
  }),
  route("GET", "/overrides", read("operator"), (ctx) => ok(ctx.store.activeOverrides())),
  route("DELETE", "/overrides/:override_id", write("production_manager"), (ctx) => {
    const body = ctx.body === null || ctx.body === undefined ? {} : bodyObject(ctx.body);
    const reason = requireReason({ reason: body.reason ?? ctx.query.str("reason") });
    return ok(ctx.models.orders.cancelOverride(ctx.params.override_id ?? "", requireUser(ctx), reason));
  }),
  route("GET", "/expedites", read("operator"), (ctx) => {
    const orderId = ctx.query.str("order_id");
    return ok(orderId ? ctx.store.expeditesForOrder(orderId, true) : ctx.store.activeExpedites());
  }),
  route("DELETE", "/expedites/:expedite_id", write("production_manager"), (ctx) => {
    const body = ctx.body === null || ctx.body === undefined ? {} : bodyObject(ctx.body);
    const reason = requireReason({ reason: body.reason ?? ctx.query.str("reason") });
    return ok(ctx.models.orders.cancelExpedite(ctx.params.expedite_id ?? "", requireUser(ctx), reason));
  }),

  // -------------------------------------------------------------- machines
  route("GET", "/machines", read("operator"), (ctx) =>
    ok(
      ctx.models.plan.machineList({
        machine_group: ctx.query.str("machine_group"),
        process_type: ctx.query.oneOf("process_type", PROCESS_TYPES) as ProcessType | undefined,
        status: ctx.query.oneOf("status", ["available", "running", "down", "maintenance", "offline"] as const) as MachineStatus | undefined,
      }),
    ),
  ),
  route("GET", "/machines/:machine_id", read("operator"), (ctx) => ok(ctx.models.plan.machineDetail(ctx.params.machine_id ?? ""))),
  route("GET", "/machines/:machine_id/schedule", read("operator"), (ctx) =>
    ok(ctx.models.plan.machineSchedule(ctx.params.machine_id ?? "", { start: ctx.query.date("start"), end: ctx.query.date("end"), version: ctx.query.optInt("version") })),
  ),

  // -------------------------------------------------------------- schedule
  route("GET", "/schedule", read("operator"), (ctx) => {
    const q = ctx.query;
    const { page, page_size } = paging(q, 100);
    const current = ctx.models.plan.current();
    if (!current) return ok({ status: "none", version: null, entries: { items: [], total: 0, page, page_size, pages: 0, has_more: false } });
    const entries = ctx.models.plan.filteredEntries(current.number, { machine_ids: q.list("machine_id"), start: q.date("start"), end: q.date("end"), order_id: q.str("order_id"), customer_id: q.str("customer_id") });
    const start = (page - 1) * page_size;
    return ok({
      status: current.response.status,
      version: current.response,
      entries: { items: entries.slice(start, start + page_size), total: entries.length, page, page_size, pages: Math.ceil(entries.length / page_size), has_more: page * page_size < entries.length },
    });
  }),
  route("GET", "/schedule/versions", read("operator"), (ctx) => {
    const { page, page_size } = paging(ctx.query, 50);
    const status = ctx.query.oneOf("status", SCHEDULE_STATUSES);
    const versions = ctx.models.plan.versions().filter((v) => !status || v.response.status === status);
    const start = (page - 1) * page_size;
    return ok({ items: versions.slice(start, start + page_size).map((v) => v.response), total: versions.length, page, page_size, pages: Math.ceil(versions.length / page_size), has_more: page * page_size < versions.length });
  }),
  route("GET", "/schedule/versions/:version", read("operator"), (ctx) => ok(ctx.models.plan.get(versionParam(ctx.params.version)).detail)),
  route("GET", "/schedule/versions/:version/entries", read("operator"), (ctx) => {
    const q = ctx.query;
    const { page, page_size } = paging(q, 100);
    const version = ctx.models.plan.get(versionParam(ctx.params.version));
    const entries = ctx.models.plan.filteredEntries(version.number, { machine_ids: q.list("machine_id"), start: q.date("start"), end: q.date("end"), order_id: q.str("order_id"), customer_id: q.str("customer_id") });
    const start = (page - 1) * page_size;
    return ok({ items: entries.slice(start, start + page_size), total: entries.length, page, page_size, pages: Math.ceil(entries.length / page_size), has_more: page * page_size < entries.length });
  }),
  route("GET", "/schedule/gantt", read("operator"), (ctx) => {
    const q = ctx.query;
    return ok(
      ctx.models.plan.gantt({
        version: q.optInt("version"),
        start: q.date("start"),
        end: q.date("end"),
        machine_group: q.str("machine_group"),
        process_type: q.oneOf("process_type", PROCESS_TYPES) as ProcessType | undefined,
        machine_ids: q.list("machine_id"),
      }),
    );
  }),
  route("GET", "/schedule/compare", read("operator"), (ctx) => {
    const a = ctx.query.optInt("a", { min: 1 });
    const b = ctx.query.optInt("b", { min: 1 });
    if (a === undefined) throw missingField("query", "a");
    if (b === undefined) throw missingField("query", "b");
    return ok(ctx.models.plan.compare(a, b));
  }),
  route("GET", "/schedule/runs/:run_id", read("operator"), (ctx) => ok(ctx.models.plan.runDetails(ctx.params.run_id ?? ""))),
  route("GET", "/schedule/locks", read("operator"), (ctx) => ok(ctx.models.plan.listLocks({ machine_id: ctx.query.str("machine_id"), order_id: ctx.query.str("order_id"), lock_type: ctx.query.oneOf("lock_type", LOCK_TYPES) }))),
  route("POST", "/schedule/lock", write("production_manager"), (ctx) => {
    const body = requireBody(ctx);
    const reason = requireReason(body);
    const lockType = requireEnum(body, "lock_type", LOCK_TYPES);
    const sequence = Array.isArray(body.sequence_order_ids) ? body.sequence_order_ids.map(String) : [];
    return ok(ctx.models.plan.createLock(requireUser(ctx), reason, { lock_type: lockType, order_id: optionalString(body, "order_id"), machine_id: optionalString(body, "machine_id"), window_start: optionalDate(body, "window_start"), window_end: optionalDate(body, "window_end"), sequence_order_ids: sequence }), 201);
  }),
  route("POST", "/schedule/unlock", write("production_manager"), (ctx) => {
    const body = requireBody(ctx);
    const reason = requireReason(body);
    return ok(ctx.models.plan.unlock(requiredString(body, "lock_id"), requireUser(ctx), reason));
  }),
  route("POST", "/schedule/generate", write("planner"), (ctx) => {
    const body = ctx.body === null || ctx.body === undefined ? {} : bodyObject(ctx.body);
    const note = optionalString(body, "note");
    if (note !== null && note.length > 2000) throw invalidField("body", "note", "String should have at most 2000 characters");
    return ok(ctx.models.plan.generate(requireUser(ctx), note), 201);
  }),
  route("POST", "/schedule/simulate", write("planner"), (ctx) => ok(ctx.models.system.simulate(requireUser(ctx), requireBody(ctx) as unknown as SimulateBody))),
  route("POST", "/schedule/approve", write("production_manager"), (ctx) => {
    const body = requireBody(ctx);
    return ok(ctx.models.plan.approve(versionBody(body), requireUser(ctx), requireReason(body)));
  }),
  route("POST", "/schedule/publish", write("production_manager"), (ctx) => {
    const body = requireBody(ctx);
    return ok(ctx.models.plan.publish(versionBody(body), requireUser(ctx), requireReason(body)));
  }),
  route("POST", "/schedule/reject", write("production_manager"), (ctx) => {
    const body = requireBody(ctx);
    return ok(ctx.models.plan.reject(versionBody(body), requireUser(ctx), requireReason(body)));
  }),
  route("POST", "/schedule/replan", write("planner"), (ctx) => {
    const body = ctx.body === null || ctx.body === undefined ? {} : bodyObject(ctx.body);
    const trigger = optionalEnum(body, "trigger", REPLAN_TRIGGERS) ?? "manual";
    return ok(ctx.models.plan.replan(requireUser(ctx), trigger, optionalString(body, "reason")));
  }),
  route("GET", "/schedule/:date", read("operator"), (ctx) => ok(ctx.models.plan.day(ctx.params.date ?? "", ctx.query.optInt("version")))),

  // ------------------------------------------------------------- simulation
  route("GET", "/simulation/scenario-types", read("operator"), (ctx) => ok(ctx.models.system.scenarioTypes())),

  // -------------------------------------------------------------- analytics
  route("GET", "/analytics/kpis", read("executive"), (ctx) => ok(ctx.models.plan.kpis())),
  route("GET", "/analytics/capacity", read("executive"), (ctx) =>
    ok(ctx.models.plan.capacity(ctx.query.oneOf("dimension", CAPACITY_DIMENSIONS) ?? "machine_group", ctx.query.oneOf("period", CAPACITY_PERIODS) ?? "week", ctx.query.optInt("horizon_days", { min: 1, max: MAX_HORIZON_DAYS }))),
  ),
  route("GET", "/analytics/bottlenecks", read("executive"), (ctx) => ok(ctx.models.plan.bottlenecks())),
  route("GET", "/analytics/on-time-delivery", read("executive"), (ctx) => ok(ctx.models.plan.onTimeDelivery(ctx.query.int("window_days", DEFAULT_OTD_WINDOW_DAYS, { min: 1, max: MAX_OTD_WINDOW_DAYS })))),
  route("GET", "/analytics/schedule-quality", read("executive"), (ctx) => ok(ctx.models.plan.qualityReport())),

  // ---------------------------------------------------- priority configuration
  route("GET", "/priority/configuration", read("planner"), (ctx) => ok(ctx.models.config.priorityConfiguration())),
  route("PUT", "/priority/configuration", write("admin"), (ctx) => {
    const body = requireBody(ctx);
    const reason = requireReason(body);
    const profile = body.profile;
    if (!profile || typeof profile !== "object") throw missingField("body", "profile");
    return ok(ctx.models.config.updateSection("priority_profile", profile, requireUser(ctx), reason));
  }),
  route("POST", "/priority/configuration/preview", write("planner"), (ctx) => {
    const body = requireBody(ctx);
    const profile = body.profile;
    if (!profile || typeof profile !== "object") throw missingField("body", "profile");
    const topN = optionalNumber(body, "top_n") ?? 50;
    if (!Number.isInteger(topN) || topN < 1 || topN > 500) throw invalidField("body", "top_n", "Input should be between 1 and 500");
    return ok(ctx.models.config.preview(profile as PriorityProfile, topN, ctx.models.orders));
  }),
  route("GET", "/priority/configuration/versions", read("planner"), (ctx) => ok(ctx.models.config.versionInfos(ctx.query.int("limit", 100, { min: 1 })))),
  route("GET", "/priority/configuration/versions/:version", read("planner"), (ctx) => ok(ctx.models.config.versionDetail(versionParam(ctx.params.version)))),
  route("POST", "/priority/configuration/versions/:version/activate", write("admin"), (ctx) => {
    const body = requireBody(ctx);
    return ok(ctx.models.config.activateVersion(versionParam(ctx.params.version), requireUser(ctx), requireReason(body)));
  }),

  // -------------------------------------------------- scheduling configuration
  route("GET", "/scheduling/configuration", read("planner"), (ctx) => ok(ctx.models.config.schedulingConfiguration())),
  route("PUT", "/scheduling/configuration", write("admin"), (ctx) => {
    const body = requireBody(ctx);
    const reason = requireReason(body);
    const user = requireUser(ctx);
    let result: ReturnType<ActiveConfig["updateSection"]> | null = null;
    const note = { demo_note: "Demo build: the new version is stored and audited, but the scheduler is not re-run (schedule versions are pre-computed presets)." };
    for (const section of ["scheduling", "replanning", "alerts", "data_quality"] as ConfigSection[]) {
      const value = body[section];
      if (value !== undefined && value !== null) {
        if (typeof value !== "object") throw invalidField("body", section, "Input should be an object");
        result = ctx.models.config.updateSection(section, value, user, reason, note);
      }
    }
    if (!result) throw validation("provide at least one of scheduling, replanning, alerts, data_quality");
    return ok(result);
  }),
  route("GET", "/scheduling/configuration/versions", read("planner"), (ctx) => ok(ctx.models.config.versionInfos(ctx.query.int("limit", 100, { min: 1 })))),
  route("GET", "/scheduling/configuration/versions/:version", read("planner"), (ctx) => ok(ctx.models.config.versionDetail(versionParam(ctx.params.version)))),
  route("POST", "/scheduling/configuration/versions/:version/activate", write("admin"), (ctx) => {
    const body = requireBody(ctx);
    return ok(ctx.models.config.activateVersion(versionParam(ctx.params.version), requireUser(ctx), requireReason(body)));
  }),

  // -------------------------------------------------------------- customers
  route("GET", "/customers", read("planner"), (ctx) => {
    const { page, page_size } = paging(ctx.query, 50);
    const search = ctx.query.str("search")?.trim().toLowerCase();
    let customers = ctx.store.data.customers.list;
    if (search) customers = customers.filter((c) => c.customer_name.toLowerCase().includes(search) || c.customer_id.toLowerCase().includes(search));
    const sorted = [...customers].sort((a, b) => a.customer_name.localeCompare(b.customer_name) || a.customer_id.localeCompare(b.customer_id));
    const start = (page - 1) * page_size;
    return ok({ items: sorted.slice(start, start + page_size).map((c) => ctx.models.config.customerResponse(c)), total: sorted.length, page, page_size, pages: Math.ceil(sorted.length / page_size), has_more: page * page_size < sorted.length });
  }),
  route("GET", "/customers/:customer_id", read("planner"), (ctx) => ok(ctx.models.config.customerResponse(ctx.models.config.customerBase(ctx.params.customer_id ?? "")))),
  route("GET", "/customers/:customer_id/rules", read("planner"), (ctx) => ok(ctx.models.config.customerRule(ctx.params.customer_id ?? ""))),
  route("PUT", "/customers/:customer_id/rules", write("production_manager"), (ctx) => {
    const body = requireBody(ctx);
    const reason = requireReason(body);
    const sla = optionalNumber(body, "sla_hours");
    if (sla !== null && sla <= 0) throw invalidField("body", "sla_hours", "Input should be greater than 0");
    const boost = optionalNumber(body, "priority_boost_points") ?? 0;
    if (boost < -100 || boost > 100) throw invalidField("body", "priority_boost_points", "Input should be between -100 and 100");
    const active = body.active === undefined ? true : Boolean(body.active);
    return ok(ctx.models.config.upsertRule(ctx.params.customer_id ?? "", requireUser(ctx), reason, { sla_hours: sla, tier_override: optionalEnum(body, "tier_override", TIERS), priority_boost_points: boost, notes: optionalString(body, "notes"), active }));
  }),
  route("DELETE", "/customers/:customer_id/rules", write("production_manager"), (ctx) => {
    const body = ctx.body === null || ctx.body === undefined ? {} : bodyObject(ctx.body);
    const reason = requireReason({ reason: body.reason ?? ctx.query.str("reason") });
    return ok(ctx.models.config.deleteRule(ctx.params.customer_id ?? "", requireUser(ctx), reason));
  }),

  // ----------------------------------------------------------------- alerts
  route("GET", "/alerts", read("supervisor"), (ctx) => {
    const q = ctx.query;
    const { page, page_size } = paging(q, 50);
    return ok(ctx.models.system.listAlerts({ severity: q.oneOf("severity", ALERT_SEVERITIES) as AlertSeverity | undefined, alert_type: q.oneOf("alert_type", ALERT_TYPES) as AlertType | undefined, order_id: q.str("order_id"), machine_id: q.str("machine_id"), acknowledged: q.bool("acknowledged") }, page, page_size));
  }),
  route("GET", "/alerts/summary", read("supervisor"), (ctx) => ok(ctx.models.system.alertSummary())),
  route("GET", "/alerts/:alert_id", read("supervisor"), (ctx) => ok(ctx.models.system.getAlert(ctx.params.alert_id ?? ""))),
  route("POST", "/alerts/:alert_id/acknowledge", write("supervisor"), (ctx) => {
    const body = ctx.body === null || ctx.body === undefined ? {} : bodyObject(ctx.body);
    const note = optionalString(body, "note");
    if (note !== null && note.length > 2000) throw invalidField("body", "note", "String should have at most 2000 characters");
    return ok(ctx.models.system.acknowledge(ctx.params.alert_id ?? "", requireUser(ctx), note));
  }),

  // ----------------------------------------------------------- data quality
  route("GET", "/data-quality", read("planner"), (ctx) => ok(ctx.models.system.dqSummary())),
  route("GET", "/data-quality/issues", read("planner"), (ctx) => {
    const q = ctx.query;
    const { page, page_size } = paging(q, 50);
    return ok(ctx.models.system.dqIssues({ severity: q.oneOf("severity", DQ_SEVERITIES) as DataQualitySeverity | undefined, code: q.oneOf("code", DQ_CODES) as DataQualityCode | undefined, entity_type: q.str("entity_type"), entity_id: q.str("entity_id") }, page, page_size));
  }),
  route("POST", "/data-quality/run", write("planner"), (ctx) => ok(ctx.models.system.runDataQuality(requireUser(ctx)))),

  // ------------------------------------------------------------------ audit
  route("GET", "/audit", write("production_manager"), (ctx) => {
    const q = ctx.query;
    const { page, page_size } = paging(q, 50);
    return ok(ctx.models.system.auditQuery({ user: q.str("user"), entity_type: q.str("entity_type"), entity_id: q.str("entity_id"), action: q.str("action"), from: q.date("from"), to: q.date("to") }, page, page_size));
  }),

  // ------------------------------------------------------------------ users
  route("GET", "/users", write("admin"), (ctx) => ok(ctx.models.system.users(ctx.query.bool("active_only") ?? false))),
  route("POST", "/users", write("admin"), (ctx) => {
    const body = requireBody(ctx);
    const username = requiredString(body, "username");
    if (username.length > 64) throw invalidField("body", "username", "String should have at most 64 characters");
    const password = requiredString(body, "password");
    if (password.length < 8 || password.length > 72) throw invalidField("body", "password", "String should have between 8 and 72 characters");
    const role = requireEnum(body, "role", ROLES);
    const displayName = requiredString(body, "display_name");
    return ok(ctx.models.system.createUser(requireUser(ctx), { username, password, role, display_name: displayName, email: optionalString(body, "email"), reason: optionalString(body, "reason") }), 201);
  }),
  route("GET", "/users/:user_id", write("admin"), (ctx) => ok(ctx.models.system.user(ctx.params.user_id ?? ""))),
  route("PATCH", "/users/:user_id", write("admin"), (ctx) => {
    const body = requireBody(ctx);
    const reason = requireReason(body);
    if (typeof body.active !== "boolean") throw missingField("body", "active");
    return ok(ctx.models.system.setUserActive(ctx.params.user_id ?? "", body.active, requireUser(ctx), reason));
  }),
  route("POST", "/users/:user_id/reset-password", write("admin"), (ctx) => {
    const body = requireBody(ctx);
    const reason = requireReason(body);
    const password = requiredString(body, "password");
    if (password.length < 8 || password.length > 72) throw invalidField("body", "password", "String should have between 8 and 72 characters");
    return ok(ctx.models.system.resetPassword(ctx.params.user_id ?? "", password, requireUser(ctx), reason));
  }),

  // ------------------------------------------------------------------- sync
  route("POST", "/sync/run", write("admin"), (ctx) => {
    const body = ctx.body === null || ctx.body === undefined ? {} : bodyObject(ctx.body);
    const mode = optionalEnum(body, "mode", ["full", "incremental"] as const) ?? "full";
    return ok(ctx.models.system.runSync(requireUser(ctx), mode as SyncMode, Boolean(body.prune_missing_orders)));
  }),
  route("GET", "/sync/runs", write("admin"), (ctx) => {
    const { page, page_size } = paging(ctx.query, 50);
    const runs = ctx.models.system.syncRuns().map((r) => ({ ...r, issues: [], issues_truncated: r.issues_truncated || r.issues.length > 0 }));
    const start = (page - 1) * page_size;
    return ok({ items: runs.slice(start, start + page_size), total: runs.length, page, page_size, pages: Math.ceil(runs.length / page_size), has_more: page * page_size < runs.length });
  }),
  route("GET", "/sync/capabilities", write("admin"), (ctx) => ok(ctx.models.system.syncCapabilities())),
  route("GET", "/sync/status", write("admin"), (ctx) => ok(ctx.models.system.syncStatus())),
  route("GET", "/sync/runs/:run_id", write("admin"), (ctx) => ok(ctx.models.system.syncRun(ctx.params.run_id ?? ""))),
];

function versionParam(raw: string | undefined): number {
  const n = Number(raw);
  if (!Number.isInteger(n)) throw invalidField("query", "version", "Input should be a valid integer");
  return n;
}

function versionBody(body: Record<string, unknown>): number {
  const raw = body.version;
  if (raw === undefined || raw === null) throw missingField("body", "version");
  if (typeof raw !== "number" || !Number.isInteger(raw) || raw < 1) throw invalidField("body", "version", "Input should be a valid integer greater than or equal to 1");
  return raw;
}

export function allRoutes(): Route[] {
  return routes;
}

/** First route (in declaration order) whose pattern matches; static segments beat `:params` because static routes are declared first. */
export function matchRoute(method: string, path: string): { route: Route; params: Record<string, string> } | null {
  const segments = path.split("/").filter(Boolean);
  let methodMismatch = false;
  for (const candidate of routes) {
    if (candidate.segments.length !== segments.length) continue;
    const params: Record<string, string> = {};
    let matched = true;
    for (let i = 0; i < segments.length; i += 1) {
      const expected = candidate.segments[i] ?? "";
      const actual = segments[i] ?? "";
      if (expected.startsWith(":")) params[expected.slice(1)] = decodeURIComponent(actual);
      else if (expected !== actual) {
        matched = false;
        break;
      }
    }
    if (!matched) continue;
    if (candidate.method !== method) {
      methodMismatch = true;
      continue;
    }
    return { route: candidate, params };
  }
  void methodMismatch;
  return null;
}
