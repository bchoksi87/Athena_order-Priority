/**
 * Operational endpoints exactly as documented by the OpenAPI components:
 * GET /health (HealthResponse), GET /metrics (MetricsResponse) and the admin-only
 * ERP sync endpoints (/sync/run, /sync/runs, /sync/runs/{id}, /sync/status, /sync/capabilities).
 */

import type { SyncMode } from "./enums";

// ----------------------------------------------------------------- health

/** ConnectorHealthInfo: connector reachability as reported by GET /health. */
export interface ConnectorHealthInfo {
  name: string;
  reachable: boolean;
  message: string;
  checked_at: string | null;
  latency_ms: number | null;
}

/** LastSyncInfo: the most recent sync_runs row (GET /health). */
export interface LastSyncInfo {
  run_id: string;
  status: string;
  mode: string;
  started_at: string;
  finished_at: string | null;
}

/** ActivePlanInfo: the current schedule version (GET /health). */
export interface ActivePlanInfo {
  version_number: number;
  status: string;
  generated_at: string;
  quality_score: number | null;
}

export interface JobInfo {
  id: string;
  name: string;
  next_run_time: string | null;
  trigger: string;
}

export interface BackgroundJobsInfo {
  enabled: boolean;
  running: boolean;
  jobs: JobInfo[];
}

/** HealthResponse: GET /health ('ok' or 'degraded'; 503 only when the database is down). */
export interface HealthResponse {
  status: string;
  database: string;
  version: string;
  environment: string;
  writeback_mode: "read_only" | "approval" | "writeback" | "controlled_auto";
  time: string;
  connector: ConnectorHealthInfo | null;
  last_sync: LastSyncInfo | null;
  active_plan: ActivePlanInfo | null;
  background_jobs: BackgroundJobsInfo;
}

/** MetricsResponse: GET /metrics (in-process counters). */
export interface MetricsResponse {
  requests_total: number;
  errors_total: number;
  by_status: Record<string, number>;
  by_path: Record<string, number>;
  counters: Record<string, number>;
  schedule_generations_total: number;
  replans_total: number;
  failed_runs_total: number;
  sync_runs_total: number;
  sync_runs_failed_total: number;
  schedule_versions_by_status: Record<string, number>;
  active_plan_version: number | null;
  active_alerts_total: number;
  background_jobs_running: boolean;
}

// ------------------------------------------------------------------- sync

/** SyncRunRequest: POST /sync/run body (all fields optional). */
export interface SyncRunRequest {
  mode?: SyncMode;
  /** Mark orders missing from a full sync as cancelled. */
  prune_missing_orders?: boolean;
}

export interface SyncIssueResponse {
  stage: string;
  entity: string;
  external_id: string;
  field: string | null;
  code: string;
  message: string;
}

export interface EntityDeltaResponse {
  entity: string;
  connector_count: number;
  stored_count: number;
  delta: number;
  delta_pct: number;
  /** ok | warning | error */
  status: string;
}

export interface ReconciliationResponse {
  status: string;
  summary: string;
  deltas: EntityDeltaResponse[];
}

/** SyncRunResponse: one sync_runs row (POST /sync/run, GET /sync/runs, GET /sync/runs/{id}). */
export interface SyncRunResponse {
  run_id: string;
  mode: SyncMode;
  /** running | completed | failed */
  status: string;
  connector: string;
  started_at: string;
  finished_at: string | null;
  /** Incremental watermark used by this run. */
  since: string | null;
  duration_seconds: number | null;
  /** Records fetched per entity (customer, material, machine, tooling, calendar, order, operation, production_status). */
  records_fetched: Record<string, number>;
  records_upserted: Record<string, number>;
  issues_count: number;
  issue_counts: Record<string, number>;
  /** Only populated on the single-run endpoints (the list returns max_issues=0). */
  issues: SyncIssueResponse[];
  issues_truncated: boolean;
  reconciliation: ReconciliationResponse | null;
  stored_totals: Record<string, number>;
  /** Open orders marked cancelled because a full sync no longer returned them (prune_missing_orders). */
  pruned_orders: number;
  /** Open orders closed because the ERP reports them as no longer open. */
  orders_closed_missing: number;
  /** none | run | watermark … where the incremental `since` came from. */
  watermark_source: string;
  triggered_by: string | null;
  error_message: string | null;
}

/** @deprecated alias kept for callers written against the scaffold; use SyncRunResponse. */
export type SyncRun = SyncRunResponse;

export interface ConnectorHealthResponse {
  connector_name: string;
  healthy: boolean;
  checked_at: string;
  latency_ms: number | null;
  message: string;
  details: Record<string, unknown>;
}

/** SyncStatusResponse: GET /sync/status. */
export interface SyncStatusResponse {
  connector: string;
  health: ConnectorHealthResponse | null;
  last_run: SyncRunResponse | null;
  last_completed: SyncRunResponse | null;
  watermark: string | null;
  runs_total: number;
  checked_at: string;
}

/** FieldAssessmentResponse: one row of the Required / Available / Missing table. */
export interface FieldAssessmentResponse {
  entity: string;
  field: string;
  /** required | recommended | optional */
  importance: string;
  /** Available | Missing | Partial … as reported by the connector. */
  status: string;
  /** Engines that consume the field (priority, scheduling, dashboard, …). */
  used_by: string[];
  impact_if_missing: string;
  recommendation: string;
}

/** CapabilityReportResponse: GET /sync/capabilities. */
export interface CapabilityReportResponse {
  connector_name: string;
  supports_incremental: boolean;
  supports_webhooks: boolean;
  coverage_pct: number;
  can_schedule: boolean;
  missing_required: string[];
  assessments: FieldAssessmentResponse[];
}

export interface SyncRunsQuery {
  page?: number;
  page_size?: number;
}
