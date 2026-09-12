import { useQuery } from "@tanstack/react-query";

import type { ApiClient } from "./client";
import { unwrapList } from "./client";
import { useApiClient } from "./context";
import { queryKeys } from "./queryKeys";
import type { Bottleneck, CapacityRow, ExecutiveKpis, ListResponse, OtdPoint, ScheduleQualityResponse } from "./types";

export interface CapacityQuery {
  dimension?: CapacityRow["dimension"];
  from?: string;
  to?: string;
  bucket?: "day" | "week";
}

export interface OtdQuery {
  from?: string;
  to?: string;
  bucket?: "day" | "week" | "month";
}

export function fetchKpis(client: ApiClient): Promise<ExecutiveKpis> {
  return client.get<ExecutiveKpis>("/analytics/kpis");
}

export async function fetchCapacity(client: ApiClient, params: CapacityQuery = {}): Promise<CapacityRow[]> {
  const res = await client.get<ListResponse<CapacityRow>>("/analytics/capacity", { ...params });
  return unwrapList(res).map((row) => ({
    ...row,
    gap_hours: row.gap_hours ?? row.available_hours - row.required_hours,
    utilization_pct:
      row.utilization_pct ??
      (row.available_hours <= 0
        ? row.required_hours > 0
          ? 100
          : 0
        : (100 * row.required_hours) / row.available_hours),
  }));
}

export async function fetchBottlenecks(client: ApiClient): Promise<Bottleneck[]> {
  const res = await client.get<ListResponse<Bottleneck>>("/analytics/bottlenecks");
  return unwrapList(res);
}

export async function fetchOnTimeDelivery(client: ApiClient, params: OtdQuery = {}): Promise<OtdPoint[]> {
  const res = await client.get<ListResponse<OtdPoint>>("/analytics/on-time-delivery", { ...params });
  return unwrapList(res);
}

export function fetchScheduleQuality(client: ApiClient): Promise<ScheduleQualityResponse> {
  return client.get<ScheduleQualityResponse>("/analytics/schedule-quality");
}

export function useKpis(refetchIntervalMs = 60_000) {
  const client = useApiClient();
  return useQuery({ queryKey: queryKeys.analytics.kpis, queryFn: () => fetchKpis(client), refetchInterval: refetchIntervalMs });
}

export function useCapacity(params: CapacityQuery = {}) {
  const client = useApiClient();
  return useQuery({ queryKey: queryKeys.analytics.capacity(params), queryFn: () => fetchCapacity(client, params) });
}

export function useBottlenecks() {
  const client = useApiClient();
  return useQuery({ queryKey: queryKeys.analytics.bottlenecks, queryFn: () => fetchBottlenecks(client) });
}

export function useOnTimeDelivery(params: OtdQuery = {}) {
  const client = useApiClient();
  return useQuery({ queryKey: queryKeys.analytics.otd(params), queryFn: () => fetchOnTimeDelivery(client, params) });
}

export function useScheduleQuality() {
  const client = useApiClient();
  return useQuery({ queryKey: queryKeys.analytics.scheduleQuality, queryFn: () => fetchScheduleQuality(client) });
}
