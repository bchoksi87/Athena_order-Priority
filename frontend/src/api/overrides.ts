/** Planner overlays: active overrides, expedites and schedule locks (+ cancel/unlock). */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { ApiClient } from "./client";
import { useApiClient } from "./context";
import { invalidateAfterOrderAction } from "./orders";
import { queryKeys } from "./queryKeys";
import type { ExpediteResponse, LockResponse, LocksQuery, OverrideResponse } from "./types";

export function fetchOverrides(client: ApiClient): Promise<OverrideResponse[]> {
  return client.get<OverrideResponse[]>("/overrides");
}

export function fetchExpedites(client: ApiClient, orderId?: string): Promise<ExpediteResponse[]> {
  return client.get<ExpediteResponse[]>("/expedites", { order_id: orderId });
}

export function fetchLocks(client: ApiClient, params: LocksQuery = {}): Promise<LockResponse[]> {
  return client.get<LockResponse[]>("/schedule/locks", { ...params });
}

/** DELETE /overrides/{id}: the mandatory reason is sent as the documented `reason` query parameter (DELETE bodies are dropped by some proxies). */
export function cancelOverride(client: ApiClient, overrideId: string, reason: string): Promise<OverrideResponse> {
  return client.delete<OverrideResponse>(`/overrides/${encodeURIComponent(overrideId)}`, { reason });
}

/** DELETE /expedites/{id} (production manager); reason as query parameter, see cancelOverride. */
export function cancelExpedite(client: ApiClient, expediteId: string, reason: string): Promise<ExpediteResponse> {
  return client.delete<ExpediteResponse>(`/expedites/${encodeURIComponent(expediteId)}`, { reason });
}

export function unlock(client: ApiClient, lockId: string, reason: string): Promise<LockResponse> {
  return client.post<LockResponse>("/schedule/unlock", { lock_id: lockId, reason });
}

export function useOverrides() {
  const client = useApiClient();
  return useQuery({ queryKey: queryKeys.overlays.overrides, queryFn: () => fetchOverrides(client) });
}

export function useExpedites(orderId?: string) {
  const client = useApiClient();
  return useQuery({ queryKey: queryKeys.overlays.expedites(orderId), queryFn: () => fetchExpedites(client, orderId) });
}

export function useLocks(params: LocksQuery = {}) {
  const client = useApiClient();
  return useQuery({ queryKey: queryKeys.overlays.locks(params), queryFn: () => fetchLocks(client, params) });
}

export type OverlayCancelInput =
  | { kind: "override"; id: string; reason: string }
  | { kind: "expedite"; id: string; reason: string }
  | { kind: "lock"; id: string; reason: string };

/** Cancels an override or expedite, or releases a lock; invalidates the same caches as order actions. */
export function useCancelOverlay() {
  const client = useApiClient();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: OverlayCancelInput): Promise<OverrideResponse | ExpediteResponse | LockResponse> => {
      if (input.kind === "override") return cancelOverride(client, input.id, input.reason);
      if (input.kind === "expedite") return cancelExpedite(client, input.id, input.reason);
      return unlock(client, input.id, input.reason);
    },
    onSuccess: () => invalidateAfterOrderAction(qc),
  });
}
