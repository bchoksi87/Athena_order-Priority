/**
 * API envelopes, auth payloads, request bodies and the response models of the
 * resource endpoints, exactly as documented by the backend OpenAPI document
 * (backend/app/api/schemas/*). Flattened *view models* (OrderListItem,
 * MachineListItem) derived from those responses live at the end of the file.
 */

import type {
  AlertSeverity,
  AlertType,
  CustomerTier,
  DataQualityCode,
  DataQualitySeverity,
  ExplanationLineKind,
  LockType,
  MachineStatus,
  MaterialStatus,
  OperationStatus,
  OrderStatus,
  OverridePriorityKind,
  PaymentRisk,
  ProcessType,
  QualityStatus,
  ReadinessState,
  RiskLevel,
  Role,
} from "./enums";
import type {
  AlertConfig,
  ConfigVersionInfo,
  DataQualityConfig,
  PriorityProfile,
  ReplanningConfig,
  SchedulingConfig,
  SystemConfig,
} from "./config";
import type { CustomerRule, Expedite, PriorityOverride, ScheduleLock, TimeWindow } from "./models";
import type {
  AuditEntry,
  FactorScore,
  PriorityAdjustment,
  Scenario,
  ScheduleEntry,
  ScheduleMetrics,
  ScheduleQuality,
  ScheduleVersion,
} from "./results";

// ----------------------------------------------------------- API envelopes

/** Page-number pagination envelope used by every resource list endpoint (PageResponse[T]). */
export interface PageResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
  has_more: boolean;
}

/** Offset envelope (PagedResponse[T]) — also the normalised shape hooks hand to the UI. */
export interface PageMeta {
  total: number;
  offset: number;
  limit: number;
  has_more: boolean;
  /** 1-based page number when the source was a PageResponse. */
  page?: number;
  /** Page count when the source was a PageResponse. */
  pages?: number;
}

export interface Paged<T> {
  items: T[];
  meta: PageMeta;
}

export interface ErrorResponse {
  error: string;
  message: string;
  details: Record<string, unknown>;
}

export interface MessageResponse {
  message: string;
}

/** A list endpoint may answer with a bare array, a PageResponse or a PagedResponse. */
export type ListResponse<T> = PageResponse<T> | Paged<T> | T[];

// ------------------------------------------------------------------- auth

export interface UserInfo {
  user_id: string;
  username: string;
  role: Role;
  display_name: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  role: Role;
  user: UserInfo;
}

// ----------------------------------------------------------------- orders

/** OrderSummary: the order columns of the list and detail endpoints. */
export interface OrderSummary {
  order_id: string;
  external_order_ref: string | null;
  customer_id: string;
  customer_name: string | null;
  part_id: string;
  part_name: string | null;
  part_family: string | null;
  quantity: number;
  pending_quantity: number;
  completed_quantity: number;
  due_date: string | null;
  order_date: string | null;
  order_status: OrderStatus;
  production_status: string | null;
  material_status: MaterialStatus;
  quality_status: QualityStatus;
  process_type: ProcessType;
  machine_group: string | null;
  required_machine_id: string | null;
  required_material_id: string | null;
  order_value: number | null;
  estimated_margin: number | null;
  erp_priority: number | null;
  on_hold: boolean;
  hold_reason: string | null;
}

/** PriorityInfo: latest stored priority result of an order. */
export interface PriorityInfo {
  score: number;
  base_score: number;
  rank: number | null;
  risk_level: RiskLevel;
  readiness: ReadinessState;
  blocked: boolean;
  blocking_reasons: string[];
  forced_next: boolean;
  hours_until_due: number | null;
  projected_completion: string | null;
  projected_lateness_hours: number | null;
  explanation: string;
  profile_id: string;
  profile_version: number;
  computed_at: string;
}

/** OrderScheduleResponse: placement of the order in the current schedule version. */
export interface OrderSchedule {
  version_number: number;
  /** draft/approved/published: which schedule the placement comes from. */
  status: string;
  machine_id: string | null;
  start: string | null;
  end: string | null;
  expected_completion: string | null;
  expected_lateness_hours: number | null;
  entries: ScheduleEntry[];
}

/** OrderListItemResponse: one row of GET /orders. */
export interface OrderListItemResponse {
  order: OrderSummary;
  priority: PriorityInfo | null;
  schedule: OrderSchedule | null;
}

/** ExplanationLine: "+25 — Due Date Urgency: Due in 18 hours", produced by the engine. */
export interface ExplanationLine {
  kind: ExplanationLineKind;
  key: string;
  label: string;
  points: number;
  reason: string;
  raw_score?: number | null;
  weight?: number | null;
  source_id?: string | null;
}

/** ExplanationResponse: GET /orders/{id}/explanation. */
export interface ExplanationResponse {
  order_id: string;
  score: number;
  rank: number | null;
  risk_level: RiskLevel;
  readiness: ReadinessState;
  blocked: boolean;
  blocking_reasons: string[];
  forced_next: boolean;
  /** Rendered text of the same lines. */
  explanation: string;
  lines: ExplanationLine[];
  profile_id: string;
  profile_version: number;
  computed_at: string;
}

export interface CustomerSummary {
  customer_id: string;
  customer_name: string;
  customer_tier: string;
  strategic_customer_flag: boolean;
  sla_hours: number | null;
}

export interface OperationResponse {
  operation_id: string;
  sequence: number;
  operation_type: ProcessType;
  machine_group: string | null;
  machine_id: string | null;
  operation_status: OperationStatus;
  quantity: number;
  completed_quantity: number;
  setup_minutes: number | null;
  cycle_minutes_per_unit: number | null;
  material_id: string | null;
  tooling_ids: string[];
  prerequisite_operation_id: string | null;
  estimated_start: string | null;
  estimated_end: string | null;
  actual_start: string | null;
  actual_end: string | null;
}

export interface MaterialResponse {
  material_id: string;
  material_name: string;
  material_type: string;
  available_quantity: number;
  reserved_quantity: number;
  free_quantity: number;
  incoming_quantity: number;
  expected_receipt_date: string | null;
  unit: string;
}

export interface ToolingResponse {
  tooling_id: string;
  tooling_name: string;
  available: boolean;
  usable: boolean;
  available_from: string | null;
  maintenance_status: string;
}

export interface DataQualityIssueBrief {
  code: DataQualityCode;
  severity: DataQualitySeverity;
  message: string;
  field_name: string | null;
  recommendation: string | null;
}

/** OverrideResponse (planner overlays). Identical to the domain PriorityOverride. */
export type OverrideResponse = PriorityOverride;
/** ExpediteResponse. Identical to the domain Expedite. */
export type ExpediteResponse = Expedite;
/** LockResponse. Identical to the domain ScheduleLock. */
export type LockResponse = ScheduleLock;

export interface MoveOrderResponse {
  override: OverrideResponse;
  lock: LockResponse;
}

/** OrderDetailResponse: GET /orders/{id}. */
export interface OrderDetail {
  order: OrderSummary;
  customer: CustomerSummary | null;
  priority: PriorityInfo | null;
  /** "Why is this order prioritised?" — the engine's explanation lines. */
  breakdown: ExplanationLine[];
  factors: FactorScore[];
  adjustments: PriorityAdjustment[];
  schedule: OrderSchedule | null;
  operations: OperationResponse[];
  materials: MaterialResponse[];
  tooling: ToolingResponse[];
  production_minutes: number | null;
  dependencies: string[];
  dependents: string[];
  overrides: OverrideResponse[];
  expedites: ExpediteResponse[];
  locks: LockResponse[];
  audit: AuditEntry[];
  data_quality_issues: DataQualityIssueBrief[];
  special_instructions: string | null;
  drawing_approved: boolean;
}

/** MachineOptionResponse: one eligible machine for an order's next operation. */
export interface MachineOption {
  machine_id: string;
  machine_name: string;
  rank: number;
  recommended: boolean;
  setup_minutes: number | null;
  run_minutes: number | null;
  soft_cost: number;
  reasons: string[];
}

/** MachineOptionsResponse: GET /orders/{id}/machines. */
export interface MachineOptions {
  order_id: string;
  operation_id: string;
  /** True when the ERP supplied no routing and an operation was derived. */
  synthetic_operation: boolean;
  /** 'live' = evaluated now by the constraint engine. */
  source: string;
  evaluated_at: string;
  recommended_machine_id: string | null;
  scheduled_machine_id: string | null;
  pinned_machine_id: string | null;
  pinned_by: string | null;
  eligible: MachineOption[];
  rejected: Record<string, string[]>;
}

/** Sort keys accepted by GET /orders (app/services/order_query_service.py SORT_KEYS). */
export type OrderSortKey =
  | "priority"
  | "rank"
  | "due_date"
  | "customer"
  | "order_value"
  | "margin"
  | "risk"
  | "status"
  | "quantity"
  | "order_id";

export type SortOrder = "asc" | "desc";

/** Query parameters of GET /orders. */
export interface OrderListQuery {
  page?: number;
  page_size?: number;
  sort?: OrderSortKey | string;
  order?: SortOrder;
  customer_id?: string;
  status?: OrderStatus | OrderStatus[];
  machine_group?: string;
  process_type?: ProcessType;
  machine_id?: string;
  due_from?: string;
  due_to?: string;
  risk?: RiskLevel;
  readiness?: ReadinessState;
  on_hold?: boolean;
  search?: string;
  open_only?: boolean;
  /** @deprecated legacy alias of `risk` kept for callers written against the scaffold. */
  risk_level?: RiskLevel;
  /** @deprecated legacy offset paging; translated to page/page_size. */
  limit?: number;
  /** @deprecated legacy offset paging; translated to page/page_size. */
  offset?: number;
}

// ---------------------------------------------------------- order actions

export interface ExpediteRequest {
  reason: string;
  /** Default: profile expedite boost. */
  boost_points?: number | null;
  /** Default: profile expedite duration. */
  duration_hours?: number | null;
  starts_at?: string | null;
  /** Alternative to duration_hours. */
  expires_at?: string | null;
}

export interface HoldRequest {
  reason: string;
  expires_at?: string | null;
}

export interface ReleaseRequest {
  reason: string;
}

export interface OverridePriorityRequest {
  reason: string;
  /** increase/decrease by `value` points or set to `value`. */
  type: OverridePriorityKind;
  value: number;
  expires_at?: string | null;
}

export interface ForceNextRequest {
  reason: string;
  expires_at?: string | null;
}

export interface MoveOrderRequest {
  reason: string;
  target_machine_id: string;
  /** Optional slot start on the target machine. */
  start_at?: string | null;
  expires_at?: string | null;
}

export interface LockMachineAssignmentRequest {
  reason: string;
  machine_id: string;
  expires_at?: string | null;
}

export interface CancelRequest {
  reason: string;
}

/** POST /schedule/lock body. */
export interface LockRequest {
  reason: string;
  lock_type: LockType;
  order_id?: string | null;
  machine_id?: string | null;
  /** Default: now (MACHINE/TIME_SLOT). */
  window_start?: string | null;
  /** Default: window_start + scheduling.lock_window_minutes. */
  window_end?: string | null;
  sequence_order_ids?: string[];
}

/** POST /schedule/unlock body. */
export interface UnlockRequest {
  reason: string;
  lock_id: string;
}

export interface LocksQuery {
  machine_id?: string;
  order_id?: string;
  lock_type?: LockType;
}

// --------------------------------------------------------------- machines

/** MachineSummary: master data of a machine as returned by the machine endpoints. */
export interface MachineSummary {
  machine_id: string;
  machine_name: string;
  machine_type: string;
  process_type: ProcessType;
  machine_group: string;
  location: string | null;
  status: MachineStatus;
  calendar_id: string | null;
  efficiency: number;
  /** Trailing utilisation reported by the ERP (0..1). */
  utilization: number | null;
  capacity_hours_per_day: number | null;
  compatible_materials: string[];
  compatible_processes: ProcessType[];
  tooling_configuration: string[];
  current_material_id: string | null;
  current_setup_family: string | null;
  available_from: string | null;
  preferred_rank: number;
}

/** MachineLoadResponse: load of a machine in the current schedule. */
export interface MachineLoad {
  version_number: number | null;
  status: string | null;
  utilization_pct: number | null;
  scheduled_hours: number;
  setup_hours: number;
  scheduled_entries: number;
  next_free: string | null;
  horizon_start: string | null;
  horizon_end: string | null;
}

/** MachineListItemResponse: one row of GET /machines. */
export interface MachineListItemResponse {
  machine: MachineSummary;
  load: MachineLoad;
  active_locks: number;
}

export interface DowntimeWindow {
  kind: string;
  start: string;
  end: string;
  reason: string;
}

/** Shape of MachineCalendar.describe() (MachineDetailResponse.calendar); keys are best-effort. */
export interface MachineCalendarDescription {
  machine_id?: string;
  calendar_id?: string | null;
  name?: string;
  timezone?: string;
  shifts?: Array<{ name: string; start: string; end: string; weekdays: number[]; crosses_midnight: boolean }>;
  holidays?: number;
  extra_working_days?: number;
  overtime_windows?: number;
  downtime_windows?: number;
  available_from?: string | null;
  summary?: string;
  [key: string]: unknown;
}

/** MachineDetailResponse: GET /machines/{id}. */
export interface MachineDetail {
  machine: MachineSummary;
  load: MachineLoad;
  calendar: MachineCalendarDescription;
  /** machine | default | fallback_24x7 */
  calendar_source: string;
  downtime: DowntimeWindow[];
  locks: LockResponse[];
  /** Entries in the next 24 h. */
  upcoming: ScheduleEntry[];
}

/** MachineScheduleResponse: GET /machines/{id}/schedule. */
export interface MachineScheduleResponse {
  machine_id: string;
  machine_name: string;
  version_number: number | null;
  status: string | null;
  start: string;
  end: string;
  entries: ScheduleEntry[];
  downtime: TimeWindow[];
  locks: LockResponse[];
}

export interface MachineScheduleQuery {
  /** Default: now. */
  start?: string;
  /** Default: start + 24 h. */
  end?: string;
  /** Schedule version (default: current). */
  version?: number;
}

export interface MachineListQuery {
  machine_group?: string;
  process_type?: ProcessType;
  status?: MachineStatus;
}

// ---------------------------------------------------------- configuration

/** GET /priority/configuration. */
export interface PriorityConfigurationResponse {
  version: ConfigVersionInfo | null;
  profile: PriorityProfile;
  /** Normalised weights (enabled factors sum to 100). */
  weights_pct: Record<string, number>;
}

export interface PriorityProfileUpdateRequest {
  reason: string;
  profile: PriorityProfile;
}

/** GET /scheduling/configuration. */
export interface SchedulingConfigurationResponse {
  version: ConfigVersionInfo | null;
  scheduling: SchedulingConfig;
  replanning: ReplanningConfig;
  alerts: AlertConfig;
  data_quality: DataQualityConfig;
}

/** PUT /scheduling/configuration: each supplied section is saved as its own version. */
export interface SchedulingConfigUpdateRequest {
  reason: string;
  scheduling?: SchedulingConfig | null;
  replanning?: ReplanningConfig | null;
  alerts?: AlertConfig | null;
  data_quality?: DataQualityConfig | null;
}

export interface ActivateVersionRequest {
  reason: string;
}

export interface ConfigUpdateResponse {
  version: ConfigVersionInfo;
  previous_version: number | null;
  /** Only the keys that changed, previous values. */
  changed_previous: Record<string, unknown>;
  /** Only the keys that changed, new values. */
  changed_new: Record<string, unknown>;
  config: SystemConfig;
}

export interface SystemConfigVersionResponse {
  version: ConfigVersionInfo;
  config: SystemConfig;
}

export interface PreviewRequest {
  profile: PriorityProfile;
  top_n?: number;
}

export interface WeightChange {
  key: string;
  previous_pct: number;
  new_pct: number;
}

export interface OrderMove {
  order_id: string;
  rank_delta: number;
  score_delta: number;
}

/** POST /priority/configuration/preview. */
export interface PreviewResponse {
  /** "Changing Due Date weight from 30% to 40% would move 27 orders into the top 50". */
  summary: string;
  active_profile_id: string;
  candidate_profile_id: string;
  top_n: number;
  orders_evaluated: number;
  entered_top_n: string[];
  left_top_n: string[];
  orders_changed_rank: number;
  mean_abs_score_delta: number;
  max_abs_score_delta: number;
  weight_changes: WeightChange[];
  top_n_before: string[];
  top_n_after: string[];
  /** Largest absolute rank changes (up to 25). */
  biggest_moves: OrderMove[];
}

// -------------------------------------------------------------- customers

export type CustomerRuleResponse = CustomerRule;

export interface CustomerRuleRequest {
  reason: string;
  sla_hours?: number | null;
  tier_override?: CustomerTier | null;
  priority_boost_points?: number;
  notes?: string | null;
  active?: boolean;
}

/** CustomerResponse: GET /customers, GET /customers/{id}. */
export interface CustomerResponse {
  customer_id: string;
  customer_name: string;
  customer_category: string;
  customer_tier: CustomerTier;
  /** Rule tier override when set, else the ERP tier. */
  effective_tier: CustomerTier;
  customer_priority: number;
  strategic_customer_flag: boolean;
  customer_revenue: number | null;
  customer_profitability: number | null;
  sla_hours: number | null;
  /** Rule SLA when set, else the ERP SLA. */
  effective_sla_hours: number | null;
  escalation_level: number;
  payment_risk: PaymentRisk;
  account_manager: string | null;
  active: boolean;
  rule: CustomerRuleResponse | null;
}

export interface CustomersQuery {
  page?: number;
  page_size?: number;
  search?: string;
}

// ------------------------------------------------------------------ alerts

export interface AlertsQuery {
  page?: number;
  page_size?: number;
  severity?: AlertSeverity;
  alert_type?: AlertType;
  order_id?: string;
  machine_id?: string;
  acknowledged?: boolean;
  /** @deprecated legacy offset paging; translated to page/page_size. */
  limit?: number;
  /** @deprecated legacy offset paging; translated to page/page_size. */
  offset?: number;
}

export interface AcknowledgeRequest {
  note?: string | null;
}

export interface AlertSummaryResponse {
  total_active: number;
  unacknowledged: number;
  by_severity: Record<string, number>;
}

// ------------------------------------------------------------------- audit

export interface AuditQuery {
  page?: number;
  page_size?: number;
  entity_type?: string;
  entity_id?: string;
  /** Acting user id. */
  user?: string;
  action?: string;
  from?: string;
  to?: string;
  /** @deprecated legacy alias of `user`. */
  user_id?: string;
  /** @deprecated legacy offset paging; translated to page/page_size. */
  limit?: number;
  /** @deprecated legacy offset paging; translated to page/page_size. */
  offset?: number;
}

// ------------------------------------------------------------ data quality

export interface DataQualityQuery {
  page?: number;
  page_size?: number;
  severity?: DataQualitySeverity;
  code?: DataQualityCode;
  entity_type?: string;
  entity_id?: string;
  /** @deprecated legacy offset paging; translated to page/page_size. */
  limit?: number;
  /** @deprecated legacy offset paging; translated to page/page_size. */
  offset?: number;
}

export interface DashboardReason {
  code: string;
  label: string;
  orders: number;
}

/** DataQualityDashboardResponse: "127 orders cannot be scheduled because: …". */
export interface DataQualityDashboard {
  as_of: string;
  open_orders: number;
  unschedulable_orders: number;
  headline: string;
  /** Primary reason per order; sums to unschedulable_orders. */
  reasons: DashboardReason[];
  orders_by_code: Record<string, number>;
  warnings_by_code: Record<string, number>;
}

/** DataQualitySummaryResponse: GET /data-quality and POST /data-quality/run. */
export interface DataQualitySummary {
  run_id: string | null;
  detected_at: string | null;
  total_issues: number;
  by_severity: Record<string, number>;
  by_code: Record<string, number>;
  by_entity_type: Record<string, number>;
  blocked_entities: number;
  dashboard: DataQualityDashboard;
  /** Present after a run in this request. */
  engine_summary: Record<string, unknown>;
}

/** @deprecated the run endpoint returns DataQualitySummary; kept for callers of the scaffold. */
export type DataQualityRunResponse = DataQualitySummary;

// ------------------------------------------------------------------- users

/** UserResponse: GET /users. */
export interface UserAccount {
  user_id: string;
  username: string;
  role: Role;
  display_name: string;
  email: string | null;
  active: boolean;
  last_login_at: string | null;
  created_at: string | null;
}

export interface UserCreateRequest {
  username: string;
  password: string;
  role: Role;
  display_name: string;
  email?: string | null;
  reason?: string | null;
}

export interface UserUpdateRequest {
  reason: string;
  /** False deactivates the account (login refused), True re-activates it. */
  active: boolean;
}

export interface ResetPasswordRequest {
  reason: string;
  password: string;
}

// --------------------------------------------- schedule / simulation (other modules)
// Kept for src/api/{schedule,analytics,simulation}.ts; the schedule endpoints are not in the
// current OpenAPI document yet, so these mirror DESIGN_CONTRACT §6/§9.

export interface ScheduleQuery {
  version?: number;
  machine_id?: string;
  from?: string;
  to?: string;
}

export interface GenerateScheduleRequest {
  note?: string;
  profile_version?: number;
  config_version?: number;
}

export interface SimulateRequest {
  scenarios: Scenario[];
  note?: string;
}

export interface ScheduleActionRequest {
  version_number: number;
  reason?: string;
}

/** @deprecated the preview endpoint returns PreviewResponse. */
export interface PriorityPreviewResponse {
  results: unknown[];
  changed_orders?: number;
  summary?: string;
}

export interface ScheduleQualityResponse {
  quality: ScheduleQuality | null;
  metrics: ScheduleMetrics;
  version: ScheduleVersion | null;
}

// -------------------------------------------------------------- view models
// Flattened rows derived from the API responses so tables can address fields directly.

/** One priority-queue row: OrderSummary plus the flattened priority and schedule fields. */
export interface OrderListItem extends OrderSummary {
  /** The raw priority block (null until the priority engine has run). */
  priority: PriorityInfo | null;
  /** The raw schedule placement (null when not in the current schedule). */
  schedule: OrderSchedule | null;
  priority_score: number | null;
  rank: number | null;
  readiness: ReadinessState | null;
  risk_level: RiskLevel | null;
  blocked: boolean;
  blocking_reasons: string[];
  forced_next: boolean;
  hours_until_due: number | null;
  projected_completion: string | null;
  projected_lateness_hours: number | null;
  scheduled_machine_id: string | null;
  scheduled_start: string | null;
  scheduled_end: string | null;
  expected_completion: string | null;
  expected_lateness_hours: number | null;
  schedule_version: number | null;
  schedule_status: string | null;
}

/** One machine row: MachineSummary plus its current load. */
export interface MachineListItem extends MachineSummary {
  load: MachineLoad;
  active_locks: number;
  utilization_pct: number | null;
  scheduled_hours: number | null;
  setup_hours: number | null;
  scheduled_entries: number;
  next_free: string | null;
  /** Downtime windows are only carried by GET /machines/{id}; empty on list rows. */
  maintenance_windows: TimeWindow[];
  planned_downtime: TimeWindow[];
  unplanned_downtime: TimeWindow[];
  /** Not provided by the current API (kept for dashboards written against the scaffold). */
  queue_length?: number | null;
  late_orders?: number | null;
  current_order_id?: string | null;
}
