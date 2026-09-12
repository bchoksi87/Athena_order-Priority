/**
 * /analytics — executive KPIs, capacity table, bottlenecks, on-time delivery and the schedule
 * quality report (spec Phases 8, 12, 13, 36). Readable by every role including executive.
 */
import { useQuery } from "@tanstack/react-query";

import type { ApiClient } from "./client";
import { useApiClient } from "./context";
import { queryKeys } from "./queryKeys";
import type { BottleneckReport, CapacityQuery, CapacityReport, KpiReport, OtdQuery, OtdReport, ScheduleQualityReport } from "./types";

export type { CapacityQuery, OtdQuery } from "./types";

export function fetchKpis(client: ApiClient): Promise<KpiReport> {
  return client.get<KpiReport>("/analytics/kpis");
}

export function fetchCapacity(client: ApiClient, params: CapacityQuery = {}): Promise<CapacityReport> {
  return client.get<CapacityReport>("/analytics/capacity", { ...params });
}

export function fetchBottlenecks(client: ApiClient): Promise<BottleneckReport> {
  return client.get<BottleneckReport>("/analytics/bottlenecks");
}

export function fetchOnTimeDelivery(client: ApiClient, params: OtdQuery = {}): Promise<OtdReport> {
  return client.get<OtdReport>("/analytics/on-time-delivery", { ...params });
}

export function fetchScheduleQuality(client: ApiClient): Promise<ScheduleQualityReport> {
  return client.get<ScheduleQualityReport>("/analytics/schedule-quality");
}

export function useKpis(refetchIntervalMs: number | false = 60_000) {
  const client = useApiClient();
  return useQuery({ queryKey: queryKeys.analytics.kpis, queryFn: () => fetchKpis(client), refetchInterval: refetchIntervalMs });
}

export function useCapacity(params: CapacityQuery = {}) {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.analytics.capacity(params),
    queryFn: () => fetchCapacity(client, params),
    placeholderData: (prev) => prev,
  });
}

export function useBottlenecks(refetchIntervalMs: number | false = false) {
  const client = useApiClient();
  return useQuery({ queryKey: queryKeys.analytics.bottlenecks, queryFn: () => fetchBottlenecks(client), refetchInterval: refetchIntervalMs });
}

export function useOnTimeDelivery(params: OtdQuery = {}) {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.analytics.otd(params),
    queryFn: () => fetchOnTimeDelivery(client, params),
    placeholderData: (prev) => prev,
  });
}

export function useScheduleQuality(refetchIntervalMs: number | false = false) {
  const client = useApiClient();
  return useQuery({ queryKey: queryKeys.analytics.scheduleQuality, queryFn: () => fetchScheduleQuality(client), refetchInterval: refetchIntervalMs });
}
