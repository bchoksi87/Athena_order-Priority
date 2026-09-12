/** Versioned configuration models (backend/app/domain/config.py). */

// ---------------------------------------------------------------- config

export interface FactorWeight {
  key: string;
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

/**
 * PriorityProfile (app/domain/config.py). The nested factor-specific scoring
 * blocks are typed loosely as records because the UI edits weights and the
 * common rule blocks; the backend validates the full shape.
 */
export interface PriorityProfile {
  profile_id: string;
  name: string;
  version: number;
  weights: FactorWeight[];
  due_date: DueDateThresholds;
  customer: Record<string, unknown>;
  sla: Record<string, unknown>;
  order_value: Record<string, unknown>;
  delay_penalty: Record<string, unknown>;
  readiness: Record<string, unknown>;
  machine_availability: Record<string, unknown>;
  setup: Record<string, unknown>;
  aging: AgingConfig;
  fairness: FairnessConfig;
  expedite: ExpediteConfig;
  risk: RiskThresholds;
  erp_priority_points: Record<string, number>;
  blocked_order_cap: number | null;
}

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

export interface ConfigVersionInfo {
  version: number;
  name?: string;
  created_at: string;
  created_by?: string | null;
  active?: boolean;
  note?: string | null;
}
