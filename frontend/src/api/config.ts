import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { ApiClient } from "./client";
import { useApiClient } from "./context";
import { queryKeys } from "./queryKeys";
import type { CustomerRule, SchedulingConfig } from "./types";

export function fetchSchedulingConfig(client: ApiClient): Promise<SchedulingConfig> {
  return client.get<SchedulingConfig>("/scheduling/configuration");
}

export function saveSchedulingConfig(client: ApiClient, config: SchedulingConfig): Promise<SchedulingConfig> {
  return client.put<SchedulingConfig>("/scheduling/configuration", config);
}

export function fetchCustomerRules(client: ApiClient, customerId: string): Promise<CustomerRule> {
  return client.get<CustomerRule>(`/customers/${encodeURIComponent(customerId)}/rules`);
}

export function saveCustomerRules(client: ApiClient, customerId: string, rule: CustomerRule): Promise<CustomerRule> {
  return client.put<CustomerRule>(`/customers/${encodeURIComponent(customerId)}/rules`, rule);
}

export function useSchedulingConfig() {
  const client = useApiClient();
  return useQuery({ queryKey: queryKeys.scheduling.configuration, queryFn: () => fetchSchedulingConfig(client) });
}

export function useSaveSchedulingConfig() {
  const client = useApiClient();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (config: SchedulingConfig) => saveSchedulingConfig(client, config),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: queryKeys.scheduling.configuration });
    },
  });
}

export function useCustomerRules(customerId: string | undefined) {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.customers.rules(customerId ?? ""),
    queryFn: () => fetchCustomerRules(client, customerId ?? ""),
    enabled: Boolean(customerId),
  });
}

export function useSaveCustomerRules(customerId: string) {
  const client = useApiClient();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (rule: CustomerRule) => saveCustomerRules(client, customerId, rule),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: queryKeys.customers.rules(customerId) });
    },
  });
}
