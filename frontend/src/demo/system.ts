/**
 * Alerts, data quality, audit queries, user administration, health / metrics / ERP sync and
 * the what-if presets of the demo backend (AlertService, DataQualityService, AuditService,
 * UserService, health router, SyncAdminService and SimulationService ports).
 */
import type {
  Alert,
  AlertSeverity,
  AlertSummaryResponse,
  AlertType,
  AuditEntry,
  CapabilityReportResponse,
  DataQualityCode,
  DataQualityIssue,
  DataQualitySeverity,
  DataQualitySummary,
  HealthResponse,
  MetricsResponse,
  PageResponse,
  Role,
  ScenarioTypes,
  SimulateBody,
  SimulationResponse,
  SimulationScenario,
  SyncMode,
  SyncRunResponse,
  SyncStatusResponse,
  UserAccount,
  UserInfo,
} from "@/api/types";

import { conflict, notFound, validation } from "./errors";
import type { OrderModel } from "./orders";
import type { PlanModel } from "./plan";
import type { DemoStore } from "./store";
import { isCapturedError, type DemoSimulationPreset } from "./types";
import { clone, iso, paginate, sortBy } from "./util";

const ALERT_SEVERITIES: AlertSeverity[] = ["info", "warning", "high", "critical"];
export const SCENARIO_KINDS = ["machine_down", "urgent_orders", "add_machine", "extra_working_day", "extra_shift", "outsource", "material_delay", "material_arrival", "prioritize_customer", "weight_change", "due_date_change", "hold_orders", "expedite_orders"] as const;
const MAX_SCENARIOS = 20;
const MAX_TOP_N = 500;
const MIN_PASSWORD_LENGTH = 8;

export interface AlertFilters {
  severity?: AlertSeverity;
  alert_type?: AlertType;
  order_id?: string;
  machine_id?: string;
  acknowledged?: boolean;
}

export interface AuditFilters {
  user?: string;
  entity_type?: string;
  entity_id?: string;
  action?: string;
  from?: Date;
  to?: Date;
}

export interface DqFilters {
  severity?: DataQualitySeverity;
  code?: DataQualityCode;
  entity_type?: string;
  entity_id?: string;
}

function scenarioIds(s: Record<string, unknown>): Set<string> {
  const ids = new Set<string>();
  for (const key of ["machine_id", "clone_of_machine_id", "customer_id", "material_id", "order_id", "machine_group"]) {
    const v = s[key];
    if (typeof v === "string") ids.add(v);
  }
  if (Array.isArray(s.order_ids)) for (const v of s.order_ids) if (typeof v === "string") ids.add(v);
  if (Array.isArray(s.orders)) for (const o of s.orders) if (o && typeof o === "object" && typeof (o as { clone_of?: unknown }).clone_of === "string") ids.add((o as { clone_of: string }).clone_of);
  return ids;
}

export class SystemModel {
  constructor(
    private readonly store: DemoStore,
    private readonly plan: PlanModel,
    private readonly orders: OrderModel,
  ) {}

  // ------------------------------------------------------------------ alerts

  private alertOf(captured: Alert): Alert {
    const ack = this.store.state.acknowledged[captured.alert_id];
    return ack ? { ...captured, acknowledged: true, acknowledged_by: ack.by, acknowledged_at: ack.at } : captured;
  }

  alerts(): Alert[] {
    return this.store.data.alerts.list.map((a) => this.alertOf(a));
  }

  listAlerts(filters: AlertFilters, page: number, pageSize: number): PageResponse<Alert> {
    let rows = this.alerts().filter((a) => a.active);
    if (filters.severity) rows = rows.filter((a) => a.severity === filters.severity);
    if (filters.alert_type) rows = rows.filter((a) => a.alert_type === filters.alert_type);
    if (filters.order_id) rows = rows.filter((a) => a.order_id === filters.order_id);
    if (filters.machine_id) rows = rows.filter((a) => a.machine_id === filters.machine_id);
    if (filters.acknowledged === true) rows = rows.filter((a) => a.acknowledged);
    if (filters.acknowledged === false) rows = rows.filter((a) => !a.acknowledged);
    rows = sortBy(rows, (a) => [a.raised_at]).reverse();
    rows = sortBy(rows, (a) => [-new Date(a.raised_at).getTime(), a.alert_id]);
    return paginate(rows, page, pageSize);
  }

  alertSummary(): AlertSummaryResponse {
    const active = this.alerts().filter((a) => a.active);
    const bySeverity: Record<string, number> = {};
    for (const sev of ALERT_SEVERITIES) bySeverity[sev] = active.filter((a) => a.severity === sev).length;
    return { total_active: active.length, unacknowledged: active.filter((a) => !a.acknowledged).length, by_severity: bySeverity };
  }

  getAlert(alertId: string): Alert {
    const found = this.alerts().find((a) => a.alert_id === alertId);
    if (!found) throw notFound(`alert '${alertId}' not found`, { alert_id: alertId });
    return found;
  }

  acknowledge(alertId: string, user: UserInfo, note: string | null): Alert {
    const current = this.getAlert(alertId);
    if (!current.active) throw conflict(`alert '${alertId}' is resolved and cannot be acknowledged`);
    if (current.acknowledged) {
      throw conflict(`alert '${alertId}' was already acknowledged by ${current.acknowledged_by}`, { acknowledged_by: current.acknowledged_by, acknowledged_at: current.acknowledged_at });
    }
    const at = this.store.nowIso();
    this.store.state.acknowledged[alertId] = { by: user.user_id, at };
    this.store.audit(
      user,
      "alert",
      alertId,
      "alert.acknowledge",
      { acknowledged: false, acknowledged_by: null },
      { acknowledged: true, acknowledged_by: user.user_id, acknowledged_at: at },
      note ?? "acknowledged",
      { alert_type: current.alert_type, severity: current.severity, order_id: current.order_id, machine_id: current.machine_id },
    );
    this.store.commit();
    return this.getAlert(alertId);
  }

  // ------------------------------------------------------------- data quality

  private latestDqRun(): { run_id: string; detected_at: string } | null {
    const runs = this.store.state.dqRuns;
    return runs.length ? (runs[runs.length - 1] ?? null) : null;
  }

  dqSummary(): DataQualitySummary {
    const captured = this.store.data.data_quality.summary;
    const run = this.latestDqRun();
    if (!run) return captured;
    return { ...captured, run_id: run.run_id, detected_at: run.detected_at, dashboard: { ...captured.dashboard, as_of: run.detected_at }, engine_summary: {} };
  }

  dqIssues(filters: DqFilters, page: number, pageSize: number): PageResponse<DataQualityIssue> {
    const run = this.latestDqRun();
    let rows = this.store.data.data_quality.issues.map((i) => (run ? { ...i, run_id: run.run_id, detected_at: run.detected_at } : i));
    if (filters.severity) rows = rows.filter((i) => i.severity === filters.severity);
    if (filters.code) rows = rows.filter((i) => i.code === filters.code);
    if (filters.entity_type) rows = rows.filter((i) => i.entity_type === filters.entity_type);
    if (filters.entity_id) rows = rows.filter((i) => i.entity_id === filters.entity_id);
    return paginate(rows, page, pageSize);
  }

  runDataQuality(user: UserInfo): DataQualitySummary {
    const at = this.store.nowIso();
    const run = { run_id: this.store.id("dqr"), detected_at: at, by: user.user_id };
    this.store.state.dqRuns.push(run);
    const template = this.store.data.data_quality.run_response ?? this.store.data.data_quality.summary;
    const response: DataQualitySummary = { ...clone(template), run_id: run.run_id, detected_at: at, dashboard: { ...template.dashboard, as_of: at } };
    this.store.audit(
      user,
      "data_quality_run",
      run.run_id,
      "data_quality.run",
      null,
      { issues: response.total_issues, unschedulable_orders: response.dashboard.unschedulable_orders, headline: response.dashboard.headline },
      "manual data quality run",
      { run_id: run.run_id, open_orders: response.dashboard.open_orders },
    );
    this.store.commit();
    return response;
  }

  // ------------------------------------------------------------------- audit

  auditQuery(filters: AuditFilters, page: number, pageSize: number): PageResponse<AuditEntry> {
    let rows = this.store.allAudit();
    if (filters.user) rows = rows.filter((a) => a.user_id === filters.user);
    if (filters.entity_type) rows = rows.filter((a) => a.entity_type === filters.entity_type);
    if (filters.entity_id) rows = rows.filter((a) => a.entity_id === filters.entity_id);
    if (filters.action) rows = rows.filter((a) => a.action === filters.action);
    if (filters.from) rows = rows.filter((a) => new Date(a.timestamp) >= (filters.from as Date));
    if (filters.to) rows = rows.filter((a) => new Date(a.timestamp) < (filters.to as Date));
    return paginate(rows, page, pageSize);
  }

  // ------------------------------------------------------------------- users

  private publicUser(u: UserAccount): Record<string, unknown> {
    return { user_id: u.user_id, username: u.username, role: u.role, display_name: u.display_name, email: u.email, active: u.active };
  }

  users(activeOnly: boolean): UserAccount[] {
    return this.store.allUsers().filter((u) => !activeOnly || u.active);
  }

  user(userId: string): UserAccount {
    const found = this.store.userById(userId);
    if (!found) throw notFound(`user '${userId}' not found`, { user_id: userId });
    return found;
  }

  createUser(actor: UserInfo, body: { username: string; password: string; role: Role; display_name: string; email: string | null; reason: string | null }): UserAccount {
    if (body.password.length < MIN_PASSWORD_LENGTH) throw validation(`password must be at least ${MIN_PASSWORD_LENGTH} characters`, { field: "password" });
    if (this.store.userByUsername(body.username)) throw conflict(`username '${body.username}' already exists`, { username: body.username });
    const record: UserAccount = {
      user_id: this.store.id("usr"),
      username: body.username.trim().toLowerCase(),
      role: body.role,
      display_name: body.display_name,
      email: body.email,
      active: true,
      last_login_at: null,
      created_at: this.store.nowIso(),
    };
    this.store.state.users.push({ ...record, password: body.password });
    this.store.audit(actor, "user", record.user_id, "user.create", null, this.publicUser(record), body.reason ?? "user created by administrator", { username: record.username, role: record.role });
    this.store.commit();
    return record;
  }

  setUserActive(userId: string, active: boolean, actor: UserInfo, reason: string): UserAccount {
    const current = this.user(userId);
    if (!active && current.user_id === actor.user_id) throw conflict("you cannot deactivate your own account");
    if (current.active === active) throw conflict(`user '${userId}' is already ${active ? "active" : "inactive"}`, { active });
    this.store.state.userPatches[userId] = { ...this.store.state.userPatches[userId], active };
    const updated = this.user(userId);
    this.store.audit(actor, "user", userId, active ? "user.activate" : "user.deactivate", this.publicUser(current), this.publicUser(updated), reason, { username: current.username });
    this.store.commit();
    return updated;
  }

  resetPassword(userId: string, password: string, actor: UserInfo, reason: string): UserAccount {
    if (password.length < MIN_PASSWORD_LENGTH) throw validation(`password must be at least ${MIN_PASSWORD_LENGTH} characters`, { field: "password" });
    const current = this.user(userId);
    this.store.state.userPatches[userId] = { ...this.store.state.userPatches[userId], password };
    this.store.audit(actor, "user", userId, "user.reset_password", { password: "***" }, { password: "*** (reset)" }, reason, { username: current.username });
    this.store.commit();
    return current;
  }

  recordLogin(user: UserAccount): void {
    this.store.state.userPatches[user.user_id] = { ...this.store.state.userPatches[user.user_id], last_login_at: this.store.nowIso() };
    this.store.commit();
  }

  // --------------------------------------------------------- health / metrics

  private latestSyncRun(): SyncRunResponse | null {
    return this.syncRuns()[0] ?? null;
  }

  health(): HealthResponse {
    const captured = this.store.data.health;
    const current = this.plan.current();
    const last = this.latestSyncRun();
    return {
      ...captured,
      status: "ok",
      database: "ok",
      time: this.store.nowIso(),
      connector: captured.connector ? { ...captured.connector, reachable: true, checked_at: this.store.nowIso() } : null,
      last_sync: last ? { run_id: last.run_id, status: last.status, mode: last.mode, started_at: last.started_at, finished_at: last.finished_at } : captured.last_sync,
      active_plan: current ? { version_number: current.number, status: current.response.status, generated_at: current.response.generated_at, quality_score: current.response.quality_score } : null,
    };
  }

  metrics(): MetricsResponse {
    const captured = this.store.data.metrics;
    const current = this.plan.current();
    const state = this.store.state;
    return {
      ...captured,
      requests_total: captured.requests_total + this.store.requestCount,
      by_status: { ...captured.by_status, "2xx": (captured.by_status["2xx"] ?? 0) + this.store.requestCount },
      schedule_generations_total: captured.schedule_generations_total + state.generateCounter + state.replanCounter,
      replans_total: captured.replans_total + state.replanCounter,
      sync_runs_total: captured.sync_runs_total + state.syncRuns.length,
      schedule_versions_by_status: this.plan.statusCounts(),
      active_plan_version: current ? current.number : null,
      active_alerts_total: this.alertSummary().total_active,
    };
  }

  // -------------------------------------------------------------------- sync

  syncRuns(): SyncRunResponse[] {
    return sortBy([...this.store.state.syncRuns, ...this.store.data.sync.runs], (r) => [-new Date(r.started_at).getTime(), r.run_id]);
  }

  syncRun(runId: string): SyncRunResponse {
    const live = this.store.state.syncRuns.find((r) => r.run_id === runId);
    if (live) return live;
    const captured = this.store.data.sync.run_detail[runId];
    if (!captured) throw notFound(`sync run '${runId}' not found`, { run_id: runId });
    return captured;
  }

  syncStatus(): SyncStatusResponse {
    const captured = this.store.data.sync.status;
    const runs = this.syncRuns();
    const last = runs[0] ?? null;
    const completed = runs.find((r) => r.status === "completed") ?? null;
    const now = this.store.nowIso();
    return {
      ...captured,
      checked_at: now,
      health: captured.health ? { ...captured.health, checked_at: now } : null,
      last_run: last,
      last_completed: completed,
      watermark: completed ? completed.started_at : null,
      runs_total: runs.length,
    };
  }

  syncCapabilities(): CapabilityReportResponse {
    return this.store.data.sync.capabilities;
  }

  runSync(user: UserInfo, mode: SyncMode, prune: boolean): SyncRunResponse {
    const now = this.store.now();
    const templateId = this.store.data.sync.runs.find((r) => r.status === "completed")?.run_id;
    const template = templateId ? this.store.data.sync.run_detail[templateId] : undefined;
    const run: SyncRunResponse = {
      ...(template ? clone(template) : { run_id: "", mode, status: "completed", connector: "mock", started_at: iso(now), finished_at: null, since: null, duration_seconds: 0, records_fetched: {}, records_upserted: {}, issues_count: 0, issue_counts: {}, issues: [], issues_truncated: false, reconciliation: null, stored_totals: {}, pruned_orders: 0, orders_closed_missing: 0, watermark_source: "none", triggered_by: null, error_message: null }),
      run_id: this.store.id("sync"),
      mode,
      status: "completed",
      started_at: iso(now),
      finished_at: iso(new Date(now.getTime() + (template?.duration_seconds ?? 0.9) * 1000)),
      since: mode === "incremental" ? (this.syncRuns().find((r) => r.status === "completed")?.started_at ?? null) : null,
      watermark_source: mode === "incremental" ? "run" : "none",
      triggered_by: user.user_id,
      error_message: null,
    };
    this.store.state.syncRuns.push(run);
    this.store.audit(
      user,
      "sync_run",
      run.run_id,
      "sync.run",
      null,
      { status: run.status, mode: run.mode, records_fetched: run.records_fetched, records_upserted: run.records_upserted, issues: run.issues_count },
      `${mode} sync via ${run.connector}`,
      { run_id: run.run_id, connector: run.connector, prune },
    );
    this.store.commit();
    return run;
  }

  // -------------------------------------------------------------- simulation

  scenarioTypes(): ScenarioTypes {
    return this.store.data.simulations.scenario_types;
  }

  private presets(): DemoSimulationPreset[] {
    return (this.store.data.simulations.presets ?? []).filter((p) => !isCapturedError(p.response));
  }

  /** Matches the request to a captured preset of the same kind (preferring shared ids); other combinations are refused. */
  simulate(user: UserInfo, body: SimulateBody): SimulationResponse {
    const scenarios = Array.isArray(body.scenarios) ? body.scenarios : [];
    if (scenarios.length === 0) throw validation("at least one scenario is required", { field: "scenarios" });
    if (scenarios.length > MAX_SCENARIOS) throw validation(`at most ${MAX_SCENARIOS} scenarios per simulation`);
    const topN = body.top_n ?? 20;
    if (!Number.isInteger(topN) || topN < 1 || topN > MAX_TOP_N) throw validation(`top_n must be in 1..${MAX_TOP_N}`, { top_n: topN });
    for (const s of scenarios) {
      if (!s || typeof s !== "object" || !(SCENARIO_KINDS as readonly string[]).includes((s as { kind?: string }).kind ?? "")) {
        throw validation("invalid scenario", { errors: [{ loc: ["scenarios", "kind"], msg: `Input should be one of: ${SCENARIO_KINDS.join(", ")}` }], kind: (s as { kind?: string })?.kind ?? null });
      }
    }
    const available = Array.from(new Set(this.presets().map((p) => p.kind)));
    if (scenarios.length !== 1) {
      throw validation(`The demo build does not run the scheduling engine: what-if answers come from presets computed by the real engine, one scenario at a time (kinds available: ${available.join(", ")}).`, { scenarios: scenarios.length });
    }
    const scenario = scenarios[0] as SimulationScenario;
    const candidates = this.presets().filter((p) => p.kind === scenario.kind);
    if (candidates.length === 0) {
      throw validation(`The demo build computes what-if presets only; no preset exists for scenario kind '${scenario.kind}' (available: ${available.join(", ")}).`, { kind: scenario.kind });
    }
    const wanted = scenarioIds(scenario as unknown as Record<string, unknown>);
    const scored = candidates.map((p) => {
      const ids = scenarioIds((p.request.scenarios[0] ?? {}) as unknown as Record<string, unknown>);
      return { preset: p, shared: Array.from(wanted).filter((id) => ids.has(id)).length };
    });
    const best = sortBy(scored, (c) => [-c.shared])[0]?.preset;
    if (!best || isCapturedError(best.response)) throw validation(`no preset for scenario kind '${scenario.kind}'`, { kind: scenario.kind });
    const response = clone(best.response);
    const now = this.store.nowIso();
    const label = (scenario as { label?: string | null }).label ?? null;
    const result: SimulationResponse = {
      ...response,
      simulation_id: this.store.id("sim"),
      generated_at: now,
      note: body.note ?? null,
      baseline_version: this.plan.current()?.number ?? response.baseline_version,
      top_n: topN,
      affected_orders: response.affected_orders.slice(0, topN),
      scenarios: response.scenarios.map((s, i) => (i === 0 ? { ...s, label: label ?? s.label } : s)),
      summary: `Demo preset (kind ${scenario.kind}): ${response.summary}`,
    };
    this.store.audit(
      user,
      "simulation",
      result.simulation_id,
      "simulation.run",
      null,
      { scenarios: result.scenarios, orders_affected: result.diff.orders_affected, late_orders_before: result.diff.late_orders_before, late_orders_after: result.diff.late_orders_after, summary: result.summary },
      body.note ?? "what-if simulation",
      { simulation_id: result.simulation_id, scenario_kinds: [scenario.kind], baseline_version: result.baseline_version, demo_preset: best.label },
    );
    this.store.commit();
    void this.orders;
    return result;
  }
}
