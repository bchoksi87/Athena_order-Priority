/** Header action buttons for one order: role-gated, each opening the audited OrderActionDialog. */
import { useState } from "react";

import type { MachineOption, OrderDetail } from "@/api/types";
import { useAuth } from "@/app/auth";
import { MenuButton } from "@/components/MenuButton";

import { OrderActionDialog } from "../shared/OrderActionDialog";
import { visibleOrderActions, type OrderActionKind } from "../shared/orderActionSpecs";

export interface OrderActionsProps {
  detail: OrderDetail;
  eligible?: MachineOption[];
  /** Controlled request from elsewhere on the page (e.g. "Move here" in the machine table). */
  request?: { action: OrderActionKind; machineId?: string | null } | null;
  onRequestHandled?: () => void;
}

export function OrderActions({ detail, eligible, request = null, onRequestHandled }: OrderActionsProps) {
  const { hasMinRole } = useAuth();
  const [local, setLocal] = useState<OrderActionKind | null>(null);
  const onHold = detail.order.on_hold || detail.order.order_status === "on_hold";
  const visible = visibleOrderActions(hasMinRole, onHold);
  if (visible.length === 0 && !request) return null;

  const primary = visible.filter((a) => a.primary || a.kind === "hold" || a.kind === "release");
  const rest = visible.filter((a) => !primary.includes(a));
  const active = request?.action ?? local;

  const close = () => {
    setLocal(null);
    onRequestHandled?.();
  };

  return (
    <>
      {primary.map((a) => (
        <button key={a.kind} type="button" className={`btn${a.danger ? " btn-danger" : a.primary ? " btn-primary" : ""}`} onClick={() => setLocal(a.kind)}>
          {a.label}
        </button>
      ))}
      {rest.length > 0 ? <MenuButton label="More actions ▾" size="md" items={rest.map((a) => ({ key: a.kind, label: a.label, danger: a.danger, onSelect: () => setLocal(a.kind) }))} /> : null}
      <OrderActionDialog
        orderId={detail.order.order_id}
        action={active}
        onClose={close}
        eligible={eligible}
        scheduledMachineId={detail.schedule?.machine_id ?? null}
        defaultMachineId={request?.machineId ?? null}
      />
    </>
  );
}
