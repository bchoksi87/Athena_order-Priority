/**
 * Order read models and the audited planner actions of the demo backend.
 *
 * Read side (OrderQueryService port): the captured order facts are joined with the *live*
 * priority result (captured engine run re-scored with the active profile, expedites,
 * overrides, holds and customer rules), the placement in the current schedule version and
 * the active overlays. Write side (OverrideService / ExpediteService port): every action
 * validates the order, stores an overlay, writes an audit row and re-ranks the queue.
 */
import type {
  CustomerRule,
  ExpediteResponse,
  ExplanationResponse,
  FactorScore,
  LockResponse,
  MachineOptions,
  OrderDetail,
  OrderListItemResponse,
  OrderSchedule,
  OrderStatus,
  OverrideResponse,
  OverrideType,
  PriorityAdjustment,
  PriorityInfo,
  ProcessType,
  ReadinessState,
  RiskLevel,
  ScheduleEntry,
  UserInfo,
} from "@/api/types";

import type { ActiveConfig } from "./config";
import { conflict, notFound, validation } from "./errors";
import type { PlanModel } from "./plan";
import { explanationLines, rankResults, renderExplanation, scoreOrder, type ScoredResult, type ScoringInput } from "./scoring";
import type { DemoStore } from "./store";
import { isCapturedError } from "./types";
import { addHours, addDays, clone, ensureFuture, fmtG, iso, sortBy } from "./util";

export const CLOSED_STATUSES: readonly OrderStatus[] = ["completed", "packed", "shipped", "cancelled"];
export const SORT_KEYS = ["priority", "rank", "due_date", "customer", "order_value", "margin", "risk", "status", "quantity", "order_id"] as const;
export type SortKey = (typeof SORT_KEYS)[number];
const RISK_ORDER: Record<RiskLevel, number> = { critical: 0, high: 1, medium: 2, low: 3 };
const MAX_DELTA_POINTS = 100;
const ENTITY_ORDER = "order";

export interface OrderListFilters {
  customer_id?: string;
  statuses: OrderStatus[];
  machine_group?: string;
  process_type?: ProcessType;
  machine_id?: string;
  due_from?: Date;
  due_to?: Date;
  risk?: RiskLevel;
  readiness?: ReadinessState;
  search?: string;
  open_only: boolean;
  on_hold?: boolean;
}

export interface EffectiveHold {
  on_hold: boolean;
  hold_reason: string | null;
  override: OverrideResponse | null;
}

export class OrderModel {
  private scoreRevision = -1;
  private scores = new Map<string, ScoredResult>();

  constructor(
    private readonly store: DemoStore,
    private readonly config: ActiveConfig,
    private readonly plan: PlanModel,
  ) {}

  // ------------------------------------------------------------------ facts

  ids(): string[] {
    return Object.keys(this.store.data.orders.detail);
  }

  base(orderId: string): OrderDetail {
    const detail = this.store.data.orders.detail[orderId];
    if (!detail) throw notFound(`order '${orderId}' not found`, { order_id: orderId });
    return detail;
  }

  has(orderId: string): boolean {
    return orderId in this.store.data.orders.detail;
  }

  /** Order.is_open (and the repository's open_only filter): not a closed status and something left to make. */
  isOpen(detail: OrderDetail): boolean {
    return !CLOSED_STATUSES.includes(detail.order.order_status) && detail.order.pending_quantity > 0;
  }

  /** ERP hold always wins; otherwise the latest active planner decision (snapshot_service.effective_hold). */
  effectiveHold(orderId: string, now: Date = this.store.now()): EffectiveHold {
    const detail = this.base(orderId);
    if (detail.order.on_hold) return { on_hold: true, hold_reason: detail.order.hold_reason, override: null };
    const decisions = this.store.overridesForOrder(orderId, true).filter((o) => o.override_type === "hold_order" || o.override_type === "release_hold");
    const latest = sortBy(decisions, (o) => [o.created_at, o.override_id]).pop() ?? null;
    if (latest && latest.override_type === "hold_order") return { on_hold: true, hold_reason: `held by ${latest.created_by}: ${latest.reason}`, override: latest };
    void now;
    return { on_hold: false, hold_reason: null, override: null };
  }

  // --------------------------------------------------------------- scoring

  private scoringInput(detail: OrderDetail, now: Date): ScoringInput | null {
    const p = detail.priority;
    if (!p) return null;
    const orderId = detail.order.order_id;
    const hold = this.effectiveHold(orderId, now);
    const expedites = this.store.expeditesForOrder(orderId, true);
    const strongest = sortBy(expedites, (e) => [e.boost_points, e.created_at]).pop() ?? null;
    const overrides = this.store.overridesForOrder(orderId, true).filter((o) => ["increase_priority", "decrease_priority", "set_priority", "force_next"].includes(o.override_type));
    return {
      order_id: orderId,
      customer_id: detail.order.customer_id,
      erp_priority: detail.order.erp_priority,
      due_date: detail.order.due_date,
      factors: detail.factors,
      capturedAdjustments: detail.adjustments,
      readiness: p.readiness,
      blocked: p.blocked,
      blocking_reasons: p.blocking_reasons,
      risk_level: p.risk_level,
      hours_until_due: p.hours_until_due,
      projected_completion: p.projected_completion,
      projected_lateness_hours: p.projected_lateness_hours,
      computed_at: this.store.state.scoredAt[orderId] ?? this.store.state.profileScoredAt ?? p.computed_at,
      hold: hold.override ? { by: hold.override.created_by, reason: hold.override.reason } : null,
      expedite: strongest,
      overrides,
      rule: this.store.customerRule(detail.order.customer_id),
    };
  }

  /** Scoring input of one order (captured facts + live overlays); null when the engine never scored it. */
  scoringInputFor(orderId: string, now: Date = this.store.now()): ScoringInput | null {
    return this.scoringInput(this.base(orderId), now);
  }

  /** Live priority results of every scored order, ranked; recomputed after each mutation. */
  results(): Map<string, ScoredResult> {
    if (this.scoreRevision === this.store.revision) return this.scores;
    const now = this.store.now();
    const profile = this.config.profile();
    const results: ScoredResult[] = [];
    for (const detail of Object.values(this.store.data.orders.detail)) {
      const input = this.scoringInput(detail, now);
      if (input) results.push(scoreOrder(input, profile, now));
    }
    rankResults(results, profile.fairness);
    for (const result of results) result.explanation = renderExplanation(result);
    this.scores = new Map(results.map((r) => [r.order_id, r]));
    this.scoreRevision = this.store.revision;
    return this.scores;
  }

  scored(orderId: string): ScoredResult | null {
    return this.results().get(orderId) ?? null;
  }

  priorityInfo(orderId: string): PriorityInfo | null {
    const r = this.scored(orderId);
    if (!r) return null;
    return {
      score: r.score,
      base_score: r.base_score,
      rank: r.rank,
      risk_level: r.risk_level,
      readiness: r.readiness,
      blocked: r.blocked,
      blocking_reasons: r.blocking_reasons,
      forced_next: r.forced_next,
      hours_until_due: r.hours_until_due,
      projected_completion: r.projected_completion,
      projected_lateness_hours: r.projected_lateness_hours,
      explanation: r.explanation,
      profile_id: r.profile_id,
      profile_version: r.profile_version,
      computed_at: r.computed_at,
    };
  }

  // ------------------------------------------------------------- placement

  /** Placement of the order in the current plan (order_query_service._schedule_info). */
  scheduleInfo(orderId: string, withEntries: boolean): OrderSchedule | null {
    const current = this.plan.current();
    if (!current) return null;
    const entries = this.plan.entries(current.number).filter((e) => e.order_id === orderId);
    if (entries.length === 0) return null;
    const ordered = sortBy(entries, (e) => [e.start, e.sequence_on_machine]);
    const last = ordered.reduce((a, b) => (b.end > a.end ? b : a));
    let lateness = ordered.find((e) => e.is_last_operation)?.expected_lateness_hours ?? null;
    const completion = ordered.find((e) => e.expected_completion)?.expected_completion ?? last.end;
    if (lateness === null && last.due_date !== null) lateness = (new Date(completion).getTime() - new Date(last.due_date).getTime()) / 3_600_000;
    const first = ordered[0] as ScheduleEntry;
    return {
      version_number: current.number,
      status: current.response.status,
      machine_id: first.machine_id,
      start: first.start,
      end: last.end,
      expected_completion: completion,
      expected_lateness_hours: lateness,
      entries: withEntries ? ordered : [],
    };
  }

  // ------------------------------------------------------------------ list

  listRows(filters: OrderListFilters, sort: SortKey, descending: boolean | null): OrderListItemResponse[] {
    const rows: Array<{ row: OrderListItemResponse; detail: OrderDetail }> = [];
    const search = filters.search?.trim().toLowerCase();
    for (const detail of Object.values(this.store.data.orders.detail)) {
      const o = detail.order;
      if (filters.statuses.length > 0 && !filters.statuses.includes(o.order_status)) continue;
      if (filters.customer_id && o.customer_id !== filters.customer_id) continue;
      if (filters.machine_group && o.machine_group !== filters.machine_group) continue;
      if (filters.process_type && o.process_type !== filters.process_type) continue;
      if (filters.due_from && (!o.due_date || new Date(o.due_date) < filters.due_from)) continue;
      if (filters.due_to && (!o.due_date || new Date(o.due_date) >= filters.due_to)) continue;
      if (filters.open_only && !this.isOpen(detail)) continue;
      if (search) {
        const haystack = [o.order_id, o.external_order_ref, o.part_id, o.part_name, o.customer_id].map((v) => (v ?? "").toLowerCase());
        if (!haystack.some((v) => v.includes(search))) continue;
      }
      const hold = this.effectiveHold(o.order_id);
      const priority = this.priorityInfo(o.order_id);
      const schedule = this.scheduleInfo(o.order_id, false);
      if (filters.risk !== undefined && (!priority || priority.risk_level !== filters.risk)) continue;
      if (filters.readiness !== undefined && (!priority || priority.readiness !== filters.readiness)) continue;
      if (filters.on_hold !== undefined && hold.on_hold !== filters.on_hold) continue;
      if (filters.machine_id !== undefined && ![schedule?.machine_id ?? null, o.required_machine_id].includes(filters.machine_id)) continue;
      rows.push({ row: { order: { ...o, on_hold: hold.on_hold, hold_reason: hold.hold_reason }, priority, schedule }, detail });
    }
    const reverse = descending ?? ["priority", "order_value", "margin"].includes(sort);
    const farFuture = 8.64e15;
    const sorted = sortBy(
      rows,
      ({ row, detail }) => {
        const order = row.order;
        const result = row.priority;
        const due = order.due_date ? new Date(order.due_date).getTime() : farFuture;
        switch (sort) {
          case "priority":
            return [result ? result.score : -1, -due, order.order_id];
          case "rank":
            return [result && result.rank !== null ? result.rank : 1e9, due, order.order_id];
          case "customer":
            return [detail.customer ? detail.customer.customer_name : order.customer_id, due, order.order_id];
          case "order_value":
            return [order.order_value ?? 0, due, order.order_id];
          case "margin":
            return [order.estimated_margin ?? -1, due, order.order_id];
          case "risk":
            return [result ? RISK_ORDER[result.risk_level] : 9, due, order.order_id];
          case "status":
            return [order.order_status, due, order.order_id];
          case "quantity":
            return [order.pending_quantity, due, order.order_id];
          case "order_id":
            return [order.order_id];
          default:
            return [due, order.order_id];
        }
      },
      reverse,
    );
    return sorted.map((r) => r.row);
  }

  // ---------------------------------------------------------------- detail

  detail(orderId: string): OrderDetail {
    const base = this.base(orderId);
    const hold = this.effectiveHold(orderId);
    const result = this.scored(orderId);
    const locks = this.store.locksForOrder(orderId, true);
    const lockIds = new Set(this.store.locksForOrder(orderId, false).map((l) => l.lock_id));
    const liveAudit = this.store.state.audit.filter((a) => (a.entity_type === ENTITY_ORDER && a.entity_id === orderId) || (a.entity_type === "schedule_lock" && lockIds.has(a.entity_id)));
    const audit = sortBy([...liveAudit, ...base.audit], (a) => [a.timestamp, a.audit_id], true).slice(0, 100);
    return {
      ...clone(base),
      order: { ...base.order, on_hold: hold.on_hold, hold_reason: hold.hold_reason },
      priority: this.priorityInfo(orderId),
      breakdown: result ? explanationLines(result) : [],
      factors: result ? result.factors : [],
      adjustments: result ? result.adjustments : [],
      schedule: this.scheduleInfo(orderId, true),
      overrides: this.store.overridesForOrder(orderId, true),
      expedites: this.store.expeditesForOrder(orderId, true),
      locks,
      audit,
    };
  }

  explanation(orderId: string): ExplanationResponse {
    this.base(orderId);
    const r = this.scored(orderId);
    if (!r) throw notFound(`no priority result stored for order '${orderId}'; run the priority evaluation first`, { order_id: orderId });
    return {
      order_id: orderId,
      score: r.score,
      rank: r.rank,
      risk_level: r.risk_level,
      readiness: r.readiness,
      blocked: r.blocked,
      blocking_reasons: r.blocking_reasons,
      forced_next: r.forced_next,
      explanation: r.explanation,
      lines: explanationLines(r),
      profile_id: r.profile_id,
      profile_version: r.profile_version,
      computed_at: r.computed_at,
    };
  }

  /** Latest active override that pins a machine (constraints.hard.pinned_machine_for_order). */
  pinnedMachine(orderId: string): { machine_id: string; by: string } | null {
    const pins = this.store.overridesForOrder(orderId, true).filter((o) => o.target_machine_id !== null);
    const latest = sortBy(pins, (o) => [o.created_at, o.override_id]).pop();
    return latest && latest.target_machine_id ? { machine_id: latest.target_machine_id, by: latest.created_by } : null;
  }

  machineOptions(orderId: string): MachineOptions {
    const detail = this.base(orderId);
    const captured = this.store.data.orders.machines[orderId];
    if (!captured || isCapturedError(captured)) {
      if (!this.isOpen(detail)) throw conflict(`order '${orderId}' is not open (${detail.order.order_status}); no machine options`, { order_status: detail.order.order_status });
      throw notFound(`no operation to evaluate for order '${orderId}'`, { order_id: orderId });
    }
    const pin = this.pinnedMachine(orderId);
    return {
      ...clone(captured),
      evaluated_at: this.store.nowIso(),
      scheduled_machine_id: this.scheduleInfo(orderId, false)?.machine_id ?? null,
      pinned_machine_id: pin?.machine_id ?? null,
      pinned_by: pin?.by ?? null,
    };
  }

  // ------------------------------------------------------------ mutations

  private prepare(orderId: string, reason: string, expiresAt: Date | null): { detail: OrderDetail; now: Date; expires: Date | null } {
    const detail = this.base(orderId);
    const now = this.store.now();
    if (!this.isOpen(detail)) {
      throw conflict(`order '${orderId}' is closed (${detail.order.order_status}); overrides apply to open orders`, {
        order_status: detail.order.order_status,
        pending_quantity: detail.order.pending_quantity,
      });
    }
    void reason;
    return { detail, now, expires: ensureFuture(expiresAt, now, "expires_at") };
  }

  private addOverride(orderId: string, kind: OverrideType, user: UserInfo, reason: string, now: Date, extra: { value?: number | null; target_machine_id?: string | null; expires_at?: Date | null } = {}): OverrideResponse {
    const override: OverrideResponse = {
      override_id: this.store.id("ovr"),
      order_id: orderId,
      override_type: kind,
      value: extra.value ?? null,
      target_machine_id: extra.target_machine_id ?? null,
      reason,
      created_by: user.user_id,
      created_at: iso(now),
      expires_at: extra.expires_at ? iso(extra.expires_at) : null,
      active: true,
    };
    this.store.state.overrides.push(override);
    return override;
  }

  private deactivate(orderId: string, kind: OverrideType): string[] {
    const ids: string[] = [];
    for (const o of this.store.overridesForOrder(orderId, true)) {
      if (o.override_type !== kind) continue;
      this.store.mutableOverride(o.override_id).active = false;
      ids.push(o.override_id);
    }
    return ids;
  }

  private priorityState(orderId: string, projected?: number | null): Record<string, unknown> {
    const result = this.scored(orderId);
    const state: Record<string, unknown> = {
      stored_score: result ? result.score : null,
      stored_rank: result ? result.rank : null,
      active_override_ids: this.store.overridesForOrder(orderId, true).map((o) => o.override_id),
    };
    if (projected !== undefined && projected !== null) state.projected_score = projected;
    return state;
  }

  private placementState(orderId: string): Record<string, unknown> {
    const schedule = this.scheduleInfo(orderId, false);
    const pins = this.store.overridesForOrder(orderId, true).filter((o) => o.target_machine_id !== null);
    return {
      scheduled_machine_id: schedule?.machine_id ?? null,
      scheduled_start: schedule?.start ?? null,
      pinned_machine_id: pins.length ? pins[pins.length - 1]?.target_machine_id : null,
      active_override_ids: pins.map((o) => o.override_id),
    };
  }

  private touch(orderId: string): void {
    this.store.state.scoredAt[orderId] = this.store.nowIso();
  }

  private auditOrder(user: UserInfo, orderId: string, action: string, previous: unknown, next: unknown, reason: string, details: Record<string, unknown>): void {
    this.store.audit(user, ENTITY_ORDER, orderId, action, previous, next, reason, details);
  }

  expedite(orderId: string, user: UserInfo, reason: string, opts: { boost_points: number | null; duration_hours: number | null; starts_at: Date | null; expires_at: Date | null }): ExpediteResponse {
    const now = this.store.now();
    const detail = this.base(orderId);
    if (!this.isOpen(detail)) throw conflict(`order '${orderId}' is closed (${detail.order.order_status}) and cannot be expedited`, { order_status: detail.order.order_status });
    const rules = this.config.profile().expedite;
    const boost = opts.boost_points ?? rules.default_boost_points;
    if (boost <= 0 || boost > rules.max_boost_points) throw validation(`boost_points must be in (0, ${fmtG(rules.max_boost_points)}]`, { boost_points: boost, max_boost_points: rules.max_boost_points });
    const start = opts.starts_at ?? now;
    if (start < now) throw validation("starts_at must not be in the past", { starts_at: iso(start) });
    let end: Date;
    if (opts.expires_at) {
      if (opts.duration_hours !== null) throw validation("give either duration_hours or expires_at, not both");
      end = opts.expires_at;
    } else {
      const hours = opts.duration_hours ?? rules.default_duration_hours;
      if (hours <= 0) throw validation("duration_hours must be positive", { duration_hours: hours });
      end = addHours(start, hours);
    }
    const duration = (end.getTime() - start.getTime()) / 3_600_000;
    if (duration <= 0) throw validation("expires_at must be after starts_at", { starts_at: iso(start), expires_at: iso(end) });
    if (duration > rules.max_duration_hours) throw validation(`expedite duration ${fmtG(duration)} h exceeds the maximum of ${fmtG(rules.max_duration_hours)} h`, { duration_hours: duration, max_duration_hours: rules.max_duration_hours });
    const previous = this.store.expeditesForOrder(orderId, true);
    for (const old of previous) this.store.mutableExpedite(old.expedite_id).active = false;
    const expedite: ExpediteResponse = {
      expedite_id: this.store.id("exp"),
      order_id: orderId,
      boost_points: boost,
      starts_at: iso(start),
      expires_at: iso(end),
      reason,
      created_by: user.user_id,
      created_at: iso(now),
      active: true,
    };
    this.store.state.expedites.push(expedite);
    this.auditOrder(user, orderId, "expedite.create", { active_expedites: previous }, { expedite, boost_points: boost, duration_hours: duration }, reason, {
      expedite_id: expedite.expedite_id,
      superseded_expedite_ids: previous.map((e) => e.expedite_id),
    });
    this.touch(orderId);
    this.store.commit();
    return expedite;
  }

  cancelExpedite(expediteId: string, user: UserInfo, reason: string): ExpediteResponse {
    const current = this.store.findExpedite(expediteId);
    if (!current) throw notFound(`expedite '${expediteId}' not found`, { expedite_id: expediteId });
    if (!current.active) throw conflict(`expedite '${expediteId}' is already cancelled`);
    const before = clone(current);
    const live = this.store.mutableExpedite(expediteId);
    live.active = false;
    this.auditOrder(user, current.order_id, "expedite.cancel", before, live, reason, { expedite_id: expediteId });
    this.touch(current.order_id);
    this.store.commit();
    return live;
  }

  hold(orderId: string, user: UserInfo, reason: string, expiresAt: Date | null): OverrideResponse {
    const { now, expires } = this.prepare(orderId, reason, expiresAt);
    const current = this.effectiveHold(orderId, now);
    if (current.on_hold) throw conflict(`order '${orderId}' is already on hold`, { hold_reason: current.hold_reason, override_id: current.override?.override_id ?? null });
    const released = this.deactivate(orderId, "release_hold");
    const override = this.addOverride(orderId, "hold_order", user, reason, now, { expires_at: expires });
    this.auditOrder(user, orderId, "override.hold", { on_hold: false, hold_reason: null }, { on_hold: true, hold_reason: reason, override }, reason, { override_id: override.override_id, superseded_override_ids: released });
    this.touch(orderId);
    this.store.commit();
    return override;
  }

  release(orderId: string, user: UserInfo, reason: string): OverrideResponse {
    const { now } = this.prepare(orderId, reason, null);
    const current = this.effectiveHold(orderId, now);
    if (!current.on_hold) throw conflict(`order '${orderId}' is not on hold`);
    if (!current.override) throw conflict(`order '${orderId}' is held in the ERP and must be released there`, { hold_reason: current.hold_reason });
    const released = this.deactivate(orderId, "hold_order");
    const override = this.addOverride(orderId, "release_hold", user, reason, now);
    this.auditOrder(user, orderId, "override.release", { on_hold: true, hold_reason: current.hold_reason, hold_override_id: current.override.override_id }, { on_hold: false, hold_reason: null, override }, reason, { override_id: override.override_id, released_override_ids: released });
    this.touch(orderId);
    this.store.commit();
    return override;
  }

  overridePriority(orderId: string, user: UserInfo, reason: string, type: "increase" | "decrease" | "set", value: number, expiresAt: Date | null): OverrideResponse {
    if (type === "set") {
      if (value < 0 || value > 100) throw validation("score must be in 0..100", { score: value });
      const { now, expires } = this.prepare(orderId, reason, expiresAt);
      const previous = this.priorityState(orderId);
      const superseded = this.deactivate(orderId, "set_priority");
      const override = this.addOverride(orderId, "set_priority", user, reason, now, { value, expires_at: expires });
      this.auditOrder(user, orderId, "override.set_priority", previous, { ...this.priorityState(orderId, value), override }, reason, { override_id: override.override_id, superseded_override_ids: superseded });
      this.touch(orderId);
      this.store.commit();
      return override;
    }
    if (value <= 0 || value > MAX_DELTA_POINTS) throw validation(`points must be in (0, ${MAX_DELTA_POINTS}]`, { points: value });
    const { now, expires } = this.prepare(orderId, reason, expiresAt);
    const previous = this.priorityState(orderId);
    const kind: OverrideType = type === "increase" ? "increase_priority" : "decrease_priority";
    const override = this.addOverride(orderId, kind, user, reason, now, { value, expires_at: expires });
    const signed = type === "increase" ? value : -value;
    const stored = previous.stored_score as number | null;
    const projected = stored === null ? null : Math.max(0, Math.min(100, stored + signed));
    this.auditOrder(user, orderId, signed > 0 ? "override.increase_priority" : "override.decrease_priority", previous, { ...this.priorityState(orderId, projected), delta_points: signed, override }, reason, { override_id: override.override_id });
    this.touch(orderId);
    this.store.commit();
    return override;
  }

  forceNext(orderId: string, user: UserInfo, reason: string, expiresAt: Date | null): OverrideResponse {
    const { now, expires } = this.prepare(orderId, reason, expiresAt);
    const previous = this.priorityState(orderId);
    const superseded = this.deactivate(orderId, "force_next");
    const override = this.addOverride(orderId, "force_next", user, reason, now, { expires_at: expires });
    this.auditOrder(user, orderId, "override.force_next", previous, { ...this.priorityState(orderId, 100), forced_next: true, override }, reason, { override_id: override.override_id, superseded_override_ids: superseded });
    this.touch(orderId);
    this.store.commit();
    return override;
  }

  /** Reject a pin the hard constraints would refuse (captured eligibility); returns the estimated minutes. */
  private checkEligible(orderId: string, machineId: string): number {
    const machine = this.store.data.machines.list.find((m) => m.machine.machine_id === machineId);
    if (!machine) throw notFound(`machine '${machineId}' not found`, { machine_id: machineId });
    const options = this.store.data.orders.machines[orderId];
    if (!options || isCapturedError(options)) throw conflict(`order '${orderId}' is not part of the planning snapshot`);
    const eligible = options.eligible.find((m) => m.machine_id === machineId);
    if (!eligible) {
      const violations = options.rejected[machineId] ?? [`${machineId} is not a candidate for the operation`];
      throw validation(`machine '${machineId}' cannot run operation '${options.operation_id}'`, { operation_id: options.operation_id, violations });
    }
    const setup = eligible.setup_minutes ?? this.config.scheduling().setup.default_setup_minutes;
    const run = eligible.run_minutes ?? this.config.scheduling().lock_window_minutes;
    return setup + run;
  }

  private releaseMoveLocks(orderId: string, createdBy?: string): string[] {
    const moves = this.store.overridesForOrder(orderId, false).filter((o) => o.override_type === "move_order" && (createdBy === undefined || o.created_by === createdBy));
    const keys = new Set(moves.map((m) => `${m.created_by}|${m.created_at}|${m.target_machine_id}`));
    const released: string[] = [];
    for (const lock of this.store.locksForOrder(orderId, true)) {
      if (lock.lock_type !== "order" && lock.lock_type !== "time_slot") continue;
      if (keys.has(`${lock.created_by}|${lock.created_at}|${lock.machine_id}`)) {
        this.store.mutableLock(lock.lock_id).active = false;
        released.push(lock.lock_id);
      }
    }
    return released;
  }

  move(orderId: string, user: UserInfo, reason: string, targetMachineId: string, startAt: Date | null, expiresAt: Date | null): { override: OverrideResponse; lock: LockResponse } {
    const { now, expires } = this.prepare(orderId, reason, expiresAt);
    const start = ensureFuture(startAt, now, "start_at");
    if (start && expires && start >= expires) throw validation("start_at must be before expires_at");
    const minutes = this.checkEligible(orderId, targetMachineId);
    const previous = this.placementState(orderId);
    const superseded = [...this.deactivate(orderId, "move_order"), ...this.deactivate(orderId, "lock_machine_assignment")];
    const released = this.releaseMoveLocks(orderId);
    const override = this.addOverride(orderId, "move_order", user, reason, now, { target_machine_id: targetMachineId, expires_at: expires });
    const lock: LockResponse = {
      lock_id: this.store.id("lock"),
      lock_type: start ? "time_slot" : "order",
      order_id: orderId,
      machine_id: targetMachineId,
      window: start
        ? { start: iso(start), end: iso(new Date(start.getTime() + Math.max(minutes, 1) * 60_000)), reason: `moved by ${user.user_id}` }
        : expires
          ? { start: iso(now), end: iso(expires), reason: "move" }
          : null,
      sequence_order_ids: [],
      reason,
      created_by: user.user_id,
      created_at: iso(now),
      active: true,
    };
    this.store.state.locks.push(lock);
    this.auditOrder(user, orderId, "override.move", previous, { target_machine_id: targetMachineId, start_at: start ? iso(start) : null, estimated_minutes: minutes, override, lock }, reason, {
      override_id: override.override_id,
      lock_id: lock.lock_id,
      superseded_override_ids: superseded,
      released_lock_ids: released,
    });
    this.touch(orderId);
    this.store.commit();
    return { override, lock };
  }

  lockMachine(orderId: string, user: UserInfo, reason: string, machineId: string, expiresAt: Date | null): OverrideResponse {
    const { now, expires } = this.prepare(orderId, reason, expiresAt);
    this.checkEligible(orderId, machineId);
    const previous = this.placementState(orderId);
    const superseded = this.deactivate(orderId, "lock_machine_assignment");
    const override = this.addOverride(orderId, "lock_machine_assignment", user, reason, now, { target_machine_id: machineId, expires_at: expires });
    this.auditOrder(user, orderId, "override.lock_machine_assignment", previous, { pinned_machine_id: machineId, override }, reason, { override_id: override.override_id, superseded_override_ids: superseded });
    this.touch(orderId);
    this.store.commit();
    return override;
  }

  cancelOverride(overrideId: string, user: UserInfo, reason: string): OverrideResponse {
    const current = this.store.findOverride(overrideId);
    if (!current) throw notFound(`override '${overrideId}' not found`, { override_id: overrideId });
    if (!current.active) throw conflict(`override '${overrideId}' is already cancelled`);
    const before = clone(current);
    const live = this.store.mutableOverride(overrideId);
    live.active = false;
    const released = current.override_type === "move_order" ? this.releaseMoveLocks(current.order_id, current.created_by) : [];
    this.auditOrder(user, current.order_id, "override.cancel", before, live, reason, { override_id: overrideId, override_type: current.override_type, released_lock_ids: released });
    this.touch(current.order_id);
    this.store.commit();
    return live;
  }

  /** Every factor score of the scored orders (used by the preview / configuration tests). */
  factorsOf(orderId: string): FactorScore[] {
    return this.base(orderId).factors;
  }

  adjustmentsOf(orderId: string): PriorityAdjustment[] {
    return this.base(orderId).adjustments;
  }

  customerRule(customerId: string): CustomerRule | null {
    return this.store.customerRule(customerId);
  }
}

/** Convenience for the simulation preset matcher and tests: ids of the top-N ranked orders. */
export function topRanked(model: OrderModel, n: number): string[] {
  return Array.from(model.results().values())
    .filter((r) => r.rank !== null)
    .sort((a, b) => (a.rank ?? 0) - (b.rank ?? 0))
    .slice(0, n)
    .map((r) => r.order_id);
}

export function withinDays(value: string | null, now: Date, days: number): boolean {
  if (!value) return false;
  const d = new Date(value);
  return d >= now && d <= addDays(now, days);
}
