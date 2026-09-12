/**
 * Audited planner/manager actions on one order (spec Phase 9/16): expedite, hold/release,
 * override priority, force next, move to machine, lock machine assignment. Every action
 * requires a reason; the dialog builds the exact request body of the endpoint.
 */
import { useEffect, useMemo, useState } from "react";

import { useMachines } from "@/api/machines";
import { useOrderAction, type OrderActionInput, type OrderActionKind } from "@/api/orders";
import { usePriorityConfiguration } from "@/api/priority";
import type { MachineOption, OverridePriorityKind, Role } from "@/api/types";
import { ActionDialog } from "@/components/ActionDialog";
import { NumberField, SelectField, TextField } from "@/components/Field";
import { useToast } from "@/components/Toast";
import { describeError } from "@/api/client";
import { formatDateTime } from "@/lib/time";

export type { OrderActionKind } from "@/api/orders";

export interface OrderActionSpec {
  kind: OrderActionKind;
  label: string;
  title: string;
  minRole: Role;
  danger?: boolean;
  primary?: boolean;
  description: string;
}

/** Order of the menu follows the spec list; roles follow DESIGN_CONTRACT §9. */
export const ORDER_ACTIONS: OrderActionSpec[] = [
  { kind: "expedite", label: "Expedite", title: "Expedite order", minRole: "production_manager", primary: true, description: "Temporarily boosts the priority score. After the expiry the normal calculation resumes; other orders are not permanently overridden." },
  { kind: "force-next", label: "Force next", title: "Force order to run next", minRole: "production_manager", description: "The scheduler places this order first on its eligible machine (until the override expires or is cancelled)." },
  { kind: "override-priority", label: "Override priority", title: "Override priority score", minRole: "production_manager", description: "Increase or decrease the engine score by a number of points, or set it to an absolute value (0–100)." },
  { kind: "move", label: "Move to machine", title: "Move order to a machine", minRole: "production_manager", description: "Pins the next operation to a machine (optionally at a start time) with a lock, and records the move as an override." },
  { kind: "lock-machine", label: "Lock machine", title: "Lock machine assignment", minRole: "production_manager", description: "Keeps the order on the chosen machine across replans." },
  { kind: "hold", label: "Hold", title: "Put order on hold", minRole: "planner", danger: true, description: "Removes the order from the queue until released (or until the optional expiry)." },
  { kind: "release", label: "Release hold", title: "Release hold", minRole: "planner", description: "Returns the order to the queue; the priority engine scores it again on the next run." },
];

export function specFor(kind: OrderActionKind): OrderActionSpec {
  const spec = ORDER_ACTIONS.find((a) => a.kind === kind);
  if (!spec) throw new Error(`unknown order action ${kind}`);
  return spec;
}

/** Actions the current user may take on an order, given its hold state. */
export function visibleOrderActions(hasMinRole: (r: Role) => boolean, onHold: boolean): OrderActionSpec[] {
  return ORDER_ACTIONS.filter((a) => hasMinRole(a.minRole)).filter((a) => (a.kind === "release" ? onHold : a.kind === "hold" ? !onHold : true));
}

/** "yyyy-MM-ddTHH:mm" from <input type=datetime-local> → ISO UTC (null when blank/invalid). */
export function localInputToIso(value: string): string | null {
  if (!value.trim()) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

export interface OrderActionDialogProps {
  orderId: string;
  action: OrderActionKind | null;
  onClose: () => void;
  /** Eligible machines (from GET /orders/{id}/machines) to pick from for move/lock; falls back to all machines. */
  eligible?: MachineOption[];
  scheduledMachineId?: string | null;
  /** Pre-selected machine for move / lock-machine (e.g. from the machine options table). */
  defaultMachineId?: string | null;
  onSuccess?: (action: OrderActionKind) => void;
}

const OVERRIDE_KINDS: ReadonlyArray<{ value: OverridePriorityKind; label: string }> = [
  { value: "increase", label: "Increase by points" },
  { value: "decrease", label: "Decrease by points" },
  { value: "set", label: "Set absolute score" },
];

export function OrderActionDialog({ orderId, action, onClose, eligible, scheduledMachineId, defaultMachineId, onSuccess }: OrderActionDialogProps) {
  const toast = useToast();
  const mutation = useOrderAction(orderId);
  const config = usePriorityConfiguration();
  const allMachines = useMachines({});
  const expediteCfg = config.data?.profile.expedite;

  const [boost, setBoost] = useState<number | null>(null);
  const [duration, setDuration] = useState<number | null>(null);
  const [expiresAt, setExpiresAt] = useState("");
  const [startAt, setStartAt] = useState("");
  const [overrideKind, setOverrideKind] = useState<OverridePriorityKind>("increase");
  const [value, setValue] = useState<number | null>(10);
  const [machineId, setMachineId] = useState("");

  useEffect(() => {
    if (action === "move" || action === "lock-machine") setMachineId(defaultMachineId ?? "");
  }, [action, defaultMachineId]);

  const spec = action ? specFor(action) : null;
  const effectiveBoost = boost ?? expediteCfg?.default_boost_points ?? 30;
  const effectiveDuration = duration ?? expediteCfg?.default_duration_hours ?? 4;

  const machineChoices = useMemo(() => {
    if (eligible && eligible.length > 0) {
      return eligible.map((m) => ({ value: m.machine_id, label: `${m.machine_id} — ${m.machine_name}${m.recommended ? " (recommended)" : ""}` }));
    }
    return (allMachines.data ?? []).map((m) => ({ value: m.machine_id, label: `${m.machine_id} — ${m.machine_name} · ${m.machine_group}` }));
  }, [eligible, allMachines.data]);

  const reset = () => {
    setBoost(null);
    setDuration(null);
    setExpiresAt("");
    setStartAt("");
    setOverrideKind("increase");
    setValue(10);
    setMachineId("");
    mutation.reset();
  };

  const close = () => {
    reset();
    onClose();
  };

  const validate = (): string | null => {
    if (!action) return null;
    if (action === "expedite") {
      if (effectiveBoost <= 0) return "Boost points must be greater than zero.";
      if (expediteCfg && effectiveBoost > expediteCfg.max_boost_points) return `Boost points cannot exceed ${expediteCfg.max_boost_points} (profile limit).`;
      if (effectiveDuration <= 0) return "Duration must be greater than zero.";
      if (expediteCfg && effectiveDuration > expediteCfg.max_duration_hours) return `Duration cannot exceed ${expediteCfg.max_duration_hours} h (profile limit).`;
    }
    if (action === "override-priority") {
      if (value === null || value < 0 || value > 100) return "Value must be between 0 and 100.";
    }
    if ((action === "move" || action === "lock-machine") && !machineId.trim()) return "Choose a machine.";
    if (expiresAt && !localInputToIso(expiresAt)) return "Expiry is not a valid date/time.";
    if (startAt && !localInputToIso(startAt)) return "Start is not a valid date/time.";
    return null;
  };

  const buildInput = (reason: string): OrderActionInput | null => {
    const expires_at = localInputToIso(expiresAt);
    switch (action) {
      case "expedite":
        return { action, body: { reason, boost_points: effectiveBoost, duration_hours: effectiveDuration } };
      case "hold":
        return { action, body: { reason, expires_at } };
      case "release":
        return { action, body: { reason } };
      case "override-priority":
        return { action, body: { reason, type: overrideKind, value: value ?? 0, expires_at } };
      case "force-next":
        return { action, body: { reason, expires_at } };
      case "move":
        return { action, body: { reason, target_machine_id: machineId.trim(), start_at: localInputToIso(startAt), expires_at } };
      case "lock-machine":
        return { action, body: { reason, machine_id: machineId.trim(), expires_at } };
      default:
        return null;
    }
  };

  const submit = async (reason: string) => {
    const input = buildInput(reason);
    if (!input || !spec) return;
    try {
      await mutation.mutateAsync(input);
      toast.push({ tone: "success", title: `${spec.title}: ${orderId}`, message: "Recorded in the audit log." });
      onSuccess?.(input.action);
      close();
    } catch (err) {
      toast.push({ tone: "error", title: `${spec.title} failed`, message: describeError(err) });
    }
  };

  const expiryHelp = "Optional. Leave blank for no expiry.";

  return (
    <ActionDialog
      open={action !== null}
      title={spec ? `${spec.title} · ${orderId}` : ""}
      description={spec?.description}
      submitLabel={spec?.label}
      danger={spec?.danger}
      busy={mutation.isPending}
      validate={validate}
      error={mutation.error}
      onSubmit={(reason) => void submit(reason)}
      onClose={close}
    >
      {action === "expedite" ? (
        <div className="grid grid-2">
          <NumberField label="Boost points" value={effectiveBoost} min={1} max={expediteCfg?.max_boost_points} onChange={setBoost} help={expediteCfg ? `Profile default ${expediteCfg.default_boost_points}, max ${expediteCfg.max_boost_points}` : undefined} unit="pts" />
          <NumberField label="Duration" value={effectiveDuration} min={0.5} step={0.5} max={expediteCfg?.max_duration_hours} onChange={setDuration} help={expediteCfg ? `Profile default ${expediteCfg.default_duration_hours} h, max ${expediteCfg.max_duration_hours} h` : "Hours until the boost expires"} unit="h" />
        </div>
      ) : null}
      {action === "override-priority" ? (
        <div className="grid grid-2">
          <SelectField label="Override" value={overrideKind} options={OVERRIDE_KINDS} onChange={setOverrideKind} />
          <NumberField label={overrideKind === "set" ? "New score" : "Points"} value={value} min={0} max={100} onChange={setValue} unit={overrideKind === "set" ? "/100" : "pts"} />
        </div>
      ) : null}
      {action === "move" || action === "lock-machine" ? (
        <div className="grid grid-2">
          <SelectField
            label={action === "move" ? "Target machine" : "Machine"}
            value={machineId}
            placeholder={machineChoices.length ? "Choose…" : allMachines.isPending ? "Loading machines…" : "No machines"}
            options={machineChoices}
            onChange={setMachineId}
            help={eligible && eligible.length > 0 ? "Eligible machines for the next operation" : "All machines (eligibility not evaluated)"}
          />
          {action === "move" ? <TextField label="Start at (optional)" type="datetime-local" value={startAt} onChange={setStartAt} help="Slot start on the target machine; blank = earliest" /> : null}
        </div>
      ) : null}
      {action && action !== "expedite" && action !== "release" ? (
        <TextField label="Expires at" type="datetime-local" value={expiresAt} onChange={setExpiresAt} help={expiresAt && localInputToIso(expiresAt) ? `Until ${formatDateTime(localInputToIso(expiresAt))}` : expiryHelp} />
      ) : null}
      {scheduledMachineId && (action === "move" || action === "lock-machine") ? (
        <div className="text-xs text-muted">
          Currently scheduled on <span className="mono">{scheduledMachineId}</span>.
        </div>
      ) : null}
    </ActionDialog>
  );
}
