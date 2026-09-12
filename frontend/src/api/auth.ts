import { useMutation, useQuery } from "@tanstack/react-query";

import type { ApiClient } from "./client";
import { useApiClient } from "./context";
import { queryKeys } from "./queryKeys";
import type { TokenResponse, UserInfo } from "./types";

export function login(client: ApiClient, username: string, password: string): Promise<TokenResponse> {
  return client.post<TokenResponse>("/auth/login", { username, password });
}

export function fetchMe(client: ApiClient): Promise<UserInfo> {
  return client.get<UserInfo>("/auth/me");
}

export function useLoginMutation() {
  const client = useApiClient();
  return useMutation({
    mutationFn: ({ username, password }: { username: string; password: string }) => login(client, username, password),
  });
}

export function useMe(enabled = true) {
  const client = useApiClient();
  return useQuery({ queryKey: queryKeys.auth.me, queryFn: () => fetchMe(client), enabled, retry: false });
}
