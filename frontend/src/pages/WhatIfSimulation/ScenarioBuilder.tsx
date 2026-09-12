/**
 * Scenario builder: one form per scenario kind (every kind of GET /simulation/scenario-types),
 * with machine / customer / order / material pickers backed by the list endpoints, and the
 * scenario list that is sent as one what-if run.
 */
import { useMemo, useState } from "react";

import type { SimulationScenario, SimulationScenarioKind } from "@/api/types";
import { FACTOR_KEYS, FACTOR_NAMES, PROCESS_TYPES, humanize } from "@/lib/constants";

import { SCENARIO_SPECS, SCENARIO_SPEC_BY_KIND, buildScenario, describeScenario, parseWeights, validateForm, visibleFields, type FieldSpec, type FormValues } from "./scenarioSpecs";

export interface PickerData {
  machines: Array<{ id: string; label: string }>;
  customers: Array<{ id: string; label: string }>;
  groups: string[];
  orderIds: string[];
  materialIds: string[];
  /** Active profile weights (points) shown as placeholders for the weight editor. */
  profileWeights: Record<string, number>;
  /** Called with the text typed into an order picker so the page can search the server. */
  onOrderQuery?: (text: string) => void;
}

export interface ScenarioBuilderProps {
  scenarios: SimulationScenario[];
  onChange: (next: SimulationScenario[]) => void;
  pickers: PickerData;
  /** Scenario kinds the backend advertises (GET /simulation/scenario-types); others are hidden. */
  availableKinds?: string[];
  disabled?: boolean;
}

function serializeWeights(map: Record<string, string>): string {
  return Object.entries(map)
    .filter(([, v]) => v.trim() !== "")
    .map(([k, v]) => `${k}=${v.trim()}`)
    .join(", ");
}

function WeightsEditor({ value, onChange, profileWeights }: { value: string; onChange: (v: string) => void; profileWeights: Record<string, number> }) {
  const current = parseWeights(value);
  const set = (key: string, v: string) => {
    const map: Record<string, string> = Object.fromEntries(Object.entries(current).map(([k, n]) => [k, String(n)]));
    map[key] = v;
    onChange(serializeWeights(map));
  };
  return (
    <div className="grid grid-2" style={{ gridColumn: "1 / -1" }}>
      {FACTOR_KEYS.map((key) => (
        <label key={key} className="field">
          <span className="label">{FACTOR_NAMES[key] ?? humanize(key)}</span>
          <span className="row gap-1">
            <input className="input num" type="number" min={0} step="any" value={current[key] !== undefined ? String(current[key]) : ""} placeholder={profileWeights[key] !== undefined ? `${profileWeights[key]} (active)` : "unchanged"} onChange={(e) => set(key, e.target.value)} aria-label={`Weight ${key}`} />
            <span className="text-faint text-xs">pts</span>
          </span>
        </label>
      ))}
    </div>
  );
}

export function ScenarioBuilder({ scenarios, onChange, pickers, availableKinds, disabled = false }: ScenarioBuilderProps) {
  const specs = useMemo(() => SCENARIO_SPECS.filter((s) => !availableKinds || availableKinds.includes(s.kind)), [availableKinds]);
  const [kind, setKind] = useState<SimulationScenarioKind>(specs[0]?.kind ?? "machine_down");
  const [values, setValues] = useState<FormValues>({});
  const [touched, setTouched] = useState(false);
  const spec = SCENARIO_SPEC_BY_KIND[kind];
  const fields = visibleFields(spec, values);
  const error = validateForm(kind, values);

  const setField = (key: string, v: string) => setValues((prev) => ({ ...prev, [key]: v }));

  const add = () => {
    setTouched(true);
    const s = buildScenario(kind, values);
    if (!s) return;
    onChange([...scenarios, s]);
    setValues({});
    setTouched(false);
  };

  const control = (f: FieldSpec) => {
    const v = values[f.key] ?? "";
    switch (f.kind) {
      case "machine":
        return (
          <select className="select" value={v} onChange={(e) => setField(f.key, e.target.value)} aria-label={f.label}>
            <option value="">{pickers.machines.length ? "Choose machine…" : "No machines loaded"}</option>
            {pickers.machines.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label}
              </option>
            ))}
          </select>
        );
      case "customer":
        return (
          <select className="select" value={v} onChange={(e) => setField(f.key, e.target.value)} aria-label={f.label}>
            <option value="">{pickers.customers.length ? "Choose customer…" : "No customers loaded"}</option>
            {pickers.customers.map((c) => (
              <option key={c.id} value={c.id}>
                {c.label}
              </option>
            ))}
          </select>
        );
      case "group":
        return (
          <select className="select" value={v} onChange={(e) => setField(f.key, e.target.value)} aria-label={f.label}>
            <option value="">any group</option>
            {pickers.groups.map((g) => (
              <option key={g} value={g}>
                {g}
              </option>
            ))}
          </select>
        );
      case "process":
        return (
          <select className="select" value={v} onChange={(e) => setField(f.key, e.target.value)} aria-label={f.label}>
            <option value="">Choose process…</option>
            {PROCESS_TYPES.map((p) => (
              <option key={p} value={p}>
                {humanize(p)}
              </option>
            ))}
          </select>
        );
      case "select":
        return (
          <select className="select" value={v || f.options?.[0]?.value || ""} onChange={(e) => setField(f.key, e.target.value)} aria-label={f.label}>
            {f.key === "tier_override" ? <option value="">unchanged</option> : null}
            {(f.options ?? []).map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        );
      case "checkbox":
        return (
          <label className="row gap-1 text-sm" style={{ minHeight: 28 }}>
            <input type="checkbox" checked={v === "true"} onChange={(e) => setField(f.key, e.target.checked ? "true" : "")} aria-label={f.label} /> yes
          </label>
        );
      case "order":
      case "orders":
        return (
          <input
            className="input mono"
            list="whatif-orders"
            value={v}
            placeholder={f.kind === "orders" ? "SO-1045, SO-1052" : "SO-1045"}
            onChange={(e) => {
              setField(f.key, e.target.value);
              pickers.onOrderQuery?.(e.target.value.split(/[\s,;]+/).pop() ?? "");
            }}
            aria-label={f.label}
          />
        );
      case "material":
        return <input className="input mono" list="whatif-materials" value={v} placeholder="MAT-AL6061" onChange={(e) => setField(f.key, e.target.value)} aria-label={f.label} />;
      case "weights":
        return <WeightsEditor value={v} onChange={(x) => setField(f.key, x)} profileWeights={pickers.profileWeights} />;
      case "number":
        return <input className="input num" type="number" min={f.min} step={f.step ?? "any"} value={v} placeholder={f.placeholder} onChange={(e) => setField(f.key, e.target.value)} aria-label={f.label} />;
      case "datetime":
        return <input className="input" type="datetime-local" value={v} onChange={(e) => setField(f.key, e.target.value)} aria-label={f.label} />;
      case "date":
        return <input className="input" type="date" value={v} onChange={(e) => setField(f.key, e.target.value)} aria-label={f.label} />;
      case "time":
        return <input className="input" type="time" value={v} onChange={(e) => setField(f.key, e.target.value)} aria-label={f.label} />;
      default:
        return <input className="input" value={v} placeholder={f.placeholder} onChange={(e) => setField(f.key, e.target.value)} aria-label={f.label} />;
    }
  };

  return (
    <div className="col gap-3" data-testid="scenario-builder">
      <datalist id="whatif-orders">
        {pickers.orderIds.map((id) => (
          <option key={id} value={id} />
        ))}
      </datalist>
      <datalist id="whatif-materials">
        {pickers.materialIds.map((id) => (
          <option key={id} value={id} />
        ))}
      </datalist>
      <label className="field">
        <span className="label">Scenario type</span>
        <select
          className="select"
          value={kind}
          aria-label="Scenario type"
          disabled={disabled}
          onChange={(e) => {
            setKind(e.target.value as SimulationScenarioKind);
            setValues({});
            setTouched(false);
          }}
        >
          {specs.map((s) => (
            <option key={s.kind} value={s.kind}>
              {s.label}
            </option>
          ))}
        </select>
        <span className="fld-msg text-faint">{spec.question}</span>
      </label>
      <div className="grid grid-2">
        {fields.map((f) =>
          f.kind === "weights" ? (
            <div key={f.key} className="col gap-1" style={{ gridColumn: "1 / -1" }}>
              <span className="label">{f.label}</span>
              {control(f)}
              {f.help ? <span className="fld-msg text-faint">{f.help}</span> : null}
            </div>
          ) : (
            <label key={f.key} className="field">
              <span className="label">
                {f.label}
                {f.required ? <span className="tone-late"> *</span> : null}
              </span>
              {control(f)}
              {f.help ? <span className="fld-msg text-faint">{f.help}</span> : null}
            </label>
          ),
        )}
        <label className="field">
          <span className="label">Label (optional)</span>
          <input className="input" value={values.label ?? ""} placeholder="CNC-07 spindle failure" onChange={(e) => setField("label", e.target.value)} aria-label="Label" />
        </label>
      </div>
      {touched && error ? <div className="fld-msg fld-msg-error">{error}</div> : null}
      <div className="row">
        <button type="button" className="btn" onClick={add} disabled={disabled || Boolean(error)} data-testid="add-scenario">
          Add scenario
        </button>
        <span className="text-faint text-xs">Scenarios are applied in order on one cloned snapshot and combined into one what-if run.</span>
      </div>
      {scenarios.length > 0 ? (
        <ol className="col gap-1 text-sm" style={{ margin: 0, paddingLeft: 0, listStyle: "none" }} data-testid="scenario-list">
          {scenarios.map((s, i) => (
            <li key={i} className="row" style={{ justifyContent: "space-between", padding: "4px 8px", background: "var(--bg-panel-raised)", borderRadius: 3 }}>
              <span className="truncate" title={describeScenario(s)}>
                <span className="mono text-muted">{i + 1}. {SCENARIO_SPEC_BY_KIND[s.kind].label}</span> · {s.label ? <strong>{s.label} · </strong> : null}
                {describeScenario(s)}
              </span>
              <button type="button" className="btn btn-sm btn-ghost" onClick={() => onChange(scenarios.filter((_, j) => j !== i))} aria-label={`Remove scenario ${i + 1}`} disabled={disabled}>
                ×
              </button>
            </li>
          ))}
        </ol>
      ) : (
        <div className="text-faint text-sm">No scenarios yet — fill the form and add one or more.</div>
      )}
    </div>
  );
}
