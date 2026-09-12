/** Operational endpoints: GET /health and GET /metrics (public). The ERP sync endpoints live in ./sync. */
import { useQuery } from "@tanstack/react-query";

import type { ApiClient } from "./client";
import { useApiClient } from "./context";
import { queryKeys } from "./queryKeys";
import type { HealthResponse, MetricsResponse } from "./types";

export function fetchHealth(client: ApiClient): Promise<HealthResponse> {
  return client.get<HealthResponse>("/health");
}

export function fetchMetrics(client: ApiClient): Promise<MetricsResponse> {
  return client.get<MetricsResponse>("/metrics");
}

export function useHealth(refetchIntervalMs: number | false = 30_000) {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.system.health,
    queryFn: () => fetchHealth(client),
    refetchInterval: refetchIntervalMs,
    retry: false,
  });
}

export function useMetrics(refetchIntervalMs: number | false = 30_000) {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.system.metrics,
    queryFn: () => fetchMetrics(client),
    refetchInterval: refetchIntervalMs,
    retry: false,
  });
}

// Backward-compatible re-exports for callers written against the scaffold's system module.
export { fetchSyncRuns, runSync, useRunSync, useSyncRuns } from "./sync";
