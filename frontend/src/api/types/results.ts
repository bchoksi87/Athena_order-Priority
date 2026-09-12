/** Engine outputs (backend/app/domain/results.py) plus schedule versioning and simulation scenarios. */

import type {
  AdjustmentKind,
  AlertSeverity,
  AlertType,
  DataQualityCode,
  DataQualitySeverity,
  FactorKind,
  ReadinessState,
  RiskLevel,
  ScheduleStatus,
  SyncMode,
} from "./enums";

// --------------------------------------------------------------- results

export interface FactorScore {
  key: string;
  name: string;
  kind: FactorKind;
  raw_score: number;
  weight: number;
  points: number;
  reason: string;
  details: Record<string, unknown>;
}

export interface PriorityAdjustment {
  kind: AdjustmentKind;
  points: number;
  reason: string;
  source_id: string | null;
}

export interface PriorityResult {
  order_id: string;
  score: number;
  base_score: number;
  factors: FactorScore[];
  adjustments: PriorityAdjustment[];
  readiness: ReadinessState;
  blocked: boolean;
  blocking_reasons: string[];
  risk_level: RiskLevel;
  explanation: string;
  profile_id: string;
  profile_version: number;
  computed_at: string;
  hours_until_due: number | null;
  projected_completion: string | null;
  projected_lateness_hours: number | null;
  forced_next: boolean;
  rank: number | null;
}

export interface Blocker {
  state: ReadinessState;
  message: string;
  resolves_at: string | null;
  details: Record<string, unknown>;
}

export interface MachineCandidate {
  machine_id: string;
  rank: number;
  expected_start: string | null;
  expected_end: string | null;
  setup_minutes: number;
  run_minutes: number;
  soft_cost: number;
  reasons: string[];
  recommended: boolean;
}

export interface MachineRecommendation {
  order_id: string;
  operation_id: string;
  eligible: MachineCandidate[];
  recommended_machine_id: string | null;
  rejected: Record<string, string[]>;
}

export interface ScheduleEntry {
  entry_id: string;
  machine_id: string;
  order_id: string;
  operation_id: string;
  sequence_on_machine: number;
  setup_start: string;
  start: string;
  end: string;
  setup_minutes: number;
  run_minutes: number;
  quantity: number;
  priority_score: number;
  placement_reason: string;
  is_last_operation: boolean;
  expected_completion: string | null;
  due_date: string | null;
  expected_lateness_hours: number | null;
  locked: boolean;
  batch_key: string | null;
  setup_family: string | null;
  material_id: string | null;
  customer_id: string | null;
}

export interface UnscheduledItem {
  order_id: string;
  operation_id: string | null;
  reason_code: string;
  reason: string;
  readiness: ReadinessState | null;
}

export interface ScheduleMetrics {
  scheduled_orders: number;
  unscheduled_orders: number;
  scheduled_operations: number;
  on_time_orders: number;
  late_orders: number;
  orders_at_risk: number;
  on_time_pct: number;
  avg_lateness_hours: number;
  max_lateness_hours: number;
  total_tardiness_hours: number;
  total_run_hours: number;
  total_setup_hours: number;
  setup_count: number;
  makespan_hours: number;
  overall_utilization_pct: number;
  machine_utilization_pct: Record<string, number>;
  revenue_scheduled: number;
  revenue_at_risk: number;
  margin_at_risk: number;
  wip_orders_avg: number;
}

export interface ScheduleQuality {
  score: number;
  components: Record<string, number>;
  weights: Record<string, number>;
  summary: string;
}

export interface ScheduleResult {
  algorithm: string;
  algorithm_version: string;
  profile_id: string;
  profile_version: number;
  config_version: number;
  generated_at: string;
  horizon_start: string;
  horizon_end: string;
  entries: ScheduleEntry[];
  unscheduled: UnscheduledItem[];
  metrics: ScheduleMetrics;
  quality: ScheduleQuality | null;
  machine_recommendations: Record<string, MachineRecommendation>;
  warnings: string[];
  run_id: string | null;
}

/** schedule_versions row (DESIGN_CONTRACT §10). */
export interface ScheduleVersion {
  version_number: number;
  status: ScheduleStatus;
  generated_at: string;
  generated_by: string | null;
  approved_by?: string | null;
  approved_at?: string | null;
  published_at?: string | null;
  algorithm: string;
  algorithm_version: string;
  profile_id: string;
  profile_version: number;
  config_version: number;
  run_id: string | null;
  input_snapshot_id?: string | null;
  note?: string | null;
  quality_score?: number | null;
}

/** GET /schedule response: a result plus its version envelope (null before any run). */
export interface ScheduleView extends ScheduleResult {
  version: ScheduleVersion | null;
}

export interface OrderDelta {
  order_id: string;
  baseline_completion: string | null;
  scenario_completion: string | null;
  delta_hours: number | null;
  baseline_late: boolean;
  scenario_late: boolean;
  baseline_machine_id: string | null;
  scenario_machine_id: string | null;
  baseline_score: number | null;
  scenario_score: number | null;
}

export interface ScheduleDiff {
  orders_affected: number;
  orders_moved_machine: number;
  orders_resequenced: number;
  orders_newly_late: number;
  orders_newly_on_time: number;
  late_orders_before: number;
  late_orders_after: number;
  on_time_pct_before: number;
  on_time_pct_after: number;
  avg_lateness_before: number;
  avg_lateness_after: number;
  utilization_before: number;
  utilization_after: number;
  setup_hours_before: number;
  setup_hours_after: number;
  revenue_at_risk_before: number;
  revenue_at_risk_after: number;
  margin_at_risk_before: number;
  margin_at_risk_after: number;
  additional_overtime_hours: number;
  bottlenecks_before: string[];
  bottlenecks_after: string[];
  order_deltas: OrderDelta[];
  summary: string;
}

export interface SimulationResult {
  simulation_id: string;
  scenarios: Scenario[];
  baseline: ScheduleResult;
  scenario: ScheduleResult;
  diff: ScheduleDiff;
  baseline_priorities: Record<string, number>;
  scenario_priorities: Record<string, number>;
  generated_at: string;
}

/** Simulation scenarios (DESIGN_CONTRACT §6.5), discriminated on `kind`. */
export type Scenario =
  | { kind: "machine_down"; machine_id: string; start: string; end: string; reason?: string }
  | { kind: "urgent_orders"; order_ids: string[]; boost_points?: number }
  | { kind: "add_machine"; template_machine_id: string; machine_id?: string; machine_name?: string }
  | { kind: "extra_shift"; machine_ids?: string[]; start: string; end: string }
  | { kind: "working_day"; date: string; machine_ids?: string[] }
  | { kind: "outsource"; order_ids: string[] }
  | { kind: "material_delay"; material_id: string; delay_hours: number }
  | { kind: "prioritize_customer"; customer_id: string; boost_points?: number }
  | { kind: "weight_change"; weights: Record<string, number> }
  | { kind: "due_date_change"; order_id: string; new_due_date: string };

export type ScenarioKind = Scenario["kind"];

export interface CapacityRow {
  dimension: "machine" | "machine_group" | "process" | "department";
  key: string;
  period_start: string;
  period_end: string;
  required_hours: number;
  available_hours: number;
  gap_hours?: number;
  utilization_pct?: number;
}

export interface Bottleneck {
  resource_type: "machine_group" | "machine" | "process" | "material" | "tooling";
  resource_id: string;
  resource_name: string;
  utilization_pct: number;
  orders_waiting: number;
  capacity_shortfall_hours: number;
  revenue_at_risk: number;
  margin_at_risk: number;
  severity: RiskLevel;
  recommendation: string;
}

export interface ExecutiveKpis {
  total_open_orders: number;
  total_pending_quantity: number;
  orders_due_today: number;
  orders_due_tomorrow: number;
  orders_due_this_week: number;
  overdue_orders: number;
  at_risk_orders: number;
  on_time_delivery_pct: number | null;
  expected_on_time_delivery_pct: number | null;
  machine_utilization_pct: number | null;
  capacity_utilization_pct: number | null;
  revenue_at_risk: number;
  margin_at_risk: number;
  blocked_by_material: number;
  blocked_by_tooling: number;
  blocked_by_machine: number;
  waiting_for_approval: number;
  blocked_total: number;
  scheduled_orders: number;
  unscheduled_orders: number;
  as_of: string;
}

/** One period of on-time-delivery history (GET /analytics/on-time-delivery). */
export interface OtdPoint {
  period_start: string;
  period_end: string;
  orders_due: number;
  on_time: number;
  late: number;
  on_time_pct: number | null;
}

/** DataQualityIssueResponse: one issue of the latest data-quality run (GET /data-quality/issues). */
export interface DataQualityIssue {
  issue_id: string;
  run_id: string;
  detected_at: string;
  /** Serialised enum value (DataQualityCode). */
  code: DataQualityCode;
  /** Serialised enum value (DataQualitySeverity). */
  severity: DataQualitySeverity;
  entity_type: string;
  entity_id: string;
  message: string;
  field_name: string | null;
  recommendation: string | null;
  details: Record<string, unknown>;
}

/** AlertResponse (GET /alerts). */
export interface Alert {
  alert_id: string;
  alert_type: AlertType;
  severity: AlertSeverity;
  title: string;
  reason: string;
  recommended_action: string;
  raised_at: string;
  last_seen_at: string;
  occurrences: number;
  active: boolean;
  order_id: string | null;
  machine_id: string | null;
  entity_ref: string | null;
  details: Record<string, unknown>;
  acknowledged: boolean;
  acknowledged_by: string | null;
  acknowledged_at: string | null;
  resolved_at: string | null;
}

/** AuditEntryResponse: one audit_log row (GET /audit, OrderDetail.audit). */
export interface AuditEntry {
  audit_id: string;
  user_id: string;
  timestamp: string;
  entity_type: string;
  entity_id: string;
  action: string;
  previous_value: Record<string, unknown> | unknown[] | null;
  new_value: Record<string, unknown> | unknown[] | null;
  reason: string | null;
  request_id: string | null;
  details: Record<string, unknown>;
}

export interface SyncRun {
  run_id: string;
  mode: SyncMode;
  started_at: string;
  finished_at: string | null;
  status: string;
  records_fetched?: number;
  records_upserted?: number;
  errors?: string[];
  message?: string | null;
}

export interface HealthResponse {
  status: string;
  database: string;
  version: string;
  environment: string;
  time: string;
}
