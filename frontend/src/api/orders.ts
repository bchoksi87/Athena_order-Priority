/**
 * /orders — priority queue, order detail, explanation, machine options and the
 * planner/manager actions (expedite, hold, release, override, force next, move, lock machine).
 */
import { useMutation, useQuery, useQueryClient, type UseQueryOptions } from "@tanstack/react-query";

import type { ApiClient, QueryParams } from "./client";
import { fromPageResponse, resolvePaging } from "./client";
import { useApiClient } from "./context";
import { queryKeys } from "./queryKeys";
import type {
  ExpediteRequest,
  ExpediteResponse,
  ExplanationResponse,
  ForceNextRequest,
  HoldRequest,
  LockMachineAssignmentRequest,
  MachineOptions,
  MoveOrderRequest,
  MoveOrderResponse,
  OrderDetail,
  OrderListItem,
  OrderListItemResponse,
  OrderListQuery,
  OverridePriorityRequest,
  OverrideResponse,
  PageResponse,
  Paged,
  ReleaseRequest,
} from "./types";

/** Flattens the API row {order, priority, schedule} into the table view model. */
export function flattenOrderListItem(row: OrderListItemResponse): OrderListItem {
  const p = row.priority;
  const s = row.schedule;
  return {
    ...row.order,
    priority: p,
    schedule: s,
    priority_score: p?.score ?? null,
    rank: p?.rank ?? null,
    readiness: p?.readiness ?? null,
    risk_level: p?.risk_level ?? null,
    blocked: p?.blocked ?? false,
    blocking_reasons: p?.blocking_reasons ?? [],
    forced_next: p?.forced_next ?? false,
    hours_until_due: p?.hours_until_due ?? null,
    projected_completion: p?.projected_completion ?? s?.expected_completion ?? null,
    projected_lateness_hours: p?.projected_lateness_hours ?? s?.expected_lateness_hours ?? null,
    scheduled_machine_id: s?.machine_id ?? null,
    scheduled_start: s?.start ?? null,
    scheduled_end: s?.end ?? null,
    expected_completion: s?.expected_completion ?? null,
    expected_lateness_hours: s?.expected_lateness_hours ?? null,
    schedule_version: s?.version_number ?? null,
    schedule_status: s?.status ?? null,
  };
}

/** Legacy sort names from the scaffold ("-priority_score") mapped onto the API's sort keys. */
const LEGACY_SORT: Record<string, string> = {
  priority_score: "priority",
  score: "priority",
  due: "due_date",
  value: "order_value",
  risk_level: "risk",
};

/** Translates the hook's query object (including legacy aliases) into GET /orders parameters. */
export function toOrderListParams(params: OrderListQuery): QueryParams {
  const { page, page_size } = resolvePaging(params, 50);
  let sort = params.sort;
  let order = params.order;
  if (sort && sort.startsWith("-")) {
    sort = sort.slice(1);
    order = order ?? "desc";
  }
  if (sort && LEGACY_SORT[sort]) sort = LEGACY_SORT[sort];
  return {
    page,
    page_size,
    sort,
    order,
    customer_id: params.customer_id,
    status: params.status,
    machine_group: params.machine_group,
    process_type: params.process_type,
    machine_id: params.machine_id,
    due_from: params.due_from,
    due_to: params.due_to,
    risk: params.risk ?? params.risk_level,
    readiness: params.readiness,
    on_hold: params.on_hold,
    search: params.search,
    open_only: params.open_only,
  };
}

export async function fetchOrders(client: ApiClient, params: OrderListQuery = {}): Promise<Paged<OrderListItem>> {
  const res = await client.get<PageResponse<OrderListItemResponse>>("/orders", toOrderListParams(params));
  const page = fromPageResponse(res);
  return { items: page.items.map(flattenOrderListItem), meta: page.meta };
}

export function fetchOrder(client: ApiClient, orderId: string): Promise<OrderDetail> {
  return client.get<OrderDetail>(`/orders/${encodeURIComponent(orderId)}`);
}

export function fetchOrderExplanation(client: ApiClient, orderId: string): Promise<ExplanationResponse> {
  return client.get<ExplanationResponse>(`/orders/${encodeURIComponent(orderId)}/explanation`);
}

export function fetchOrderMachines(client: ApiClient, orderId: string): Promise<MachineOptions> {
  return client.get<MachineOptions>(`/orders/${encodeURIComponent(orderId)}/machines`);
}

export function fetchOrderOverrides(client: ApiClient, orderId: string): Promise<OverrideResponse[]> {
  return client.get<OverrideResponse[]>(`/orders/${encodeURIComponent(orderId)}/overrides`);
}

const orderPath = (orderId: string, action: string) => `/orders/${encodeURIComponent(orderId)}/${action}`;

export function expediteOrder(client: ApiClient, orderId: string, body: ExpediteRequest): Promise<ExpediteResponse> {
  return client.post<ExpediteResponse>(orderPath(orderId, "expedite"), body);
}

export function holdOrder(client: ApiClient, orderId: string, body: HoldRequest): Promise<OverrideResponse> {
  return client.post<OverrideResponse>(orderPath(orderId, "hold"), body);
}

export function releaseOrder(client: ApiClient, orderId: string, body: ReleaseRequest): Promise<OverrideResponse> {
  return client.post<OverrideResponse>(orderPath(orderId, "release"), body);
}

export function overridePriority(
  client: ApiClient,
  orderId: string,
  body: OverridePriorityRequest,
): Promise<OverrideResponse> {
  return client.post<OverrideResponse>(orderPath(orderId, "override-priority"), body);
}

export function forceNext(client: ApiClient, orderId: string, body: ForceNextRequest): Promise<OverrideResponse> {
  return client.post<OverrideResponse>(orderPath(orderId, "force-next"), body);
}

export function moveOrder(client: ApiClient, orderId: string, body: MoveOrderRequest): Promise<MoveOrderResponse> {
  return client.post<MoveOrderResponse>(orderPath(orderId, "move"), body);
}

export function lockMachineAssignment(
  client: ApiClient,
  orderId: string,
  body: LockMachineAssignmentRequest,
): Promise<OverrideResponse> {
  return client.post<OverrideResponse>(orderPath(orderId, "lock-machine"), body);
}

// ------------------------------------------------------------------ hooks

type QueryOverrides<T> = Omit<UseQueryOptions<T>, "queryKey" | "queryFn">;

export function useOrders(params: OrderListQuery = {}, enabled = true, options: QueryOverrides<Paged<OrderListItem>> = {}) {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.orders.list(params),
    queryFn: () => fetchOrders(client, params),
    enabled,
    placeholderData: (prev) => prev,
    ...options,
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
    retry: false,
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

export function useOrderOverrides(orderId: string | undefined) {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.orders.overrides(orderId ?? ""),
    queryFn: () => fetchOrderOverrides(client, orderId ?? ""),
    enabled: Boolean(orderId),
  });
}

/** Discriminated union of every order action and its exact request body. */
export type OrderActionInput =
  | { action: "expedite"; body: ExpediteRequest }
  | { action: "hold"; body: HoldRequest }
  | { action: "release"; body: ReleaseRequest }
  | { action: "override-priority"; body: OverridePriorityRequest }
  | { action: "force-next"; body: ForceNextRequest }
  | { action: "move"; body: MoveOrderRequest }
  | { action: "lock-machine"; body: LockMachineAssignmentRequest };

export type OrderActionKind = OrderActionInput["action"];

export type OrderActionResult = ExpediteResponse | OverrideResponse | MoveOrderResponse;

export function runOrderAction(client: ApiClient, orderId: string, input: OrderActionInput): Promise<OrderActionResult> {
  switch (input.action) {
    case "expedite":
      return expediteOrder(client, orderId, input.body);
    case "hold":
      return holdOrder(client, orderId, input.body);
    case "release":
      return releaseOrder(client, orderId, input.body);
    case "override-priority":
      return overridePriority(client, orderId, input.body);
    case "force-next":
      return forceNext(client, orderId, input.body);
    case "move":
      return moveOrder(client, orderId, input.body);
    case "lock-machine":
      return lockMachineAssignment(client, orderId, input.body);
  }
}

/** Query keys every order action invalidates: order lists/detail, overlays, schedule, alerts and the audit trail. */
export async function invalidateAfterOrderAction(qc: ReturnType<typeof useQueryClient>): Promise<void> {
  await Promise.all([
    qc.invalidateQueries({ queryKey: queryKeys.orders.all }),
    qc.invalidateQueries({ queryKey: queryKeys.overlays.all }),
    qc.invalidateQueries({ queryKey: queryKeys.schedule.all }),
    qc.invalidateQueries({ queryKey: queryKeys.machines.all }),
    qc.invalidateQueries({ queryKey: queryKeys.alerts.all }),
    qc.invalidateQueries({ queryKey: queryKeys.audit.all }),
  ]);
}

/** One mutation hook for every order action; invalidates order/overlay/schedule/audit caches on success. */
export function useOrderAction(orderId: string) {
  const client = useApiClient();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: OrderActionInput) => runOrderAction(client, orderId, input),
    onSuccess: () => invalidateAfterOrderAction(qc),
  });
}

/** Same as useOrderAction but the order id travels with each call (for row-level quick actions). */
export function useAnyOrderAction() {
  const client = useApiClient();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ orderId, ...input }: OrderActionInput & { orderId: string }) => runOrderAction(client, orderId, input),
    onSuccess: () => invalidateAfterOrderAction(qc),
  });
}
