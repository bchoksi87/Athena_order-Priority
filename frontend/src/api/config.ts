/** /scheduling/configuration — scheduling, replanning, alert and data-quality rule sets with versions. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { ApiClient } from "./client";
import { unwrapList } from "./client";
import { useApiClient } from "./context";
import { queryKeys } from "./queryKeys";
import type {
  ConfigUpdateResponse,
  ConfigVersionInfo,
  ListResponse,
  SchedulingConfigUpdateRequest,
  SchedulingConfigurationResponse,
  SystemConfigVersionResponse,
} from "./types";

export function fetchSchedulingConfiguration(client: ApiClient): Promise<SchedulingConfigurationResponse> {
  return client.get<SchedulingConfigurationResponse>("/scheduling/configuration");
}

export function saveSchedulingConfiguration(client: ApiClient, body: SchedulingConfigUpdateRequest): Promise<ConfigUpdateResponse> {
  return client.put<ConfigUpdateResponse>("/scheduling/configuration", body);
}

export async function fetchSchedulingConfigVersions(client: ApiClient, limit = 100): Promise<ConfigVersionInfo[]> {
  const res = await client.get<ListResponse<ConfigVersionInfo>>("/scheduling/configuration/versions", { limit });
  return unwrapList(res);
}

export function fetchSchedulingConfigVersion(client: ApiClient, version: number): Promise<SystemConfigVersionResponse> {
  return client.get<SystemConfigVersionResponse>(`/scheduling/configuration/versions/${version}`);
}

export function activateSchedulingConfigVersion(client: ApiClient, version: number, reason: string): Promise<ConfigUpdateResponse> {
  return client.post<ConfigUpdateResponse>(`/scheduling/configuration/versions/${version}/activate`, { reason });
}

/** Full response: scheduling + replanning + alerts + data_quality sections and the active version. */
export function useSchedulingConfiguration() {
  const client = useApiClient();
  return useQuery({ queryKey: queryKeys.scheduling.configuration, queryFn: () => fetchSchedulingConfiguration(client) });
}

/** The scheduling section only (kept for pages that read at_risk_slack_hours etc.). */
export function useSchedulingConfig() {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.scheduling.configuration,
    queryFn: () => fetchSchedulingConfiguration(client),
    select: (res) => res.scheduling,
  });
}

export function useSchedulingConfigVersions(limit = 100) {
  const client = useApiClient();
  return useQuery({ queryKey: queryKeys.scheduling.versions, queryFn: () => fetchSchedulingConfigVersions(client, limit) });
}

export function useSchedulingConfigVersion(version: number | undefined) {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.scheduling.version(version ?? 0),
    queryFn: () => fetchSchedulingConfigVersion(client, version ?? 0),
    enabled: version !== undefined,
  });
}

async function invalidateConfig(qc: ReturnType<typeof useQueryClient>): Promise<void> {
  await Promise.all([
    qc.invalidateQueries({ queryKey: queryKeys.scheduling.all }),
    qc.invalidateQueries({ queryKey: queryKeys.priority.all }),
    qc.invalidateQueries({ queryKey: queryKeys.audit.all }),
  ]);
}

export function useSaveSchedulingConfiguration() {
  const client = useApiClient();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: SchedulingConfigUpdateRequest) => saveSchedulingConfiguration(client, body),
    onSuccess: () => invalidateConfig(qc),
  });
}

/** @deprecated use useSaveSchedulingConfiguration (sections + mandatory reason). */
export const useSaveSchedulingConfig = useSaveSchedulingConfiguration;

export function useActivateSchedulingConfigVersion() {
  const client = useApiClient();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ version, reason }: { version: number; reason: string }) => activateSchedulingConfigVersion(client, version, reason),
    onSuccess: () => invalidateConfig(qc),
  });
}
