/**
 * /sync — admin-only ERP synchronisation: run now, run history, one run, connector status and the
 * Required / Available / Missing field capability report (contract §9, spec Phase 2).
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { ApiClient } from "./client";
import { fromPageResponse, resolvePaging } from "./client";
import { useApiClient } from "./context";
import { queryKeys } from "./queryKeys";
import type { CapabilityReportResponse, PageResponse, Paged, SyncRunRequest, SyncRunResponse, SyncRunsQuery, SyncStatusResponse } from "./types";

export function runSync(client: ApiClient, body: SyncRunRequest = {}): Promise<SyncRunResponse> {
  return client.post<SyncRunResponse>("/sync/run", { mode: body.mode ?? "full", prune_missing_orders: body.prune_missing_orders ?? false });
}

export async function fetchSyncRuns(client: ApiClient, params: SyncRunsQuery = {}): Promise<Paged<SyncRunResponse>> {
  const res = await client.get<PageResponse<SyncRunResponse>>("/sync/runs", { ...resolvePaging(params, 20) });
  return fromPageResponse(res);
}

export function fetchSyncRun(client: ApiClient, runId: string): Promise<SyncRunResponse> {
  return client.get<SyncRunResponse>(`/sync/runs/${encodeURIComponent(runId)}`);
}

export function fetchSyncStatus(client: ApiClient): Promise<SyncStatusResponse> {
  return client.get<SyncStatusResponse>("/sync/status");
}

export function fetchSyncCapabilities(client: ApiClient): Promise<CapabilityReportResponse> {
  return client.get<CapabilityReportResponse>("/sync/capabilities");
}

// ------------------------------------------------------------------ hooks

export function useSyncRuns(params: SyncRunsQuery = {}, enabled = true) {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.system.syncRuns(params),
    queryFn: () => fetchSyncRuns(client, params),
    enabled,
    retry: false,
    placeholderData: (prev) => prev,
  });
}

export function useSyncRun(runId: string | undefined) {
  const client = useApiClient();
  return useQuery({ queryKey: queryKeys.system.syncRun(runId ?? ""), queryFn: () => fetchSyncRun(client, runId ?? ""), enabled: Boolean(runId) });
}

export function useSyncStatus(enabled = true, refetchIntervalMs: number | false = 30_000) {
  const client = useApiClient();
  return useQuery({ queryKey: queryKeys.system.syncStatus, queryFn: () => fetchSyncStatus(client), enabled, retry: false, refetchInterval: refetchIntervalMs });
}

export function useSyncCapabilities(enabled = true) {
  const client = useApiClient();
  return useQuery({ queryKey: queryKeys.system.syncCapabilities, queryFn: () => fetchSyncCapabilities(client), enabled, retry: false, staleTime: 5 * 60_000 });
}

/** Runs a sync; every cache is invalidated afterwards because master data, orders and priorities may all have changed. */
export function useRunSync() {
  const client = useApiClient();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: SyncRunRequest) => runSync(client, body),
    onSuccess: async () => {
      await qc.invalidateQueries();
    },
  });
}
