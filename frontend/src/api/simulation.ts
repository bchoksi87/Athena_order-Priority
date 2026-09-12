import { useMutation } from "@tanstack/react-query";

import type { ApiClient } from "./client";
import { useApiClient } from "./context";
import type { SimulateRequest, SimulationResult } from "./types";

export function runSimulation(client: ApiClient, body: SimulateRequest): Promise<SimulationResult> {
  return client.post<SimulationResult>("/schedule/simulate", body);
}

/** What-if simulation: never cached, always an explicit user action. */
export function useRunSimulation() {
  const client = useApiClient();
  return useMutation({ mutationFn: (body: SimulateRequest) => runSimulation(client, body) });
}
