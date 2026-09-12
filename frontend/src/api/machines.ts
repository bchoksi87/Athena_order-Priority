import { useQuery } from "@tanstack/react-query";

import type { ApiClient } from "./client";
import { unwrapList } from "./client";
import { useApiClient } from "./context";
import { queryKeys } from "./queryKeys";
import type { ListResponse, MachineDetail, MachineListItem, MachineScheduleResponse, ScheduleEntry } from "./types";

export interface MachineListQuery {
  machine_group?: string;
  process_type?: string;
  status?: string;
}

export async function fetchMachines(client: ApiClient, params: MachineListQuery = {}): Promise<MachineListItem[]> {
  const res = await client.get<ListResponse<MachineListItem>>("/machines", { ...params });
  return unwrapList(res);
}

export function fetchMachine(client: ApiClient, machineId: string): Promise<MachineDetail> {
  return client.get<MachineDetail>(`/machines/${encodeURIComponent(machineId)}`);
}

export async function fetchMachineSchedule(
  client: ApiClient,
  machineId: string,
  params: { from?: string; to?: string; version?: number } = {},
): Promise<ScheduleEntry[]> {
  const res = await client.get<MachineScheduleResponse | ListResponse<ScheduleEntry>>(
    `/machines/${encodeURIComponent(machineId)}/schedule`,
    { ...params },
  );
  if (res && !Array.isArray(res) && "entries" in res) return res.entries;
  return unwrapList(res as ListResponse<ScheduleEntry>);
}

export function useMachines(params: MachineListQuery = {}) {
  const client = useApiClient();
  return useQuery({ queryKey: queryKeys.machines.list(params), queryFn: () => fetchMachines(client, params) });
}

export function useMachine(machineId: string | undefined) {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.machines.detail(machineId ?? ""),
    queryFn: () => fetchMachine(client, machineId ?? ""),
    enabled: Boolean(machineId),
  });
}

export function useMachineSchedule(machineId: string | undefined, params: { from?: string; to?: string } = {}) {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.machines.schedule(machineId ?? "", params),
    queryFn: () => fetchMachineSchedule(client, machineId ?? "", params),
    enabled: Boolean(machineId),
  });
}
