/**
 * Schedule endpoints exactly as documented by the OpenAPI components
 * (backend/app/api/schemas/{schedule,schedule_views}.py): the active plan, versions,
 * generation / approval workflow, Gantt + day views, comparison and continuous replanning.
 */

import type { OrderStatus, ProcessType, ScheduleStatus } from "./enums";
import type { LockResponse, PageResponse } from "./api";
import type { TimeWindow } from "./models";
import type { Bottleneck, ExecutiveKpis, ScheduleEntry, ScheduleMetrics, ScheduleQuality, UnscheduledItem } from "./results";
import type { CapacityReport } from "./analytics";

// --------------------------------------------------------------- versions

/** ScheduleVersionResponse: spec Phase 37 "Schedule v124 — Generated, Algorithm, Configuration, Status". */
export interface ScheduleVersionResponse {
  schedule_version_id: string;
  version_number: number;
  status: ScheduleStatus;
  label: string | null;
  algorithm: string;
  algorithm_version: string;
  profile_id: string;
  profile_version: number;
  config_version: number;
  generated_by: string | null;
  generated_at: string;
  horizon_start: string;
  horizon_end: string;
  run_id: string | null;
  input_snapshot_id: string | null;
  approved_by: string | null;
  approved_at: string | null;
  published_by: string | null;
  published_at: string | null;
  superseded_at: string | null;
  entry_count: number;
  quality_score: number | null;
  quality_summary: string | null;
  metrics: ScheduleMetrics;
  /** 'manual', 'replan:<trigger>' … */
  trigger: string | null;
  previous_version: number | null;
  notes: string | null;
}

export interface AlertCounts {
  total: number;
  by_severity: Record<string, number>;
}

/** VersionAnalyticsResponse: analytics computed together with the version. */
export interface VersionAnalytics {
  as_of: string;
  run_id: string | null;
  kpis: ExecutiveKpis;
  capacity: CapacityReport;
  bottlenecks: Bottleneck[];
  data_quality: Record<string, unknown>;
  alerts: AlertCounts;
  timings: Record<string, number>;
}

export interface WritebackReceipt {
  receipt_id: string;
  mode: string;
  status: string;
  attempted_at: string;
  entries_published: number;
  approved_by: string | null;
  message: string;
  run_id: string | null;
  details: Record<string, unknown>;
}

/** ScheduleVersionDetailResponse: GET /schedule/versions/{v}, POST /schedule/approve|reject. */
export interface ScheduleVersionDetail extends ScheduleVersionResponse {
  quality: ScheduleQuality | null;
  unscheduled: UnscheduledItem[];
  warnings: string[];
  analytics: VersionAnalytics | null;
  writeback_receipt: WritebackReceipt | null;
  details: Record<string, unknown>;
}

export interface ScheduleVersionsQuery {
  page?: number;
  page_size?: number;
  status?: ScheduleStatus;
}

// ------------------------------------------------------------ active plan

/** Which version GET /schedule answers with: latest published, else approved, else draft; none when empty. */
export type PlanStatus = "published" | "approved" | "draft" | "none";

/** SchedulePlanResponse: the active plan with one page of its entries. */
export interface SchedulePlan {
  status: PlanStatus | string;
  version: ScheduleVersionResponse | null;
  entries: PageResponse<ScheduleEntry>;
}

/** Query of GET /schedule and GET /schedule/versions/{v}/entries. */
export interface ScheduleEntriesQuery {
  page?: number;
  /** 1..500 (default 100). */
  page_size?: number;
  /** Repeatable. */
  machine_id?: string | string[];
  start?: string;
  end?: string;
  order_id?: string;
  customer_id?: string;
}

// ------------------------------------------------------------- generation

export interface GenerateRequest {
  /** Stored with the version. */
  note?: string | null;
}

export interface SnapshotInfo {
  snapshot_id: string;
  as_of: string;
  source: string;
  size_bytes: number;
  sha256: string;
  summary: Record<string, number>;
}

/** OptimizationRunResponse: observability record of one engine run. */
export interface OptimizationRun {
  run_id: string;
  kind: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  duration_seconds: number | null;
  orders_considered: number;
  orders_scheduled: number;
  orders_blocked: number;
  objective_score: number | null;
  quality_score: number | null;
  algorithm: string;
  algorithm_version: string;
  profile_id: string | null;
  profile_version: number | null;
  config_version: number | null;
  input_snapshot_id: string | null;
  triggered_by: string | null;
  trigger_reason: string | null;
  error_message: string | null;
  metrics: Record<string, unknown>;
  warnings: string[];
}

/** ScheduleGenerateResponse: POST /schedule/generate (201). */
export interface ScheduleGenerateResponse {
  version: ScheduleVersionDetail;
  run: OptimizationRun;
  snapshot: SnapshotInfo;
  previous_version: number | null;
  priority_results: number;
  entries: number;
  unscheduled: number;
  data_quality_issues: number;
  alerts: number;
  timings: Record<string, number>;
}

// --------------------------------------------------------------- workflow

/** VersionActionRequest: body of POST /schedule/approve, /publish and /reject. */
export interface VersionActionRequest {
  version: number;
  /** Mandatory, audited. */
  reason: string;
}

export interface PublishResponse {
  version: ScheduleVersionDetail;
  receipt: WritebackReceipt;
  superseded: number;
}

// ------------------------------------------------------------- comparison

/** MetricPairResponse: "On-time delivery: 87% → 94%". */
export interface MetricPair {
  label: string;
  before: number | null;
  after: number | null;
  delta: number | null;
}

export type EntryChangeKind = "moved" | "added" | "removed" | "unchanged" | string;

export interface EntryChange {
  operation_id: string;
  order_id: string;
  kind: EntryChangeKind;
  previous_machine_id: string | null;
  proposed_machine_id: string | null;
  previous_start: string | null;
  proposed_start: string | null;
  shift_minutes: number | null;
  reason: string;
}

export interface ScheduleChanges {
  moved_entries: number;
  added_entries: number;
  removed_entries: number;
  unchanged_entries: number;
  changed_orders: string[];
  frozen_violations: number;
  entries: EntryChange[];
}

/** ScheduleComparisonResponse: GET /schedule/compare?a=&b= (spec Phase 36). */
export interface ScheduleComparison {
  a: ScheduleVersionResponse;
  b: ScheduleVersionResponse;
  /** Keyed by metric (on_time_pct, avg_lateness_hours, overall_utilization_pct, total_setup_hours, late_orders, …). */
  metrics: Record<string, MetricPair>;
  quality: MetricPair;
  changes: ScheduleChanges;
  moved_orders: number;
  summary: string;
}

/** ScheduleQualityReportResponse: GET /analytics/schedule-quality. */
export interface ScheduleQualityReport {
  version: ScheduleVersionResponse | null;
  quality: ScheduleQuality | null;
  metrics: ScheduleMetrics | null;
  newest_draft: ScheduleVersionResponse | null;
  /** Active plan vs the newest draft, when both exist. */
  comparison: ScheduleComparison | null;
}

// ---------------------------------------------------------- machine views

/** GanttBlockResponse: one job on the machine board (setup block + run block). */
export interface GanttBlock {
  entry: ScheduleEntry;
  order_id: string;
  customer_id: string | null;
  customer_name: string | null;
  part_id: string | null;
  part_name: string | null;
  order_status: OrderStatus | null;
  setup_start: string;
  start: string;
  end: string;
  locked: boolean;
  late: boolean;
}

/** MachineRowResponse: one machine of the Gantt / day view with its blocks, downtime and locks. */
export interface MachineRow {
  machine_id: string;
  machine_name: string;
  machine_group: string;
  process_type: ProcessType;
  status: string;
  busy_hours: number;
  blocks: GanttBlock[];
  downtime: TimeWindow[];
  locks: LockResponse[];
}

/** GanttResponse: GET /schedule/gantt. */
export interface GanttView {
  version: ScheduleVersionResponse;
  axis_start: string;
  axis_end: string;
  machine_group: string | null;
  process_type: ProcessType | null;
  entries: number;
  rows: MachineRow[];
}

export interface GanttQuery {
  version?: number;
  /** Default: now (or the horizon start). */
  start?: string;
  /** Default: start + 7 days. */
  end?: string;
  machine_group?: string;
  process_type?: ProcessType;
  /** Repeatable. */
  machine_id?: string[];
}

/** DayScheduleResponse: GET /schedule/{date} (plant-local day). */
export interface DaySchedule {
  /** yyyy-MM-dd */
  date: string;
  timezone: string;
  start: string;
  end: string;
  version: ScheduleVersionResponse;
  entries: number;
  machines: MachineRow[];
}

// ------------------------------------------------------------- replanning

export type ReplanTriggerType =
  | "new_order"
  | "order_completed"
  | "machine_down"
  | "machine_up"
  | "material_arrived"
  | "quality_failure"
  | "rework"
  | "production_delay"
  | "customer_priority_change"
  | "manual"
  | "config_change"
  | "scheduled";

export interface ReplanRequest {
  trigger?: ReplanTriggerType;
  reason?: string | null;
}

export interface ReplanEvent {
  type: ReplanTriggerType;
  entity_type: string;
  entity_id: string;
  occurred_at: string;
  message: string;
  order_id: string | null;
  machine_id: string | null;
  details: Record<string, unknown>;
}

export interface ReplanDecision {
  should_replan: boolean;
  reason: string;
  improvement_pct: number;
  changed_entries: number;
  frozen_violations: number;
  requires_approval: boolean;
  triggers: string[];
}

export type ReplanAction = "not_triggered" | "rejected" | "awaiting_approval" | "approved" | "published" | string;

/** ReplanResponse: POST /schedule/replan (spec Phase 11: compare old vs new, approval). */
export interface ReplanOutcome {
  trigger: ReplanTriggerType;
  evaluated_at: string;
  triggered: boolean;
  action: ReplanAction;
  reason: string;
  event_types: string[];
  events: ReplanEvent[];
  decision: ReplanDecision | null;
  active_version: ScheduleVersionResponse | null;
  candidate_version: ScheduleVersionResponse | null;
  comparison: ScheduleComparison | null;
  alert_id: string | null;
}
