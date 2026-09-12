/**
 * UI constants: status tones, role labels and factor names. These mirror the
 * backend enums (app/domain/enums.py) and are presentation-only — business
 * rules stay in the backend configuration.
 */
import type {
  AlertSeverity,
  AlertType,
  DataQualityCode,
  DataQualitySeverity,
  LockType,
  MachineStatus,
  OrderStatus,
  OverrideType,
  ProcessType,
  ReadinessState,
  RiskLevel,
  Role,
  ScheduleStatus,
} from "@/api/types";

/** Semantic tone used by StatusPill / KpiCard / Gantt colouring. */
export type Tone = "ready" | "running" | "blocked" | "late" | "at-risk" | "hold" | "neutral" | "done";

export const ROLE_RANK: Record<Role, number> = {
  executive: 0,
  operator: 1,
  supervisor: 2,
  planner: 3,
  production_manager: 4,
  admin: 5,
};

export const ROLE_LABELS: Record<Role, string> = {
  admin: "Administrator",
  production_manager: "Production Manager",
  planner: "Planner",
  supervisor: "Supervisor",
  operator: "Operator",
  executive: "Executive",
};

export const ORDER_STATUS_TONE: Record<OrderStatus, Tone> = {
  new: "neutral",
  released: "ready",
  planned: "ready",
  scheduled: "running",
  material_waiting: "blocked",
  tooling_waiting: "blocked",
  in_production: "running",
  partially_completed: "running",
  quality_inspection: "hold",
  rework: "at-risk",
  completed: "done",
  packed: "done",
  shipped: "done",
  on_hold: "hold",
  cancelled: "neutral",
};

export const READINESS_TONE: Record<ReadinessState, Tone> = {
  ready: "ready",
  waiting_material: "blocked",
  waiting_tooling: "blocked",
  waiting_approval: "hold",
  waiting_previous_operation: "at-risk",
  machine_unavailable: "blocked",
  quality_hold: "hold",
  on_hold: "hold",
  other_constraint: "blocked",
};

export const READINESS_LABELS: Record<ReadinessState, string> = {
  ready: "Ready",
  waiting_material: "Waiting material",
  waiting_tooling: "Waiting tooling",
  waiting_approval: "Waiting approval",
  waiting_previous_operation: "Waiting previous op",
  machine_unavailable: "Machine unavailable",
  quality_hold: "Quality hold",
  on_hold: "On hold",
  other_constraint: "Other constraint",
};

export const RISK_TONE: Record<RiskLevel, Tone> = {
  low: "ready",
  medium: "at-risk",
  high: "blocked",
  critical: "late",
};

export const MACHINE_STATUS_TONE: Record<MachineStatus, Tone> = {
  available: "ready",
  running: "running",
  down: "late",
  maintenance: "at-risk",
  offline: "neutral",
};

export const ALERT_SEVERITY_TONE: Record<AlertSeverity, Tone> = {
  info: "running",
  warning: "at-risk",
  high: "blocked",
  critical: "late",
};

export const DQ_SEVERITY_TONE: Record<DataQualitySeverity, Tone> = {
  blocking: "late",
  warning: "at-risk",
  info: "neutral",
};

export const SCHEDULE_STATUS_TONE: Record<ScheduleStatus, Tone> = {
  draft: "neutral",
  approved: "at-risk",
  published: "ready",
  superseded: "done",
  rejected: "late",
};

/** Canonical priority factor keys and display names (app/domain/config.py). */
export const FACTOR_NAMES: Record<string, string> = {
  due_date_urgency: "Due Date Urgency",
  sla_risk: "SLA Risk",
  customer_importance: "Customer Importance",
  order_value: "Order Value",
  margin: "Contribution Margin",
  delay_penalty: "Delay Penalty",
  production_readiness: "Production Readiness",
  machine_availability: "Machine Availability",
  setup_efficiency: "Setup Efficiency",
  batching_affinity: "Batching Affinity",
  downstream_impact: "Downstream Impact",
};

export const FACTOR_KEYS: readonly string[] = Object.keys(FACTOR_NAMES);

export const ADJUSTMENT_LABELS: Record<string, string> = {
  aging: "Age",
  fairness: "Fairness",
  expedite: "Expedite",
  override: "Override",
  customer_rule: "Customer rule",
  erp_priority: "ERP priority",
};

export const WRITEBACK_LABELS: Record<string, string> = {
  read_only: "READ ONLY",
  approval: "APPROVAL",
  writeback: "WRITEBACK",
  controlled_auto: "AUTO",
};

/** Turns a snake_case enum value into a human label ("waiting_material" -> "Waiting material"). */
export function humanize(value: string | null | undefined): string {
  if (!value) return "—";
  const text = value.replace(/_/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Score thresholds for the ScoreBar tone (presentation only). */
export const SCORE_TONE_THRESHOLDS = { critical: 85, high: 65, medium: 40 } as const;

export function scoreTone(score: number): Tone {
  if (score >= SCORE_TONE_THRESHOLDS.critical) return "late";
  if (score >= SCORE_TONE_THRESHOLDS.high) return "blocked";
  if (score >= SCORE_TONE_THRESHOLDS.medium) return "at-risk";
  return "ready";
}

export const STORAGE_KEYS = {
  auth: "ppse.auth",
  theme: "ppse.theme",
  rememberedUsername: "ppse.login.username",
  queueColumns: "ppse.queue.columns",
  queuePageSize: "ppse.queue.pageSize",
} as const;

export const ROLES: readonly Role[] = ["admin", "production_manager", "planner", "supervisor", "operator", "executive"];

export const RISK_LEVELS: readonly RiskLevel[] = ["critical", "high", "medium", "low"];

export const RISK_RANK: Record<RiskLevel, number> = { critical: 4, high: 3, medium: 2, low: 1 };

export const ORDER_STATUSES: readonly OrderStatus[] = [
  "new",
  "released",
  "planned",
  "scheduled",
  "material_waiting",
  "tooling_waiting",
  "in_production",
  "partially_completed",
  "quality_inspection",
  "rework",
  "completed",
  "packed",
  "shipped",
  "on_hold",
  "cancelled",
];

export const PROCESS_TYPES: readonly ProcessType[] = [
  "cnc_machining",
  "additive_3d_printing",
  "support_removal",
  "deburring",
  "finishing",
  "heat_treatment",
  "surface_treatment",
  "inspection",
  "assembly",
  "packing",
  "other",
];

export const ALERT_TYPES: readonly AlertType[] = [
  "order_likely_late",
  "order_overdue",
  "machine_downtime",
  "material_shortage",
  "tool_shortage",
  "capacity_overload",
  "bottleneck",
  "sla_breach_risk",
  "production_behind_schedule",
  "schedule_disruption",
  "starvation",
  "data_quality",
];

export const ALERT_SEVERITIES: readonly AlertSeverity[] = ["critical", "high", "warning", "info"];

export const ALERT_SEVERITY_RANK: Record<AlertSeverity, number> = { critical: 4, high: 3, warning: 2, info: 1 };

export const DQ_CODES: readonly DataQualityCode[] = [
  "missing_due_date",
  "invalid_date",
  "missing_cycle_time",
  "missing_setup_time",
  "missing_machine_assignment",
  "missing_material",
  "negative_quantity",
  "duplicate_order",
  "incorrect_status",
  "impossible_production_time",
  "missing_customer",
  "conflicting_machine_capability",
  "invalid_routing",
  "missing_operations",
  "unknown_reference",
];

export const DQ_SEVERITIES: readonly DataQualitySeverity[] = ["blocking", "warning", "info"];

export const DQ_SEVERITY_RANK: Record<DataQualitySeverity, number> = { blocking: 3, warning: 2, info: 1 };

export const OVERRIDE_TYPE_LABELS: Record<OverrideType, string> = {
  increase_priority: "Increase priority",
  decrease_priority: "Decrease priority",
  set_priority: "Set priority",
  force_next: "Force next",
  hold_order: "Hold",
  release_hold: "Release hold",
  move_order: "Move to machine",
  lock_machine_assignment: "Lock machine",
};

export const OVERRIDE_TYPE_TONE: Record<OverrideType, Tone> = {
  increase_priority: "ready",
  decrease_priority: "neutral",
  set_priority: "running",
  force_next: "hold",
  hold_order: "hold",
  release_hold: "ready",
  move_order: "running",
  lock_machine_assignment: "at-risk",
};

export const LOCK_TYPE_LABELS: Record<LockType, string> = {
  order: "Order lock",
  machine: "Machine lock",
  sequence: "Sequence lock",
  time_slot: "Time-slot lock",
};

/** Audit entity types the backend writes (best-effort list for the filter select). */
export const AUDIT_ENTITY_TYPES: readonly string[] = [
  "order",
  "override",
  "expedite",
  "lock",
  "schedule",
  "schedule_version",
  "config",
  "priority_profile",
  "scheduling_config",
  "customer_rule",
  "alert",
  "user",
  "data_quality",
];
