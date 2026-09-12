/** Planner/manager actions on an order (expedite, hold, release, override, force next) with audit reasons. */
import { useState } from "react";

import { describeError } from "@/api/client";
import { useOrderAction } from "@/api/orders";
import type { OrderDetail, OverrideType } from "@/api/types";
import { useAuth } from "@/app/auth";
import { Modal } from "@/components/Modal";
import { useToast } from "@/components/Toast";

type ActionKind = "expedite" | "hold" | "release" | "override-priority" | "force-next";

interface ActionSpec {
  kind: ActionKind;
  label: string;
  title: string;
  minRole: "planner" | "production_manager";
  danger?: boolean;
}

const ACTIONS: ActionSpec[] = [
  { kind: "expedite", label: "Expedite", title: "Expedite order", minRole: "production_manager" },
  { kind: "force-next", label: "Force next", title: "Force order to run next", minRole: "production_manager" },
  { kind: "override-priority", label: "Override priority", title: "Override priority score", minRole: "production_manager" },
  { kind: "hold", label: "Hold", title: "Put order on hold", minRole: "planner", danger: true },
  { kind: "release", label: "Release hold", title: "Release hold", minRole: "planner" },
];

export interface OrderActionsProps {
  detail: OrderDetail;
}

export function OrderActions({ detail }: OrderActionsProps) {
  const { hasMinRole } = useAuth();
  const toast = useToast();
  const mutation = useOrderAction(detail.order.order_id);
  const [active, setActive] = useState<ActionSpec | null>(null);
  const [reason, setReason] = useState("");
  const [points, setPoints] = useState("30");
  const [hours, setHours] = useState("4");
  const [overrideType, setOverrideType] = useState<OverrideType>("increase_priority");
  const [machineId, setMachineId] = useState("");

  const onHold = detail.order.on_hold || detail.order.order_status === "on_hold";
  const visible = ACTIONS.filter((a) => hasMinRole(a.minRole)).filter((a) => (a.kind === "release" ? onHold : a.kind === "hold" ? !onHold : true));
  if (visible.length === 0) return null;

  const close = () => {
    setActive(null);
    setReason("");
  };

  const submit = async () => {
    if (!active) return;
    const body: Record<string, unknown> = { reason: reason.trim() };
    if (active.kind === "expedite") {
      body.boost_points = Number(points);
      body.duration_hours = Number(hours);
    } else if (active.kind === "override-priority") {
      body.override_type = overrideType;
      if (overrideType !== "force_next") body.value = Number(points);
    } else if (active.kind === "force-next" && machineId.trim()) {
      body.machine_id = machineId.trim();
    }
    try {
      const res = await mutation.mutateAsync({ action: active.kind, body });
      toast.push({ tone: "success", title: active.title, message: res.message });
      close();
    } catch (err) {
      toast.push({ tone: "error", title: `${active.title} failed`, message: describeError(err) });
    }
  };

  const canSubmit = reason.trim().length >= 3 && !mutation.isPending;

  return (
    <>
      {visible.map((a) => (
        <button key={a.kind} type="button" className={`btn${a.danger ? " btn-danger" : a.kind === "expedite" ? " btn-primary" : ""}`} onClick={() => setActive(a)}>
          {a.label}
        </button>
      ))}
      <Modal
        open={active !== null}
        title={active?.title ?? ""}
        onClose={close}
        footer={
          <>
            <button type="button" className="btn" onClick={close} disabled={mutation.isPending}>
              Cancel
            </button>
            <button type="button" className="btn btn-primary" onClick={() => void submit()} disabled={!canSubmit}>
              {mutation.isPending ? "Applying…" : "Apply"}
            </button>
          </>
        }
      >
        <div className="col gap-3">
          <div className="text-muted text-sm">
            Order <span className="mono strong">{detail.order.order_id}</span>. This action is recorded in the audit log with your user id, the previous value and your reason.
          </div>
          {active?.kind === "expedite" ? (
            <div className="grid grid-2">
              <label className="field">
                <span className="label">Boost points</span>
                <input className="input" type="number" min={0} max={100} value={points} onChange={(e) => setPoints(e.target.value)} />
              </label>
              <label className="field">
                <span className="label">Duration (hours)</span>
                <input className="input" type="number" min={1} value={hours} onChange={(e) => setHours(e.target.value)} />
              </label>
            </div>
          ) : null}
          {active?.kind === "override-priority" ? (
            <div className="grid grid-2">
              <label className="field">
                <span className="label">Override type</span>
                <select className="select" value={overrideType} onChange={(e) => setOverrideType(e.target.value as OverrideType)}>
                  <option value="increase_priority">Increase by points</option>
                  <option value="decrease_priority">Decrease by points</option>
                  <option value="set_priority">Set absolute score</option>
                </select>
              </label>
              <label className="field">
                <span className="label">Value</span>
                <input className="input" type="number" min={0} max={100} value={points} onChange={(e) => setPoints(e.target.value)} />
              </label>
            </div>
          ) : null}
          {active?.kind === "force-next" ? (
            <label className="field">
              <span className="label">Machine (optional)</span>
              <input className="input" value={machineId} placeholder="e.g. CNC-04" onChange={(e) => setMachineId(e.target.value)} />
            </label>
          ) : null}
          <label className="field">
            <span className="label">Reason (required)</span>
            <textarea className="textarea" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why is this override needed?" />
          </label>
        </div>
      </Modal>
    </>
  );
}
