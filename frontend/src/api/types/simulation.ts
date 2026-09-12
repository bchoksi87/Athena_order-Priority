/**
 * What-if simulation exactly as documented by the OpenAPI components
 * (backend/app/api/schemas/simulation.py, backend/app/engines/simulation/scenarios*.py):
 * the Scenario union sent to POST /schedule/simulate, its response and the scenario catalogue.
 */

import type { CustomerTier, ProcessType } from "./enums";
import type { PriorityProfile } from "./config";
import type { OrderDelta, ScheduleDiff, ScheduleMetrics, ScheduleQuality } from "./results";
import type { MetricPair } from "./schedule";

// -------------------------------------------------------------- scenarios

interface ScenarioCommon {
  /** Optional planner-facing label ("CNC-07 spindle failure"). */
  label?: string | null;
}

/** Machine unavailable for a window (start defaults to the snapshot instant; end or duration_hours). */
export interface MachineDownScenario extends ScenarioCommon {
  kind: "machine_down";
  machine_id: string;
  start?: string | null;
  end?: string | null;
  duration_hours?: number | null;
  reason?: string;
}

export interface RouteStep {
  process: ProcessType;
  machine_group?: string | null;
  machine_id?: string | null;
  setup_minutes?: number | null;
  cycle_minutes_per_unit?: number | null;
  material_id?: string | null;
  setup_family?: string | null;
}

/** One urgent order: either a clone of an existing order (clone_of) or an inline routing. */
export interface UrgentOrderSpec {
  order_id?: string | null;
  clone_of?: string | null;
  customer_id?: string | null;
  part_id?: string | null;
  part_family?: string | null;
  quantity?: number | null;
  due?: string | null;
  route?: RouteStep[];
  order_value?: number | null;
  estimated_margin?: number | null;
  material_id?: string | null;
  expedite?: boolean;
  boost_points?: number | null;
}

export interface UrgentOrdersScenario extends ScenarioCommon {
  kind: "urgent_orders";
  orders: UrgentOrderSpec[];
}

export interface AddMachineScenario extends ScenarioCommon {
  kind: "add_machine";
  clone_of_machine_id: string;
  new_machine_id: string;
  name?: string | null;
  calendar_id?: string | null;
  available_from?: string | null;
}

export interface ExtraWorkingDayScenario extends ScenarioCommon {
  kind: "extra_working_day";
  /** yyyy-MM-dd */
  day: string;
  /** Calendar id or "all" (default). */
  calendar_id?: string;
}

export interface ExtraShiftScenario extends ScenarioCommon {
  kind: "extra_shift";
  /** yyyy-MM-dd */
  day: string;
  /** HH:MM:SS local plant time. */
  start: string;
  end: string;
  calendar_id?: string;
  name?: string;
}

export interface OutsourceScenario extends ScenarioCommon {
  kind: "outsource";
  order_ids?: string[];
  machine_group?: string | null;
  quantity?: number | null;
  supplier?: string;
}

export interface MaterialDelayScenario extends ScenarioCommon {
  kind: "material_delay";
  material_id: string;
  delay_days?: number | null;
  new_expected_receipt_date?: string | null;
  affects_allocated_stock?: boolean;
}

export interface MaterialArrivalScenario extends ScenarioCommon {
  kind: "material_arrival";
  material_id: string;
  arrives_at?: string | null;
  quantity?: number | null;
}

export interface PrioritizeCustomerScenario extends ScenarioCommon {
  kind: "prioritize_customer";
  customer_id: string;
  boost_points?: number | null;
  tier_override?: CustomerTier | null;
  sla_hours?: number | null;
}

export interface WeightChangeScenario extends ScenarioCommon {
  kind: "weight_change";
  /** factor key → weight (points). */
  weights?: Record<string, number>;
  profile?: PriorityProfile | null;
}

export interface DueDateChangeScenario extends ScenarioCommon {
  kind: "due_date_change";
  order_id: string;
  new_due: string;
}

export interface HoldOrdersScenario extends ScenarioCommon {
  kind: "hold_orders";
  order_ids: string[];
  reason?: string;
}

export interface ExpediteOrdersScenario extends ScenarioCommon {
  kind: "expedite_orders";
  order_ids: string[];
  boost_points?: number | null;
  hours?: number | null;
  reason?: string;
}

/** The Scenario union (discriminated on `kind`) accepted by POST /schedule/simulate. */
export type SimulationScenario =
  | MachineDownScenario
  | UrgentOrdersScenario
  | AddMachineScenario
  | ExtraWorkingDayScenario
  | ExtraShiftScenario
  | OutsourceScenario
  | MaterialDelayScenario
  | MaterialArrivalScenario
  | PrioritizeCustomerScenario
  | WeightChangeScenario
  | DueDateChangeScenario
  | HoldOrdersScenario
  | ExpediteOrdersScenario;

export type SimulationScenarioKind = SimulationScenario["kind"];

/** SimulateRequest body. */
export interface SimulateBody {
  /** 1..20 scenarios, applied in order on one cloned snapshot. */
  scenarios: SimulationScenario[];
  note?: string | null;
  /** Affected orders to return (1..500, default 20). */
  top_n?: number;
}

// --------------------------------------------------------------- response

/** ScenarioRecordResponse: a scenario as applied, plus what it touched. */
export interface ScenarioRecord {
  kind: string;
  label: string | null;
  description: string;
  notes: string[];
  affected_order_ids: string[];
  affected_machine_ids: string[];
  parameters: Record<string, unknown>;
}

/** PlanSummaryResponse: baseline or scenario plan in brief (never persisted). */
export interface PlanSummary {
  algorithm: string;
  algorithm_version: string;
  horizon_start: string;
  horizon_end: string;
  entries: number;
  unscheduled: number;
  metrics: ScheduleMetrics;
  quality: ScheduleQuality | null;
  warnings: string[];
}

/** ScheduleDiffResponse (the domain ScheduleDiff without the per-order deltas). */
export type SimulationDiff = Omit<ScheduleDiff, "order_deltas">;

export interface AffectedOrder {
  order_id: string;
  customer_id: string | null;
  customer_name: string | null;
  part_id: string | null;
  part_name: string | null;
  due_date: string | null;
  order_value: number | null;
  delta: OrderDelta;
}

/** SimulationResponse: POST /schedule/simulate. */
export interface SimulationResponse {
  simulation_id: string;
  generated_at: string;
  note: string | null;
  /** Plan whose entries fed the frozen window. */
  baseline_version: number | null;
  scenarios: ScenarioRecord[];
  baseline: PlanSummary;
  scenario: PlanSummary;
  diff: SimulationDiff;
  /** Before → after metric pairs. */
  comparison: Record<string, MetricPair>;
  comparison_summary: string;
  affected_orders: AffectedOrder[];
  top_n: number;
  /** Management summary sentence rendered by the diff engine. */
  summary: string;
}

// -------------------------------------------------------------- catalogue

export interface ScenarioType {
  kind: string;
  title: string;
  description: string;
  /** JSON schema of the scenario model. */
  schema: Record<string, unknown>;
}

/** ScenarioTypesResponse: GET /simulation/scenario-types. */
export interface ScenarioTypes {
  kinds: ScenarioType[];
  schema: Record<string, unknown>;
}
