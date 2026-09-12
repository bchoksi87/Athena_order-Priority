import { createContext, useContext } from "react";

import type { ApiClient } from "./client";

const ApiClientContext = createContext<ApiClient | null>(null);

export const ApiClientProvider = ApiClientContext.Provider;

/** The ApiClient wired by AppProviders; throws when used outside the provider tree. */
export function useApiClient(): ApiClient {
  const client = useContext(ApiClientContext);
  if (!client) throw new Error("useApiClient must be used inside <AppProviders>");
  return client;
}
