/** /customers — customers with tier/SLA and their planner rules (spec Phase 15). */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { ApiClient } from "./client";
import { fromPageResponse, resolvePaging } from "./client";
import { useApiClient } from "./context";
import { queryKeys } from "./queryKeys";
import type { CustomerResponse, CustomerRuleRequest, CustomerRuleResponse, CustomersQuery, PageResponse, Paged } from "./types";

export async function fetchCustomers(client: ApiClient, params: CustomersQuery = {}): Promise<Paged<CustomerResponse>> {
  const paging = resolvePaging(params, 100);
  const res = await client.get<PageResponse<CustomerResponse>>("/customers", { ...paging, search: params.search });
  return fromPageResponse(res);
}

export function fetchCustomer(client: ApiClient, customerId: string): Promise<CustomerResponse> {
  return client.get<CustomerResponse>(`/customers/${encodeURIComponent(customerId)}`);
}

export function fetchCustomerRules(client: ApiClient, customerId: string): Promise<CustomerRuleResponse> {
  return client.get<CustomerRuleResponse>(`/customers/${encodeURIComponent(customerId)}/rules`);
}

export function saveCustomerRules(client: ApiClient, customerId: string, body: CustomerRuleRequest): Promise<CustomerRuleResponse> {
  return client.put<CustomerRuleResponse>(`/customers/${encodeURIComponent(customerId)}/rules`, body);
}

export function deleteCustomerRules(client: ApiClient, customerId: string, reason: string): Promise<CustomerRuleResponse> {
  return client.delete<CustomerRuleResponse>(`/customers/${encodeURIComponent(customerId)}/rules`, { reason });
}

export function useCustomers(params: CustomersQuery = {}, enabled = true) {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.customers.list(params),
    queryFn: () => fetchCustomers(client, params),
    enabled,
    placeholderData: (prev) => prev,
    staleTime: 5 * 60_000,
  });
}

export function useCustomer(customerId: string | undefined) {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.customers.detail(customerId ?? ""),
    queryFn: () => fetchCustomer(client, customerId ?? ""),
    enabled: Boolean(customerId),
  });
}

export function useCustomerRules(customerId: string | undefined) {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.customers.rules(customerId ?? ""),
    queryFn: () => fetchCustomerRules(client, customerId ?? ""),
    enabled: Boolean(customerId),
    retry: false,
  });
}

export function useSaveCustomerRules(customerId: string) {
  const client = useApiClient();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CustomerRuleRequest) => saveCustomerRules(client, customerId, body),
    onSuccess: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: queryKeys.customers.all }),
        qc.invalidateQueries({ queryKey: queryKeys.orders.all }),
        qc.invalidateQueries({ queryKey: queryKeys.audit.all }),
      ]);
    },
  });
}
