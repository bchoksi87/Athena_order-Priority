/**
 * What-if simulation (spec Phase 7): POST /schedule/simulate runs baseline and scenario
 * schedules on a cloned snapshot — nothing is persisted; GET /simulation/scenario-types lists
 * the scenario kinds with their JSON schema for the builder.
 */
import { useMutation, useQuery } from "@tanstack/react-query";

import type { ApiClient } from "./client";
import { useApiClient } from "./context";
import { queryKeys } from "./queryKeys";
import type { ScenarioTypes, SimulateBody, SimulationResponse, SimulationScenarioKind } from "./types";

/** Every scenario kind accepted by the backend, in the catalogue's order. */
export const SIMULATION_SCENARIO_KINDS: readonly SimulationScenarioKind[] = [
  "machine_down",
  "urgent_orders",
  "add_machine",
  "extra_working_day",
  "extra_shift",
  "outsource",
  "material_delay",
  "material_arrival",
  "prioritize_customer",
  "weight_change",
  "due_date_change",
  "hold_orders",
  "expedite_orders",
];

export function runSimulation(client: ApiClient, body: SimulateBody): Promise<SimulationResponse> {
  return client.post<SimulationResponse>("/schedule/simulate", body);
}

export function fetchScenarioTypes(client: ApiClient): Promise<ScenarioTypes> {
  return client.get<ScenarioTypes>("/simulation/scenario-types");
}

/** A simulation is never cached: it is always an explicit user action. */
export function useRunSimulation() {
  const client = useApiClient();
  return useMutation({ mutationFn: (body: SimulateBody) => runSimulation(client, body) });
}

export function useScenarioTypes() {
  const client = useApiClient();
  return useQuery({ queryKey: queryKeys.simulation.scenarioTypes, queryFn: () => fetchScenarioTypes(client), staleTime: 10 * 60_000 });
}
