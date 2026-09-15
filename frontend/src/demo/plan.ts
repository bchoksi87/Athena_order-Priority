/**
 * Schedule versions, the approval workflow and every schedule view of the demo backend
 * (ScheduleService / ScheduleViewService / MachineQueryService / AnalyticsService ports).
 *
 * Captured versions keep their captured entries; versions generated in the demo clone a
 * captured draft (new number, run id and timestamps). Gantt boards, day views, machine
 * schedules and placements are derived from the version's entries exactly as the services
 * derive them from the database rows.
 */
import type {
  BottleneckReport,
  CapacityReport,
  DaySchedule,
  GanttBlock,
  GanttView,
  KpiReport,
  LockResponse,
  LockType,
  MachineDetail,
  MachineListItemResponse,
  MachineRow,
  MachineScheduleResponse,
  MachineStatus,
  MachineSummary,
  MetricPair,
  OtdReport,
  ProcessType,
  PublishResponse,
  ReplanOutcome,
  ReplanTriggerType,
  ScheduleComparison,
  ScheduleEntry,
  ScheduleGenerateResponse,
  ScheduleQualityReport,
  ScheduleStatus,
  ScheduleVersionDetail,
  ScheduleVersionResponse,
  TimeWindow,
  UserInfo,
} from "@/api/types";
import { RISK_RANK } from "@/lib/constants";

import type { ActiveConfig } from "./config";
import { conflict, notFound, validation } from "./errors";
import type { ClonedVersion, DemoStore } from "./store";
import { isCapturedError, type DemoRunDetails } from "./types";
import { addDays, addHours, clone, iso, overlaps, sortBy } from "./util";

const DETAIL_ONLY_KEYS = ["quality", "unscheduled", "warnings", "analytics", "writeback_receipt", "details"] as const;
const ENTITY_SCHEDULE_VERSION = "schedule_version";
const ENTITY_LOCK = "schedule_lock";
const DEFAULT_GANTT_DAYS = 7;
const MAX_GANTT_DAYS = 62;
const MAX_SCHEDULE_WINDOW_DAYS = 62;
const DEFAULT_SCHEDULE_WINDOW_HOURS = 24;
export const CAPACITY_DIMENSIONS = ["machine", "machine_group", "process", "department"] as const;
export const CAPACITY_PERIODS = ["day", "week"] as const;
export const MAX_HORIZON_DAYS = 120;
export const MAX_OTD_WINDOW_DAYS = 365;
export const DEFAULT_OTD_WINDOW_DAYS = 30;
const METRIC_SUMMARY_ORDER = ["on_time_pct", "avg_lateness_hours", "overall_utilization_pct", "total_setup_hours", "late_orders", "orders_at_risk", "revenue_at_risk", "scheduled_orders"];

export interface VersionRecord {
  number: number;
  template: number;
  detail: ScheduleVersionDetail;
  response: ScheduleVersionResponse;
}

export interface EntryFilters {
  machine_ids?: string[];
  start?: Date;
  end?: Date;
  order_id?: string;
  customer_id?: string;
}

function toResponse(detail: ScheduleVersionDetail): ScheduleVersionResponse {
  const copy = { ...detail } as Record<string, unknown>;
  for (const key of DETAIL_ONLY_KEYS) delete copy[key];
  return copy as unknown as ScheduleVersionResponse;
}

function fmtMetric(key: string, value: number | null): string {
  if (value === null) return "—";
  switch (key) {
    case "on_time_pct":
    case "overall_utilization_pct":
      return `${Math.round(value)}%`;
    case "avg_lateness_hours":
      return `${value.toFixed(1)}h`;
    case "revenue_at_risk":
      return Math.round(value).toLocaleString("en-US");
    default:
      return String(Math.round(value));
  }
}

/** "Schedule quality: 65 → 65; On-time delivery: 71% → 71%; …; 195 entries change (195 moved, 0 added, 0 removed)". */
export function comparisonSummary(metrics: Record<string, MetricPair>, quality: MetricPair, changes: ScheduleComparison["changes"]): string {
  const parts = [`${quality.label}: ${fmtMetric("quality", quality.before)} → ${fmtMetric("quality", quality.after)}`];
  const keys = [...METRIC_SUMMARY_ORDER.filter((k) => k in metrics), ...Object.keys(metrics).filter((k) => !METRIC_SUMMARY_ORDER.includes(k)).sort()];
  for (const key of keys) {
    const pair = metrics[key];
    if (pair) parts.push(`${pair.label}: ${fmtMetric(key, pair.before)} → ${fmtMetric(key, pair.after)}`);
  }
  const total = changes.moved_entries + changes.added_entries + changes.removed_entries;
  parts.push(`${total} entries change (${changes.moved_entries} moved, ${changes.added_entries} added, ${changes.removed_entries} removed)`);
  return parts.join("; ");
}

export class PlanModel {
  private cacheRevision = -1;
  private cache: VersionRecord[] = [];

  constructor(
    private readonly store: DemoStore,
    private readonly config: ActiveConfig,
  ) {}

  // --------------------------------------------------------------- versions

  private capturedDetail(template: number): ScheduleVersionDetail {
    const detail = this.store.data.schedule.version_detail[String(template)];
    if (!detail) throw notFound(`schedule version ${template} not found`, { version: template });
    return detail;
  }

  private build(number: number, template: number, cloned: ClonedVersion | null): VersionRecord {
    const detail = clone(this.capturedDetail(template));
    if (cloned) {
      Object.assign(detail, {
        version_number: cloned.version_number,
        schedule_version_id: cloned.schedule_version_id,
        run_id: cloned.run_id,
        input_snapshot_id: cloned.input_snapshot_id,
        generated_at: cloned.generated_at,
        generated_by: cloned.generated_by,
        label: cloned.label,
        notes: cloned.notes,
        trigger: cloned.trigger,
        previous_version: cloned.previous_version,
        status: cloned.status,
        approved_by: null,
        approved_at: null,
        published_by: null,
        published_at: null,
        superseded_at: null,
        writeback_receipt: null,
        details: {
          ...detail.details,
          trigger: cloned.trigger,
          previous_version: cloned.previous_version,
          note: cloned.notes,
          system_config_version: this.config.activeVersionNumber(),
        },
      });
      if (detail.analytics) detail.analytics = { ...detail.analytics, run_id: cloned.run_id };
    }
    const patch = this.store.state.versionPatches[String(number)];
    if (patch) {
      const { details, ...rest } = patch;
      Object.assign(detail, rest);
      if (details) detail.details = { ...detail.details, ...details };
    }
    return { number, template, detail, response: toResponse(detail) };
  }

  /** Every version, newest first. */
  versions(): VersionRecord[] {
    if (this.cacheRevision === this.store.revision) return this.cache;
    const records: VersionRecord[] = [];
    for (const key of Object.keys(this.store.data.schedule.version_detail)) {
      const number = Number(key);
      records.push(this.build(number, number, null));
    }
    for (const cloned of this.store.state.clonedVersions) records.push(this.build(cloned.version_number, cloned.template, cloned));
    this.cache = sortBy(records, (v) => [v.number], true);
    this.cacheRevision = this.store.revision;
    return this.cache;
  }

  find(number: number): VersionRecord | undefined {
    return this.versions().find((v) => v.number === number);
  }

  get(number: number): VersionRecord {
    const found = this.find(number);
    if (!found) throw notFound(`schedule version ${number} not found`, { version: number });
    return found;
  }

  latest(status?: ScheduleStatus): VersionRecord | null {
    return this.versions().find((v) => status === undefined || v.response.status === status) ?? null;
  }

  /** Latest PUBLISHED, else APPROVED, else DRAFT (ScheduleRepository.get_current). */
  current(): VersionRecord | null {
    return this.latest("published") ?? this.latest("approved") ?? this.latest("draft");
  }

  resolveVersion(number?: number): VersionRecord {
    if (number !== undefined) return this.get(number);
    const current = this.current();
    if (!current) throw notFound("no schedule version exists yet; generate one first");
    return current;
  }

  statusCounts(): Record<string, number> {
    const counts: Record<string, number> = { draft: 0, approved: 0, published: 0, superseded: 0, rejected: 0 };
    for (const v of this.versions()) counts[v.response.status] = (counts[v.response.status] ?? 0) + 1;
    return counts;
  }

  // ---------------------------------------------------------------- entries

  entries(number: number): ScheduleEntry[] {
    const record = this.get(number);
    let key = String(record.template);
    const table = this.store.data.schedule.version_entries as Record<string, ScheduleEntry[] | { same_as: string }>;
    for (let hop = 0; hop < 10; hop += 1) {
      const value = table[key];
      if (!value) return [];
      if (Array.isArray(value)) return value;
      key = value.same_as;
    }
    return [];
  }

  /** ScheduleService.entries_of: window / machine / order / customer filters, sorted by machine, setup start, sequence. */
  filteredEntries(number: number, filters: EntryFilters = {}): ScheduleEntry[] {
    const { start, end } = filters;
    let entries = this.entries(number);
    if (start && end) entries = entries.filter((e) => new Date(e.setup_start) < end && new Date(e.end) > start);
    else if (start) entries = entries.filter((e) => new Date(e.start) >= start);
    else if (end) entries = entries.filter((e) => new Date(e.start) < end);
    if (filters.order_id) entries = entries.filter((e) => e.order_id === filters.order_id);
    if (filters.machine_ids && filters.machine_ids.length) {
      const wanted = new Set(filters.machine_ids);
      entries = entries.filter((e) => wanted.has(e.machine_id));
    }
    if (filters.customer_id) entries = entries.filter((e) => e.customer_id === filters.customer_id);
    return sortBy(entries, (e) => [e.machine_id, e.setup_start, e.sequence_on_machine]);
  }

  // ---------------------------------------------------------------- machines

  machineSummaries(filters: { machine_group?: string; process_type?: ProcessType; status?: MachineStatus } = {}): MachineSummary[] {
    return this.store.data.machines.list
      .map((m) => m.machine)
      .filter((m) => (!filters.machine_group || m.machine_group === filters.machine_group) && (!filters.process_type || m.process_type === filters.process_type) && (!filters.status || m.status === filters.status));
  }

  private machineDowntime(machineId: string): TimeWindow[] {
    const detail = this.store.data.machines.detail[machineId];
    return (detail?.downtime ?? []).map((w) => ({ start: w.start, end: w.end, reason: w.reason }));
  }

  private block(entry: ScheduleEntry): GanttBlock {
    const order = this.store.data.orders.detail[entry.order_id]?.order;
    const late = entry.expected_lateness_hours !== null ? entry.expected_lateness_hours > 0 : entry.due_date !== null && new Date(entry.end) > new Date(entry.due_date);
    return {
      entry,
      order_id: entry.order_id,
      customer_id: order ? order.customer_id : entry.customer_id,
      customer_name: order?.customer_name ?? null,
      part_id: order?.part_id ?? null,
      part_name: order?.part_name ?? null,
      order_status: order?.order_status ?? null,
      setup_start: entry.setup_start,
      start: entry.start,
      end: entry.end,
      locked: entry.locked,
      late,
    };
  }

  private rows(machines: MachineSummary[], entries: ScheduleEntry[], start: Date, end: Date): MachineRow[] {
    const locks = this.store.activeLocks();
    const byMachine = new Map<string, ScheduleEntry[]>();
    for (const entry of entries) {
      const list = byMachine.get(entry.machine_id) ?? [];
      list.push(entry);
      byMachine.set(entry.machine_id, list);
    }
    return machines.map((m) => {
      const blocks = sortBy(byMachine.get(m.machine_id) ?? [], (e) => [e.setup_start, e.sequence_on_machine]).map((e) => this.block(e));
      return {
        machine_id: m.machine_id,
        machine_name: m.machine_name,
        machine_group: m.machine_group,
        process_type: m.process_type,
        status: m.status,
        busy_hours: blocks.reduce((s, b) => s + b.entry.setup_minutes + b.entry.run_minutes, 0) / 60,
        blocks,
        downtime: sortBy(
          this.machineDowntime(m.machine_id).filter((w) => overlaps(new Date(w.start), new Date(w.end), start, end)),
          (w) => [w.start],
        ),
        locks: locks.filter((l) => l.machine_id === m.machine_id && (l.window === null || overlaps(new Date(l.window.start), new Date(l.window.end), start, end))),
      };
    });
  }

  gantt(params: { version?: number; start?: Date; end?: Date; machine_group?: string; process_type?: ProcessType; machine_ids?: string[] }): GanttView {
    const version = this.resolveVersion(params.version);
    const now = this.store.now();
    const horizonStart = new Date(version.response.horizon_start);
    const axisStart = params.start ?? (now > horizonStart ? now : horizonStart);
    const axisEnd = params.end ?? addDays(axisStart, DEFAULT_GANTT_DAYS);
    if (axisEnd <= axisStart) throw validation("end must be after start", { start: iso(axisStart), end: iso(axisEnd) });
    if (axisEnd.getTime() - axisStart.getTime() > MAX_GANTT_DAYS * 86_400_000) throw validation(`the Gantt window may span at most ${MAX_GANTT_DAYS} days`);
    let machines = this.machineSummaries({ machine_group: params.machine_group, process_type: params.process_type });
    if (params.machine_ids && params.machine_ids.length) {
      const wanted = new Set(params.machine_ids);
      machines = machines.filter((m) => wanted.has(m.machine_id));
    }
    const entries = this.filteredEntries(version.number, { machine_ids: machines.map((m) => m.machine_id), start: axisStart, end: axisEnd });
    const rows = this.rows(machines, entries, axisStart, axisEnd);
    return {
      version: version.response,
      axis_start: iso(axisStart),
      axis_end: iso(axisEnd),
      machine_group: params.machine_group ?? null,
      process_type: params.process_type ?? null,
      entries: rows.reduce((s, r) => s + r.blocks.length, 0),
      rows,
    };
  }

  /** Plant-local day (timezone of the default calendar, captured as an offset) grouped by machine. */
  day(date: string, versionNumber?: number): DaySchedule {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || Number.isNaN(Date.parse(`${date}T00:00:00Z`))) {
      throw validation(`invalid date '${date}'; expected YYYY-MM-DD`, { date });
    }
    const version = this.resolveVersion(versionNumber);
    const meta = this.store.data.schedule.day_meta;
    const start = new Date(Date.parse(`${date}T00:00:00Z`) + meta.offset_minutes * 60_000);
    const end = addHours(start, 24);
    const entries = this.filteredEntries(version.number, { start, end });
    const rows = this.rows(this.machineSummaries(), entries, start, end);
    return {
      date,
      timezone: meta.timezone,
      start: iso(start),
      end: iso(end),
      version: version.response,
      entries: rows.reduce((s, r) => s + r.blocks.length, 0),
      machines: rows,
    };
  }

  private machineBase(machineId: string): MachineListItemResponse {
    const found = this.store.data.machines.list.find((m) => m.machine.machine_id === machineId);
    if (!found) throw notFound(`machine '${machineId}' not found`, { machine_id: machineId });
    return found;
  }

  private loadOf(item: MachineListItemResponse): MachineListItemResponse["load"] {
    const current = this.current();
    return { ...item.load, version_number: current ? current.number : null, status: current ? current.response.status : null, horizon_start: current?.response.horizon_start ?? null, horizon_end: current?.response.horizon_end ?? null };
  }

  machineList(filters: { machine_group?: string; process_type?: ProcessType; status?: MachineStatus } = {}): MachineListItemResponse[] {
    const locks = this.store.activeLocks();
    return this.store.data.machines.list
      .filter((m) => (!filters.machine_group || m.machine.machine_group === filters.machine_group) && (!filters.process_type || m.machine.process_type === filters.process_type) && (!filters.status || m.machine.status === filters.status))
      .map((m) => ({ machine: m.machine, load: this.loadOf(m), active_locks: locks.filter((l) => l.machine_id === m.machine.machine_id).length }));
  }

  machineDetail(machineId: string): MachineDetail {
    const item = this.machineBase(machineId);
    const captured = this.store.data.machines.detail[machineId];
    if (!captured) throw notFound(`machine '${machineId}' not found`, { machine_id: machineId });
    const now = this.store.now();
    const horizon = addHours(now, DEFAULT_SCHEDULE_WINDOW_HOURS);
    const current = this.current();
    const upcoming = current
      ? sortBy(
          this.entries(current.number).filter((e) => e.machine_id === machineId && new Date(e.end) > now && new Date(e.setup_start) < horizon),
          (e) => [e.setup_start, e.sequence_on_machine],
        )
      : [];
    return { ...captured, load: this.loadOf(item), locks: this.store.activeLocks().filter((l) => l.machine_id === machineId), upcoming };
  }

  machineSchedule(machineId: string, params: { start?: Date; end?: Date; version?: number }): MachineScheduleResponse {
    const item = this.machineBase(machineId);
    const now = this.store.now();
    const start = params.start ?? now;
    const end = params.end ?? addHours(start, DEFAULT_SCHEDULE_WINDOW_HOURS);
    if (end <= start) throw validation("end must be after start", { start: iso(start), end: iso(end) });
    if (end.getTime() - start.getTime() > MAX_SCHEDULE_WINDOW_DAYS * 86_400_000) throw validation(`the schedule window may span at most ${MAX_SCHEDULE_WINDOW_DAYS} days`);
    const version = params.version !== undefined ? this.get(params.version) : this.current();
    const entries = version ? sortBy(this.filteredEntries(version.number, { machine_ids: [machineId], start, end }), (e) => [e.setup_start, e.sequence_on_machine]) : [];
    return {
      machine_id: machineId,
      machine_name: item.machine.machine_name,
      version_number: version ? version.number : null,
      status: version ? version.response.status : null,
      start: iso(start),
      end: iso(end),
      entries,
      downtime: sortBy(
        this.machineDowntime(machineId).filter((w) => overlaps(new Date(w.start), new Date(w.end), start, end)),
        (w) => [w.start],
      ),
      locks: this.store.activeLocks().filter((l) => l.machine_id === machineId && (l.window === null || overlaps(new Date(l.window.start), new Date(l.window.end), start, end))),
    };
  }

  // -------------------------------------------------------------- comparison

  private swapComparison(c: ScheduleComparison): ScheduleComparison {
    const swapPair = (p: MetricPair): MetricPair => ({ label: p.label, before: p.after, after: p.before, delta: p.delta === null ? null : -p.delta });
    const metrics = Object.fromEntries(Object.entries(c.metrics).map(([k, v]) => [k, swapPair(v)]));
    const quality = swapPair(c.quality);
    const changes: ScheduleComparison["changes"] = {
      ...c.changes,
      added_entries: c.changes.removed_entries,
      removed_entries: c.changes.added_entries,
      entries: c.changes.entries.map((e) => ({
        ...e,
        kind: e.kind === "added" ? "removed" : e.kind === "removed" ? "added" : e.kind,
        previous_machine_id: e.proposed_machine_id,
        proposed_machine_id: e.previous_machine_id,
        previous_start: e.proposed_start,
        proposed_start: e.previous_start,
        shift_minutes: e.shift_minutes === null ? null : -e.shift_minutes,
      })),
    };
    return { a: c.b, b: c.a, metrics, quality, changes, moved_orders: c.moved_orders, summary: comparisonSummary(metrics, quality, changes) };
  }

  private identicalComparison(a: VersionRecord, b: VersionRecord): ScheduleComparison {
    const sample = Object.values(this.store.data.schedule.compare)[0];
    const metricsSource = a.response.metrics as unknown as Record<string, number>;
    const keys = sample ? Object.keys(sample.metrics) : METRIC_SUMMARY_ORDER;
    const metrics: Record<string, MetricPair> = {};
    for (const key of keys) {
      const value = metricsSource[key] ?? null;
      metrics[key] = { label: sample?.metrics[key]?.label ?? key, before: value, after: value, delta: value === null ? null : 0 };
    }
    const quality: MetricPair = { label: "Schedule quality", before: a.response.quality_score, after: b.response.quality_score, delta: a.response.quality_score !== null && b.response.quality_score !== null ? b.response.quality_score - a.response.quality_score : null };
    const changes: ScheduleComparison["changes"] = { moved_entries: 0, added_entries: 0, removed_entries: 0, unchanged_entries: a.response.entry_count, changed_orders: [], frozen_violations: 0, entries: [] };
    return { a: a.response, b: b.response, metrics, quality, changes, moved_orders: 0, summary: comparisonSummary(metrics, quality, changes) };
  }

  compare(a: number, b: number): ScheduleComparison {
    const A = this.get(a);
    const B = this.get(b);
    const table = this.store.data.schedule.compare;
    const direct = table[`${A.template}-${B.template}`];
    if (direct) return { ...clone(direct), a: A.response, b: B.response };
    const reverse = table[`${B.template}-${A.template}`];
    if (reverse) return { ...this.swapComparison(clone(reverse)), a: A.response, b: B.response };
    return this.identicalComparison(A, B);
  }

  qualityReport(): ScheduleQualityReport {
    const current = this.current();
    if (!current) return { version: null, quality: null, metrics: null, newest_draft: null, comparison: null };
    const draft = this.latest("draft");
    const comparison = draft && draft.number !== current.number ? this.compare(current.number, draft.number) : null;
    return { version: current.response, quality: current.detail.quality, metrics: current.response.metrics, newest_draft: draft?.response ?? null, comparison };
  }

  // --------------------------------------------------------------- analytics

  private capturedIsCurrent(current: VersionRecord | null): boolean {
    return current === null || current.template === this.store.data.meta.active_version;
  }

  kpis(): KpiReport {
    const captured = this.store.data.analytics.kpis;
    const current = this.current();
    if (!current) return { ...captured, version_number: null, version_status: null };
    const analytics = current.detail.analytics;
    if (this.capturedIsCurrent(current) || !analytics) return { ...captured, version_number: current.number, version_status: current.response.status };
    return { kpis: analytics.kpis, source: "stored", as_of: analytics.as_of, version_number: current.number, version_status: current.response.status };
  }

  bottlenecks(): BottleneckReport {
    const captured = this.store.data.analytics.bottlenecks;
    const current = this.current();
    if (!current) return { ...captured, version_number: null, version_status: null };
    const analytics = current.detail.analytics;
    if (this.capturedIsCurrent(current) || !analytics) return { ...captured, version_number: current.number, version_status: current.response.status };
    const items = sortBy(analytics.bottlenecks, (b) => [RISK_RANK[b.severity], b.revenue_at_risk], true);
    return { current: items[0] ?? null, items, source: "stored", as_of: analytics.as_of, version_number: current.number, version_status: current.response.status };
  }

  capacity(dimension: string, period: string, horizonDays: number | undefined): CapacityReport {
    if (!(CAPACITY_DIMENSIONS as readonly string[]).includes(dimension)) throw validation(`dimension must be one of ${CAPACITY_DIMENSIONS.join(", ")}`, { dimension });
    if (!(CAPACITY_PERIODS as readonly string[]).includes(period)) throw validation(`period must be one of ${CAPACITY_PERIODS.join(", ")}`, { period });
    if (horizonDays !== undefined && (horizonDays < 1 || horizonDays > MAX_HORIZON_DAYS)) throw validation(`horizon_days must be in 1..${MAX_HORIZON_DAYS}`, { horizon_days: horizonDays });
    const capacity = this.store.data.analytics.capacity;
    const wanted = horizonDays === undefined ? "default" : String(horizonDays);
    let report = capacity.reports[`${dimension}|${period}|${wanted}`];
    let horizonEnd: Date | null = null;
    if (!report) {
      const candidates = Object.keys(capacity.reports)
        .filter((k) => k.startsWith(`${dimension}|${period}|`) && !k.endsWith("|default"))
        .map((k) => ({ key: k, horizon: Number(k.split("|")[2]) }));
      const nearest = sortBy(candidates, (c) => [Math.abs(c.horizon - (horizonDays ?? 14))])[0];
      const base = nearest ? capacity.reports[nearest.key] : undefined;
      if (!base) throw notFound("no capacity data captured for this dimension", { dimension, period });
      report = base;
      horizonEnd = addDays(new Date(base.horizon_start), horizonDays ?? 14);
    }
    const end = horizonEnd ?? new Date(report.horizon_end);
    const rows = (capacity.rows[`${dimension}|${period}`] ?? [])
      .map((packed) => {
        const [key, periodStart, periodEnd, required, available] = packed.split("|");
        const req = Number(required);
        const avail = Number(available);
        return {
          key: key ?? "",
          period_start: periodStart ?? "",
          period_end: periodEnd ?? "",
          required_hours: req,
          available_hours: avail,
          gap_hours: avail - req,
          utilization_pct: avail > 0 ? (100 * req) / avail : req > 0 ? 100 : 0,
        };
      })
      .filter((r) => new Date(r.period_start) < end);
    return { ...clone(report), horizon_end: iso(end), rows };
  }

  onTimeDelivery(windowDays: number): OtdReport {
    if (windowDays < 1 || windowDays > MAX_OTD_WINDOW_DAYS) throw validation(`window_days must be in 1..${MAX_OTD_WINDOW_DAYS}`, { window_days: windowDays });
    const table = this.store.data.analytics.on_time_delivery;
    const direct = table[String(windowDays)];
    if (direct) return direct;
    const nearest = sortBy(Object.keys(table), (k) => [Math.abs(Number(k) - windowDays)])[0];
    const report = nearest ? table[nearest] : undefined;
    if (!report) throw notFound("no on-time-delivery data captured");
    return { ...report, window_days: windowDays };
  }

  // ---------------------------------------------------------------- workflow

  private nextNumber(): number {
    return Math.max(0, ...this.versions().map((v) => v.number)) + 1;
  }

  private cloneVersion(template: number, user: UserInfo, opts: { label: string | null; notes: string | null; trigger: string; status: ScheduleStatus }): VersionRecord {
    const now = this.store.nowIso();
    const current = this.current();
    const cloned: ClonedVersion = {
      version_number: this.nextNumber(),
      template,
      schedule_version_id: this.store.id("sch"),
      run_id: this.store.id("run"),
      input_snapshot_id: this.store.id("snap"),
      generated_at: now,
      generated_by: user.user_id,
      label: opts.label,
      notes: opts.notes,
      trigger: opts.trigger,
      previous_version: current ? current.number : null,
      status: opts.status,
    };
    this.store.state.clonedVersions.push(cloned);
    this.store.revision += 1;
    const record = this.get(cloned.version_number);
    this.store.audit(
      user,
      ENTITY_SCHEDULE_VERSION,
      cloned.schedule_version_id,
      "schedule.generated",
      current ? { version: current.number, status: current.response.status } : null,
      {
        version: record.number,
        status: record.response.status,
        run_id: cloned.run_id,
        quality_score: record.response.quality_score,
        entries: record.response.entry_count,
        scheduled_orders: record.response.metrics.scheduled_orders,
        unscheduled_orders: record.response.metrics.unscheduled_orders,
      },
      opts.notes ?? `${opts.trigger} schedule generation`,
      { version: record.number, run_id: cloned.run_id, trigger: opts.trigger, input_snapshot_id: cloned.input_snapshot_id, algorithm: `${record.response.algorithm} ${record.response.algorithm_version}` },
    );
    return record;
  }

  /** Captured drafts serve as templates, in turn (the demo never runs the scheduler). */
  private templateForGenerate(): number {
    const captured = Object.values(this.store.data.schedule.version_detail)
      .filter((v) => v.status === "draft" && (v.trigger ?? "manual") === "manual")
      .map((v) => v.version_number)
      .sort((a, b) => a - b);
    const pool = captured.length ? captured : Object.keys(this.store.data.schedule.version_detail).map(Number).sort((a, b) => b - a).slice(0, 1);
    const index = this.store.state.generateCounter % Math.max(pool.length, 1);
    return pool[index] ?? 1;
  }

  generate(user: UserInfo, note: string | null): ScheduleGenerateResponse {
    const previous = this.current();
    const template = this.templateForGenerate();
    this.store.state.generateCounter += 1;
    const record = this.cloneVersion(template, user, { label: note ? note.slice(0, 255) : null, notes: note, trigger: "manual", status: "draft" });
    this.store.commit();
    const base = this.store.data.schedule.generate_responses[String(template)] ?? Object.values(this.store.data.schedule.generate_responses)[0];
    const now = this.store.now();
    const run = base
      ? { ...base.run, run_id: record.response.run_id ?? base.run.run_id, started_at: iso(now), finished_at: iso(new Date(now.getTime() + (base.run.duration_seconds ?? 0.2) * 1000)), input_snapshot_id: record.response.input_snapshot_id, triggered_by: user.user_id, trigger_reason: note ? `manual: ${note}` : "manual" }
      : this.runFromVersion(record);
    const snapshot = base ? { ...base.snapshot, snapshot_id: record.response.input_snapshot_id ?? base.snapshot.snapshot_id, as_of: iso(now) } : { snapshot_id: record.response.input_snapshot_id ?? "", as_of: iso(now), source: "db", size_bytes: 0, sha256: "", summary: {} };
    return {
      version: record.detail,
      run,
      snapshot,
      previous_version: previous ? previous.number : null,
      priority_results: base?.priority_results ?? 0,
      entries: record.response.entry_count,
      unscheduled: record.detail.unscheduled.length,
      data_quality_issues: base?.data_quality_issues ?? 0,
      alerts: base?.alerts ?? 0,
      timings: base?.timings ?? {},
    };
  }

  private runFromVersion(record: VersionRecord): ScheduleGenerateResponse["run"] {
    const r = record.response;
    return {
      run_id: r.run_id ?? "",
      kind: "schedule",
      status: "completed",
      started_at: r.generated_at,
      finished_at: r.generated_at,
      duration_seconds: 0,
      orders_considered: r.metrics.scheduled_orders + r.metrics.unscheduled_orders,
      orders_scheduled: r.metrics.scheduled_orders,
      orders_blocked: 0,
      objective_score: r.quality_score,
      quality_score: r.quality_score,
      algorithm: r.algorithm,
      algorithm_version: r.algorithm_version,
      profile_id: r.profile_id,
      profile_version: r.profile_version,
      config_version: r.config_version,
      input_snapshot_id: r.input_snapshot_id,
      triggered_by: r.generated_by,
      trigger_reason: r.trigger,
      error_message: null,
      metrics: {},
      warnings: [],
    };
  }

  private patch(number: number, patch: Partial<Record<string, unknown>>): void {
    const key = String(number);
    const existing = this.store.state.versionPatches[key] ?? {};
    const { details, ...rest } = patch as { details?: Record<string, unknown> } & Record<string, unknown>;
    this.store.state.versionPatches[key] = { ...existing, ...rest, details: details ? { ...(existing.details ?? {}), ...details } : existing.details };
    this.store.revision += 1;
  }

  approve(number: number, user: UserInfo, reason: string): ScheduleVersionDetail {
    const version = this.get(number);
    if (version.response.status !== "draft") throw conflict(`schedule v${number} is ${version.response.status}; only a draft can be approved`, { version: number, status: version.response.status });
    const now = this.store.nowIso();
    const superseded: number[] = [];
    for (const other of this.versions()) {
      if (other.number !== number && other.response.status === "approved") {
        this.patch(other.number, { status: "superseded", superseded_at: now });
        superseded.push(other.number);
      }
    }
    this.patch(number, { status: "approved", approved_by: user.user_id, approved_at: now });
    const updated = this.get(number);
    this.store.audit(user, ENTITY_SCHEDULE_VERSION, updated.response.schedule_version_id, "schedule.approved", { status: version.response.status }, { status: "approved", approved_by: user.user_id, approved_at: now }, reason, { version: number, superseded_versions: superseded });
    this.store.commit();
    return this.get(number).detail;
  }

  reject(number: number, user: UserInfo, reason: string): ScheduleVersionDetail {
    const version = this.get(number);
    if (version.response.status !== "draft" && version.response.status !== "approved") {
      throw conflict(`schedule v${number} is ${version.response.status}; only a draft or approved version can be rejected`, { version: number, status: version.response.status });
    }
    this.patch(number, { status: "rejected", details: { rejected_by: user.user_id, rejection_reason: reason } });
    const updated = this.get(number);
    this.store.audit(user, ENTITY_SCHEDULE_VERSION, updated.response.schedule_version_id, "schedule.rejected", { status: version.response.status }, { status: "rejected" }, reason, { version: number });
    this.store.commit();
    return this.get(number).detail;
  }

  publish(number: number, user: UserInfo, reason: string): PublishResponse {
    const version = this.get(number);
    if (version.response.status !== "approved") {
      const hint = version.response.status === "draft" ? "approve it first" : "it cannot be published";
      throw conflict(`schedule v${number} is ${version.response.status}; ${hint}`, { version: number, status: version.response.status });
    }
    const now = this.store.now();
    const template = this.store.data.schedule.publish_response?.receipt ?? this.store.data.schedule.version_detail[String(this.store.data.meta.active_version ?? 1)]?.writeback_receipt ?? null;
    const receipt = {
      receipt_id: this.store.id("wb"),
      mode: template?.mode ?? "read_only",
      status: template?.status ?? "skipped_read_only",
      attempted_at: iso(now),
      entries_published: template?.entries_published ?? 0,
      approved_by: user.user_id,
      message: template?.message ?? "writeback gateway is read-only; schedule was not sent to the ERP",
      run_id: version.response.run_id,
      details: { ...(template?.details ?? {}), configured_mode: template?.mode ?? "read_only", requested_mode: template?.mode ?? "read_only", entries: version.response.entry_count, version_number: number },
    };
    let superseded = 0;
    for (const other of this.versions()) {
      if (other.number !== number && (other.response.status === "published" || other.response.status === "approved")) {
        this.patch(other.number, { status: "superseded", superseded_at: iso(now) });
        superseded += 1;
      }
    }
    this.patch(number, { status: "published", published_by: user.user_id, published_at: iso(now), writeback_receipt: receipt });
    const updated = this.get(number);
    this.store.audit(
      user,
      ENTITY_SCHEDULE_VERSION,
      updated.response.schedule_version_id,
      "schedule.published",
      { status: version.response.status },
      { status: "published", published_by: user.user_id, receipt },
      reason,
      { version: number, writeback_mode: receipt.mode, receipt_status: receipt.status, superseded, auto: false },
    );
    this.store.commit();
    return { version: this.get(number).detail, receipt, superseded };
  }

  /** Captured replan outcome; when the capture produced a candidate version it is cloned again. */
  replan(user: UserInfo, trigger: ReplanTriggerType, reason: string | null): ReplanOutcome {
    const captured = this.store.data.schedule.replan;
    const now = this.store.now();
    const active = this.current();
    if (!captured) {
      return { trigger, evaluated_at: iso(now), triggered: false, action: "not_triggered", reason: "no triggering events", event_types: [], events: [], decision: null, active_version: active?.response ?? null, candidate_version: null, comparison: null, alert_id: null };
    }
    const events = captured.events.map((e) => (e.entity_type === "system" || e.type === "manual" ? { ...e, type: trigger, occurred_at: iso(now), entity_id: user.user_id, message: reason ?? `${trigger} replanning requested by ${user.user_id}` } : e));
    if (!captured.triggered || !captured.candidate_version) {
      return { ...clone(captured), trigger, evaluated_at: iso(now), events, active_version: active?.response ?? null, candidate_version: null, comparison: null };
    }
    const candidate = this.cloneVersion(captured.candidate_version.version_number, user, { label: `replan: ${trigger}`, notes: `replan: ${trigger}`, trigger: `replan:${trigger}`, status: "draft" });
    const decision = captured.decision ? { ...captured.decision, triggers: [trigger] } : null;
    if (captured.action === "rejected") {
      this.patch(candidate.number, { status: "rejected", details: { rejected_by: user.user_id, rejection_reason: captured.reason } });
      this.store.audit(user, ENTITY_SCHEDULE_VERSION, candidate.response.schedule_version_id, "schedule.rejected", { status: "draft" }, { status: "rejected" }, captured.reason, { version: candidate.number });
    } else if (captured.action === "approved" || captured.action === "published") {
      this.patch(candidate.number, { status: "approved", approved_by: user.user_id, approved_at: iso(now) });
    }
    this.patch(candidate.number, { details: { replan: { trigger, decision, comparison: captured.comparison?.summary ?? null } } });
    this.store.state.replanCounter += 1;
    const comparison = captured.comparison && active ? { ...clone(captured.comparison), a: active.response, b: this.get(candidate.number).response } : null;
    this.store.audit(
      user,
      ENTITY_SCHEDULE_VERSION,
      candidate.response.schedule_version_id,
      "replan.decision",
      { active_version: active ? active.number : null },
      { candidate_version: candidate.number, action: captured.action, decision, comparison: comparison?.summary ?? null },
      captured.reason,
      { trigger, events: captured.event_types, candidate_version: candidate.number },
    );
    this.store.commit();
    return {
      trigger,
      evaluated_at: iso(now),
      triggered: true,
      action: captured.action,
      reason: captured.reason,
      event_types: [trigger],
      events,
      decision,
      active_version: active?.response ?? null,
      candidate_version: this.get(candidate.number).response,
      comparison,
      alert_id: captured.alert_id,
    };
  }

  runDetails(runId: string): DemoRunDetails {
    const captured = this.store.data.schedule.runs[runId];
    if (captured && !isCapturedError(captured)) {
      const version = this.versions().find((v) => v.response.run_id === runId);
      return { ...captured, version: version ? version.response : captured.version };
    }
    const cloned = this.store.state.clonedVersions.find((c) => c.run_id === runId);
    if (cloned) {
      const record = this.get(cloned.version_number);
      const templateRunId = this.capturedDetail(cloned.template).run_id;
      const base = templateRunId ? this.store.data.schedule.runs[templateRunId] : undefined;
      const templateRun = base && !isCapturedError(base) ? base : null;
      return {
        run: templateRun ? { ...templateRun.run, run_id: runId, started_at: record.response.generated_at, finished_at: record.response.generated_at, input_snapshot_id: record.response.input_snapshot_id, triggered_by: record.response.generated_by, trigger_reason: record.response.notes ?? record.response.trigger } : this.runFromVersion(record),
        version: record.response,
        snapshot: templateRun?.snapshot ? { ...templateRun.snapshot, snapshot_id: record.response.input_snapshot_id ?? "", as_of: record.response.generated_at } : null,
        priority_results: templateRun?.priority_results ?? 0,
        data_quality_issues: templateRun?.data_quality_issues ?? 0,
      };
    }
    throw notFound(`optimization run '${runId}' not found`, { run_id: runId });
  }

  // ------------------------------------------------------------------- locks

  listLocks(filters: { machine_id?: string; order_id?: string; lock_type?: LockType }): LockResponse[] {
    let locks = this.store.activeLocks();
    if (filters.machine_id) locks = locks.filter((l) => l.machine_id === filters.machine_id);
    if (filters.order_id) locks = locks.filter((l) => l.order_id === filters.order_id || l.sequence_order_ids.includes(filters.order_id as string));
    if (filters.lock_type) locks = locks.filter((l) => l.lock_type === filters.lock_type);
    return locks;
  }

  private openOrder(orderId: string): void {
    const detail = this.store.data.orders.detail[orderId];
    if (!detail) throw notFound(`order '${orderId}' not found`, { order_id: orderId });
    if (["completed", "packed", "shipped", "cancelled"].includes(detail.order.order_status)) throw conflict(`order '${orderId}' is closed (${detail.order.order_status}) and cannot be locked`, { order_status: detail.order.order_status });
  }

  private lockWindow(start: Date | null, end: Date | null, now: Date, required: boolean): TimeWindow | null {
    const minutes = this.config.scheduling().lock_window_minutes;
    if (!start && !end) {
      if (!required) return null;
      return { start: iso(now), end: iso(new Date(now.getTime() + minutes * 60_000)), reason: "locked" };
    }
    const s = start ?? now;
    const e = end ?? new Date(s.getTime() + minutes * 60_000);
    if (e <= s) throw validation("window_end must be after window_start", { window_start: iso(s), window_end: iso(e) });
    if (e <= now) throw validation("the lock window has already ended", { window_end: iso(e) });
    return { start: iso(s), end: iso(e), reason: "locked" };
  }

  createLock(user: UserInfo, reason: string, body: { lock_type: LockType; order_id: string | null; machine_id: string | null; window_start: Date | null; window_end: Date | null; sequence_order_ids: string[] }): LockResponse {
    const now = this.store.now();
    const sequence = body.sequence_order_ids.filter(Boolean);
    if (body.machine_id !== null) this.machineBase(body.machine_id);
    if (body.order_id !== null) this.openOrder(body.order_id);
    let window: TimeWindow | null;
    if (body.lock_type === "order") {
      if (body.order_id === null) throw validation("order_id is required for an ORDER lock");
      window = this.lockWindow(body.window_start, body.window_end, now, false);
    } else if (body.lock_type === "machine" || body.lock_type === "time_slot") {
      if (body.machine_id === null) throw validation(`machine_id is required for a ${body.lock_type.toUpperCase()} lock`);
      window = this.lockWindow(body.window_start, body.window_end, now, true);
    } else {
      if (sequence.length < 2 || new Set(sequence).size !== sequence.length) throw validation("sequence_order_ids must list at least two distinct orders", { sequence_order_ids: sequence });
      for (const id of sequence) this.openOrder(id);
      window = this.lockWindow(body.window_start, body.window_end, now, false);
    }
    const active = this.store.activeLocks(now);
    if ((body.lock_type === "machine" || body.lock_type === "time_slot") && window) {
      for (const other of active) {
        if (other.machine_id !== body.machine_id || other.window === null) continue;
        if ((other.lock_type === "machine" || other.lock_type === "time_slot") && overlaps(new Date(other.window.start), new Date(other.window.end), new Date(window.start), new Date(window.end))) {
          throw conflict(`machine '${body.machine_id}' already has lock '${other.lock_id}' overlapping this window`, { conflicting_lock_id: other.lock_id, lock_type: other.lock_type });
        }
      }
    }
    if (body.lock_type === "order") {
      const other = active.find((l) => l.lock_type === "order" && l.order_id === body.order_id);
      if (other) throw conflict(`order '${body.order_id}' is already locked by '${other.lock_id}'`, { conflicting_lock_id: other.lock_id });
    }
    if (body.lock_type === "sequence") {
      for (const other of active) {
        if (other.lock_type !== "sequence") continue;
        const overlap = sequence.filter((id) => other.sequence_order_ids.includes(id)).sort();
        if (overlap.length) throw conflict(`orders ${JSON.stringify(overlap)} are already part of sequence lock '${other.lock_id}'`, { conflicting_lock_id: other.lock_id, order_ids: overlap });
      }
    }
    const lock: LockResponse = {
      lock_id: this.store.id("lock"),
      lock_type: body.lock_type,
      order_id: body.order_id,
      machine_id: body.machine_id,
      window,
      sequence_order_ids: sequence,
      reason,
      created_by: user.user_id,
      created_at: iso(now),
      active: true,
    };
    this.store.state.locks.push(lock);
    this.store.audit(user, ENTITY_LOCK, lock.lock_id, "lock.create", null, lock, reason, { lock_type: body.lock_type, order_id: body.order_id, machine_id: body.machine_id, sequence_order_ids: sequence });
    this.store.commit();
    return lock;
  }

  unlock(lockId: string, user: UserInfo, reason: string): LockResponse {
    const current = this.store.findLock(lockId);
    if (!current) throw notFound(`lock '${lockId}' not found`, { lock_id: lockId });
    if (!current.active) throw conflict(`lock '${lockId}' is already released`);
    const before = clone(current);
    const live = this.store.mutableLock(lockId);
    live.active = false;
    this.store.audit(user, ENTITY_LOCK, lockId, "lock.release", before, live, reason, { lock_type: current.lock_type, order_id: current.order_id, machine_id: current.machine_id });
    this.store.commit();
    return live;
  }
}

