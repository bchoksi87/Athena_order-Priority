/** /audit — the immutable trail of overrides, locks, expedites, approvals and configuration changes. */
import { useQuery } from "@tanstack/react-query";

import type { ApiClient } from "./client";
import { fromPageResponse, resolvePaging } from "./client";
import { useApiClient } from "./context";
import { queryKeys } from "./queryKeys";
import type { AuditEntry, AuditQuery, PageResponse, Paged } from "./types";

export async function fetchAudit(client: ApiClient, params: AuditQuery = {}): Promise<Paged<AuditEntry>> {
  const paging = resolvePaging(params, 50);
  const res = await client.get<PageResponse<AuditEntry>>("/audit", {
    ...paging,
    entity_type: params.entity_type,
    entity_id: params.entity_id,
    user: params.user ?? params.user_id,
    action: params.action,
    from: params.from,
    to: params.to,
  });
  return fromPageResponse(res);
}

export function useAudit(params: AuditQuery = {}, enabled = true) {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.audit.list(params),
    queryFn: () => fetchAudit(client, params),
    enabled,
    placeholderData: (prev) => prev,
  });
}
