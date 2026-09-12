/**
 * /schedule — the active plan, versions, generation and the approval workflow, the Gantt and
 * day views, version comparison, continuous replanning and locks (spec Phases 6, 8, 10, 11, 36, 37).
 * Response shapes follow backend/app/api/schemas/{schedule,schedule_views}.py exactly.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { ApiClient, QueryParams } from "./client";
import { fromPageResponse, resolvePaging } from "./client";
import { useApiClient } from "./context";
import { queryKeys } from "./queryKeys";
import type {
  DaySchedule,
  GanttQuery,
  GanttView,
  GenerateRequest,
  LockRequest,
  LockResponse,
  PageResponse,
  Paged,
  PublishResponse,
  ReplanOutcome,
  ReplanRequest,
  ScheduleComparison,
  ScheduleEntriesQuery,
  ScheduleEntry,
  ScheduleGenerateResponse,
  SchedulePlan,
  ScheduleVersionDetail,
  ScheduleVersionResponse,
  ScheduleVersionsQuery,
  UnlockRequest,
  VersionActionRequest,
} from "./types";

function entriesParams(params: ScheduleEntriesQuery): QueryParams {
  const paging = resolvePaging(params, 100);
  return {
    ...paging,
    machine_id: params.machine_id,
    start: params.start,
    end: params.end,
    order_id: params.order_id,
    customer_id: params.customer_id,
  };
}

// ------------------------------------------------------------ active plan

/** GET /schedule: latest published, else approved, else draft version with one page of entries. */
export function fetchSchedulePlan(client: ApiClient, params: ScheduleEntriesQuery = {}): Promise<SchedulePlan> {
  return client.get<SchedulePlan>("/schedule", entriesParams(params));
}

// --------------------------------------------------------------- versions

export async function fetchScheduleVersions(client: ApiClient, params: ScheduleVersionsQuery = {}): Promise<Paged<ScheduleVersionResponse>> {
  const paging = resolvePaging(params, 50);
  const res = await client.get<PageResponse<ScheduleVersionResponse>>("/schedule/versions", { ...paging, status: params.status });
  return fromPageResponse(res);
}

export function fetchScheduleVersion(client: ApiClient, version: number): Promise<ScheduleVersionDetail> {
  return client.get<ScheduleVersionDetail>(`/schedule/versions/${version}`);
}

export async function fetchVersionEntries(client: ApiClient, version: number, params: ScheduleEntriesQuery = {}): Promise<Paged<ScheduleEntry>> {
  const res = await client.get<PageResponse<ScheduleEntry>>(`/schedule/versions/${version}/entries`, entriesParams(params));
  return fromPageResponse(res);
}

// ------------------------------------------------------------------ views

/** GET /schedule/gantt: rows per machine with setup/run blocks, downtime and locks for a window. */
export function fetchGantt(client: ApiClient, params: GanttQuery = {}): Promise<GanttView> {
  return client.get<GanttView>("/schedule/gantt", { ...params });
}

/** GET /schedule/{date}: plant-local day grouped by machine (yyyy-MM-dd). */
export function fetchDaySchedule(client: ApiClient, date: string, version?: number): Promise<DaySchedule> {
  return client.get<DaySchedule>(`/schedule/${encodeURIComponent(date)}`, { version });
}

/** GET /schedule/compare?a=&b=: "On-time delivery: 87% → 94%" pairs plus moved/added/removed entries. */
export function fetchScheduleComparison(client: ApiClient, a: number, b: number): Promise<ScheduleComparison> {
  return client.get<ScheduleComparison>("/schedule/compare", { a, b });
}

// ---------------------------------------------------------------- actions

export function generateSchedule(client: ApiClient, body: GenerateRequest = {}): Promise<ScheduleGenerateResponse> {
  return client.post<ScheduleGenerateResponse>("/schedule/generate", body);
}

export function approveSchedule(client: ApiClient, body: VersionActionRequest): Promise<ScheduleVersionDetail> {
  return client.post<ScheduleVersionDetail>("/schedule/approve", body);
}

export function publishSchedule(client: ApiClient, body: VersionActionRequest): Promise<PublishResponse> {
  return client.post<PublishResponse>("/schedule/publish", body);
}

export function rejectSchedule(client: ApiClient, body: VersionActionRequest): Promise<ScheduleVersionDetail> {
  return client.post<ScheduleVersionDetail>("/schedule/reject", body);
}

/** POST /schedule/replan: detect changes, generate a candidate and apply the stability rules. */
export function evaluateReplan(client: ApiClient, body: ReplanRequest = {}): Promise<ReplanOutcome> {
  return client.post<ReplanOutcome>("/schedule/replan", body);
}

export function lockSchedule(client: ApiClient, body: LockRequest): Promise<LockResponse> {
  return client.post<LockResponse>("/schedule/lock", body);
}

export function unlockSchedule(client: ApiClient, body: UnlockRequest): Promise<LockResponse> {
  return client.post<LockResponse>("/schedule/unlock", body);
}

// ------------------------------------------------------------------ hooks

export function useSchedulePlan(params: ScheduleEntriesQuery = {}, refetchIntervalMs: number | false = false) {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.schedule.plan(params),
    queryFn: () => fetchSchedulePlan(client, params),
    refetchInterval: refetchIntervalMs,
    placeholderData: (prev) => prev,
  });
}

export function useScheduleVersions(params: ScheduleVersionsQuery = {}) {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.schedule.versionList(params),
    queryFn: () => fetchScheduleVersions(client, params),
    placeholderData: (prev) => prev,
  });
}

export function useScheduleVersion(version: number | undefined) {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.schedule.versionDetail(version ?? -1),
    queryFn: () => fetchScheduleVersion(client, version ?? -1),
    enabled: version !== undefined && version >= 1,
  });
}

export function useVersionEntries(version: number | undefined, params: ScheduleEntriesQuery = {}) {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.schedule.versionEntries(version ?? -1, params),
    queryFn: () => fetchVersionEntries(client, version ?? -1, params),
    enabled: version !== undefined && version >= 1,
    placeholderData: (prev) => prev,
  });
}

export function useGantt(params: GanttQuery = {}, enabled = true) {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.schedule.gantt(params),
    queryFn: () => fetchGantt(client, params),
    enabled,
    placeholderData: (prev) => prev,
  });
}

export function useDaySchedule(date: string | undefined, version?: number) {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.schedule.day(date ?? "", version),
    queryFn: () => fetchDaySchedule(client, date ?? "", version),
    enabled: Boolean(date),
    placeholderData: (prev) => prev,
  });
}

export function useScheduleComparison(a: number | undefined, b: number | undefined) {
  const client = useApiClient();
  const ok = a !== undefined && b !== undefined && a >= 1 && b >= 1 && a !== b;
  return useQuery({
    queryKey: queryKeys.schedule.compare(a ?? -1, b ?? -1),
    queryFn: () => fetchScheduleComparison(client, a ?? -1, b ?? -1),
    enabled: ok,
  });
}

/** Every plan-changing action touches orders, machines, analytics, alerts, audit and the health probe. */
function useInvalidatePlan() {
  const qc = useQueryClient();
  return async () => {
    await Promise.all([
      qc.invalidateQueries({ queryKey: queryKeys.schedule.all }),
      qc.invalidateQueries({ queryKey: queryKeys.orders.all }),
      qc.invalidateQueries({ queryKey: queryKeys.machines.all }),
      qc.invalidateQueries({ queryKey: queryKeys.analytics.all }),
      qc.invalidateQueries({ queryKey: queryKeys.alerts.all }),
      qc.invalidateQueries({ queryKey: queryKeys.audit.all }),
      qc.invalidateQueries({ queryKey: queryKeys.system.health }),
    ]);
  };
}

export function useGenerateSchedule() {
  const client = useApiClient();
  const invalidate = useInvalidatePlan();
  return useMutation({ mutationFn: (body: GenerateRequest = {}) => generateSchedule(client, body), onSuccess: invalidate });
}

export function useApproveSchedule() {
  const client = useApiClient();
  const invalidate = useInvalidatePlan();
  return useMutation({ mutationFn: (body: VersionActionRequest) => approveSchedule(client, body), onSuccess: invalidate });
}

export function usePublishSchedule() {
  const client = useApiClient();
  const invalidate = useInvalidatePlan();
  return useMutation({ mutationFn: (body: VersionActionRequest) => publishSchedule(client, body), onSuccess: invalidate });
}

export function useRejectSchedule() {
  const client = useApiClient();
  const invalidate = useInvalidatePlan();
  return useMutation({ mutationFn: (body: VersionActionRequest) => rejectSchedule(client, body), onSuccess: invalidate });
}

export function useEvaluateReplan() {
  const client = useApiClient();
  const invalidate = useInvalidatePlan();
  return useMutation({ mutationFn: (body: ReplanRequest = {}) => evaluateReplan(client, body), onSuccess: invalidate });
}

export function useLockSchedule() {
  const client = useApiClient();
  const invalidate = useInvalidatePlan();
  return useMutation({ mutationFn: (body: LockRequest) => lockSchedule(client, body), onSuccess: invalidate });
}

export function useUnlockSchedule() {
  const client = useApiClient();
  const invalidate = useInvalidatePlan();
  return useMutation({ mutationFn: (body: UnlockRequest) => unlockSchedule(client, body), onSuccess: invalidate });
}
