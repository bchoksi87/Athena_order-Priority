/** /machines — machine list with load, machine detail and the per-machine schedule. */
import { useQuery } from "@tanstack/react-query";

import type { ApiClient } from "./client";
import { unwrapList } from "./client";
import { useApiClient } from "./context";
import { queryKeys } from "./queryKeys";
import type {
  ListResponse,
  MachineDetail,
  MachineListItem,
  MachineListItemResponse,
  MachineListQuery,
  MachineScheduleQuery,
  MachineScheduleResponse,
  ScheduleEntry,
} from "./types";

export type { MachineListQuery } from "./types";

/** Flattens {machine, load, active_locks} into one row for tables. */
export function flattenMachineListItem(row: MachineListItemResponse): MachineListItem {
  return {
    ...row.machine,
    load: row.load,
    active_locks: row.active_locks,
    utilization_pct: row.load.utilization_pct,
    scheduled_hours: row.load.scheduled_hours,
    setup_hours: row.load.setup_hours,
    scheduled_entries: row.load.scheduled_entries,
    next_free: row.load.next_free,
    maintenance_windows: [],
    planned_downtime: [],
    unplanned_downtime: [],
  };
}

export async function fetchMachines(client: ApiClient, params: MachineListQuery = {}): Promise<MachineListItem[]> {
  const res = await client.get<ListResponse<MachineListItemResponse>>("/machines", { ...params });
  return unwrapList(res).map(flattenMachineListItem);
}

export function fetchMachine(client: ApiClient, machineId: string): Promise<MachineDetail> {
  return client.get<MachineDetail>(`/machines/${encodeURIComponent(machineId)}`);
}

export function fetchMachineScheduleView(
  client: ApiClient,
  machineId: string,
  params: MachineScheduleQuery = {},
): Promise<MachineScheduleResponse> {
  return client.get<MachineScheduleResponse>(`/machines/${encodeURIComponent(machineId)}/schedule`, { ...params });
}

/** Entries only (kept for the machine schedule/Gantt pages). Accepts legacy from/to as start/end. */
export async function fetchMachineSchedule(
  client: ApiClient,
  machineId: string,
  params: MachineScheduleQuery & { from?: string; to?: string } = {},
): Promise<ScheduleEntry[]> {
  const { from, to, ...rest } = params;
  const res = await fetchMachineScheduleView(client, machineId, { start: rest.start ?? from, end: rest.end ?? to, version: rest.version });
  return res.entries;
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

export function useMachineSchedule(machineId: string | undefined, params: MachineScheduleQuery & { from?: string; to?: string } = {}) {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.machines.schedule(machineId ?? "", params),
    queryFn: () => fetchMachineSchedule(client, machineId ?? "", params),
    enabled: Boolean(machineId),
  });
}

/** Full GET /machines/{id}/schedule response (entries + downtime + locks + window). */
export function useMachineScheduleView(machineId: string | undefined, params: MachineScheduleQuery = {}) {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.machines.schedule(machineId ?? "", { view: true, ...params }),
    queryFn: () => fetchMachineScheduleView(client, machineId ?? "", params),
    enabled: Boolean(machineId),
    placeholderData: (prev) => prev,
  });
}
