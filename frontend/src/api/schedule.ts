import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { ApiClient } from "./client";
import { unwrapList } from "./client";
import { useApiClient } from "./context";
import { queryKeys } from "./queryKeys";
import type {
  GenerateScheduleRequest,
  ListResponse,
  LockRequest,
  MessageResponse,
  ScheduleActionRequest,
  ScheduleQuery,
  ScheduleVersion,
  ScheduleView,
  UnlockRequest,
} from "./types";

export function fetchSchedule(client: ApiClient, params: ScheduleQuery = {}): Promise<ScheduleView> {
  return client.get<ScheduleView>("/schedule", { ...params });
}

export function fetchScheduleForDate(client: ApiClient, date: string): Promise<ScheduleView> {
  return client.get<ScheduleView>(`/schedule/${encodeURIComponent(date)}`);
}

export async function fetchScheduleVersions(client: ApiClient): Promise<ScheduleVersion[]> {
  const res = await client.get<ListResponse<ScheduleVersion>>("/schedule/versions");
  return unwrapList(res);
}

export function fetchScheduleVersion(client: ApiClient, version: number): Promise<ScheduleView> {
  return client.get<ScheduleView>(`/schedule/versions/${version}`);
}

export function generateSchedule(client: ApiClient, body: GenerateScheduleRequest = {}): Promise<ScheduleView> {
  return client.post<ScheduleView>("/schedule/generate", body);
}

export function approveSchedule(client: ApiClient, body: ScheduleActionRequest): Promise<ScheduleVersion> {
  return client.post<ScheduleVersion>("/schedule/approve", body);
}

export function publishSchedule(client: ApiClient, body: ScheduleActionRequest): Promise<ScheduleVersion> {
  return client.post<ScheduleVersion>("/schedule/publish", body);
}

export function lockSchedule(client: ApiClient, body: LockRequest): Promise<MessageResponse> {
  return client.post<MessageResponse>("/schedule/lock", body);
}

export function unlockSchedule(client: ApiClient, body: UnlockRequest): Promise<MessageResponse> {
  return client.post<MessageResponse>("/schedule/unlock", body);
}

// ------------------------------------------------------------------ hooks

export function useSchedule(params: ScheduleQuery = {}, enabled = true) {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.schedule.current(params),
    queryFn: () => fetchSchedule(client, params),
    enabled,
    placeholderData: (prev) => prev,
  });
}

export function useScheduleForDate(date: string | undefined) {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.schedule.byDate(date ?? ""),
    queryFn: () => fetchScheduleForDate(client, date ?? ""),
    enabled: Boolean(date),
  });
}

export function useScheduleVersions() {
  const client = useApiClient();
  return useQuery({ queryKey: queryKeys.schedule.versions, queryFn: () => fetchScheduleVersions(client) });
}

export function useScheduleVersion(version: number | undefined) {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.schedule.version(version ?? -1),
    queryFn: () => fetchScheduleVersion(client, version ?? -1),
    enabled: version !== undefined && version >= 0,
  });
}

function useInvalidateSchedule() {
  const qc = useQueryClient();
  return async () => {
    await Promise.all([
      qc.invalidateQueries({ queryKey: queryKeys.schedule.all }),
      qc.invalidateQueries({ queryKey: queryKeys.orders.all }),
      qc.invalidateQueries({ queryKey: queryKeys.machines.all }),
      qc.invalidateQueries({ queryKey: queryKeys.analytics.kpis }),
      qc.invalidateQueries({ queryKey: queryKeys.audit.all }),
    ]);
  };
}

export function useGenerateSchedule() {
  const client = useApiClient();
  const invalidate = useInvalidateSchedule();
  return useMutation({
    mutationFn: (body: GenerateScheduleRequest = {}) => generateSchedule(client, body),
    onSuccess: invalidate,
  });
}

export function useApproveSchedule() {
  const client = useApiClient();
  const invalidate = useInvalidateSchedule();
  return useMutation({ mutationFn: (body: ScheduleActionRequest) => approveSchedule(client, body), onSuccess: invalidate });
}

export function usePublishSchedule() {
  const client = useApiClient();
  const invalidate = useInvalidateSchedule();
  return useMutation({ mutationFn: (body: ScheduleActionRequest) => publishSchedule(client, body), onSuccess: invalidate });
}

export function useLockSchedule() {
  const client = useApiClient();
  const invalidate = useInvalidateSchedule();
  return useMutation({ mutationFn: (body: LockRequest) => lockSchedule(client, body), onSuccess: invalidate });
}

export function useUnlockSchedule() {
  const client = useApiClient();
  const invalidate = useInvalidateSchedule();
  return useMutation({ mutationFn: (body: UnlockRequest) => unlockSchedule(client, body), onSuccess: invalidate });
}
