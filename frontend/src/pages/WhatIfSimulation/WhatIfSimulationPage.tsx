import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { describeError } from "@/api/client";
import { useMachines } from "@/api/machines";
import { useRunSimulation } from "@/api/simulation";
import type { OrderDelta, Scenario, SimulationResult } from "@/api/types";
import { routes } from "@/app/nav";
import { DataTable, type Column } from "@/components/DataTable";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { KpiCard } from "@/components/KpiCard";
import { LoadingState } from "@/components/LoadingState";
import { PageHeader } from "@/components/PageHeader";
import { Section } from "@/components/Section";
import { useToast } from "@/components/Toast";
import { formatCurrency, formatHours, formatPct, formatScore, formatSigned, formatTransition } from "@/lib/formatters";
import { formatDateTime } from "@/lib/time";

import { ScenarioBuilder } from "./ScenarioBuilder";

const deltaColumns: Column<OrderDelta>[] = [
  { key: "order", header: "Order", cell: (d) => <span className="mono strong">{d.order_id}</span>, sortValue: (d) => d.order_id, filterValue: (d) => d.order_id },
  { key: "base", header: "Baseline completion", cell: (d) => <span className="num">{formatDateTime(d.baseline_completion, "dd MMM HH:mm")}</span>, sortValue: (d) => d.baseline_completion ?? "9999" },
  { key: "scen", header: "Scenario completion", cell: (d) => <span className="num">{formatDateTime(d.scenario_completion, "dd MMM HH:mm")}</span>, sortValue: (d) => d.scenario_completion ?? "9999" },
  { key: "delta", header: "Δ hours", numeric: true, cell: (d) => <span className={d.delta_hours === null ? "" : d.delta_hours > 0 ? "tone-late" : d.delta_hours < 0 ? "tone-ready" : ""}>{d.delta_hours === null ? "—" : formatSigned(d.delta_hours, 1)}</span>, sortValue: (d) => d.delta_hours ?? 0 },
  { key: "late", header: "Late", cell: (d) => (d.baseline_late === d.scenario_late ? (d.scenario_late ? <span className="tone-late">late → late</span> : "on time") : d.scenario_late ? <span className="tone-late strong">newly late</span> : <span className="tone-ready strong">now on time</span>), sortValue: (d) => (d.scenario_late ? 1 : 0) - (d.baseline_late ? 1 : 0) },
  { key: "machine", header: "Machine", cell: (d) => <span className="mono">{d.baseline_machine_id === d.scenario_machine_id ? (d.scenario_machine_id ?? "—") : `${d.baseline_machine_id ?? "—"} → ${d.scenario_machine_id ?? "—"}`}</span> },
  { key: "score", header: "Score", numeric: true, cell: (d) => (d.baseline_score === d.scenario_score ? formatScore(d.scenario_score) : `${formatScore(d.baseline_score)} → ${formatScore(d.scenario_score)}`) },
];

function ComparisonGrid({ result }: { result: SimulationResult }) {
  const d = result.diff;
  const rows: Array<{ label: string; before: string; after: string; good: boolean | undefined }> = [
    { label: "On-time delivery", before: formatPct(d.on_time_pct_before, 0), after: formatPct(d.on_time_pct_after, 0), good: d.on_time_pct_after >= d.on_time_pct_before },
    { label: "Late orders", before: String(d.late_orders_before), after: String(d.late_orders_after), good: d.late_orders_after <= d.late_orders_before },
    { label: "Average lateness", before: formatHours(d.avg_lateness_before), after: formatHours(d.avg_lateness_after), good: d.avg_lateness_after <= d.avg_lateness_before },
    { label: "Machine utilisation", before: formatPct(d.utilization_before, 0), after: formatPct(d.utilization_after, 0), good: d.utilization_after >= d.utilization_before },
    { label: "Setup hours", before: formatHours(d.setup_hours_before, 0), after: formatHours(d.setup_hours_after, 0), good: d.setup_hours_after <= d.setup_hours_before },
    { label: "Revenue at risk", before: formatCurrency(d.revenue_at_risk_before), after: formatCurrency(d.revenue_at_risk_after), good: d.revenue_at_risk_after <= d.revenue_at_risk_before },
    { label: "Margin at risk", before: formatCurrency(d.margin_at_risk_before), after: formatCurrency(d.margin_at_risk_after), good: d.margin_at_risk_after <= d.margin_at_risk_before },
    { label: "Schedule quality", before: formatScore(result.baseline.quality?.score), after: formatScore(result.scenario.quality?.score), good: (result.scenario.quality?.score ?? 0) >= (result.baseline.quality?.score ?? 0) },
  ];
  return (
    <div className="grid grid-kpi">
      {rows.map((r) => (
        <KpiCard key={r.label} label={r.label} value={r.after} delta={`was ${r.before}`} deltaGood={r.before === r.after ? undefined : r.good} tone={r.before === r.after ? "neutral" : r.good ? "ready" : "late"} />
      ))}
    </div>
  );
}

/** "What happens if I change it?" — scenario builder and baseline vs scenario comparison. */
export default function WhatIfSimulationPage() {
  const navigate = useNavigate();
  const toast = useToast();
  const machines = useMachines();
  const simulation = useRunSimulation();
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const machineIds = useMemo(() => (machines.data ?? []).map((m) => m.machine_id).sort(), [machines.data]);
  const result = simulation.data;

  const run = async () => {
    try {
      const r = await simulation.mutateAsync({ scenarios });
      toast.push({ tone: "success", title: "Simulation complete", message: r.diff.summary || `${r.diff.orders_affected} orders affected` });
    } catch (err) {
      toast.push({ tone: "error", title: "Simulation failed", message: describeError(err) });
    }
  };

  return (
    <div className="page">
      <PageHeader eyebrow="Analyse" title="What-If Simulation" subtitle="Simulations run on a cloned snapshot and never change the live schedule. Compare the baseline with the scenario before acting." actions={<button type="button" className="btn btn-primary" onClick={() => void run()} disabled={scenarios.length === 0 || simulation.isPending}>{simulation.isPending ? "Simulating…" : `Run ${scenarios.length || ""} scenario${scenarios.length === 1 ? "" : "s"}`}</button>} />
      <div className="grid grid-main-side" style={{ gridTemplateColumns: "minmax(300px, 1fr) minmax(0, 2fr)" }}>
        <Section title="Scenarios" count={scenarios.length}>
          <ScenarioBuilder scenarios={scenarios} onChange={setScenarios} machineIds={machineIds} />
        </Section>
        <div className="col gap-3">
          {simulation.isPending ? <LoadingState label="Running baseline and scenario schedules" /> : null}
          {simulation.isError ? <ErrorState error={simulation.error} onRetry={() => void run()} /> : null}
          {!simulation.isPending && !result ? <EmptyState title="No simulation yet" message="Add one or more scenarios and run the simulation to compare outcomes." /> : null}
          {result ? (
            <>
              <Section title="Baseline → scenario" actions={<span className="text-muted text-xs">{result.simulation_id} · {formatDateTime(result.generated_at)}</span>}>
                <ComparisonGrid result={result} />
                <div className="mt-4 text-sm text-muted">
                  {result.diff.summary || `${result.diff.orders_affected} orders affected · ${result.diff.orders_moved_machine} moved machine · ${result.diff.orders_resequenced} resequenced · ${result.diff.orders_newly_late} newly late · ${result.diff.orders_newly_on_time} newly on time`}
                  {result.diff.additional_overtime_hours > 0 ? ` · +${formatHours(result.diff.additional_overtime_hours)} overtime` : ""}
                </div>
                {result.diff.bottlenecks_before.length || result.diff.bottlenecks_after.length ? (
                  <div className="mt-2 text-sm">
                    Bottlenecks: {formatTransition(result.diff.bottlenecks_before.join(", ") || "none", result.diff.bottlenecks_after.join(", ") || "none")}
                  </div>
                ) : null}
              </Section>
              <Section title="Affected orders" count={result.diff.order_deltas.length} flush>
                <DataTable rows={result.diff.order_deltas} columns={deltaColumns} rowKey={(d) => d.order_id} onRowClick={(d) => navigate(routes.orderDetail(encodeURIComponent(d.order_id)))} rowClassName={(d) => (d.scenario_late && !d.baseline_late ? "row-late" : !d.scenario_late && d.baseline_late ? "row-ready" : undefined)} initialSort={{ key: "delta", direction: "desc" }} filters dense pageSize={50} emptyMessage="No order changed between baseline and scenario" />
              </Section>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}
