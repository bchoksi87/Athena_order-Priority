/**
 * What-If Simulation (spec Phase 7): build scenarios, run them on a cloned snapshot (POST
 * /schedule/simulate — the real plan is never touched) and read the impact: management summary,
 * before/after KPIs, bottlenecks before/after, affected orders with completion deltas and machine
 * changes. Scenario sets can be saved to this browser and re-run.
 */
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { describeError } from "@/api/client";
import { useCustomers } from "@/api/customers";
import { useMachines } from "@/api/machines";
import { useOrders } from "@/api/orders";
import { usePriorityConfiguration } from "@/api/priority";
import { useRunSimulation, useScenarioTypes } from "@/api/simulation";
import type { AffectedOrder, SimulationResponse, SimulationScenario } from "@/api/types";
import { useAuth } from "@/app/auth";
import { routes } from "@/app/nav";
import { ComparisonTable } from "@/components/ComparisonTable";
import { DataTable, type Column } from "@/components/DataTable";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { KpiCard } from "@/components/KpiCard";
import { LoadingState } from "@/components/LoadingState";
import { PageHeader } from "@/components/PageHeader";
import { Section } from "@/components/Section";
import { useToast } from "@/components/Toast";
import { formatCurrency, formatNumber, formatPct, formatScore, formatSigned } from "@/lib/formatters";
import { formatWorkHours } from "@/lib/metricPairs";
import { formatDateTime } from "@/lib/time";
import { useLocalStorage } from "@/lib/useLocalStorage";

import { ScenarioBuilder, type PickerData } from "./ScenarioBuilder";
import { SAVED_SCENARIOS_KEY, describeScenario, type SavedScenarioSet } from "./scenarioSpecs";

const DEFAULT_TOP_N = 20;
const TOP_N_OPTIONS = [20, 50, 100, 250] as const;

const affectedColumns: Column<AffectedOrder>[] = [
  { key: "order", header: "Order", cell: (a) => <span className="mono strong">{a.order_id}</span>, sortValue: (a) => a.order_id, filterValue: (a) => a.order_id },
  { key: "customer", header: "Customer", cell: (a) => <span className="truncate" style={{ maxWidth: 160, display: "inline-block" }} title={a.customer_id ?? undefined}>{a.customer_name ?? a.customer_id ?? "—"}</span>, sortValue: (a) => a.customer_name ?? a.customer_id ?? "", filterValue: (a) => `${a.customer_id ?? ""} ${a.customer_name ?? ""}` },
  { key: "part", header: "Part", cell: (a) => <span className="truncate" style={{ maxWidth: 140, display: "inline-block" }}>{a.part_name ?? a.part_id ?? "—"}</span>, sortValue: (a) => a.part_name ?? a.part_id ?? "" },
  { key: "due", header: "Due", cell: (a) => <span className="num">{formatDateTime(a.due_date, "dd MMM HH:mm")}</span>, sortValue: (a) => a.due_date ?? "9999" },
  { key: "value", header: "Value", numeric: true, cell: (a) => formatCurrency(a.order_value), sortValue: (a) => a.order_value ?? -1 },
  { key: "base", header: "Baseline completion", cell: (a) => <span className="num">{formatDateTime(a.delta.baseline_completion, "dd MMM HH:mm")}</span>, sortValue: (a) => a.delta.baseline_completion ?? "9999" },
  { key: "scen", header: "Scenario completion", cell: (a) => <span className="num">{formatDateTime(a.delta.scenario_completion, "dd MMM HH:mm")}</span>, sortValue: (a) => a.delta.scenario_completion ?? "9999" },
  {
    key: "delta",
    header: "Δ completion",
    numeric: true,
    cell: (a) => {
      const d = a.delta.delta_hours;
      if (d === null) return <span className="text-faint">—</span>;
      return <span className={d > 0 ? "tone-late strong" : d < 0 ? "tone-ready strong" : "text-muted"}>{d === 0 ? "0h" : `${formatSigned(d, 1)}h`}</span>;
    },
    sortValue: (a) => a.delta.delta_hours ?? 0,
    title: "Positive = finishes later in the scenario",
  },
  {
    key: "late",
    header: "Delivery",
    cell: (a) => {
      const d = a.delta;
      if (d.baseline_late === d.scenario_late) return d.scenario_late ? <span className="tone-late">late → late</span> : <span className="text-muted">on time → on time</span>;
      return d.scenario_late ? <span className="tone-late strong">newly late</span> : <span className="tone-ready strong">now on time</span>;
    },
    sortValue: (a) => (a.delta.scenario_late ? 1 : 0) - (a.delta.baseline_late ? 1 : 0),
  },
  {
    key: "machine",
    header: "Machine",
    cell: (a) => {
      const d = a.delta;
      if (d.baseline_machine_id === d.scenario_machine_id) return <span className="mono">{d.scenario_machine_id ?? "—"}</span>;
      return (
        <span className="mono tone-at-risk" title="moved to another machine">
          {d.baseline_machine_id ?? "—"} → {d.scenario_machine_id ?? "—"}
        </span>
      );
    },
    sortValue: (a) => (a.delta.baseline_machine_id === a.delta.scenario_machine_id ? 0 : 1),
    filterValue: (a) => `${a.delta.baseline_machine_id ?? ""} ${a.delta.scenario_machine_id ?? ""}`,
  },
  { key: "score", header: "Priority", numeric: true, cell: (a) => (a.delta.baseline_score === a.delta.scenario_score ? formatScore(a.delta.scenario_score) : `${formatScore(a.delta.baseline_score)} → ${formatScore(a.delta.scenario_score)}`), sortValue: (a) => a.delta.scenario_score ?? -1 },
];

function KpiStrip({ result }: { result: SimulationResponse }) {
  const d = result.diff;
  const qb = result.baseline.quality?.score ?? null;
  const qs = result.scenario.quality?.score ?? null;
  // Long values (currency) carry their "was …" in the hint so the value never wraps.
  const tile = (label: string, before: string, after: string, good: boolean | undefined, hint?: string, inlineDelta = true) => (
    <KpiCard key={label} label={label} value={after} delta={inlineDelta ? `was ${before}` : undefined} deltaGood={before === after ? undefined : good} tone={before === after ? "neutral" : good ? "ready" : "late"} hint={inlineDelta ? hint : `was ${before}${hint ? ` · ${hint}` : ""}`} />
  );
  return (
    <div className="grid grid-kpi" data-testid="simulation-kpis">
      <KpiCard label="Orders affected" value={formatNumber(d.orders_affected)} tone={d.orders_affected > 0 ? "at-risk" : "neutral"} hint={`${formatNumber(d.orders_moved_machine)} moved machine · ${formatNumber(d.orders_resequenced)} resequenced`} />
      {tile("Late orders", formatNumber(d.late_orders_before), formatNumber(d.late_orders_after), d.late_orders_after <= d.late_orders_before, `${formatNumber(d.orders_newly_late)} newly late · ${formatNumber(d.orders_newly_on_time)} newly on time`)}
      {tile("On-time delivery", formatPct(d.on_time_pct_before, 0), formatPct(d.on_time_pct_after, 0), d.on_time_pct_after >= d.on_time_pct_before)}
      {tile("Average lateness", formatWorkHours(d.avg_lateness_before), formatWorkHours(d.avg_lateness_after), d.avg_lateness_after <= d.avg_lateness_before)}
      {tile("Machine utilisation", formatPct(d.utilization_before, 0), formatPct(d.utilization_after, 0), d.utilization_after >= d.utilization_before)}
      {tile("Setup hours", formatWorkHours(d.setup_hours_before, 0), formatWorkHours(d.setup_hours_after, 0), d.setup_hours_after <= d.setup_hours_before)}
      {tile("Revenue at risk", formatCurrency(d.revenue_at_risk_before), formatCurrency(d.revenue_at_risk_after), d.revenue_at_risk_after <= d.revenue_at_risk_before, undefined, false)}
      {tile("Margin at risk", formatCurrency(d.margin_at_risk_before), formatCurrency(d.margin_at_risk_after), d.margin_at_risk_after <= d.margin_at_risk_before, undefined, false)}
      <KpiCard label="Additional overtime" value={formatWorkHours(d.additional_overtime_hours, 1)} tone={d.additional_overtime_hours > 0 ? "at-risk" : "neutral"} hint="extra working hours the scenario adds" />
      {tile("Schedule quality", formatScore(qb), formatScore(qs), (qs ?? 0) >= (qb ?? 0), "/100")}
      {tile("Unscheduled", formatNumber(result.baseline.unscheduled), formatNumber(result.scenario.unscheduled), result.scenario.unscheduled <= result.baseline.unscheduled)}
    </div>
  );
}

export default function WhatIfSimulationPage() {
  const navigate = useNavigate();
  const toast = useToast();
  const { hasMinRole } = useAuth();
  const canRun = hasMinRole("planner");
  const simulation = useRunSimulation();
  const scenarioTypes = useScenarioTypes();
  const machines = useMachines();
  const customers = useCustomers({ page_size: 500 });
  const priorityConfig = usePriorityConfiguration();
  const [orderQuery, setOrderQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const topOrders = useOrders({ sort: "rank", order: "asc", page_size: 50 });
  const searchedOrders = useOrders({ search: debouncedQuery, page_size: 20 }, debouncedQuery.length >= 2);

  const [scenarios, setScenarios] = useState<SimulationScenario[]>([]);
  const [note, setNote] = useState("");
  const [topN, setTopN] = useState<number>(DEFAULT_TOP_N);
  const [saved, setSaved] = useLocalStorage<SavedScenarioSet[]>(SAVED_SCENARIOS_KEY, []);
  const [saveName, setSaveName] = useState("");
  const result = simulation.data;

  useEffect(() => {
    const id = window.setTimeout(() => setDebouncedQuery(orderQuery.trim()), 300);
    return () => window.clearTimeout(id);
  }, [orderQuery]);

  const pickers = useMemo<PickerData>(() => {
    const orderIds = Array.from(new Set([...(searchedOrders.data?.items ?? []).map((o) => o.order_id), ...(topOrders.data?.items ?? []).map((o) => o.order_id)]));
    const materialIds = Array.from(new Set((topOrders.data?.items ?? []).map((o) => o.required_material_id).filter((m): m is string => Boolean(m)))).sort();
    const profileWeights: Record<string, number> = {};
    for (const w of priorityConfig.data?.profile.weights ?? []) profileWeights[w.key] = w.weight;
    return {
      machines: (machines.data ?? []).map((m) => ({ id: m.machine_id, label: `${m.machine_id} — ${m.machine_name} · ${m.machine_group}` })),
      customers: (customers.data?.items ?? []).map((c) => ({ id: c.customer_id, label: `${c.customer_name} (${c.customer_id}) · ${c.effective_tier}` })),
      groups: Array.from(new Set((machines.data ?? []).map((m) => m.machine_group))).sort(),
      orderIds,
      materialIds,
      profileWeights,
      onOrderQuery: setOrderQuery,
    };
  }, [machines.data, customers.data, topOrders.data, searchedOrders.data, priorityConfig.data]);

  const availableKinds = scenarioTypes.data?.kinds.map((k) => k.kind);

  const run = async () => {
    try {
      const r = await simulation.mutateAsync({ scenarios, top_n: topN, ...(note.trim() ? { note: note.trim() } : {}) });
      toast.push({ tone: "success", title: "Simulation complete", message: r.summary || `${r.diff.orders_affected} orders affected` });
    } catch (err) {
      toast.push({ tone: "error", title: "Simulation failed", message: describeError(err) });
    }
  };

  const saveSet = () => {
    const name = saveName.trim() || `Scenario set ${saved.length + 1}`;
    setSaved((prev) => [{ id: `${Date.now()}`, name, saved_at: new Date().toISOString(), scenarios }, ...prev].slice(0, 30));
    setSaveName("");
    toast.push({ tone: "success", title: "Scenario set saved", message: `"${name}" is stored in this browser.` });
  };

  return (
    <div className="page" data-testid="whatif-simulation">
      <PageHeader
        eyebrow="Analyse"
        title="What-If Simulation"
        subtitle="Simulations run the priority and scheduling engines on a cloned snapshot. Nothing is persisted and the live plan, priorities and ERP are never changed — compare the baseline with the scenario before acting."
        actions={
          <>
            <label className="row gap-1 text-sm text-muted">
              Affected orders
              <select className="select" value={topN} onChange={(e) => setTopN(Number(e.target.value))} aria-label="Affected orders to return">
                {TOP_N_OPTIONS.map((n) => (
                  <option key={n} value={n}>
                    top {n}
                  </option>
                ))}
              </select>
            </label>
            <button type="button" className="btn btn-primary" onClick={() => void run()} disabled={!canRun || scenarios.length === 0 || simulation.isPending} data-testid="run-simulation" title={canRun ? undefined : "Running a simulation requires the planner role"}>
              {simulation.isPending ? "Simulating…" : `Run ${scenarios.length || ""} scenario${scenarios.length === 1 ? "" : "s"}`}
            </button>
          </>
        }
      />
      {!canRun ? <div className="badge badge-writeback" style={{ alignSelf: "flex-start" }}>read only — the planner role can run simulations</div> : null}

      <div className="grid grid-main-side" style={{ gridTemplateColumns: "minmax(340px, 2fr) minmax(0, 3fr)" }}>
        <div className="col gap-3">
          <Section title="Scenarios" count={scenarios.length} actions={scenarioTypes.data ? <span className="text-faint text-xs">{scenarioTypes.data.kinds.length} kinds available</span> : scenarioTypes.isError ? <span className="text-faint text-xs">catalogue unavailable — using the built-in list</span> : null}>
            <ScenarioBuilder scenarios={scenarios} onChange={setScenarios} pickers={pickers} availableKinds={availableKinds} disabled={simulation.isPending} />
            <div className="divider" />
            <label className="field">
              <span className="label">Note for this run (optional)</span>
              <input className="input" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Board meeting question: can we take the Acme order?" aria-label="Note" />
            </label>
          </Section>
          <Section title="Saved scenario sets" count={saved.length} actions={<span className="text-faint text-xs">stored in this browser</span>}>
            <div className="row row-wrap gap-2 mb-2">
              <input className="input" value={saveName} onChange={(e) => setSaveName(e.target.value)} placeholder="Name this set…" aria-label="Scenario set name" style={{ maxWidth: 240 }} />
              <button type="button" className="btn btn-sm" onClick={saveSet} disabled={scenarios.length === 0} data-testid="save-scenarios">
                Save scenario set
              </button>
            </div>
            {saved.length === 0 ? (
              <span className="text-faint text-sm">Nothing saved yet.</span>
            ) : (
              <ul className="col gap-1 text-sm" style={{ margin: 0, padding: 0, listStyle: "none" }}>
                {saved.map((s) => (
                  <li key={s.id} className="row" style={{ justifyContent: "space-between", padding: "4px 8px", background: "var(--bg-panel-raised)", borderRadius: 3 }}>
                    <span className="truncate" title={s.scenarios.map(describeScenario).join("\n")}>
                      <strong>{s.name}</strong> <span className="text-faint">· {s.scenarios.length} scenario{s.scenarios.length === 1 ? "" : "s"} · {formatDateTime(s.saved_at, "dd MMM HH:mm")}</span>
                    </span>
                    <span className="row gap-1">
                      <button type="button" className="btn btn-sm" onClick={() => setScenarios(s.scenarios)}>
                        Load
                      </button>
                      <button type="button" className="btn btn-sm btn-ghost" onClick={() => setSaved((prev) => prev.filter((x) => x.id !== s.id))} aria-label={`Delete ${s.name}`}>
                        ×
                      </button>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Section>
        </div>

        <div className="col gap-3">
          {simulation.isPending ? <LoadingState label="Running the baseline and the scenario schedules (typically 10–30 s on a full plant)" /> : null}
          {simulation.isError ? <ErrorState error={simulation.error} onRetry={() => void run()} /> : null}
          {!simulation.isPending && !result ? <EmptyState title="No simulation yet" message="Add one or more scenarios on the left and run the simulation to compare the baseline plan with the scenario. The real plan is not modified." /> : null}
          {result ? (
            <>
              <Section title="Management summary" actions={<span className="text-faint text-xs mono">{result.simulation_id} · {formatDateTime(result.generated_at)}{result.baseline_version !== null ? ` · baseline v${result.baseline_version}` : ""}</span>}>
                <div className="dq-headline" data-testid="simulation-summary">{result.summary}</div>
                {result.comparison_summary ? <div className="text-sm text-muted mt-2">{result.comparison_summary}</div> : null}
                {result.note ? <div className="text-xs text-faint mt-2">Note: {result.note}</div> : null}
                <ul className="reason-list text-sm mt-2">
                  {result.scenarios.map((s, i) => (
                    <li key={i}>
                      <strong>{s.label ?? s.kind}</strong>: {s.description}
                      {s.affected_order_ids.length ? <span className="text-faint"> · {s.affected_order_ids.length} orders</span> : null}
                      {s.affected_machine_ids.length ? <span className="text-faint"> · {s.affected_machine_ids.length} machines</span> : null}
                      {s.notes.length ? <div className="text-xs text-muted">{s.notes.slice(0, 4).join(" · ")}{s.notes.length > 4 ? " · …" : ""}</div> : null}
                    </li>
                  ))}
                </ul>
                {result.scenario.warnings.length ? <div className="text-xs tone-at-risk mt-2">{result.scenario.warnings.slice(0, 3).join(" · ")}</div> : null}
              </Section>

              <Section title="Baseline → scenario">
                <KpiStrip result={result} />
                {Object.keys(result.comparison).length > 0 ? (
                  <div className="mt-4">
                    <ComparisonTable metrics={result.comparison} beforeLabel="Baseline" afterLabel="Scenario" dense />
                  </div>
                ) : null}
              </Section>

              <div className="grid grid-2">
                <Section title="Bottlenecks before" count={result.diff.bottlenecks_before.length}>
                  {result.diff.bottlenecks_before.length ? <ul className="reason-list text-sm">{result.diff.bottlenecks_before.map((b) => <li key={b}>{b}</li>)}</ul> : <span className="text-muted text-sm">none</span>}
                </Section>
                <Section title="Bottlenecks after" count={result.diff.bottlenecks_after.length}>
                  {result.diff.bottlenecks_after.length ? (
                    <ul className="reason-list text-sm">
                      {result.diff.bottlenecks_after.map((b) => (
                        <li key={b} className={result.diff.bottlenecks_before.includes(b) ? "" : "tone-late"}>
                          {b}
                          {result.diff.bottlenecks_before.includes(b) ? "" : " (new)"}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <span className="tone-ready text-sm">none</span>
                  )}
                </Section>
              </div>

              <Section title="Affected orders" count={`${result.affected_orders.length} of ${formatNumber(result.diff.orders_affected)} (top ${result.top_n})`} flush>
                <DataTable rows={result.affected_orders} columns={affectedColumns} rowKey={(a) => a.order_id} onRowClick={(a) => navigate(routes.orderDetail(encodeURIComponent(a.order_id)))} rowClassName={(a) => (a.delta.scenario_late && !a.delta.baseline_late ? "row-late" : !a.delta.scenario_late && a.delta.baseline_late ? "row-ready" : a.delta.baseline_machine_id !== a.delta.scenario_machine_id ? "row-at-risk" : undefined)} initialSort={{ key: "delta", direction: "desc" }} filters dense pageSize={50} emptyMessage="No order changed between baseline and scenario" ariaLabel="Affected orders" />
              </Section>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}
