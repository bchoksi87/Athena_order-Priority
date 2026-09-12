/** /data-quality — dashboard summary, issues of the latest run and manual runs (spec Phase 21). */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { ApiClient } from "./client";
import { fromPageResponse, resolvePaging } from "./client";
import { useApiClient } from "./context";
import { queryKeys } from "./queryKeys";
import type { DataQualityIssue, DataQualityQuery, DataQualitySummary, PageResponse, Paged } from "./types";

export function fetchDataQualitySummary(client: ApiClient): Promise<DataQualitySummary> {
  return client.get<DataQualitySummary>("/data-quality");
}

export async function fetchDataQualityIssues(client: ApiClient, params: DataQualityQuery = {}): Promise<Paged<DataQualityIssue>> {
  const paging = resolvePaging(params, 50);
  const res = await client.get<PageResponse<DataQualityIssue>>("/data-quality/issues", {
    ...paging,
    severity: params.severity,
    code: params.code,
    entity_type: params.entity_type,
    entity_id: params.entity_id,
  });
  return fromPageResponse(res);
}

/** @deprecated use fetchDataQualityIssues. */
export const fetchDataQuality = fetchDataQualityIssues;

export function runDataQuality(client: ApiClient): Promise<DataQualitySummary> {
  return client.post<DataQualitySummary>("/data-quality/run", {});
}

export function useDataQualitySummary() {
  const client = useApiClient();
  return useQuery({ queryKey: queryKeys.dataQuality.summary, queryFn: () => fetchDataQualitySummary(client) });
}

export function useDataQualityIssues(params: DataQualityQuery = {}) {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.dataQuality.list(params),
    queryFn: () => fetchDataQualityIssues(client, params),
    placeholderData: (prev) => prev,
  });
}

/** @deprecated use useDataQualityIssues. */
export const useDataQuality = useDataQualityIssues;

export function useRunDataQuality() {
  const client = useApiClient();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => runDataQuality(client),
    onSuccess: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: queryKeys.dataQuality.all }),
        qc.invalidateQueries({ queryKey: queryKeys.orders.all }),
        qc.invalidateQueries({ queryKey: queryKeys.alerts.all }),
        qc.invalidateQueries({ queryKey: queryKeys.audit.all }),
      ]);
    },
  });
}
