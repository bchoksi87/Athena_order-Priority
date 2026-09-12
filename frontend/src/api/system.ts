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

export async function fetchSyncRuns(client: ApiClient): Promise<SyncRun[]> {
  const res = await client.get<ListResponse<SyncRun>>("/sync/runs");
  return unwrapList(res);
}

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

export function useSyncRuns() {
  const client = useApiClient();
  return useQuery({ queryKey: queryKeys.system.syncRuns, queryFn: () => fetchSyncRuns(client) });
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
