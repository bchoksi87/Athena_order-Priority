/** /priority/configuration — the active profile, versions (activate/rollback) and the weight preview. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { ApiClient } from "./client";
import { unwrapList } from "./client";
import { useApiClient } from "./context";
import { queryKeys } from "./queryKeys";
import type {
  ConfigUpdateResponse,
  ConfigVersionInfo,
  ListResponse,
  PreviewRequest,
  PreviewResponse,
  PriorityConfigurationResponse,
  PriorityProfile,
  SystemConfigVersionResponse,
} from "./types";

export function fetchPriorityConfiguration(client: ApiClient): Promise<PriorityConfigurationResponse> {
  return client.get<PriorityConfigurationResponse>("/priority/configuration");
}

export function savePriorityProfile(client: ApiClient, profile: PriorityProfile, reason: string): Promise<ConfigUpdateResponse> {
  return client.put<ConfigUpdateResponse>("/priority/configuration", { profile, reason });
}

export async function fetchPriorityProfileVersions(client: ApiClient, limit = 100): Promise<ConfigVersionInfo[]> {
  const res = await client.get<ListResponse<ConfigVersionInfo>>("/priority/configuration/versions", { limit });
  return unwrapList(res);
}

export function fetchPriorityProfileVersion(client: ApiClient, version: number): Promise<SystemConfigVersionResponse> {
  return client.get<SystemConfigVersionResponse>(`/priority/configuration/versions/${version}`);
}

export function activatePriorityProfileVersion(client: ApiClient, version: number, reason: string): Promise<ConfigUpdateResponse> {
  return client.post<ConfigUpdateResponse>(`/priority/configuration/versions/${version}/activate`, { reason });
}

export function previewPriorityProfile(client: ApiClient, body: PreviewRequest): Promise<PreviewResponse> {
  return client.post<PreviewResponse>("/priority/configuration/preview", body);
}

export function usePriorityConfiguration() {
  const client = useApiClient();
  return useQuery({ queryKey: queryKeys.priority.configuration, queryFn: () => fetchPriorityConfiguration(client) });
}

/** @deprecated use usePriorityConfiguration (returns the full response). */
export const usePriorityProfile = usePriorityConfiguration;

export function usePriorityProfileVersions(limit = 100) {
  const client = useApiClient();
  return useQuery({ queryKey: queryKeys.priority.versions, queryFn: () => fetchPriorityProfileVersions(client, limit) });
}

export function usePriorityProfileVersion(version: number | undefined) {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.priority.version(version ?? 0),
    queryFn: () => fetchPriorityProfileVersion(client, version ?? 0),
    enabled: version !== undefined,
  });
}

async function invalidateConfig(qc: ReturnType<typeof useQueryClient>): Promise<void> {
  await Promise.all([
    qc.invalidateQueries({ queryKey: queryKeys.priority.all }),
    qc.invalidateQueries({ queryKey: queryKeys.scheduling.all }),
    qc.invalidateQueries({ queryKey: queryKeys.orders.all }),
    qc.invalidateQueries({ queryKey: queryKeys.audit.all }),
  ]);
}

export function useSavePriorityProfile() {
  const client = useApiClient();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ profile, reason }: { profile: PriorityProfile; reason: string }) => savePriorityProfile(client, profile, reason),
    onSuccess: () => invalidateConfig(qc),
  });
}

export function useActivatePriorityProfileVersion() {
  const client = useApiClient();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ version, reason }: { version: number; reason: string }) => activatePriorityProfileVersion(client, version, reason),
    onSuccess: () => invalidateConfig(qc),
  });
}

export function usePreviewPriorityProfile() {
  const client = useApiClient();
  return useMutation({ mutationFn: (body: PreviewRequest) => previewPriorityProfile(client, body) });
}
