/** Operational endpoints: /health, /metrics and the (contract §9) ERP sync endpoints. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { ApiClient } from "./client";
import { unwrapList } from "./client";
import { useApiClient } from "./context";
import { queryKeys } from "./queryKeys";
import type { HealthResponse, ListResponse, SyncMode, SyncRun } from "./types";

export function fetchHealth(client: ApiClient): Promise<HealthResponse> {
  return client.get<HealthResponse>("/health");
}

export function fetchMetrics(client: ApiClient): Promise<Record<string, unknown>> {
  return client.get<Record<string, unknown>>("/metrics");
}

/** GET /sync/runs — not yet exposed by the backend; callers must treat a 404 as "unavailable". */
export async function fetchSyncRuns(client: ApiClient): Promise<SyncRun[]> {
  const res = await client.get<ListResponse<SyncRun>>("/sync/runs");
  return unwrapList(res);
}

/** POST /sync/run — not yet exposed by the backend (contract §9). */
export function runSync(client: ApiClient, mode: SyncMode): Promise<SyncRun> {
  return client.post<SyncRun>("/sync/run", { mode });
}

export function useHealth(refetchIntervalMs = 30_000) {
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

export function useSyncRuns(enabled = true) {
  const client = useApiClient();
  return useQuery({ queryKey: queryKeys.system.syncRuns, queryFn: () => fetchSyncRuns(client), enabled, retry: false });
}

export function useRunSync() {
  const client = useApiClient();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (mode: SyncMode) => runSync(client, mode),
    onSuccess: async () => {
      await qc.invalidateQueries();
    },
  });
}
