/** Builds the list of Scenario objects sent to POST /schedule/simulate. */
import { useState } from "react";

import type { Scenario, ScenarioKind } from "@/api/types";
import { toUtcIso } from "@/lib/time";

const KIND_LABELS: Record<ScenarioKind, string> = {
  machine_down: "Machine down",
  urgent_orders: "Urgent orders",
  add_machine: "Add machine",
  extra_shift: "Extra shift",
  working_day: "Extra working day",
  outsource: "Outsource orders",
  material_delay: "Material delay",
  prioritize_customer: "Prioritise customer",
  weight_change: "Change factor weights",
  due_date_change: "Change due date",
};

export interface ScenarioBuilderProps {
  scenarios: Scenario[];
  onChange: (next: Scenario[]) => void;
  machineIds: string[];
}

function splitIds(text: string): string[] {
  return text
    .split(/[\s,;]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

function localToIso(value: string): string {
  return value ? toUtcIso(new Date(value)) : "";
}

function describeScenario(s: Scenario): string {
  switch (s.kind) {
    case "machine_down":
      return `${s.machine_id} down ${s.start.slice(0, 16)} → ${s.end.slice(0, 16)}`;
    case "urgent_orders":
      return `Urgent: ${s.order_ids.join(", ")}${s.boost_points ? ` (+${s.boost_points})` : ""}`;
    case "add_machine":
      return `Add machine like ${s.template_machine_id}${s.machine_id ? ` as ${s.machine_id}` : ""}`;
    case "extra_shift":
      return `Extra shift ${s.start.slice(0, 16)} → ${s.end.slice(0, 16)}${s.machine_ids?.length ? ` on ${s.machine_ids.join(", ")}` : ""}`;
    case "working_day":
      return `Work on ${s.date}${s.machine_ids?.length ? ` (${s.machine_ids.join(", ")})` : ""}`;
    case "outsource":
      return `Outsource ${s.order_ids.join(", ")}`;
    case "material_delay":
      return `Material ${s.material_id} delayed ${s.delay_hours}h`;
    case "prioritize_customer":
      return `Prioritise customer ${s.customer_id}${s.boost_points ? ` (+${s.boost_points})` : ""}`;
    case "weight_change":
      return `Weights: ${Object.entries(s.weights)
        .map(([k, v]) => `${k}=${v}`)
        .join(", ")}`;
    case "due_date_change":
      return `${s.order_id} due ${s.new_due_date.slice(0, 16)}`;
  }
}

export function ScenarioBuilder({ scenarios, onChange, machineIds }: ScenarioBuilderProps) {
  const [kind, setKind] = useState<ScenarioKind>("machine_down");
  const [f, setF] = useState<Record<string, string>>({});
  const field = (k: string) => f[k] ?? "";
  const setField = (k: string, v: string) => setF((prev) => ({ ...prev, [k]: v }));

  const build = (): Scenario | null => {
    switch (kind) {
      case "machine_down":
        return field("machine_id") && field("start") && field("end")
          ? { kind, machine_id: field("machine_id"), start: localToIso(field("start")), end: localToIso(field("end")), reason: field("reason") || undefined }
          : null;
      case "urgent_orders":
        return splitIds(field("order_ids")).length ? { kind, order_ids: splitIds(field("order_ids")), boost_points: field("points") ? Number(field("points")) : undefined } : null;
      case "add_machine":
        return field("template") ? { kind, template_machine_id: field("template"), machine_id: field("machine_id") || undefined } : null;
      case "extra_shift":
        return field("start") && field("end") ? { kind, start: localToIso(field("start")), end: localToIso(field("end")), machine_ids: splitIds(field("machine_ids")) } : null;
      case "working_day":
        return field("date") ? { kind, date: field("date"), machine_ids: splitIds(field("machine_ids")) } : null;
      case "outsource":
        return splitIds(field("order_ids")).length ? { kind, order_ids: splitIds(field("order_ids")) } : null;
      case "material_delay":
        return field("material_id") && field("hours") ? { kind, material_id: field("material_id"), delay_hours: Number(field("hours")) } : null;
      case "prioritize_customer":
        return field("customer_id") ? { kind, customer_id: field("customer_id"), boost_points: field("points") ? Number(field("points")) : undefined } : null;
      case "weight_change": {
        const weights: Record<string, number> = {};
        for (const part of splitIds(field("weights"))) {
          const [k, v] = part.split("=");
          if (k && v && !Number.isNaN(Number(v))) weights[k] = Number(v);
        }
        return Object.keys(weights).length ? { kind, weights } : null;
      }
      case "due_date_change":
        return field("order_id") && field("due") ? { kind, order_id: field("order_id"), new_due_date: localToIso(field("due")) } : null;
    }
  };

  const add = () => {
    const s = build();
    if (!s) return;
    onChange([...scenarios, s]);
    setF({});
  };

  const machineSelect = (key: string, label: string) => (
    <label className="field">
      <span className="label">{label}</span>
      <input className="input" list="sim-machines" value={field(key)} onChange={(e) => setField(key, e.target.value)} placeholder="CNC-01" />
    </label>
  );
  const text = (key: string, label: string, placeholder = "") => (
    <label className="field">
      <span className="label">{label}</span>
      <input className="input" value={field(key)} onChange={(e) => setField(key, e.target.value)} placeholder={placeholder} />
    </label>
  );
  const dateTime = (key: string, label: string) => (
    <label className="field">
      <span className="label">{label}</span>
      <input className="input" type="datetime-local" value={field(key)} onChange={(e) => setField(key, e.target.value)} />
    </label>
  );

  return (
    <div className="col gap-3">
      <datalist id="sim-machines">
        {machineIds.map((id) => (
          <option key={id} value={id} />
        ))}
      </datalist>
      <label className="field">
        <span className="label">Scenario type</span>
        <select
          className="select"
          value={kind}
          onChange={(e) => {
            setKind(e.target.value as ScenarioKind);
            setF({});
          }}
        >
          {(Object.keys(KIND_LABELS) as ScenarioKind[]).map((k) => (
            <option key={k} value={k}>
              {KIND_LABELS[k]}
            </option>
          ))}
        </select>
      </label>
      <div className="grid grid-2">
        {kind === "machine_down" ? (
          <>
            {machineSelect("machine_id", "Machine")}
            {text("reason", "Reason", "breakdown")}
            {dateTime("start", "From")}
            {dateTime("end", "To")}
          </>
        ) : null}
        {kind === "urgent_orders" ? (
          <>
            {text("order_ids", "Order ids (comma separated)", "R3D-10482, R3D-10490")}
            {text("points", "Boost points", "30")}
          </>
        ) : null}
        {kind === "add_machine" ? (
          <>
            {machineSelect("template", "Clone capabilities of")}
            {text("machine_id", "New machine id", "CNC-NEW")}
          </>
        ) : null}
        {kind === "extra_shift" ? (
          <>
            {dateTime("start", "From")}
            {dateTime("end", "To")}
            {text("machine_ids", "Machines (blank = all)", "CNC-01, CNC-02")}
          </>
        ) : null}
        {kind === "working_day" ? (
          <>
            <label className="field">
              <span className="label">Date</span>
              <input className="input" type="date" value={field("date")} onChange={(e) => setField("date", e.target.value)} />
            </label>
            {text("machine_ids", "Machines (blank = all)")}
          </>
        ) : null}
        {kind === "outsource" ? text("order_ids", "Order ids", "R3D-10482") : null}
        {kind === "material_delay" ? (
          <>
            {text("material_id", "Material id", "AL-6061")}
            {text("hours", "Delay (hours)", "48")}
          </>
        ) : null}
        {kind === "prioritize_customer" ? (
          <>
            {text("customer_id", "Customer id", "C-1")}
            {text("points", "Boost points", "20")}
          </>
        ) : null}
        {kind === "weight_change" ? text("weights", "Weights key=value", "due_date_urgency=35, order_value=5") : null}
        {kind === "due_date_change" ? (
          <>
            {text("order_id", "Order id")}
            {dateTime("due", "New due date")}
          </>
        ) : null}
      </div>
      <div className="row">
        <button type="button" className="btn" onClick={add} disabled={!build()}>
          Add scenario
        </button>
        <span className="text-faint text-xs">Scenarios are combined into one what-if run.</span>
      </div>
      {scenarios.length > 0 ? (
        <ul className="col gap-1 text-sm" style={{ margin: 0, padding: 0, listStyle: "none" }}>
          {scenarios.map((s, i) => (
            <li key={i} className="row" style={{ justifyContent: "space-between", padding: "4px 8px", background: "var(--bg-panel-raised)", borderRadius: 3 }}>
              <span>
                <span className="mono text-muted">{KIND_LABELS[s.kind]}</span> · {describeScenario(s)}
              </span>
              <button type="button" className="btn btn-sm btn-ghost" onClick={() => onChange(scenarios.filter((_, j) => j !== i))} aria-label="Remove scenario">
                ×
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
