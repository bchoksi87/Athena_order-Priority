/** String enums of the domain (backend/app/domain/enums.py, mirrored in the OpenAPI document). */

// ---------------------------------------------------------------- enums

export type ProcessType =
  | "cnc_machining"
  | "additive_3d_printing"
  | "support_removal"
  | "deburring"
  | "finishing"
  | "heat_treatment"
  | "surface_treatment"
  | "inspection"
  | "assembly"
  | "packing"
  | "other";

export type OrderStatus =
  | "new"
  | "released"
  | "planned"
  | "scheduled"
  | "material_waiting"
  | "tooling_waiting"
  | "in_production"
  | "partially_completed"
  | "quality_inspection"
  | "rework"
  | "completed"
  | "packed"
  | "shipped"
  | "on_hold"
  | "cancelled";

export type OperationStatus =
  | "pending"
  | "ready"
  | "scheduled"
  | "in_progress"
  | "completed"
  | "on_hold"
  | "rework"
  | "cancelled";

export type ReadinessState =
  | "ready"
  | "waiting_material"
  | "waiting_tooling"
  | "waiting_approval"
  | "waiting_previous_operation"
  | "machine_unavailable"
  | "quality_hold"
  | "on_hold"
  | "other_constraint";

export type MachineStatus = "available" | "running" | "down" | "maintenance" | "offline";
export type MaterialStatus = "available" | "partial" | "unavailable" | "on_order" | "unknown";
export type QualityStatus = "none" | "pending" | "passed" | "failed" | "rework" | "hold";
export type ShippingStatus = "not_shipped" | "partial" | "shipped";
export type CustomerTier = "strategic" | "key" | "standard" | "low";
export type PaymentRisk = "low" | "medium" | "high" | "unknown";
export type RiskLevel = "low" | "medium" | "high" | "critical";
export type ScheduleStatus = "draft" | "approved" | "published" | "superseded" | "rejected";
export type LockType = "order" | "machine" | "sequence" | "time_slot";

export type OverrideType =
  | "increase_priority"
  | "decrease_priority"
  | "set_priority"
  | "force_next"
  | "hold_order"
  | "release_hold"
  | "move_order"
  | "lock_machine_assignment";

/** `type` field of POST /orders/{id}/override-priority. */
export type OverridePriorityKind = "increase" | "decrease" | "set";

export type AlertSeverity = "info" | "warning" | "high" | "critical";

export type AlertType =
  | "order_likely_late"
  | "order_overdue"
  | "machine_downtime"
  | "material_shortage"
  | "tool_shortage"
  | "capacity_overload"
  | "bottleneck"
  | "sla_breach_risk"
  | "production_behind_schedule"
  | "schedule_disruption"
  | "starvation"
  | "data_quality";

export type Role = "admin" | "production_manager" | "planner" | "supervisor" | "operator" | "executive";
export type WritebackMode = "read_only" | "approval" | "writeback" | "controlled_auto";
export type DataQualitySeverity = "blocking" | "warning" | "info";

export type DataQualityCode =
  | "missing_due_date"
  | "invalid_date"
  | "missing_cycle_time"
  | "missing_setup_time"
  | "missing_machine_assignment"
  | "missing_material"
  | "negative_quantity"
  | "duplicate_order"
  | "incorrect_status"
  | "impossible_production_time"
  | "missing_customer"
  | "conflicting_machine_capability"
  | "invalid_routing"
  | "missing_operations"
  | "unknown_reference";

export type SyncMode = "full" | "incremental";

export type FactorKind = "bonus" | "penalty";
export type AdjustmentKind = "aging" | "fairness" | "expedite" | "override" | "customer_rule" | "erp_priority";

/** Priority factor keys accepted by FactorWeight.key (app/domain/config.py FactorKey). */
export type FactorKey =
  | "due_date_urgency"
  | "sla_risk"
  | "customer_importance"
  | "order_value"
  | "margin"
  | "delay_penalty"
  | "production_readiness"
  | "machine_availability"
  | "setup_efficiency"
  | "batching_affinity"
  | "downstream_impact";

/** Kind of an explanation line (ExplanationLine.kind). */
export type ExplanationLineKind = "factor" | "adjustment" | "cap";
