/** /users — administrator account management (admin only). */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { ApiClient } from "./client";
import { unwrapList } from "./client";
import { useApiClient } from "./context";
import { queryKeys } from "./queryKeys";
import type { ListResponse, ResetPasswordRequest, UserAccount, UserCreateRequest, UserUpdateRequest } from "./types";

export async function fetchUsers(client: ApiClient, activeOnly = false): Promise<UserAccount[]> {
  const res = await client.get<ListResponse<UserAccount>>("/users", { active_only: activeOnly });
  return unwrapList(res);
}

export function fetchUser(client: ApiClient, userId: string): Promise<UserAccount> {
  return client.get<UserAccount>(`/users/${encodeURIComponent(userId)}`);
}

export function createUser(client: ApiClient, body: UserCreateRequest): Promise<UserAccount> {
  return client.post<UserAccount>("/users", body);
}

export function updateUserActive(client: ApiClient, userId: string, body: UserUpdateRequest): Promise<UserAccount> {
  return client.request<UserAccount>("PATCH", `/users/${encodeURIComponent(userId)}`, { body });
}

export function resetUserPassword(client: ApiClient, userId: string, body: ResetPasswordRequest): Promise<UserAccount> {
  return client.post<UserAccount>(`/users/${encodeURIComponent(userId)}/reset-password`, body);
}

export function useUsers(activeOnly = false, enabled = true) {
  const client = useApiClient();
  return useQuery({ queryKey: queryKeys.users.list({ activeOnly }), queryFn: () => fetchUsers(client, activeOnly), enabled });
}

function useInvalidateUsers() {
  const qc = useQueryClient();
  return async () => {
    await Promise.all([qc.invalidateQueries({ queryKey: queryKeys.users.all }), qc.invalidateQueries({ queryKey: queryKeys.audit.all })]);
  };
}

export function useCreateUser() {
  const client = useApiClient();
  const invalidate = useInvalidateUsers();
  return useMutation({ mutationFn: (body: UserCreateRequest) => createUser(client, body), onSuccess: invalidate });
}

export function useSetUserActive() {
  const client = useApiClient();
  const invalidate = useInvalidateUsers();
  return useMutation({
    mutationFn: ({ userId, ...body }: UserUpdateRequest & { userId: string }) => updateUserActive(client, userId, body),
    onSuccess: invalidate,
  });
}

export function useResetUserPassword() {
  const client = useApiClient();
  const invalidate = useInvalidateUsers();
  return useMutation({
    mutationFn: ({ userId, ...body }: ResetPasswordRequest & { userId: string }) => resetUserPassword(client, userId, body),
    onSuccess: invalidate,
  });
}
