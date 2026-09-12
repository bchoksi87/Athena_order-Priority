/** Fixtures shaped exactly like the backend API responses (see backend/app/api/schemas). */
import type {
  Alert,
  AuditEntry,
  CapabilityReportResponse,
  ConfigVersionInfo,
  ExplanationLine,
  ExplanationResponse,
  HealthResponse,
  MachineDetail,
  MachineOptions,
  MetricsResponse,
  OrderDetail,
  OrderListItemResponse,
  PageResponse,
  PreviewResponse,
  PriorityConfigurationResponse,
  PriorityProfile,
  SyncRunResponse,
  SyncStatusResponse,
} from "@/api/types";

export function pageOf<T>(items: T[], page = 1, page_size = 50, total = items.length): PageResponse<T> {
  return { items, total, page, page_size, pages: Math.max(1, Math.ceil(total / page_size)), has_more: page * page_size < total };
}

export const explanationLines: ExplanationLine[] = [
  { kind: "factor", key: "due_date_urgency", label: "Due Date Urgency", points: 28, reason: "Due in 18 hours", raw_score: 95, weight: 0.3 },
  { kind: "factor", key: "sla_risk", label: "SLA Risk", points: 20, reason: "SLA 48 h: 9 hours remaining (19%)", raw_score: 85, weight: 0.2 },
  { kind: "factor", key: "customer_importance", label: "Customer Importance", points: 15, reason: "Strategic customer", raw_score: 100, weight: 0.15 },
  { kind: "factor", key: "order_value", label: "Order Value", points: 8, reason: "₹7.9 L (top 20%)", raw_score: 80, weight: 0.1 },
  { kind: "factor", key: "production_readiness", label: "Production Readiness", points: 10, reason: "Material and tooling available", raw_score: 100, weight: 0.1 },
  { kind: "factor", key: "machine_availability", label: "Machine Availability", points: 7, reason: "MC-LATHE-01 free now", raw_score: 70, weight: 0.1 },
  { kind: "factor", key: "setup_efficiency", label: "Setup Efficiency", points: -3, reason: "Large setup required (93 min)", raw_score: 20, weight: 0.05 },
  { kind: "adjustment", key: "aging", label: "Aging", points: 6, reason: "Waiting 8 days (3 beyond 5)", source_id: null },
];

export function makeOrderRow(overrides: Partial<OrderListItemResponse["order"]> = {}, priority: OrderListItemResponse["priority"] = null): OrderListItemResponse {
  return {
    order: {
      order_id: "SO2609-00093-02",
      external_order_ref: "SO2609-00093",
      customer_id: "CUST-0062",
      customer_name: "Krypton Tooling Industries",
      part_id: "P-COU-5072",
      part_name: "Coupling Rev C",
      part_family: "Coupling",
      quantity: 13,
      pending_quantity: 13,
      completed_quantity: 0,
      due_date: "2026-09-13T12:00:00Z",
      order_date: "2026-08-26T12:00:00Z",
      order_status: "scheduled",
      production_status: "SCHEDULED",
      material_status: "available",
      quality_status: "none",
      process_type: "cnc_machining",
      machine_group: "LATHE",
      required_machine_id: null,
      required_material_id: "MAT-AL6061",
      order_value: 18034.65,
      estimated_margin: 6108.84,
      erp_priority: 3,
      on_hold: false,
      hold_reason: null,
      ...overrides,
    },
    priority,
    schedule: null,
  };
}

export const priorityInfo: NonNullable<OrderListItemResponse["priority"]> = {
  score: 91,
  base_score: 85,
  rank: 1,
  risk_level: "high",
  readiness: "ready",
  blocked: false,
  blocking_reasons: [],
  forced_next: false,
  hours_until_due: 18,
  projected_completion: "2026-09-12T20:00:00Z",
  projected_lateness_hours: null,
  explanation: "ORDER #SO2609-00093-02\nPriority: 91",
  profile_id: "PriorityProfile-A",
  profile_version: 1,
  computed_at: "2026-09-12T06:00:00Z",
};

export const explanationResponse: ExplanationResponse = {
  order_id: "SO2609-00093-02",
  score: 91,
  rank: 1,
  risk_level: "high",
  readiness: "ready",
  blocked: false,
  blocking_reasons: [],
  forced_next: false,
  explanation: "ORDER #SO2609-00093-02\nPriority: 91\nWhy?\n+28 — Due Date Urgency: Due in 18 hours\nTotal: 91",
  lines: explanationLines,
  profile_id: "PriorityProfile-A",
  profile_version: 1,
  computed_at: "2026-09-12T06:00:00Z",
};

export const orderDetail: OrderDetail = {
  order: makeOrderRow().order,
  customer: { customer_id: "CUST-0062", customer_name: "Krypton Tooling Industries", customer_tier: "strategic", strategic_customer_flag: true, sla_hours: 48 },
  priority: priorityInfo,
  breakdown: explanationLines,
  factors: [],
  adjustments: [{ kind: "aging", points: 6, reason: "Waiting 8 days (3 beyond 5)", source_id: null }],
  schedule: {
    version_number: 3,
    status: "published",
    machine_id: "MC-LATHE-01",
    start: "2026-09-12T14:30:00Z",
    end: "2026-09-12T20:00:00Z",
    expected_completion: "2026-09-12T20:00:00Z",
    expected_lateness_hours: null,
    entries: [],
  },
  operations: [
    {
      operation_id: "OP-0000758",
      sequence: 10,
      operation_type: "cnc_machining",
      machine_group: "LATHE",
      machine_id: null,
      operation_status: "scheduled",
      quantity: 13,
      completed_quantity: 0,
      setup_minutes: 80.3,
      cycle_minutes_per_unit: 1.09,
      material_id: "MAT-AL6061",
      tooling_ids: ["TL-GRV-3-A"],
      prerequisite_operation_id: null,
      estimated_start: "2026-09-14T17:30:00Z",
      estimated_end: null,
      actual_start: null,
      actual_end: null,
    },
  ],
  materials: [],
  tooling: [],
  production_minutes: 94.5,
  dependencies: [],
  dependents: ["SO2609-00093-03"],
  overrides: [],
  expedites: [],
  locks: [],
  audit: [],
  data_quality_issues: [],
  special_instructions: null,
  drawing_approved: true,
};

export const machineOptions: MachineOptions = {
  order_id: "SO2609-00093-02",
  operation_id: "OP-0000758",
  synthetic_operation: false,
  source: "live",
  evaluated_at: "2026-09-12T06:00:00Z",
  recommended_machine_id: "MC-LATHE-01",
  scheduled_machine_id: null,
  pinned_machine_id: null,
  pinned_by: null,
  eligible: [{ machine_id: "MC-LATHE-01", machine_name: "TL-250 #1", rank: 1, recommended: true, setup_minutes: 80.3, run_minutes: 13, soft_cost: 93.3, reasons: ["93.3 min setup on MC-LATHE-01"] }],
  rejected: { "MC-MILL-01": ["process cnc_machining not supported"] },
};

export const priorityProfile: PriorityProfile = {
  profile_id: "PriorityProfile-A",
  name: "Default balanced profile",
  version: 1,
  weights: [
    { key: "due_date_urgency", weight: 25, enabled: true, params: {} },
    { key: "customer_importance", weight: 25, enabled: true, params: {} },
    { key: "sla_risk", weight: 25, enabled: true, params: {} },
    { key: "order_value", weight: 25, enabled: true, params: {} },
    { key: "margin", weight: 10, enabled: false, params: {} },
  ],
  due_date: { overdue_score: 100, critical_hours: 24, critical_score: 95, high_days: 2, high_score: 80, medium_days: 7, medium_score: 50, low_days: 14, low_score: 25, floor_score: 5, use_projected_lateness: true },
  customer: { tier_scores: { strategic: 100, key: 75, standard: 45, low: 20 }, strategic_flag_bonus: 10, escalation_points_per_level: 8, revenue_weight: 0.3, profitability_weight: 0.2, max_score: 100 },
  sla: { default_sla_hours: null, breach_score: 100, imminent_ratio: 0.25, imminent_score: 85, watch_ratio: 0.5, watch_score: 55, safe_score: 15 },
  order_value: { scaling: "percentile", cap_value: null, min_score: 5 },
  delay_penalty: { default_penalty_per_day_ratio: 0.01, escalation_multiplier_per_level: 0.25, strategic_multiplier: 1.5, reference_penalty: null },
  readiness: { ready_score: 100, material_partial_score: 40, waiting_material_score: 10, waiting_tooling_score: 15, waiting_approval_score: 5, waiting_previous_operation_score: 35, machine_unavailable_score: 20, hold_score: 0 },
  machine_availability: { available_now_score: 100, available_within_hours: 8, available_soon_score: 60, single_machine_bonus: 15, none_available_score: 5 },
  setup: { same_setup_score: 100, same_material_score: 70, changeover_score: 20, unknown_score: 50, large_setup_minutes: 90, large_setup_penalty_score: 100 },
  aging: { enabled: true, start_after_days: 5, points_per_day: 2, max_points: 20 },
  fairness: { enabled: true, max_wait_days: 10, starvation_boost_points: 25, max_top_n_share_per_customer: 0.5, top_n: 50 },
  expedite: { default_boost_points: 30, max_boost_points: 60, default_duration_hours: 4, max_duration_hours: 72 },
  risk: { critical_lateness_hours: 0, high_slack_hours: 8, medium_slack_hours: 48 },
  erp_priority_points: { "1": 15, "2": 8 },
  blocked_order_cap: null,
};

export const configVersion: ConfigVersionInfo = {
  config_id: "cfg_1",
  version: 1,
  is_active: true,
  created_by: null,
  reason: "initial default configuration",
  created_at: "2026-09-12T05:19:54Z",
  profile_id: "PriorityProfile-A",
  scheduling_config_id: "SchedulingConfig-A",
};

export const priorityConfiguration: PriorityConfigurationResponse = {
  version: configVersion,
  profile: priorityProfile,
  weights_pct: { due_date_urgency: 25, customer_importance: 25, sla_risk: 25, order_value: 25 },
};

export const previewResponse: PreviewResponse = {
  summary: "Changing Due Date Urgency weight from 25% to 40% would move 3 orders into the top 50",
  active_profile_id: "PriorityProfile-A",
  candidate_profile_id: "PriorityProfile-A",
  top_n: 50,
  orders_evaluated: 277,
  entered_top_n: ["SO2609-00010-01"],
  left_top_n: ["SO2609-00093-03"],
  orders_changed_rank: 40,
  mean_abs_score_delta: 2.4,
  max_abs_score_delta: 11.2,
  weight_changes: [{ key: "due_date_urgency", previous_pct: 25, new_pct: 40 }],
  top_n_before: ["SO2609-00093-02", "SO2609-00093-03"],
  top_n_after: ["SO2609-00093-02", "SO2609-00010-01"],
  biggest_moves: [{ order_id: "SO2609-00010-01", rank_delta: -12, score_delta: 11.2 }],
};

export function makeAlert(overrides: Partial<Alert> = {}): Alert {
  return {
    alert_id: "alt_1",
    alert_type: "order_likely_late",
    severity: "high",
    title: "SO2609-00093-02 likely late",
    reason: "Projected completion 6 h after the due date",
    recommended_action: "Expedite or move to MC-LATHE-02",
    raised_at: "2026-09-12T05:00:00Z",
    last_seen_at: "2026-09-12T05:30:00Z",
    occurrences: 2,
    active: true,
    order_id: "SO2609-00093-02",
    machine_id: null,
    entity_ref: null,
    details: {},
    acknowledged: false,
    acknowledged_by: null,
    acknowledged_at: null,
    resolved_at: null,
    ...overrides,
  };
}

export function makeAuditEntry(overrides: Partial<AuditEntry> = {}): AuditEntry {
  return {
    audit_id: "aud_1",
    user_id: "usr_manager",
    timestamp: "2026-09-11T09:00:00Z",
    entity_type: "order",
    entity_id: "SO2609-00093-02",
    action: "expedite",
    previous_value: { score: 72, expedite: null },
    new_value: { score: 91, expedite: { boost_points: 30, expires_at: "2026-09-11T13:00:00Z" } },
    reason: "Customer escalation — line down",
    request_id: "req_1",
    details: {},
    ...overrides,
  };
}

// ------------------------------------------------------------ operational

export const healthResponse: HealthResponse = {
  status: "ok",
  database: "ok",
  version: "0.1.0",
  environment: "dev",
  time: "2026-09-12T13:35:04.111741+00:00",
  connector: { name: "mock", reachable: true, message: "mock connector ready", checked_at: "2026-09-12T13:35:04Z", latency_ms: 0.4 },
  last_sync: { run_id: "sync_1", status: "completed", mode: "full", started_at: "2026-09-12T13:18:23Z", finished_at: "2026-09-12T13:18:48Z" },
  active_plan: { version_number: 4, status: "draft", generated_at: "2026-09-12T13:19:12Z", quality_score: 34.4 },
  background_jobs: { enabled: false, running: false, jobs: [] },
};

export const metricsResponse: MetricsResponse = {
  requests_total: 142,
  errors_total: 0,
  by_status: { "2xx": 142 },
  by_path: {},
  counters: {},
  schedule_generations_total: 4,
  replans_total: 2,
  failed_runs_total: 0,
  sync_runs_total: 3,
  sync_runs_failed_total: 0,
  schedule_versions_by_status: { draft: 3, rejected: 1 },
  active_plan_version: 4,
  active_alerts_total: 5542,
  background_jobs_running: false,
};

export function makeSyncRun(overrides: Partial<SyncRunResponse> = {}): SyncRunResponse {
  return {
    run_id: "sync_1",
    mode: "full",
    status: "completed",
    connector: "mock",
    started_at: "2026-09-12T13:18:23Z",
    finished_at: "2026-09-12T13:18:48Z",
    since: null,
    duration_seconds: 25.19,
    records_fetched: { customer: 800, order: 5021, operation: 22940 },
    records_upserted: { customer: 800, order: 5021, operation: 22940 },
    issues_count: 21,
    issue_counts: { duplicate_id: 21 },
    issues: [],
    issues_truncated: true,
    reconciliation: {
      status: "warning",
      summary: "order: connector 5021 vs stored 5101",
      deltas: [
        { entity: "customer", connector_count: 800, stored_count: 800, delta: 0, delta_pct: 0, status: "ok" },
        { entity: "order", connector_count: 5021, stored_count: 5101, delta: 80, delta_pct: 1.6, status: "warning" },
      ],
    },
    stored_totals: { order: 5101 },
    pruned_orders: 0,
    orders_closed_missing: 0,
    watermark_source: "none",
    triggered_by: "cli",
    error_message: null,
    ...overrides,
  };
}

export const syncStatus: SyncStatusResponse = {
  connector: "mock",
  health: { connector_name: "mock", healthy: true, checked_at: "2026-09-12T13:35:04Z", latency_ms: 0.01, message: "mock connector ready", details: { orders: 301, machines: 16 } },
  last_run: makeSyncRun(),
  last_completed: makeSyncRun(),
  watermark: "2026-09-12T13:18:23Z",
  runs_total: 3,
  checked_at: "2026-09-12T13:35:04Z",
};

export const capabilityReport: CapabilityReportResponse = {
  connector_name: "mock",
  supports_incremental: true,
  supports_webhooks: false,
  coverage_pct: 96.5,
  can_schedule: false,
  missing_required: ["operation.cycle_minutes_per_unit"],
  assessments: [
    { entity: "customer", field: "customer_id", importance: "required", status: "Available", used_by: ["priority"], impact_if_missing: "Orders cannot be linked to customers", recommendation: "Expose the customer master key" },
    { entity: "operation", field: "cycle_minutes_per_unit", importance: "required", status: "Missing", used_by: ["scheduling"], impact_if_missing: "Run times cannot be computed", recommendation: "Map the routing cycle time column" },
    { entity: "customer", field: "customer_tier", importance: "recommended", status: "Available", used_by: ["priority"], impact_if_missing: "Customer importance falls back to standard", recommendation: "Derive a tier from revenue band" },
  ],
};

export const machineDetail: MachineDetail = {
  machine: {
    machine_id: "MC-LATHE-01",
    machine_name: "TL-250 #1",
    machine_type: "CNC lathe",
    process_type: "cnc_machining",
    machine_group: "LATHE",
    location: "Bay 2",
    status: "running",
    calendar_id: "CAL-2SHIFT",
    efficiency: 1.05,
    utilization: 0.72,
    capacity_hours_per_day: 16,
    compatible_materials: ["MAT-AL6061", "MAT-SS304"],
    compatible_processes: ["cnc_machining", "deburring"],
    tooling_configuration: ["TL-GRV-3-A"],
    current_material_id: "MAT-AL6061",
    current_setup_family: "F-A",
    available_from: null,
    preferred_rank: 1,
  },
  load: { version_number: 4, status: "draft", utilization_pct: 81.5, scheduled_hours: 120.5, setup_hours: 9.25, scheduled_entries: 37, next_free: "2026-09-20T08:00:00Z", horizon_start: "2026-09-12T00:00:00Z", horizon_end: "2026-09-26T00:00:00Z" },
  calendar: { machine_id: "MC-LATHE-01", calendar_id: "CAL-2SHIFT", name: "Two-shift weekday calendar", timezone: "Asia/Kolkata", shifts: [{ name: "Shift A", start: "06:00", end: "14:00", weekdays: [0, 1, 2, 3, 4], crosses_midnight: false }], holidays: 7, extra_working_days: 0, overtime_windows: 0, downtime_windows: 1, available_from: null, summary: "Calendar 'Two-shift weekday calendar' (Asia/Kolkata): Shift A 06:00-14:00" },
  calendar_source: "machine",
  downtime: [{ kind: "planned", start: "2026-10-01T08:00:00Z", end: "2026-10-01T12:00:00Z", reason: "Spindle service" }],
  locks: [],
  upcoming: [],
};
