import { useQuery } from "@tanstack/react-query";

import type { ApiClient } from "./client";
import { toPaged } from "./client";
import { useApiClient } from "./context";
import { queryKeys } from "./queryKeys";
import type { AuditEntry, AuditQuery, ListResponse, Paged } from "./types";

export async function fetchAudit(client: ApiClient, params: AuditQuery = {}): Promise<Paged<AuditEntry>> {
  const res = await client.get<ListResponse<AuditEntry>>("/audit", { ...params });
  return toPaged(res);
}

export function useAudit(params: AuditQuery = {}) {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.audit.list(params),
    queryFn: () => fetchAudit(client, params),
    placeholderData: (prev) => prev,
  });
}
