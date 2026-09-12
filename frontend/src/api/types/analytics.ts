/**
 * Analytics endpoints exactly as documented by the OpenAPI components
 * (backend/app/api/schemas/analytics.py): GET /analytics/kpis, /capacity,
 * /bottlenecks and /on-time-delivery. The schedule-quality report lives in ./schedule.
 */

import type { Bottleneck, ExecutiveKpis } from "./results";

// ------------------------------------------------------------------- KPIs

/** KpiReportResponse: the KPI block plus where it came from (stored with the plan or recomputed live). */
export interface KpiReport {
  kpis: ExecutiveKpis;
  /** 'stored' (analytics of the active plan) or 'live' (recomputed). */
  source: string;
  as_of: string;
  /** Active plan the numbers refer to. */
  version_number: number | null;
  version_status: string | null;
}

// --------------------------------------------------------------- capacity

export type CapacityDimension = "machine" | "machine_group" | "process" | "department";
export type CapacityPeriod = "day" | "week";

/** CapacityRowResponse: one resource in one period bucket. */
export interface CapacityPeriodRow {
  key: string;
  period_start: string;
  period_end: string;
  required_hours: number;
  available_hours: number;
  /** available − required (negative = shortfall). */
  gap_hours: number;
  utilization_pct: number;
}

/** CapacityTotalsResponse: one line of the spec's "Process | Required Hrs | Available Hrs | Gap" table. */
export interface CapacityTotals {
  key: string;
  required_hours: number;
  available_hours: number;
  gap_hours: number;
  utilization_pct: number;
  /** Hours placed by the scheduler inside the horizon. */
  scheduled_hours: number;
  /** Hours of pending work estimated from routing (not yet placed). */
  estimated_hours: number;
  shortfall_hours: number;
}

/** CapacityResponse: GET /analytics/capacity. */
export interface CapacityReport {
  dimension: CapacityDimension | string;
  period: CapacityPeriod | string;
  horizon_start: string;
  horizon_end: string;
  rows: CapacityPeriodRow[];
  totals: CapacityTotals[];
  total_required_hours: number;
  total_available_hours: number;
  gap_hours: number;
  utilization_pct: number | null;
  /** Demand that could not be attributed to any resource of the dimension. */
  unallocated_hours: number;
  unallocated_operations: number;
  estimated_hours: number;
  scheduled_hours: number;
  /** Explanations from the engine ("… withheld by data quality carry no demand"). */
  notes: string[];
}

export interface CapacityQuery {
  dimension?: CapacityDimension;
  period?: CapacityPeriod;
  /** 1..120 (default: scheduling horizon). */
  horizon_days?: number;
}

// ------------------------------------------------------------ bottlenecks

/** BottleneckReportResponse: GET /analytics/bottlenecks. */
export interface BottleneckReport {
  /** The most severe bottleneck (spec Phase 12 "Current bottleneck"). */
  current: Bottleneck | null;
  items: Bottleneck[];
  source: string;
  as_of: string;
  version_number: number | null;
  version_status: string | null;
}

// -------------------------------------------------------------------- OTD

export interface OtdBucket {
  key: string;
  total: number;
  on_time: number;
  late: number;
  pct: number | null;
}

export interface OtdTrendPoint {
  /** yyyy-MM-dd */
  day: string;
  historical: OtdBucket | null;
  projected: OtdBucket | null;
}

/** OtdResponse: GET /analytics/on-time-delivery. */
export interface OtdReport {
  as_of: string;
  window_days: number;
  historical_pct: number | null;
  projected_pct: number | null;
  historical: OtdBucket;
  projected: OtdBucket;
  historical_by_tier: Record<string, OtdBucket>;
  projected_by_tier: Record<string, OtdBucket>;
  historical_by_process: Record<string, OtdBucket>;
  projected_by_process: Record<string, OtdBucket>;
  trend: OtdTrendPoint[];
  notes: Record<string, number>;
  summary: string;
}

export interface OtdQuery {
  /** 1..365 (default 30). */
  window_days?: number;
}
