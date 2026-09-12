/** Fixtures shaped exactly like the schedule / analytics / simulation API responses (backend/app/api/schemas). */
import type {
  BottleneckReport,
  CapacityReport,
  DaySchedule,
  ExecutiveKpis,
  GanttBlock,
  GanttView,
  KpiReport,
  MachineRow,
  MetricPair,
  OtdReport,
  ReplanOutcome,
  ScheduleComparison,
  SchedulePlan,
  ScheduleQualityReport,
  ScheduleVersionResponse,
  SimulationResponse,
} from "@/api/types";

import { makeEntry } from "./fixtures";

export const scheduleMetrics: ScheduleVersionResponse["metrics"] = {
  scheduled_orders: 2697,
  unscheduled_orders: 1156,
  scheduled_operations: 11008,
  on_time_orders: 176,
  late_orders: 2521,
  orders_at_risk: 20,
  on_time_pct: 6.5,
  avg_lateness_hours: 332.3,
  max_lateness_hours: 900,
  total_tardiness_hours: 12000,
  total_run_hours: 6000,
  total_setup_hours: 2984.8,
  setup_count: 9000,
  makespan_hours: 336,
  overall_utilization_pct: 69.1,
  machine_utilization_pct: {},
  revenue_scheduled: 500_000_000,
  revenue_at_risk: 233_825_175.83,
  margin_at_risk: 63_116_968.33,
  wip_orders_avg: 120,
};

export function makeVersion(overrides: Partial<ScheduleVersionResponse> = {}): ScheduleVersionResponse {
  return {
    schedule_version_id: "sv_4",
    version_number: 4,
    status: "draft",
    label: "e2e",
    algorithm: "rule_based",
    algorithm_version: "1.0.0",
    profile_id: "PriorityProfile-A",
    profile_version: 1,
    config_version: 1,
    generated_by: "planner",
    generated_at: "2026-09-12T13:19:12Z",
    horizon_start: "2026-09-12T13:19:00Z",
    horizon_end: "2026-09-26T13:19:00Z",
    run_id: "run_4",
    input_snapshot_id: "snap_4",
    approved_by: null,
    approved_at: null,
    published_by: null,
    published_at: null,
    superseded_at: null,
    entry_count: 11008,
    quality_score: 34.4,
    quality_summary: "On-time 7% · Utilization 69% · Setup efficiency 74% · Avg lateness 332.3 h · At risk 20",
    metrics: scheduleMetrics,
    trigger: "manual",
    previous_version: 3,
    notes: null,
    ...overrides,
  };
}

export const schedulePlan: SchedulePlan = {
  status: "draft",
  version: makeVersion(),
  entries: { items: [makeEntry()], total: 11008, page: 1, page_size: 1, pages: 11008, has_more: true },
};

export const executiveKpis: ExecutiveKpis = {
  total_open_orders: 3853,
  total_pending_quantity: 59365,
  orders_due_today: 4,
  orders_due_tomorrow: 118,
  orders_due_this_week: 118,
  overdue_orders: 373,
  at_risk_orders: 3515,
  on_time_delivery_pct: 84.3,
  expected_on_time_delivery_pct: 6.5,
  machine_utilization_pct: 69.1,
  capacity_utilization_pct: 321.5,
  revenue_at_risk: 238_852_790.9,
  margin_at_risk: 63_116_968.33,
  blocked_by_material: 370,
  blocked_by_tooling: 192,
  blocked_by_machine: 74,
  waiting_for_approval: 74,
  blocked_total: 1117,
  scheduled_orders: 2697,
  unscheduled_orders: 1156,
  as_of: "2026-09-12T13:19:10Z",
};

export const kpiReport: KpiReport = { kpis: executiveKpis, source: "stored", as_of: "2026-09-12T13:19:10Z", version_number: 4, version_status: "draft" };

export const otdReport: OtdReport = {
  as_of: "2026-09-12T14:06:31Z",
  window_days: 30,
  historical_pct: 28.4,
  projected_pct: 6.5,
  historical: { key: "historical", total: 1219, on_time: 346, late: 873, pct: 28.4 },
  projected: { key: "projected", total: 2697, on_time: 176, late: 2521, pct: 6.5 },
  historical_by_tier: { strategic: { key: "strategic", total: 133, on_time: 40, late: 93, pct: 30.1 }, key: { key: "key", total: 344, on_time: 100, late: 244, pct: 29.1 } },
  projected_by_tier: { strategic: { key: "strategic", total: 200, on_time: 20, late: 180, pct: 10 } },
  historical_by_process: { "CNC Machining": { key: "CNC Machining", total: 700, on_time: 200, late: 500, pct: 28.6 } },
  projected_by_process: { "CNC Machining": { key: "CNC Machining", total: 1757, on_time: 89, late: 1668, pct: 5.1 } },
  trend: [
    { day: "2026-09-10", historical: { key: "2026-09-10", total: 694, on_time: 223, late: 471, pct: 32.1 }, projected: null },
    { day: "2026-09-11", historical: { key: "2026-09-11", total: 525, on_time: 123, late: 402, pct: 23.4 }, projected: null },
    { day: "2026-09-14", historical: null, projected: { key: "2026-09-14", total: 300, on_time: 30, late: 270, pct: 10 } },
  ],
  notes: { delivered_without_completion_date: 8, delivered_without_due_date: 0, outside_window: 0, scheduled_without_due_date: 0 },
  summary: "On-time delivery: 28% over the last 30 days (346/1219 delivered orders); expected 7% for scheduled open orders (176/2697)",
};

export const bottleneckReport: BottleneckReport = {
  current: {
    resource_type: "process",
    resource_id: "additive_3d_printing",
    resource_name: "Additive 3D Printing",
    utilization_pct: 529.9,
    orders_waiting: 772,
    capacity_shortfall_hours: 9955.5,
    revenue_at_risk: 70_690_341.43,
    margin_at_risk: 18_277_877.69,
    severity: "critical",
    recommendation: "Add 9956 machine hours on Additive 3D Printing this week (e.g. extra shift or overtime) to clear 772 waiting order(s)",
  },
  items: [
    {
      resource_type: "process",
      resource_id: "additive_3d_printing",
      resource_name: "Additive 3D Printing",
      utilization_pct: 529.9,
      orders_waiting: 772,
      capacity_shortfall_hours: 9955.5,
      revenue_at_risk: 70_690_341.43,
      margin_at_risk: 18_277_877.69,
      severity: "critical",
      recommendation: "Add 9956 machine hours on Additive 3D Printing this week (e.g. extra shift or overtime) to clear 772 waiting order(s)",
    },
    {
      resource_type: "machine_group",
      resource_id: "CNC5",
      resource_name: "CNC 5-axis",
      utilization_pct: 96,
      orders_waiting: 73,
      capacity_shortfall_hours: 41,
      revenue_at_risk: 8_535_733,
      margin_at_risk: 2_100_000,
      severity: "high",
      recommendation: "Add 41 machine hours on CNC 5-axis this week (e.g. Saturday shift) to clear 73 waiting order(s)",
    },
  ],
  source: "stored",
  as_of: "2026-09-12T13:19:08Z",
  version_number: 4,
  version_status: "draft",
};

export const comparisonMetrics: Record<string, MetricPair> = {
  on_time_pct: { label: "On-time delivery", before: 87, after: 94, delta: 7 },
  avg_lateness_hours: { label: "Average lateness", before: 8.2, after: 2.4, delta: -5.8 },
  overall_utilization_pct: { label: "Machine utilization", before: 76, after: 87, delta: 11 },
  total_setup_hours: { label: "Setup hours", before: 126, after: 101, delta: -25 },
  late_orders: { label: "Late orders", before: 131, after: 20, delta: -111 },
};

export const scheduleComparison: ScheduleComparison = {
  a: makeVersion({ version_number: 4, status: "published", schedule_version_id: "sv_4" }),
  b: makeVersion({ version_number: 5, status: "draft", schedule_version_id: "sv_5", trigger: "replan:manual", previous_version: 4 }),
  metrics: comparisonMetrics,
  quality: { label: "Schedule quality", before: 81, after: 89, delta: 8 },
  changes: { moved_entries: 12, added_entries: 3, removed_entries: 1, unchanged_entries: 900, changed_orders: ["SO-1", "SO-2"], frozen_violations: 0, entries: [] },
  moved_orders: 2,
  summary: "Schedule quality: 81 → 89; On-time delivery: 87% → 94%; Average lateness: 8.2h → 2.4h; Machine utilization: 76% → 87%; Setup hours: 126 → 101",
};

export const scheduleQualityReport: ScheduleQualityReport = {
  version: makeVersion(),
  quality: {
    score: 34.4,
    components: { on_time_delivery: 6.5, lateness: 2.4, utilization: 69.1, setup_efficiency: 73.5, at_risk: 99.3 },
    weights: { on_time_delivery: 0.4, lateness: 0.2, utilization: 0.15, setup_efficiency: 0.15, at_risk: 0.1 },
    summary: "On-time 7% · Utilization 69% · Setup efficiency 74% · Avg lateness 332.3 h · At risk 20",
  },
  metrics: scheduleMetrics,
  newest_draft: null,
  comparison: null,
};

export const replanOutcome: ReplanOutcome = {
  trigger: "manual",
  evaluated_at: "2026-09-12T14:30:00Z",
  triggered: true,
  action: "awaiting_approval",
  reason: "Candidate v5 improves quality by 9.9% (threshold 5%); approval required by configuration",
  event_types: ["machine_down"],
  events: [{ type: "machine_down", entity_type: "machine", entity_id: "MC-CNC5-01", occurred_at: "2026-09-12T14:00:00Z", message: "MC-CNC5-01 reported down", order_id: null, machine_id: "MC-CNC5-01", details: {} }],
  decision: { should_replan: true, reason: "improvement 9.9% above threshold", improvement_pct: 9.9, changed_entries: 16, frozen_violations: 0, requires_approval: true, triggers: ["machine_down"] },
  active_version: scheduleComparison.a,
  candidate_version: scheduleComparison.b,
  comparison: scheduleComparison,
  alert_id: "alt_replan",
};

export const capacityReport: CapacityReport = {
  dimension: "process",
  period: "week",
  horizon_start: "2026-09-12T14:06:26Z",
  horizon_end: "2026-09-26T14:06:26Z",
  rows: [
    { key: "cnc_3axis", period_start: "2026-09-12T14:06:26Z", period_end: "2026-09-19T14:06:26Z", required_hours: 410, available_hours: 450, gap_hours: 40, utilization_pct: 91.1 },
    { key: "cnc_3axis", period_start: "2026-09-19T14:06:26Z", period_end: "2026-09-26T14:06:26Z", required_hours: 410, available_hours: 450, gap_hours: 40, utilization_pct: 91.1 },
    { key: "cnc_5axis", period_start: "2026-09-12T14:06:26Z", period_end: "2026-09-19T14:06:26Z", required_hours: 470, available_hours: 390, gap_hours: -80, utilization_pct: 120.5 },
    { key: "cnc_5axis", period_start: "2026-09-19T14:06:26Z", period_end: "2026-09-26T14:06:26Z", required_hours: 470, available_hours: 390, gap_hours: -80, utilization_pct: 120.5 },
  ],
  totals: [
    { key: "cnc_3axis", required_hours: 820, available_hours: 900, gap_hours: 80, utilization_pct: 91.1, scheduled_hours: 700, estimated_hours: 120, shortfall_hours: 0 },
    { key: "cnc_5axis", required_hours: 940, available_hours: 780, gap_hours: -160, utilization_pct: 120.5, scheduled_hours: 780, estimated_hours: 160, shortfall_hours: 160 },
    { key: "sla", required_hours: 410, available_hours: 600, gap_hours: 190, utilization_pct: 68.3, scheduled_hours: 400, estimated_hours: 10, shortfall_hours: 0 },
  ],
  total_required_hours: 2170,
  total_available_hours: 2280,
  gap_hours: 110,
  utilization_pct: 95.2,
  unallocated_hours: 12.5,
  unallocated_operations: 3,
  estimated_hours: 290,
  scheduled_hours: 1880,
  notes: ["570 pending operation(s) of orders withheld by data quality carry no demand"],
};

export function makeBlock(overrides: Parameters<typeof makeEntry>[0] = {}, extra: Partial<GanttBlock> = {}): GanttBlock {
  const entry = makeEntry(overrides);
  return {
    entry,
    order_id: entry.order_id,
    customer_id: entry.customer_id,
    customer_name: "Krypton Tooling Industries",
    part_id: "P-COU-5072",
    part_name: "Coupling Rev C",
    order_status: "scheduled",
    setup_start: entry.setup_start,
    start: entry.start,
    end: entry.end,
    locked: entry.locked,
    late: (entry.expected_lateness_hours ?? 0) > 0,
    ...extra,
  };
}

export function makeMachineRow(overrides: Partial<MachineRow> = {}): MachineRow {
  return {
    machine_id: "CNC-01",
    machine_name: "5-axis #1",
    machine_group: "CNC5",
    process_type: "cnc_machining",
    status: "available",
    busy_hours: 6.5,
    blocks: [
      makeBlock({ entry_id: "e1", order_id: "1045", setup_start: "2026-09-11T08:00:00Z", start: "2026-09-11T08:30:00Z", end: "2026-09-11T10:30:00Z", setup_minutes: 30, run_minutes: 120 }),
      makeBlock({ entry_id: "e2", order_id: "1052", setup_start: "2026-09-11T10:30:00Z", start: "2026-09-11T10:30:00Z", end: "2026-09-11T12:00:00Z", setup_minutes: 0, run_minutes: 90, sequence_on_machine: 2 }),
      makeBlock({ entry_id: "e3", order_id: "1078", setup_start: "2026-09-11T12:30:00Z", start: "2026-09-11T12:30:00Z", end: "2026-09-11T15:00:00Z", setup_minutes: 0, run_minutes: 150, sequence_on_machine: 3, expected_lateness_hours: 4, locked: true }),
    ],
    downtime: [{ start: "2026-09-11T16:00:00Z", end: "2026-09-11T18:00:00Z", reason: "preventive maintenance" }],
    locks: [],
    ...overrides,
  };
}

export const daySchedule: DaySchedule = {
  date: "2026-09-11",
  timezone: "Asia/Kolkata",
  start: "2026-09-10T18:30:00Z",
  end: "2026-09-11T18:30:00Z",
  version: makeVersion(),
  entries: 3,
  machines: [makeMachineRow(), makeMachineRow({ machine_id: "CNC-02", machine_name: "5-axis #2", blocks: [], busy_hours: 0, downtime: [] })],
};

export const ganttView: GanttView = {
  version: makeVersion(),
  axis_start: "2026-09-11T00:00:00Z",
  axis_end: "2026-09-18T00:00:00Z",
  machine_group: null,
  process_type: null,
  entries: 3,
  rows: daySchedule.machines,
};

export const simulationResponse: SimulationResponse = {
  simulation_id: "sim_1",
  generated_at: "2026-09-12T14:20:00Z",
  note: null,
  baseline_version: 4,
  scenarios: [{ kind: "machine_down", label: null, description: "MC-CNC5-01 down for 8.0 h from 2026-09-12 14:20", notes: ["12 entries displaced"], affected_order_ids: ["SO-1", "SO-2"], affected_machine_ids: ["MC-CNC5-01"], parameters: { machine_id: "MC-CNC5-01", duration_hours: 8 } }],
  baseline: { algorithm: "rule_based", algorithm_version: "1.0.0", horizon_start: "2026-09-12T14:20:00Z", horizon_end: "2026-09-26T14:20:00Z", entries: 11008, unscheduled: 1156, metrics: scheduleMetrics, quality: { score: 34.4, components: {}, weights: {}, summary: "" }, warnings: [] },
  scenario: { algorithm: "rule_based", algorithm_version: "1.0.0", horizon_start: "2026-09-12T14:20:00Z", horizon_end: "2026-09-26T14:20:00Z", entries: 11000, unscheduled: 1160, metrics: { ...scheduleMetrics, late_orders: 2540, on_time_pct: 6.1 }, quality: { score: 33.9, components: {}, weights: {}, summary: "" }, warnings: [] },
  diff: {
    orders_affected: 37,
    orders_moved_machine: 12,
    orders_resequenced: 25,
    orders_newly_late: 19,
    orders_newly_on_time: 0,
    late_orders_before: 2521,
    late_orders_after: 2540,
    on_time_pct_before: 6.5,
    on_time_pct_after: 6.1,
    avg_lateness_before: 332.3,
    avg_lateness_after: 336.0,
    utilization_before: 69.1,
    utilization_after: 68.4,
    setup_hours_before: 2984.8,
    setup_hours_after: 2990.1,
    revenue_at_risk_before: 233_825_175.83,
    revenue_at_risk_after: 236_100_000,
    margin_at_risk_before: 63_116_968.33,
    margin_at_risk_after: 63_900_000,
    additional_overtime_hours: 0,
    bottlenecks_before: ["Additive 3D Printing", "CNC Machining"],
    bottlenecks_after: ["Additive 3D Printing", "CNC Machining", "CNC 5-axis"],
    summary: "37 orders affected: 19 newly late; on-time 6.5% → 6.1%",
  },
  comparison: {
    on_time_pct: { label: "On-time delivery", before: 6.5, after: 6.1, delta: -0.4 },
    late_orders: { label: "Late orders", before: 2521, after: 2540, delta: 19 },
  },
  comparison_summary: "On-time delivery: 7% → 6%; Late orders: 2521 → 2540",
  affected_orders: [
    { order_id: "SO-1", customer_id: "CUST-0062", customer_name: "Krypton Tooling Industries", part_id: "P-1", part_name: "Coupling Rev C", due_date: "2026-09-13T12:00:00Z", order_value: 18034.65, delta: { order_id: "SO-1", baseline_completion: "2026-09-12T20:00:00Z", scenario_completion: "2026-09-13T20:00:00Z", delta_hours: 24, baseline_late: false, scenario_late: true, baseline_machine_id: "MC-CNC5-01", scenario_machine_id: "MC-CNC5-02", baseline_score: 91, scenario_score: 91 } },
  ],
  top_n: 20,
  summary: "If MC-CNC5-01 is down for 8 h, 37 orders are affected and 19 become late; on-time delivery drops from 6.5% to 6.1%.",
};
