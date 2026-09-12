/**
 * Catalogue of the what-if scenario kinds (backend/app/engines/simulation/scenarios*.py): labels,
 * the spec question each answers, the form fields, and the exact request body built from the form.
 * The builder is a thin function so tests can assert the body byte for byte.
 */
import type { ProcessType, SimulationScenario, SimulationScenarioKind } from "@/api/types";
import { humanize } from "@/lib/constants";
import { formatDateTime } from "@/lib/time";

export type FieldKind = "text" | "number" | "datetime" | "date" | "time" | "select" | "checkbox" | "machine" | "customer" | "order" | "orders" | "material" | "group" | "process" | "weights";

export interface FieldSpec {
  key: string;
  label: string;
  kind: FieldKind;
  required?: boolean;
  placeholder?: string;
  help?: string;
  options?: ReadonlyArray<{ value: string; label: string }>;
  /** Number fields: min / step. */
  min?: number;
  step?: number;
  /** Only shown when another field has this value. */
  showWhen?: { key: string; value: string };
}

export interface ScenarioSpec {
  kind: SimulationScenarioKind;
  label: string;
  /** The spec's example question (Phase 7). */
  question: string;
  fields: FieldSpec[];
}

export type FormValues = Record<string, string>;

const TIERS = ["strategic", "key", "standard", "low"].map((t) => ({ value: t, label: humanize(t) }));

export const SCENARIO_SPECS: ScenarioSpec[] = [
  {
    kind: "machine_down",
    label: "Machine down",
    question: "What happens if machine CNC-07 goes down for 8 hours?",
    fields: [
      { key: "machine_id", label: "Machine", kind: "machine", required: true },
      { key: "duration_hours", label: "Duration (hours)", kind: "number", min: 0.5, step: 0.5, placeholder: "8", help: "Blank = until the end date below" },
      { key: "start", label: "From", kind: "datetime", help: "Blank = now" },
      { key: "end", label: "Until", kind: "datetime", help: "Alternative to the duration" },
      { key: "reason", label: "Reason", kind: "text", placeholder: "spindle failure" },
    ],
  },
  {
    kind: "urgent_orders",
    label: "Urgent orders arrive",
    question: "What happens if 3 new urgent orders arrive?",
    fields: [
      { key: "mode", label: "Source", kind: "select", options: [{ value: "clone", label: "Clone an existing order" }, { value: "new", label: "New order with inline routing" }] },
      { key: "clone_of", label: "Order to clone", kind: "order", required: true, showWhen: { key: "mode", value: "clone" } },
      { key: "customer_id", label: "Customer", kind: "customer", required: true, showWhen: { key: "mode", value: "new" } },
      { key: "part_id", label: "Part id", kind: "text", required: true, placeholder: "P-NEW-001", showWhen: { key: "mode", value: "new" } },
      { key: "process", label: "Process", kind: "process", required: true, showWhen: { key: "mode", value: "new" } },
      { key: "machine_group", label: "Machine group", kind: "group", showWhen: { key: "mode", value: "new" } },
      { key: "setup_minutes", label: "Setup (min)", kind: "number", min: 0, placeholder: "30", showWhen: { key: "mode", value: "new" } },
      { key: "cycle_minutes_per_unit", label: "Cycle (min/unit)", kind: "number", min: 0, step: 0.1, placeholder: "2.5", showWhen: { key: "mode", value: "new" } },
      { key: "order_value", label: "Order value (₹)", kind: "number", min: 0, showWhen: { key: "mode", value: "new" } },
      { key: "quantity", label: "Quantity", kind: "number", min: 1, placeholder: "clone: keep" },
      { key: "due", label: "Due", kind: "datetime", required: true },
      { key: "count", label: "How many such orders", kind: "number", min: 1, step: 1, placeholder: "1" },
      { key: "boost_points", label: "Expedite boost (pts)", kind: "number", min: 0, help: "Blank = profile default; the orders arrive expedited" },
    ],
  },
  {
    kind: "add_machine",
    label: "Add a machine",
    question: "What happens if we add one additional CNC machine?",
    fields: [
      { key: "clone_of_machine_id", label: "Clone capabilities of", kind: "machine", required: true },
      { key: "new_machine_id", label: "New machine id", kind: "text", required: true, placeholder: "MC-CNC5-NEW" },
      { key: "name", label: "Name", kind: "text", placeholder: "5-axis #9" },
      { key: "available_from", label: "Available from", kind: "datetime", help: "Blank = immediately" },
    ],
  },
  {
    kind: "extra_working_day",
    label: "Extra working day",
    question: "What happens if Saturday becomes a working day?",
    fields: [
      { key: "day", label: "Day", kind: "date", required: true },
      { key: "calendar_id", label: "Calendar", kind: "text", placeholder: "all", help: "Calendar id, or blank for every calendar" },
    ],
  },
  {
    kind: "extra_shift",
    label: "Extra shift",
    question: "What happens if we run an extra shift?",
    fields: [
      { key: "day", label: "Day", kind: "date", required: true },
      { key: "start", label: "Shift start", kind: "time", required: true },
      { key: "end", label: "Shift end", kind: "time", required: true },
      { key: "name", label: "Shift name", kind: "text", placeholder: "extra shift" },
      { key: "calendar_id", label: "Calendar", kind: "text", placeholder: "all" },
    ],
  },
  {
    kind: "outsource",
    label: "Outsource",
    question: "What happens if we outsource 500 pieces?",
    fields: [
      { key: "order_ids", label: "Orders", kind: "orders", help: "Comma separated; blank = take quantity from a machine group's queue" },
      { key: "machine_group", label: "Machine group", kind: "group" },
      { key: "quantity", label: "Quantity (pieces)", kind: "number", min: 1, placeholder: "500", help: "Blank = the whole pending quantity of the orders" },
      { key: "supplier", label: "Supplier", kind: "text", placeholder: "external supplier" },
    ],
  },
  {
    kind: "material_delay",
    label: "Material delay",
    question: "What happens if material arrives two days late?",
    fields: [
      { key: "material_id", label: "Material", kind: "material", required: true },
      { key: "delay_days", label: "Delay (days)", kind: "number", min: 0.5, step: 0.5, placeholder: "2" },
      { key: "new_expected_receipt_date", label: "New receipt date", kind: "datetime", help: "Alternative to the delay" },
      { key: "affects_allocated_stock", label: "Stock already counted on is the delayed shipment", kind: "checkbox" },
    ],
  },
  {
    kind: "material_arrival",
    label: "Material arrives",
    question: "What happens if the missing material is received?",
    fields: [
      { key: "material_id", label: "Material", kind: "material", required: true },
      { key: "arrives_at", label: "Arrives at", kind: "datetime", help: "Blank = now" },
      { key: "quantity", label: "Quantity", kind: "number", min: 0, help: "Blank = enough for every waiting order" },
    ],
  },
  {
    kind: "prioritize_customer",
    label: "Prioritise a customer",
    question: "What happens if I prioritise Customer A?",
    fields: [
      { key: "customer_id", label: "Customer", kind: "customer", required: true },
      { key: "boost_points", label: "Boost (pts)", kind: "number", min: 0, placeholder: "20" },
      { key: "tier_override", label: "Treat as tier", kind: "select", options: TIERS },
      { key: "sla_hours", label: "SLA (hours)", kind: "number", min: 1 },
    ],
  },
  {
    kind: "weight_change",
    label: "Change factor weights",
    question: "What happens if we increase the priority of high-margin orders?",
    fields: [{ key: "weights", label: "Factor weights", kind: "weights", help: "Only the factors you set are changed; the rest keep the active profile's weight" }],
  },
  {
    kind: "due_date_change",
    label: "Change a due date",
    question: "What happens if Customer X's order must be completed tomorrow?",
    fields: [
      { key: "order_id", label: "Order", kind: "order", required: true },
      { key: "new_due", label: "New due date", kind: "datetime", required: true },
    ],
  },
  {
    kind: "hold_orders",
    label: "Hold orders",
    question: "What happens if we put these orders on hold?",
    fields: [
      { key: "order_ids", label: "Orders", kind: "orders", required: true },
      { key: "reason", label: "Reason", kind: "text", placeholder: "simulated hold" },
    ],
  },
  {
    kind: "expedite_orders",
    label: "Expedite orders",
    question: "What happens if we expedite these orders?",
    fields: [
      { key: "order_ids", label: "Orders", kind: "orders", required: true },
      { key: "boost_points", label: "Boost (pts)", kind: "number", min: 0, placeholder: "30" },
      { key: "hours", label: "For (hours)", kind: "number", min: 0.5, step: 0.5, placeholder: "profile default" },
      { key: "reason", label: "Reason", kind: "text", placeholder: "simulated expedite" },
    ],
  },
];

export const SCENARIO_SPEC_BY_KIND: Record<SimulationScenarioKind, ScenarioSpec> = Object.fromEntries(SCENARIO_SPECS.map((s) => [s.kind, s])) as Record<SimulationScenarioKind, ScenarioSpec>;

export function splitIds(text: string): string[] {
  return text
    .split(/[\s,;]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

/** "yyyy-MM-ddTHH:mm" (datetime-local, browser zone) → ISO UTC; undefined when blank / invalid. */
export function localToIso(value: string | undefined): string | undefined {
  if (!value || !value.trim()) return undefined;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? undefined : d.toISOString();
}

function num(value: string | undefined): number | undefined {
  if (value === undefined || value.trim() === "") return undefined;
  const n = Number(value);
  return Number.isFinite(n) ? n : undefined;
}

function text(value: string | undefined): string | undefined {
  const t = value?.trim();
  return t ? t : undefined;
}

/** "HH:mm" → "HH:mm:00" (the API expects a time). */
function timeOfDay(value: string | undefined): string | undefined {
  const t = text(value);
  if (!t) return undefined;
  return /^\d{2}:\d{2}$/.test(t) ? `${t}:00` : t;
}

/** Drops undefined keys so the request body carries only what the planner set. */
function compact<T extends object>(obj: T): T {
  return Object.fromEntries(Object.entries(obj).filter(([, v]) => v !== undefined)) as T;
}

/** Fields visible for the current values (respecting showWhen). */
export function visibleFields(spec: ScenarioSpec, values: FormValues): FieldSpec[] {
  return spec.fields.filter((f) => !f.showWhen || (values[f.showWhen.key] ?? spec.fields.find((x) => x.key === f.showWhen?.key)?.options?.[0]?.value ?? "") === f.showWhen.value);
}

/** Human validation message, or null when the form can be turned into a scenario. */
export function validateForm(kind: SimulationScenarioKind, values: FormValues): string | null {
  const spec = SCENARIO_SPEC_BY_KIND[kind];
  for (const f of visibleFields(spec, values)) {
    if (f.required && !text(values[f.key])) return `${f.label} is required.`;
  }
  switch (kind) {
    case "machine_down":
      if (!num(values.duration_hours) && !localToIso(values.end)) return "Give a duration or an end time.";
      return null;
    case "outsource":
      if (splitIds(values.order_ids ?? "").length === 0 && !text(values.machine_group)) return "Name the orders or a machine group to outsource from.";
      return null;
    case "material_delay":
      if (!num(values.delay_days) && !localToIso(values.new_expected_receipt_date)) return "Give a delay in days or a new receipt date.";
      return null;
    case "weight_change": {
      const w = parseWeights(values.weights ?? "");
      return Object.keys(w).length === 0 ? "Set at least one factor weight." : null;
    }
    default:
      return null;
  }
}

export function parseWeights(text: string): Record<string, number> {
  const out: Record<string, number> = {};
  for (const part of text.split(/[\n,;]+/)) {
    const [k, v] = part.split("=").map((s) => s.trim());
    if (k && v !== undefined && v !== "" && Number.isFinite(Number(v))) out[k] = Number(v);
  }
  return out;
}

/** The exact scenario object for the API, or null when the form is incomplete. */
export function buildScenario(kind: SimulationScenarioKind, values: FormValues): SimulationScenario | null {
  if (validateForm(kind, values)) return null;
  const label = text(values.label);
  const withLabel = <T extends SimulationScenario>(s: T): T => (label ? { ...s, label } : s);
  switch (kind) {
    case "machine_down":
      return withLabel(compact({ kind, machine_id: values.machine_id?.trim() ?? "", duration_hours: num(values.duration_hours), start: localToIso(values.start), end: localToIso(values.end), reason: text(values.reason) }));
    case "urgent_orders": {
      const mode = values.mode || "clone";
      const count = Math.max(1, Math.min(10, Math.round(num(values.count) ?? 1)));
      const base =
        mode === "clone"
          ? compact({ clone_of: text(values.clone_of), quantity: num(values.quantity), due: localToIso(values.due), boost_points: num(values.boost_points) })
          : compact({
              customer_id: text(values.customer_id),
              part_id: text(values.part_id),
              quantity: num(values.quantity) ?? 1,
              due: localToIso(values.due),
              order_value: num(values.order_value),
              boost_points: num(values.boost_points),
              route: [compact({ process: (values.process ?? "other") as ProcessType, machine_group: text(values.machine_group), setup_minutes: num(values.setup_minutes), cycle_minutes_per_unit: num(values.cycle_minutes_per_unit) })],
            });
      return withLabel({ kind, orders: Array.from({ length: count }, () => ({ ...base })) });
    }
    case "add_machine":
      return withLabel(compact({ kind, clone_of_machine_id: values.clone_of_machine_id?.trim() ?? "", new_machine_id: values.new_machine_id?.trim() ?? "", name: text(values.name), available_from: localToIso(values.available_from) }));
    case "extra_working_day":
      return withLabel(compact({ kind, day: values.day?.trim() ?? "", calendar_id: text(values.calendar_id) }));
    case "extra_shift":
      return withLabel(compact({ kind, day: values.day?.trim() ?? "", start: timeOfDay(values.start) ?? "", end: timeOfDay(values.end) ?? "", name: text(values.name), calendar_id: text(values.calendar_id) }));
    case "outsource": {
      const ids = splitIds(values.order_ids ?? "");
      return withLabel(compact({ kind, order_ids: ids.length ? ids : undefined, machine_group: text(values.machine_group), quantity: num(values.quantity), supplier: text(values.supplier) }));
    }
    case "material_delay":
      return withLabel(compact({ kind, material_id: values.material_id?.trim() ?? "", delay_days: num(values.delay_days), new_expected_receipt_date: localToIso(values.new_expected_receipt_date), affects_allocated_stock: values.affects_allocated_stock === "true" ? true : undefined }));
    case "material_arrival":
      return withLabel(compact({ kind, material_id: values.material_id?.trim() ?? "", arrives_at: localToIso(values.arrives_at), quantity: num(values.quantity) }));
    case "prioritize_customer":
      return withLabel(compact({ kind, customer_id: values.customer_id?.trim() ?? "", boost_points: num(values.boost_points), tier_override: text(values.tier_override) as "strategic" | "key" | "standard" | "low" | undefined, sla_hours: num(values.sla_hours) }));
    case "weight_change":
      return withLabel({ kind, weights: parseWeights(values.weights ?? "") });
    case "due_date_change":
      return withLabel({ kind, order_id: values.order_id?.trim() ?? "", new_due: localToIso(values.new_due) ?? "" });
    case "hold_orders":
      return withLabel(compact({ kind, order_ids: splitIds(values.order_ids ?? ""), reason: text(values.reason) }));
    case "expedite_orders":
      return withLabel(compact({ kind, order_ids: splitIds(values.order_ids ?? ""), boost_points: num(values.boost_points), hours: num(values.hours), reason: text(values.reason) }));
  }
}

/** One-line description of a built scenario for the scenario list. */
export function describeScenario(s: SimulationScenario): string {
  const dt = (v: string | null | undefined) => (v ? formatDateTime(v, "dd MMM HH:mm") : "");
  switch (s.kind) {
    case "machine_down":
      return `${s.machine_id} down${s.duration_hours ? ` for ${s.duration_hours} h` : ""}${s.start ? ` from ${dt(s.start)}` : ""}${s.end ? ` until ${dt(s.end)}` : ""}${s.reason ? ` (${s.reason})` : ""}`;
    case "urgent_orders": {
      const first = s.orders[0];
      return `${s.orders.length} urgent order${s.orders.length === 1 ? "" : "s"} ${first?.clone_of ? `like ${first.clone_of}` : `for ${first?.customer_id ?? "?"} · ${first?.part_id ?? "?"}`}${first?.due ? ` due ${dt(first.due)}` : ""}`;
    }
    case "add_machine":
      return `Add ${s.new_machine_id} cloned from ${s.clone_of_machine_id}${s.available_from ? ` from ${dt(s.available_from)}` : ""}`;
    case "extra_working_day":
      return `${s.day} becomes a working day (${s.calendar_id ?? "all calendars"})`;
    case "extra_shift":
      return `Extra shift ${s.start}–${s.end} on ${s.day} (${s.calendar_id ?? "all calendars"})`;
    case "outsource":
      return `Outsource ${s.quantity ? `${s.quantity} pcs ` : ""}${s.order_ids?.length ? `of ${s.order_ids.join(", ")}` : s.machine_group ? `from ${s.machine_group}` : ""}${s.supplier ? ` to ${s.supplier}` : ""}`;
    case "material_delay":
      return `${s.material_id} delayed${s.delay_days ? ` ${s.delay_days} d` : ""}${s.new_expected_receipt_date ? ` to ${dt(s.new_expected_receipt_date)}` : ""}`;
    case "material_arrival":
      return `${s.material_id} arrives ${s.arrives_at ? dt(s.arrives_at) : "now"}${s.quantity ? ` (${s.quantity})` : ""}`;
    case "prioritize_customer":
      return `Prioritise ${s.customer_id}${s.boost_points ? ` +${s.boost_points} pts` : ""}${s.tier_override ? ` as ${s.tier_override}` : ""}${s.sla_hours ? ` SLA ${s.sla_hours} h` : ""}`;
    case "weight_change":
      return `Weights: ${Object.entries(s.weights ?? {})
        .map(([k, v]) => `${k}=${v}`)
        .join(", ")}`;
    case "due_date_change":
      return `${s.order_id} due ${dt(s.new_due)}`;
    case "hold_orders":
      return `Hold ${s.order_ids.join(", ")}`;
    case "expedite_orders":
      return `Expedite ${s.order_ids.join(", ")}${s.boost_points ? ` +${s.boost_points} pts` : ""}${s.hours ? ` for ${s.hours} h` : ""}`;
  }
}

/** Saved scenario sets live in localStorage so a planner can re-run "CNC-07 down" every morning. */
export interface SavedScenarioSet {
  id: string;
  name: string;
  saved_at: string;
  scenarios: SimulationScenario[];
}

export const SAVED_SCENARIOS_KEY = "ppse.whatif.saved";
