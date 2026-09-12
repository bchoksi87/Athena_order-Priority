/** Master data and order/operation models (backend/app/domain/models.py). */

import type {
  CustomerTier,
  LockType,
  MachineStatus,
  MaterialStatus,
  OperationStatus,
  OrderStatus,
  OverrideType,
  PaymentRisk,
  ProcessType,
  QualityStatus,
  ShippingStatus,
} from "./enums";

// ---------------------------------------------------------------- models

export interface TimeWindow {
  start: string;
  end: string;
  reason: string;
}

export interface Customer {
  customer_id: string;
  customer_name: string;
  customer_category: string;
  customer_tier: CustomerTier;
  customer_priority: number;
  strategic_customer_flag: boolean;
  annual_revenue: number | null;
  customer_revenue: number | null;
  customer_profitability: number | null;
  customer_service_level: number | null;
  sla_hours: number | null;
  escalation_level: number;
  historical_on_time_delivery: number | null;
  payment_risk: PaymentRisk;
  preferred_delivery_expectation: string | null;
  account_manager: string | null;
  active: boolean;
  external_ref: string | null;
  attributes: Record<string, unknown>;
}

export interface Machine {
  machine_id: string;
  machine_name: string;
  machine_type: string;
  process_type: ProcessType;
  machine_group: string;
  location: string | null;
  status: MachineStatus;
  calendar_id: string | null;
  efficiency: number;
  utilization: number | null;
  capacity_hours_per_day: number | null;
  maintenance_windows: TimeWindow[];
  planned_downtime: TimeWindow[];
  unplanned_downtime: TimeWindow[];
  compatible_materials: string[];
  compatible_processes: ProcessType[];
  max_part_size_mm: [number, number, number] | null;
  tooling_configuration: string[];
  setup_requirements: Record<string, unknown>;
  current_material_id: string | null;
  current_setup_family: string | null;
  available_from: string | null;
  preferred_rank: number;
  attributes: Record<string, unknown>;
}

export interface Operation {
  operation_id: string;
  order_id: string;
  sequence: number;
  operation_type: ProcessType;
  machine_group: string | null;
  machine_id: string | null;
  eligible_machine_ids: string[];
  setup_minutes: number | null;
  cycle_minutes_per_unit: number | null;
  machine_cycle_minutes: Record<string, number>;
  quantity: number;
  completed_quantity: number;
  operation_status: OperationStatus;
  prerequisite_operation_id: string | null;
  material_id: string | null;
  material_quantity_per_unit: number | null;
  tooling_ids: string[];
  operator_requirement: string | null;
  quality_requirement: string | null;
  setup_family: string | null;
  estimated_start: string | null;
  estimated_end: string | null;
  actual_start: string | null;
  actual_end: string | null;
  attributes: Record<string, unknown>;
}

export interface Order {
  order_id: string;
  customer_id: string;
  part_id: string;
  order_line_id: string | null;
  external_order_ref: string | null;
  part_name: string | null;
  part_family: string | null;
  order_date: string | null;
  received_date: string | null;
  requested_delivery_date: string | null;
  promised_delivery_date: string | null;
  revised_delivery_date: string | null;
  quantity: number;
  completed_quantity: number;
  cancelled_quantity: number;
  order_status: OrderStatus;
  erp_priority: number | null;
  production_status: string | null;
  material_status: MaterialStatus;
  quality_status: QualityStatus;
  shipping_status: ShippingStatus;
  order_value: number | null;
  estimated_cost: number | null;
  estimated_margin: number | null;
  actual_margin: number | null;
  process_type: ProcessType;
  manufacturing_route: ProcessType[];
  machine_group: string | null;
  required_machine_id: string | null;
  required_material_id: string | null;
  tooling_requirement: string[];
  estimated_setup_minutes: number | null;
  estimated_cycle_minutes_per_unit: number | null;
  estimated_total_production_minutes: number | null;
  customer_priority: number | null;
  technical_priority: number | null;
  commercial_priority: number | null;
  lateness_penalty_per_day: number | null;
  sla_hours: number | null;
  special_instructions: string | null;
  drawing_approved: boolean;
  on_hold: boolean;
  hold_reason: string | null;
  depends_on_order_ids: string[];
  surface_finish: string | null;
  technology: string | null;
  attributes: Record<string, unknown>;
}

export interface ScheduleLock {
  lock_id: string;
  lock_type: LockType;
  created_by: string;
  created_at: string;
  reason: string;
  order_id: string | null;
  machine_id: string | null;
  window: TimeWindow | null;
  sequence_order_ids: string[];
  active: boolean;
}

export interface PriorityOverride {
  override_id: string;
  order_id: string;
  override_type: OverrideType;
  created_by: string;
  created_at: string;
  reason: string;
  value: number | null;
  target_machine_id: string | null;
  expires_at: string | null;
  active: boolean;
}

export interface Expedite {
  expedite_id: string;
  order_id: string;
  created_by: string;
  created_at: string;
  reason: string;
  boost_points: number;
  starts_at: string;
  expires_at: string;
  active: boolean;
}

export interface CustomerRule {
  customer_id: string;
  sla_hours: number | null;
  tier_override: CustomerTier | null;
  priority_boost_points: number;
  notes: string | null;
  active: boolean;
}
