import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { ApiClient } from "./client";
import { toPaged } from "./client";
import { useApiClient } from "./context";
import { queryKeys } from "./queryKeys";
import type {
  ExpediteRequest,
  ForceNextRequest,
  HoldRequest,
  ListResponse,
  MachineRecommendation,
  MessageResponse,
  OrderDetail,
  OrderListItem,
  OrderListQuery,
  OverridePriorityRequest,
  Paged,
  PriorityResult,
} from "./types";

export async function fetchOrders(client: ApiClient, params: OrderListQuery = {}): Promise<Paged<OrderListItem>> {
  const res = await client.get<ListResponse<OrderListItem>>("/orders", { ...params });
  return toPaged(res);
}

export function fetchOrder(client: ApiClient, orderId: string): Promise<OrderDetail> {
  return client.get<OrderDetail>(`/orders/${encodeURIComponent(orderId)}`);
}

export function fetchOrderExplanation(client: ApiClient, orderId: string): Promise<PriorityResult> {
  return client.get<PriorityResult>(`/orders/${encodeURIComponent(orderId)}/explanation`);
}

export function fetchOrderMachines(client: ApiClient, orderId: string): Promise<MachineRecommendation> {
  return client.get<MachineRecommendation>(`/orders/${encodeURIComponent(orderId)}/machines`);
}

export function expediteOrder(client: ApiClient, orderId: string, body: ExpediteRequest): Promise<MessageResponse> {
  return client.post<MessageResponse>(`/orders/${encodeURIComponent(orderId)}/expedite`, body);
}

export function holdOrder(client: ApiClient, orderId: string, body: HoldRequest): Promise<MessageResponse> {
  return client.post<MessageResponse>(`/orders/${encodeURIComponent(orderId)}/hold`, body);
}

export function releaseOrder(client: ApiClient, orderId: string, body: HoldRequest): Promise<MessageResponse> {
  return client.post<MessageResponse>(`/orders/${encodeURIComponent(orderId)}/release`, body);
}

export function overridePriority(
  client: ApiClient,
  orderId: string,
  body: OverridePriorityRequest,
): Promise<MessageResponse> {
  return client.post<MessageResponse>(`/orders/${encodeURIComponent(orderId)}/override-priority`, body);
}

export function forceNext(client: ApiClient, orderId: string, body: ForceNextRequest): Promise<MessageResponse> {
  return client.post<MessageResponse>(`/orders/${encodeURIComponent(orderId)}/force-next`, body);
}

// ------------------------------------------------------------------ hooks

export function useOrders(params: OrderListQuery = {}, enabled = true) {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.orders.list(params),
    queryFn: () => fetchOrders(client, params),
    enabled,
    placeholderData: (prev) => prev,
  });
}

export function useOrder(orderId: string | undefined) {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.orders.detail(orderId ?? ""),
    queryFn: () => fetchOrder(client, orderId ?? ""),
    enabled: Boolean(orderId),
  });
}

export function useOrderExplanation(orderId: string | undefined) {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.orders.explanation(orderId ?? ""),
    queryFn: () => fetchOrderExplanation(client, orderId ?? ""),
    enabled: Boolean(orderId),
  });
}

export function useOrderMachines(orderId: string | undefined) {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.orders.machines(orderId ?? ""),
    queryFn: () => fetchOrderMachines(client, orderId ?? ""),
    enabled: Boolean(orderId),
  });
}

type OrderAction = "expedite" | "hold" | "release" | "override-priority" | "force-next";

/** One mutation hook for all order actions; invalidates order/priority/schedule caches on success. */
export function useOrderAction(orderId: string) {
  const client = useApiClient();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ action, body }: { action: OrderAction; body: Record<string, unknown> }) =>
      client.post<MessageResponse>(`/orders/${encodeURIComponent(orderId)}/${action}`, body),
    onSuccess: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: queryKeys.orders.all }),
        qc.invalidateQueries({ queryKey: queryKeys.schedule.all }),
        qc.invalidateQueries({ queryKey: queryKeys.audit.all }),
      ]);
    },
  });
}
