import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { ApiClient } from "./client";
import { toPaged } from "./client";
import { useApiClient } from "./context";
import { queryKeys } from "./queryKeys";
import type { Alert, AlertsQuery, ListResponse, MessageResponse, Paged } from "./types";

export async function fetchAlerts(client: ApiClient, params: AlertsQuery = {}): Promise<Paged<Alert>> {
  const res = await client.get<ListResponse<Alert>>("/alerts", { ...params });
  return toPaged(res);
}

export function acknowledgeAlert(client: ApiClient, alertId: string, note?: string): Promise<MessageResponse> {
  return client.post<MessageResponse>(`/alerts/${encodeURIComponent(alertId)}/acknowledge`, note ? { note } : {});
}

export function useAlerts(params: AlertsQuery = {}, refetchIntervalMs = 60_000) {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.alerts.list(params),
    queryFn: () => fetchAlerts(client, params),
    refetchInterval: refetchIntervalMs,
    placeholderData: (prev) => prev,
  });
}

export function useAcknowledgeAlert() {
  const client = useApiClient();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ alertId, note }: { alertId: string; note?: string }) => acknowledgeAlert(client, alertId, note),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: queryKeys.alerts.all });
    },
  });
}
