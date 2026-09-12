import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { ApiClient } from "./client";
import { toPaged } from "./client";
import { useApiClient } from "./context";
import { queryKeys } from "./queryKeys";
import type { DataQualityIssue, DataQualityQuery, DataQualityRunResponse, ListResponse, Paged } from "./types";

export async function fetchDataQuality(
  client: ApiClient,
  params: DataQualityQuery = {},
): Promise<Paged<DataQualityIssue>> {
  const res = await client.get<ListResponse<DataQualityIssue>>("/data-quality", { ...params });
  return toPaged(res);
}

export function runDataQuality(client: ApiClient): Promise<DataQualityRunResponse> {
  return client.post<DataQualityRunResponse>("/data-quality/run", {});
}

export function useDataQuality(params: DataQualityQuery = {}) {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.dataQuality.list(params),
    queryFn: () => fetchDataQuality(client, params),
    placeholderData: (prev) => prev,
  });
}

export function useRunDataQuality() {
  const client = useApiClient();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => runDataQuality(client),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: queryKeys.dataQuality.all });
    },
  });
}
