/** API envelopes, auth payloads, request bodies and enriched read models returned by the API layer. */

import type {
  AlertSeverity,
  AlertType,
  DataQualityCode,
  DataQualitySeverity,
  LockType,
  OrderStatus,
  OverrideType,
  ProcessType,
  ReadinessState,
  RiskLevel,
  Role,
} from "./enums";
import type { Customer, Expedite, Machine, Operation, Order, PriorityOverride, ScheduleLock, TimeWindow } from "./models";
import type {
  Blocker,
  DataQualityIssue,
  PriorityResult,
  Scenario,
  ScheduleEntry,
  ScheduleMetrics,
  ScheduleQuality,
  ScheduleVersion,
} from "./results";

// ----------------------------------------------------------- API envelopes

export interface PageMeta {
  total: number;
  offset: number;
  limit: number;
  has_more: boolean;
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

// -------------------------------------------------------- API view models
// Enriched read models the list/detail endpoints return: the domain object
// plus computed fields. Every enrichment is optional so a lean backend
// response still renders.

export interface OrderListItem extends Order {
  customer_name?: string | null;
  priority_score?: number | null;
  rank?: number | null;
  readiness?: ReadinessState | null;
  risk_level?: RiskLevel | null;
  blocked?: boolean;
  blocking_reasons?: string[];
  projected_completion?: string | null;
  projected_lateness_hours?: number | null;
  scheduled_machine_id?: string | null;
  scheduled_start?: string | null;
  scheduled_end?: string | null;
  next_operation_type?: ProcessType | null;
  forced_next?: boolean;
}

export interface OrderDetail {
  order: Order;
  customer: Customer | null;
  operations: Operation[];
  priority: PriorityResult | null;
  schedule_entries: ScheduleEntry[];
  blockers: Blocker[];
  overrides: PriorityOverride[];
  expedites: Expedite[];
  locks: ScheduleLock[];
  dependents?: string[];
}

export interface MachineListItem extends Machine {
  scheduled_hours?: number | null;
  utilization_pct?: number | null;
  next_free?: string | null;
  current_order_id?: string | null;
  queue_length?: number | null;
  late_orders?: number | null;
}

export interface MachineDetail {
  machine: Machine;
  entries: ScheduleEntry[];
  utilization_pct: number | null;
  queue: OrderListItem[];
  downtime: TimeWindow[];
  calendar?: unknown;
}

export interface MachineScheduleResponse {
  machine_id: string;
  entries: ScheduleEntry[];
  horizon_start?: string;
  horizon_end?: string;
}

export interface OrderListQuery {
  status?: OrderStatus | OrderStatus[];
  customer_id?: string;
  machine_group?: string;
  process_type?: ProcessType;
  readiness?: ReadinessState;
  risk_level?: RiskLevel;
  search?: string;
  blocked?: boolean;
  sort?: string;
  offset?: number;
  limit?: number;
}

export interface ScheduleQuery {
  version?: number;
  machine_id?: string;
  from?: string;
  to?: string;
}

export interface AlertsQuery {
  severity?: AlertSeverity;
  alert_type?: AlertType;
  acknowledged?: boolean;
  offset?: number;
  limit?: number;
}

export interface AuditQuery {
  entity_type?: string;
  entity_id?: string;
  user_id?: string;
  action?: string;
  from?: string;
  to?: string;
  offset?: number;
  limit?: number;
}

export interface DataQualityQuery {
  severity?: DataQualitySeverity;
  code?: DataQualityCode;
  entity_type?: string;
  offset?: number;
  limit?: number;
}

export interface ExpediteRequest {
  reason: string;
  boost_points?: number;
  duration_hours?: number;
}

export interface HoldRequest {
  reason: string;
}

export interface OverridePriorityRequest {
  override_type: OverrideType;
  value?: number;
  reason: string;
  expires_at?: string;
}

export interface ForceNextRequest {
  reason: string;
  machine_id?: string;
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

export interface LockRequest {
  lock_type: LockType;
  reason: string;
  order_id?: string;
  machine_id?: string;
  window?: TimeWindow;
  sequence_order_ids?: string[];
}

export interface UnlockRequest {
  lock_id: string;
  reason?: string;
}

export interface PriorityPreviewResponse {
  results: PriorityResult[];
  changed_orders?: number;
  summary?: string;
}

export interface DataQualityRunResponse {
  issues: DataQualityIssue[];
  blocking: number;
  warning: number;
  info: number;
  ran_at: string;
}

export interface ScheduleQualityResponse {
  quality: ScheduleQuality | null;
  metrics: ScheduleMetrics;
  version: ScheduleVersion | null;
}

export type ListResponse<T> = Paged<T> | T[];
