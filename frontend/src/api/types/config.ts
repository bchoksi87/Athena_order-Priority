/**
 * Versioned configuration models (backend/app/domain/config.py) exactly as they
 * appear in the OpenAPI components: PriorityProfile, SchedulingConfig,
 * ReplanningConfig, AlertConfig, DataQualityConfig and the SystemConfig aggregate.
 */

import type { FactorKey } from "./enums";

// ---------------------------------------------------------- priority profile

export interface FactorWeight {
  key: FactorKey;
  /** Relative weight in percent (normalised at runtime), 0..100. */
  weight: number;
  enabled: boolean;
  params: Record<string, number | string | boolean>;
}

export interface DueDateThresholds {
  overdue_score: number;
  critical_hours: number;
  critical_score: number;
  high_days: number;
  high_score: number;
  medium_days: number;
  medium_score: number;
  low_days: number;
  low_score: number;
  floor_score: number;
  use_projected_lateness: boolean;
}

export interface CustomerScoring {
  tier_scores: Record<string, number>;
  strategic_flag_bonus: number;
  escalation_points_per_level: number;
  revenue_weight: number;
  profitability_weight: number;
  max_score: number;
}

export interface SlaRiskConfig {
  default_sla_hours: number | null;
  breach_score: number;
  imminent_ratio: number;
  imminent_score: number;
  watch_ratio: number;
  watch_score: number;
  safe_score: number;
}

export type OrderValueScaling = "log" | "linear" | "percentile";

export interface OrderValueConfig {
  scaling: OrderValueScaling;
  cap_value: number | null;
  min_score: number;
}

export interface DelayPenaltyConfig {
  default_penalty_per_day_ratio: number;
  escalation_multiplier_per_level: number;
  strategic_multiplier: number;
  reference_penalty: number | null;
}

export interface ReadinessScoring {
  ready_score: number;
  material_partial_score: number;
  waiting_material_score: number;
  waiting_tooling_score: number;
  waiting_approval_score: number;
  waiting_previous_operation_score: number;
  machine_unavailable_score: number;
  hold_score: number;
}

export interface MachineAvailabilityScoring {
  available_now_score: number;
  available_within_hours: number;
  available_soon_score: number;
  single_machine_bonus: number;
  none_available_score: number;
}

export interface SetupEfficiencyScoring {
  same_setup_score: number;
  same_material_score: number;
  changeover_score: number;
  unknown_score: number;
  large_setup_minutes: number;
  large_setup_penalty_score: number;
}

export interface AgingConfig {
  enabled: boolean;
  start_after_days: number;
  points_per_day: number;
  max_points: number;
}

export interface FairnessConfig {
  enabled: boolean;
  max_wait_days: number;
  starvation_boost_points: number;
  max_top_n_share_per_customer: number;
  top_n: number;
}

export interface ExpediteConfig {
  default_boost_points: number;
  max_boost_points: number;
  default_duration_hours: number;
  max_duration_hours: number;
}

export interface RiskThresholds {
  critical_lateness_hours: number;
  high_slack_hours: number;
  medium_slack_hours: number;
}

export interface PriorityProfile {
  profile_id: string;
  name: string;
  version: number;
  weights: FactorWeight[];
  due_date: DueDateThresholds;
  customer: CustomerScoring;
  sla: SlaRiskConfig;
  order_value: OrderValueConfig;
  delay_penalty: DelayPenaltyConfig;
  readiness: ReadinessScoring;
  machine_availability: MachineAvailabilityScoring;
  setup: SetupEfficiencyScoring;
  aging: AgingConfig;
  fairness: FairnessConfig;
  expedite: ExpediteConfig;
  risk: RiskThresholds;
  /** Adjustment points by ERP priority code. */
  erp_priority_points: Record<string, number>;
  /** Optional cap on the score of blocked orders (null = no cap). */
  blocked_order_cap: number | null;
}

// -------------------------------------------------------------- scheduling

export interface StabilityRules {
  frozen_window_minutes: number;
  min_improvement_pct: number;
  max_moves_per_replan: number | null;
}

export interface SetupRules {
  same_family_setup_factor: number;
  same_material_setup_factor: number;
  default_setup_minutes: number;
  setup_penalty_cost_per_minute: number;
}

export type BatchDimension =
  | "material"
  | "machine"
  | "tool"
  | "fixture"
  | "process"
  | "part_family"
  | "customer"
  | "surface_finish"
  | "technology";

export interface BatchingRules {
  enabled: boolean;
  dimensions: BatchDimension[];
  max_delay_hours: number;
  min_priority_gap: number;
}

export interface ObjectiveWeights {
  on_time_delivery: number;
  total_tardiness: number;
  setup_time: number;
  machine_utilization: number;
  contribution_margin: number;
  wip: number;
}

export interface OvertimeRules {
  allow_overtime: boolean;
  max_overtime_hours_per_day: number;
  overtime_cost_per_hour: number;
}

export interface MachinePreferenceRules {
  preferred_machine_cost: number;
  non_preferred_machine_cost_minutes: number;
  utilization_balance_cost_per_pct: number;
  energy_cost_per_hour: Record<string, number>;
}

export interface SchedulingConfig {
  config_id: string;
  name: string;
  version: number;
  algorithm: string;
  horizon_days: number;
  lock_window_minutes: number;
  stability: StabilityRules;
  setup: SetupRules;
  batching: BatchingRules;
  objectives: ObjectiveWeights;
  overtime: OvertimeRules;
  machine_preference: MachinePreferenceRules;
  schedule_blocked_orders: boolean;
  at_risk_slack_hours: number;
  max_orders_per_run: number | null;
  quality_weights: Record<string, number>;
}

export interface ReplanningConfig {
  enabled: boolean;
  auto_replan_interval_minutes: number;
  trigger_on: string[];
  require_approval: boolean;
  significant_change_orders: number;
  notify_roles: string[];
}

export interface AlertConfig {
  likely_late_slack_hours: number;
  capacity_overload_pct: number;
  bottleneck_utilization_pct: number;
  material_shortage_days_ahead: number;
  starvation_days: number;
  behind_schedule_minutes: number;
}

export interface DataQualityConfig {
  max_cycle_minutes_per_unit: number;
  max_total_production_days: number;
  max_due_date_years_ahead: number;
  treat_missing_setup_as_blocking: boolean;
  treat_missing_cycle_as_blocking: boolean;
  treat_missing_due_date_as_blocking: boolean;
  treat_missing_machine_as_blocking: boolean;
}

/** Aggregate of all configurable rule sets (one row per version in the DB). */
export interface SystemConfig {
  priority_profile: PriorityProfile;
  scheduling: SchedulingConfig;
  replanning: ReplanningConfig;
  alerts: AlertConfig;
  data_quality: DataQualityConfig;
  currency: string;
}

/** ConfigVersionResponse: one row of the versioned system configuration. */
export interface ConfigVersionInfo {
  config_id: string;
  version: number;
  is_active: boolean;
  created_by: string | null;
  reason: string | null;
  created_at: string | null;
  profile_id: string;
  scheduling_config_id: string;
}
