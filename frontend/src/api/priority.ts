import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { ApiClient } from "./client";
import { unwrapList } from "./client";
import { useApiClient } from "./context";
import { queryKeys } from "./queryKeys";
import type { ConfigVersionInfo, ListResponse, PriorityPreviewResponse, PriorityProfile } from "./types";

export function fetchPriorityProfile(client: ApiClient): Promise<PriorityProfile> {
  return client.get<PriorityProfile>("/priority/configuration");
}

export function savePriorityProfile(client: ApiClient, profile: PriorityProfile): Promise<PriorityProfile> {
  return client.put<PriorityProfile>("/priority/configuration", profile);
}

export async function fetchPriorityProfileVersions(client: ApiClient): Promise<ConfigVersionInfo[]> {
  const res = await client.get<ListResponse<ConfigVersionInfo>>("/priority/configuration/versions");
  return unwrapList(res);
}

export function previewPriorityProfile(client: ApiClient, profile: PriorityProfile): Promise<PriorityPreviewResponse> {
  return client.post<PriorityPreviewResponse>("/priority/configuration/preview", profile);
}

export function usePriorityProfile() {
  const client = useApiClient();
  return useQuery({ queryKey: queryKeys.priority.configuration, queryFn: () => fetchPriorityProfile(client) });
}

export function usePriorityProfileVersions() {
  const client = useApiClient();
  return useQuery({ queryKey: queryKeys.priority.versions, queryFn: () => fetchPriorityProfileVersions(client) });
}

export function useSavePriorityProfile() {
  const client = useApiClient();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (profile: PriorityProfile) => savePriorityProfile(client, profile),
    onSuccess: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: queryKeys.priority.all }),
        qc.invalidateQueries({ queryKey: queryKeys.orders.all }),
      ]);
    },
  });
}

export function usePreviewPriorityProfile() {
  const client = useApiClient();
  return useMutation({ mutationFn: (profile: PriorityProfile) => previewPriorityProfile(client, profile) });
}
