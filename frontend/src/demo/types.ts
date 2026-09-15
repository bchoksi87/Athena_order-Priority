/**
 * Shape of `demo-data.json` as written by backend/scripts/capture_demo_data.py: real API
 * responses of the small synthetic plant, keyed the way the demo handlers look them up.
 */
import type {
  Alert,
  AlertSummaryResponse,
  AuditEntry,
  BottleneckReport,
  CapabilityReportResponse,
  CapacityReport,
  ConfigVersionInfo,
  CustomerResponse,
  CustomerRuleResponse,
  DaySchedule,
  DataQualityIssue,
  DataQualitySummary,
  ExpediteResponse,
  ExplanationResponse,
  GanttView,
  HealthResponse,
  KpiReport,
  LockResponse,
  MachineDetail,
  MachineListItemResponse,
  MachineOptions,
  MachineScheduleResponse,
  MetricsResponse,
  OptimizationRun,
  OrderDetail,
  OrderListItemResponse,
  OtdReport,
  OverrideResponse,
  PreviewRequest,
  PreviewResponse,
  PriorityConfigurationResponse,
  PublishResponse,
  ReplanOutcome,
  Role,
  ScenarioTypes,
  ScheduleComparison,
  ScheduleEntry,
  ScheduleGenerateResponse,
  SchedulePlan,
  ScheduleQualityReport,
  ScheduleVersionDetail,
  ScheduleVersionResponse,
  SchedulingConfigurationResponse,
  SimulateBody,
  SimulationResponse,
  SnapshotInfo,
  SyncRunResponse,
  SyncStatusResponse,
  SystemConfigVersionResponse,
  UserAccount,
  UserInfo,
} from "@/api/types";

/** A captured error envelope (e.g. GET /orders/{id}/explanation answering 404 for an unscored order). */
export interface CapturedError {
  _error: { status: number; error?: string; message?: string; details?: Record<string, unknown> };
}

export function isCapturedError(value: unknown): value is CapturedError {
  return typeof value === "object" && value !== null && "_error" in value;
}

export interface DemoCredential {
  username: string;
  password: string;
  role: Role;
  display_name: string;
}

/** GET /schedule/runs/{run_id} (RunDetailsResponse). */
export interface DemoRunDetails {
  run: OptimizationRun;
  version: ScheduleVersionResponse | null;
  snapshot: SnapshotInfo | null;
  priority_results: number;
  data_quality_issues: number;
}

export interface DemoSimulationPreset {
  kind: string;
  label: string;
  request: SimulateBody;
  response: SimulationResponse | CapturedError;
}

export interface DemoMeta {
  captured_at: string;
  api_version?: string | null;
  plant: { connector: string; scale: string; seed: number };
  horizon_start: string;
  horizon_end: string;
  active_version: number | null;
  versions: Array<{ version_number: number; status: string }>;
  orders: number;
  machines: number;
  customers: number;
  requests: number;
}

export interface DemoData {
  meta: DemoMeta;
  credentials: DemoCredential[];
  auth: { me: Partial<Record<Role, UserInfo>>; expires_in?: number };
  orders: {
    /** GET /orders (open_only=true, sort rank). */
    list: OrderListItemResponse[];
    /** GET /orders?open_only=false (every order). */
    list_all: OrderListItemResponse[];
    detail: Record<string, OrderDetail>;
    explanation: Record<string, ExplanationResponse | CapturedError>;
    machines: Record<string, MachineOptions | CapturedError>;
    overrides: Record<string, OverrideResponse[]>;
  };
  overrides: OverrideResponse[];
  expedites: ExpediteResponse[];
  machines: {
    list: MachineListItemResponse[];
    detail: Record<string, MachineDetail>;
    schedule: Record<string, MachineScheduleResponse>;
  };
  schedule: {
    plan: SchedulePlan;
    versions: ScheduleVersionResponse[];
    version_detail: Record<string, ScheduleVersionDetail>;
    /** Entries per captured version; a version may alias another one (`{same_as}`) when the plans are identical. */
    version_entries: Record<string, ScheduleEntry[] | { same_as: string }>;
    gantt: Record<string, GanttView>;
    days: Record<string, Record<string, DaySchedule>>;
    compare: Record<string, ScheduleComparison>;
    /** Plant-local day boundaries: `timezone` of the default calendar and its UTC offset in minutes. */
    day_meta: { timezone: string; offset_minutes: number };
    runs: Record<string, DemoRunDetails | CapturedError>;
    locks: LockResponse[];
    replan?: ReplanOutcome;
    generate_responses: Record<string, ScheduleGenerateResponse>;
    publish_response?: PublishResponse;
  };
  analytics: {
    kpis: KpiReport;
    bottlenecks: BottleneckReport;
    schedule_quality: ScheduleQualityReport;
    capacity: {
      /** Keyed `${dimension}|${period}|${horizon_days|"default"}`; the report without its rows. */
      reports: Record<string, Omit<CapacityReport, "rows">>;
      /** Keyed `${dimension}|${period}`: the 120-day rows packed as "key|period_start|period_end|required|available". */
      rows: Record<string, string[]>;
    };
    /** Keyed by window_days. */
    on_time_delivery: Record<string, OtdReport>;
  };
  simulations: {
    scenario_types: ScenarioTypes;
    presets?: DemoSimulationPreset[];
    inputs?: Record<string, unknown>;
  };
  configuration: {
    priority: PriorityConfigurationResponse;
    priority_versions: ConfigVersionInfo[];
    priority_version_detail: Record<string, SystemConfigVersionResponse>;
    scheduling: SchedulingConfigurationResponse;
    scheduling_versions: ConfigVersionInfo[];
    scheduling_version_detail: Record<string, SystemConfigVersionResponse>;
    preview: { request: PreviewRequest; response: PreviewResponse };
  };
  customers: {
    list: CustomerResponse[];
    detail: Record<string, CustomerResponse>;
    rules: Record<string, CustomerRuleResponse | CapturedError>;
  };
  alerts: { list: Alert[]; summary: AlertSummaryResponse };
  data_quality: { summary: DataQualitySummary; issues: DataQualityIssue[]; run_response?: DataQualitySummary };
  audit: AuditEntry[];
  users: UserAccount[];
  health: HealthResponse;
  metrics: MetricsResponse;
  sync: {
    runs: SyncRunResponse[];
    run_detail: Record<string, SyncRunResponse>;
    status: SyncStatusResponse;
    capabilities: CapabilityReportResponse;
  };
}
