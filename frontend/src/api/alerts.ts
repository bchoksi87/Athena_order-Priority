/** /alerts — inbox, severity summary and acknowledgement (spec Phase 20). */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { ApiClient } from "./client";
import { fromPageResponse, resolvePaging } from "./client";
import { useApiClient } from "./context";
import { queryKeys } from "./queryKeys";
import type { Alert, AlertSummaryResponse, AlertsQuery, PageResponse, Paged } from "./types";

export async function fetchAlerts(client: ApiClient, params: AlertsQuery = {}): Promise<Paged<Alert>> {
  const paging = resolvePaging(params, 50);
  const res = await client.get<PageResponse<Alert>>("/alerts", {
    ...paging,
    severity: params.severity,
    alert_type: params.alert_type,
    order_id: params.order_id,
    machine_id: params.machine_id,
    acknowledged: params.acknowledged,
  });
  return fromPageResponse(res);
}

export function fetchAlertSummary(client: ApiClient): Promise<AlertSummaryResponse> {
  return client.get<AlertSummaryResponse>("/alerts/summary");
}

export function fetchAlert(client: ApiClient, alertId: string): Promise<Alert> {
  return client.get<Alert>(`/alerts/${encodeURIComponent(alertId)}`);
}

export function acknowledgeAlert(client: ApiClient, alertId: string, note?: string): Promise<Alert> {
  return client.post<Alert>(`/alerts/${encodeURIComponent(alertId)}/acknowledge`, { note: note && note.trim() !== "" ? note.trim() : null });
}

export function useAlerts(params: AlertsQuery = {}, refetchIntervalMs: number | false = 60_000) {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.alerts.list(params),
    queryFn: () => fetchAlerts(client, params),
    refetchInterval: refetchIntervalMs,
    placeholderData: (prev) => prev,
  });
}

export function useAlertSummary(refetchIntervalMs: number | false = 60_000) {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.alerts.summary,
    queryFn: () => fetchAlertSummary(client),
    refetchInterval: refetchIntervalMs,
  });
}

export function useAcknowledgeAlert() {
  const client = useApiClient();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ alertId, note }: { alertId: string; note?: string }) => acknowledgeAlert(client, alertId, note),
    onSuccess: async () => {
      await Promise.all([qc.invalidateQueries({ queryKey: queryKeys.alerts.all }), qc.invalidateQueries({ queryKey: queryKeys.audit.all })]);
    },
  });
}
