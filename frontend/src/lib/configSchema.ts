/**
 * Field descriptors for the configuration editors, derived from the OpenAPI
 * component schemas (PriorityProfile sub-configs, SchedulingConfig, ReplanningConfig,
 * AlertConfig, DataQualityConfig). Labels/help text paraphrase the spec and
 * app/domain/config.py comments; types, defaults and bounds come from the schema.
 * Business defaults live in the backend — nothing here is used as a value.
 */

export type FieldSchema =
  | { key: string; label: string; type: "number" | "integer"; help?: string; min?: number; max?: number; step?: number; nullable?: boolean; unit?: string }
  | { key: string; label: string; type: "boolean"; help?: string }
  | { key: string; label: string; type: "enum"; options: readonly string[]; help?: string }
  | { key: string; label: string; type: "map-number"; help?: string; keyLabel?: string; valueLabel?: string; fixedKeys?: readonly string[] }
  | { key: string; label: string; type: "string-list"; help?: string; options?: readonly string[] };

export interface SectionSchema {
  key: string;
  title: string;
  description?: string;
  fields: FieldSchema[];
}

const pts = "points";
const h = "h";

/** Sub-configurations of the priority profile, in the order the spec lists the factors. */
export const PRIORITY_SECTIONS: SectionSchema[] = [
  {
    key: "due_date",
    title: "Due date urgency",
    description: "Raw 0–100 score by time to due date; anchors are hours/days before the due date.",
    fields: [
      { key: "overdue_score", label: "Overdue score", type: "number", min: 0, max: 100, help: "Raw score once the due date has passed." },
      { key: "critical_hours", label: "Critical within", type: "number", min: 0, unit: h, help: "Orders due within this many hours score the critical value." },
      { key: "critical_score", label: "Critical score", type: "number", min: 0, max: 100 },
      { key: "high_days", label: "High within", type: "number", min: 0, unit: "days" },
      { key: "high_score", label: "High score", type: "number", min: 0, max: 100 },
      { key: "medium_days", label: "Medium within", type: "number", min: 0, unit: "days" },
      { key: "medium_score", label: "Medium score", type: "number", min: 0, max: 100 },
      { key: "low_days", label: "Low within", type: "number", min: 0, unit: "days" },
      { key: "low_score", label: "Low score", type: "number", min: 0, max: 100 },
      { key: "floor_score", label: "Floor score", type: "number", min: 0, max: 100, help: "Score for orders due later than the low anchor." },
      { key: "use_projected_lateness", label: "Use projected lateness", type: "boolean", help: "Treat an order whose projected completion is already late as overdue." },
    ],
  },
  {
    key: "customer",
    title: "Customer importance",
    description: "Tier score plus strategic flag, escalation level, revenue and profitability.",
    fields: [
      { key: "tier_scores", label: "Tier scores", type: "map-number", fixedKeys: ["strategic", "key", "standard", "low"], keyLabel: "Tier", valueLabel: "Score" },
      { key: "strategic_flag_bonus", label: "Strategic flag bonus", type: "number", min: 0, unit: pts },
      { key: "escalation_points_per_level", label: "Escalation points / level", type: "number", min: 0, unit: pts },
      { key: "revenue_weight", label: "Revenue weight", type: "number", min: 0, max: 1, step: 0.05, help: "Share of the score driven by customer revenue rank (0–1)." },
      { key: "profitability_weight", label: "Profitability weight", type: "number", min: 0, max: 1, step: 0.05 },
      { key: "max_score", label: "Max score", type: "number", min: 0, max: 100 },
    ],
  },
  {
    key: "sla",
    title: "SLA risk",
    description: "Score by the fraction of the SLA window remaining (customer rule SLA overrides the ERP value).",
    fields: [
      { key: "default_sla_hours", label: "Default SLA", type: "number", min: 0, nullable: true, unit: h, help: "Applied when neither the order nor the customer has an SLA (blank = none)." },
      { key: "breach_score", label: "Breached score", type: "number", min: 0, max: 100 },
      { key: "imminent_ratio", label: "Imminent below", type: "number", min: 0, max: 1, step: 0.05, help: "Remaining share of the SLA window counted as imminent." },
      { key: "imminent_score", label: "Imminent score", type: "number", min: 0, max: 100 },
      { key: "watch_ratio", label: "Watch below", type: "number", min: 0, max: 1, step: 0.05 },
      { key: "watch_score", label: "Watch score", type: "number", min: 0, max: 100 },
      { key: "safe_score", label: "Safe score", type: "number", min: 0, max: 100 },
    ],
  },
  {
    key: "order_value",
    title: "Order value",
    description: "How order value (and margin) is scaled to 0–100 across the open book.",
    fields: [
      { key: "scaling", label: "Scaling", type: "enum", options: ["percentile", "log", "linear"], help: "percentile = rank among open orders; log/linear = against cap value." },
      { key: "cap_value", label: "Cap value", type: "number", min: 0, nullable: true, unit: "₹", help: "Value that scores 100 for log/linear scaling (blank = max open order)." },
      { key: "min_score", label: "Minimum score", type: "number", min: 0, max: 100 },
    ],
  },
  {
    key: "delay_penalty",
    title: "Delay penalty",
    description: "Contractual lateness penalty scaled by escalation level and strategic multiplier.",
    fields: [
      { key: "default_penalty_per_day_ratio", label: "Default penalty / day", type: "number", min: 0, step: 0.005, help: "Fraction of order value per late day when the ERP has no penalty." },
      { key: "escalation_multiplier_per_level", label: "Escalation multiplier / level", type: "number", min: 0, step: 0.05 },
      { key: "strategic_multiplier", label: "Strategic multiplier", type: "number", min: 0, step: 0.1 },
      { key: "reference_penalty", label: "Reference penalty", type: "number", min: 0, nullable: true, unit: "₹/day", help: "Penalty that scores 100 (blank = largest open penalty)." },
    ],
  },
  {
    key: "readiness",
    title: "Production readiness",
    description: "Raw score per readiness state — material, tooling, approval, previous operation, machine, hold.",
    fields: [
      { key: "ready_score", label: "Ready", type: "number", min: 0, max: 100 },
      { key: "material_partial_score", label: "Material partial", type: "number", min: 0, max: 100 },
      { key: "waiting_material_score", label: "Waiting material", type: "number", min: 0, max: 100 },
      { key: "waiting_tooling_score", label: "Waiting tooling", type: "number", min: 0, max: 100 },
      { key: "waiting_approval_score", label: "Waiting approval", type: "number", min: 0, max: 100 },
      { key: "waiting_previous_operation_score", label: "Waiting previous op", type: "number", min: 0, max: 100 },
      { key: "machine_unavailable_score", label: "Machine unavailable", type: "number", min: 0, max: 100 },
      { key: "hold_score", label: "On hold", type: "number", min: 0, max: 100 },
    ],
  },
  {
    key: "machine_availability",
    title: "Machine availability",
    description: "Whether an eligible machine is free now or soon; single-machine orders get a bonus.",
    fields: [
      { key: "available_now_score", label: "Available now", type: "number", min: 0, max: 100 },
      { key: "available_within_hours", label: "Soon means within", type: "number", min: 0, unit: h },
      { key: "available_soon_score", label: "Available soon", type: "number", min: 0, max: 100 },
      { key: "single_machine_bonus", label: "Single machine bonus", type: "number", min: 0, unit: pts },
      { key: "none_available_score", label: "None available", type: "number", min: 0, max: 100 },
    ],
  },
  {
    key: "setup",
    title: "Setup efficiency",
    description: "Reward jobs that continue the current setup family/material; penalise large changeovers.",
    fields: [
      { key: "same_setup_score", label: "Same setup", type: "number", min: 0, max: 100 },
      { key: "same_material_score", label: "Same material", type: "number", min: 0, max: 100 },
      { key: "changeover_score", label: "Changeover", type: "number", min: 0, max: 100 },
      { key: "unknown_score", label: "Unknown", type: "number", min: 0, max: 100 },
      { key: "large_setup_minutes", label: "Large setup from", type: "number", min: 0, unit: "min" },
      { key: "large_setup_penalty_score", label: "Large setup penalty", type: "number", min: 0, max: 100, help: "Raw penalty score applied above the large-setup threshold." },
    ],
  },
  {
    key: "aging",
    title: "Aging",
    description: "Waiting orders gain points per day after a grace period, capped (spec Phase 17).",
    fields: [
      { key: "enabled", label: "Enabled", type: "boolean" },
      { key: "start_after_days", label: "Start after", type: "number", min: 0, unit: "days" },
      { key: "points_per_day", label: "Points per day", type: "number", min: 0, step: 0.5, unit: pts },
      { key: "max_points", label: "Max points", type: "number", min: 0, unit: pts },
    ],
  },
  {
    key: "fairness",
    title: "Fairness / starvation",
    description: "Boost orders waiting beyond the limit; cap one customer's share of the top N.",
    fields: [
      { key: "enabled", label: "Enabled", type: "boolean" },
      { key: "max_wait_days", label: "Starvation after", type: "number", min: 0, unit: "days" },
      { key: "starvation_boost_points", label: "Starvation boost", type: "number", min: 0, unit: pts },
      { key: "max_top_n_share_per_customer", label: "Max top-N share / customer", type: "number", min: 0, max: 1, step: 0.05 },
      { key: "top_n", label: "Top N", type: "integer", min: 1 },
    ],
  },
  {
    key: "expedite",
    title: "Expedite",
    description: "Defaults and limits for manual expedites (spec Phase 16: temporary boost, reason required).",
    fields: [
      { key: "default_boost_points", label: "Default boost", type: "number", min: 0, unit: pts },
      { key: "max_boost_points", label: "Max boost", type: "number", min: 0, unit: pts },
      { key: "default_duration_hours", label: "Default duration", type: "number", min: 0, unit: h },
      { key: "max_duration_hours", label: "Max duration", type: "number", min: 0, unit: h },
    ],
  },
  {
    key: "risk",
    title: "Risk thresholds",
    description: "Slack (due date minus projected completion) below which an order is high/medium risk.",
    fields: [
      { key: "critical_lateness_hours", label: "Critical when late by", type: "number", min: 0, unit: h },
      { key: "high_slack_hours", label: "High risk slack", type: "number", min: 0, unit: h },
      { key: "medium_slack_hours", label: "Medium risk slack", type: "number", min: 0, unit: h },
    ],
  },
];

/** Top-level PriorityProfile fields edited outside the sub-config sections. */
export const PRIORITY_TOP_LEVEL: SectionSchema = {
  key: "profile",
  title: "ERP priority & caps",
  fields: [
    { key: "erp_priority_points", label: "ERP priority points", type: "map-number", keyLabel: "ERP code", valueLabel: "Points", help: "Adjustment points by ERP priority code (e.g. 1 → +15)." },
    { key: "blocked_order_cap", label: "Blocked order cap", type: "number", min: 0, max: 100, nullable: true, help: "Maximum score of a blocked order (blank = no cap)." },
  ],
};

export const SCHEDULING_SECTIONS: SectionSchema[] = [
  {
    key: "general",
    title: "Horizon & locking",
    description: "How far ahead to plan and how much of the near future stays frozen.",
    fields: [
      { key: "horizon_days", label: "Horizon", type: "integer", min: 1, unit: "days", help: "Scheduling horizon; work beyond it is left unscheduled." },
      { key: "lock_window_minutes", label: "Default lock window", type: "number", min: 0, unit: "min", help: "Manager 'lock the next N hours' default (spec Phase 10)." },
      { key: "at_risk_slack_hours", label: "At-risk slack", type: "number", min: 0, unit: h, help: "Completion within this many hours of the due date counts as at risk." },
      { key: "schedule_blocked_orders", label: "Schedule blocked orders", type: "boolean", help: "Place blocked orders after their blocker resolves instead of leaving them unscheduled." },
      { key: "max_orders_per_run", label: "Max orders per run", type: "integer", min: 1, nullable: true, help: "Blank = all open orders." },
    ],
  },
  {
    key: "stability",
    title: "Stability (replanning)",
    description: "Avoid churn: keep near-term entries fixed and replan only for a real improvement.",
    fields: [
      { key: "frozen_window_minutes", label: "Frozen window", type: "number", min: 0, unit: "min", help: "Entries starting within this window are not moved." },
      { key: "min_improvement_pct", label: "Min improvement to replan", type: "number", min: 0, step: 0.5, unit: "%", help: "Replan only if the quality score improves by this much." },
      { key: "max_moves_per_replan", label: "Max moves per replan", type: "integer", min: 0, nullable: true, help: "Blank = unlimited." },
    ],
  },
  {
    key: "setup",
    title: "Setup",
    description: "Changeover time model when the ERP setup time is missing or partially reusable.",
    fields: [
      { key: "same_family_setup_factor", label: "Same-family setup factor", type: "number", min: 0, max: 1, step: 0.1, help: "Fraction of setup needed when the previous job has the same setup family." },
      { key: "same_material_setup_factor", label: "Same-material setup factor", type: "number", min: 0, max: 1, step: 0.1 },
      { key: "default_setup_minutes", label: "Default setup", type: "number", min: 0, unit: "min", help: "Used when the ERP has no setup time (flagged by data quality)." },
      { key: "setup_penalty_cost_per_minute", label: "Setup penalty cost / min", type: "number", min: 0, step: 0.1 },
    ],
  },
  {
    key: "batching",
    title: "Batching",
    description: "Group similar jobs to save changeovers without delaying urgent work.",
    fields: [
      { key: "enabled", label: "Enabled", type: "boolean" },
      { key: "dimensions", label: "Dimensions", type: "string-list", options: ["material", "machine", "tool", "fixture", "process", "part_family", "customer", "surface_finish", "technology"], help: "Attributes that make two jobs batchable." },
      { key: "max_delay_hours", label: "Max delay to batch", type: "number", min: 0, step: 0.5, unit: h, help: "Never delay a job more than this to batch it." },
      { key: "min_priority_gap", label: "Min priority gap", type: "number", min: 0, unit: pts, help: "Only pull a job forward if within this score gap of the queue head." },
    ],
  },
  {
    key: "objectives",
    title: "Objectives",
    description: "Relative importance of the scheduling objectives (spec Phase 19); normalised at runtime.",
    fields: [
      { key: "on_time_delivery", label: "On-time delivery", type: "number", min: 0, max: 100 },
      { key: "total_tardiness", label: "Total tardiness", type: "number", min: 0, max: 100 },
      { key: "setup_time", label: "Setup time", type: "number", min: 0, max: 100 },
      { key: "machine_utilization", label: "Machine utilisation", type: "number", min: 0, max: 100 },
      { key: "contribution_margin", label: "Contribution margin", type: "number", min: 0, max: 100 },
      { key: "wip", label: "Work in progress", type: "number", min: 0, max: 100 },
    ],
  },
  {
    key: "overtime",
    title: "Overtime",
    fields: [
      { key: "allow_overtime", label: "Allow overtime", type: "boolean", help: "Let the scheduler extend shifts when capacity is short." },
      { key: "max_overtime_hours_per_day", label: "Max overtime / day", type: "number", min: 0, step: 0.5, unit: h },
      { key: "overtime_cost_per_hour", label: "Overtime cost / hour", type: "number", min: 0 },
    ],
  },
  {
    key: "machine_preference",
    title: "Machine preference",
    description: "Soft costs that steer machine choice (spec Phase 35).",
    fields: [
      { key: "preferred_machine_cost", label: "Preferred machine cost", type: "number", min: 0 },
      { key: "non_preferred_machine_cost_minutes", label: "Non-preferred machine cost", type: "number", min: 0, unit: "min", help: "Soft cost when not the ERP-preferred machine." },
      { key: "utilization_balance_cost_per_pct", label: "Utilisation balance cost / %", type: "number", min: 0, step: 0.1, help: "Cost per % above the group's average load." },
      { key: "energy_cost_per_hour", label: "Energy cost / hour", type: "map-number", keyLabel: "Machine", valueLabel: "Cost/h" },
    ],
  },
  {
    key: "quality_weights",
    title: "Quality score weights",
    description: "Components of the schedule quality score (spec Phase 36).",
    fields: [{ key: "quality_weights", label: "Weights", type: "map-number", keyLabel: "Component", valueLabel: "Weight" }],
  },
];

export const REPLANNING_SECTION: SectionSchema = {
  key: "replanning",
  title: "Continuous replanning",
  description: "When the engine re-runs automatically and who approves significant changes (spec Phase 11).",
  fields: [
    { key: "enabled", label: "Enabled", type: "boolean" },
    { key: "auto_replan_interval_minutes", label: "Auto replan interval", type: "number", min: 0, unit: "min" },
    { key: "trigger_on", label: "Trigger on", type: "string-list", options: ["new_order", "order_completed", "machine_down", "machine_up", "material_arrived", "quality_failure", "rework", "production_delay", "customer_priority_change", "config_change"], help: "ERP/plant events that start a replan." },
    { key: "require_approval", label: "Require approval", type: "boolean", help: "Significant changes stay DRAFT until a production manager approves." },
    { key: "significant_change_orders", label: "Significant change", type: "integer", min: 0, unit: "orders", help: "Number of changed orders that counts as significant." },
    { key: "notify_roles", label: "Notify roles", type: "string-list", options: ["admin", "production_manager", "planner", "supervisor", "operator", "executive"] },
  ],
};

export const ALERTS_SECTION: SectionSchema = {
  key: "alerts",
  title: "Alert thresholds",
  description: "When the alert engine raises exceptions (spec Phase 20).",
  fields: [
    { key: "likely_late_slack_hours", label: "Likely-late slack", type: "number", min: 0, unit: h, help: "Raise 'order likely late' when slack drops below this." },
    { key: "capacity_overload_pct", label: "Capacity overload", type: "number", min: 0, max: 200, unit: "%" },
    { key: "bottleneck_utilization_pct", label: "Bottleneck utilisation", type: "number", min: 0, max: 200, unit: "%" },
    { key: "material_shortage_days_ahead", label: "Material shortage look-ahead", type: "integer", min: 0, unit: "days" },
    { key: "starvation_days", label: "Starvation after", type: "number", min: 0, unit: "days" },
    { key: "behind_schedule_minutes", label: "Behind schedule after", type: "number", min: 0, unit: "min" },
  ],
};

export const DATA_QUALITY_SECTION: SectionSchema = {
  key: "data_quality",
  title: "Data quality rules",
  description: "Plausibility limits and which missing fields block scheduling (spec Phase 21).",
  fields: [
    { key: "max_cycle_minutes_per_unit", label: "Max cycle / unit", type: "number", min: 0, unit: "min" },
    { key: "max_total_production_days", label: "Max total production", type: "number", min: 0, unit: "days" },
    { key: "max_due_date_years_ahead", label: "Max due date ahead", type: "number", min: 0, step: 0.5, unit: "years" },
    { key: "treat_missing_setup_as_blocking", label: "Missing setup time blocks", type: "boolean" },
    { key: "treat_missing_cycle_as_blocking", label: "Missing cycle time blocks", type: "boolean" },
    { key: "treat_missing_due_date_as_blocking", label: "Missing due date blocks", type: "boolean" },
    { key: "treat_missing_machine_as_blocking", label: "Missing machine blocks", type: "boolean" },
  ],
};

/** Human labels for the factor weights table (spec Phase 14 example wording). */
export const FACTOR_HELP: Record<string, string> = {
  due_date_urgency: "How close the due date is (or projected lateness).",
  sla_risk: "Remaining share of the customer SLA window.",
  customer_importance: "Tier, strategic flag, escalation, revenue, profitability.",
  order_value: "Order value scaled across the open book.",
  margin: "Contribution margin of the order.",
  delay_penalty: "Contractual penalty per late day.",
  production_readiness: "Material, tooling, approval and previous operation status.",
  machine_availability: "Whether an eligible machine is free now or soon.",
  setup_efficiency: "Continues the current setup family/material vs. changeover.",
  batching_affinity: "Similarity to jobs already queued on the machine.",
  downstream_impact: "Dependent orders and later operations waiting on this one.",
};
