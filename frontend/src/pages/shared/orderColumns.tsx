/** Order table column presets shared by Control Tower, Priority Queue and Machine Detail. */
import type { OrderListItem } from "@/api/types";
import type { Column } from "@/components/DataTable";
import { RiskBadge } from "@/components/RiskBadge";
import { ScoreBar } from "@/components/ScoreBar";
import { OrderStatusPill, ReadinessPill } from "@/components/StatusPill";
import { formatCurrency, formatHours, formatNumber } from "@/lib/formatters";
import { formatDateTime, hoursUntil } from "@/lib/time";
import { humanize } from "@/lib/constants";

export function orderDueDate(o: OrderListItem): string | null {
  return o.revised_delivery_date ?? o.promised_delivery_date ?? o.requested_delivery_date ?? null;
}

/** Exception highlighting class for an order row. */
export function orderRowClass(o: OrderListItem, now: Date = new Date()): string | undefined {
  const due = orderDueDate(o);
  const h = hoursUntil(due, now);
  if ((o.projected_lateness_hours ?? 0) > 0 || (h !== null && h < 0)) return "row-late";
  if (o.blocked || (o.readiness && o.readiness !== "ready")) return "row-blocked";
  if (o.risk_level === "high" || o.risk_level === "critical") return "row-at-risk";
  if (o.on_hold) return "row-hold";
  return undefined;
}

export function rankColumn(): Column<OrderListItem> {
  return {
    key: "rank",
    header: "#",
    width: 40,
    numeric: true,
    cell: (o) => o.rank ?? "—",
    sortValue: (o) => o.rank ?? Number.MAX_SAFE_INTEGER,
  };
}

export function orderIdColumn(): Column<OrderListItem> {
  return {
    key: "order_id",
    header: "Order",
    cell: (o) => (
      <span className="mono strong">
        {o.order_id}
        {o.forced_next ? <span className="pill pill-hold pill-sm" style={{ marginLeft: 6 }}>NEXT</span> : null}
      </span>
    ),
    sortValue: (o) => o.order_id,
    filterValue: (o) => `${o.order_id} ${o.external_order_ref ?? ""}`,
  };
}

export function partColumn(): Column<OrderListItem> {
  return {
    key: "part",
    header: "Part",
    cell: (o) => (
      <span className="truncate" style={{ maxWidth: 200, display: "inline-block" }} title={o.part_name ?? o.part_id}>
        {o.part_name ?? o.part_id}
      </span>
    ),
    sortValue: (o) => o.part_name ?? o.part_id,
    filterValue: (o) => `${o.part_id} ${o.part_name ?? ""} ${o.part_family ?? ""}`,
  };
}

export function customerColumn(): Column<OrderListItem> {
  return {
    key: "customer",
    header: "Customer",
    cell: (o) => o.customer_name ?? o.customer_id,
    sortValue: (o) => o.customer_name ?? o.customer_id,
    filterValue: (o) => `${o.customer_id} ${o.customer_name ?? ""}`,
  };
}

export function processColumn(): Column<OrderListItem> {
  return {
    key: "process",
    header: "Process",
    cell: (o) => humanize(o.next_operation_type ?? o.process_type),
    sortValue: (o) => o.next_operation_type ?? o.process_type,
    filterValue: (o) => `${o.process_type} ${o.machine_group ?? ""}`,
  };
}

export function scoreColumn(): Column<OrderListItem> {
  return {
    key: "score",
    header: "Priority",
    width: 130,
    cell: (o) => <ScoreBar value={o.priority_score ?? null} width={120} />,
    sortValue: (o) => o.priority_score ?? -1,
  };
}

export function readinessColumn(): Column<OrderListItem> {
  return {
    key: "readiness",
    header: "Readiness",
    cell: (o) => <ReadinessPill state={o.readiness ?? (o.blocked ? "other_constraint" : null)} size="sm" />,
    sortValue: (o) => o.readiness ?? "zzz",
    filterValue: (o) => o.readiness ?? "",
  };
}

export function riskColumn(): Column<OrderListItem> {
  return {
    key: "risk",
    header: "Risk",
    width: 80,
    cell: (o) => <RiskBadge level={o.risk_level ?? null} />,
    sortValue: (o) => ({ critical: 4, high: 3, medium: 2, low: 1 })[o.risk_level ?? "low"] ?? 0,
    filterValue: (o) => o.risk_level ?? "",
  };
}

export function statusColumn(): Column<OrderListItem> {
  return {
    key: "status",
    header: "Status",
    cell: (o) => <OrderStatusPill status={o.order_status} size="sm" />,
    sortValue: (o) => o.order_status,
    filterValue: (o) => o.order_status,
  };
}

export function dueColumn(now: Date = new Date()): Column<OrderListItem> {
  return {
    key: "due",
    header: "Due",
    cell: (o) => {
      const due = orderDueDate(o);
      const h = hoursUntil(due, now);
      const cls = h === null ? "text-faint" : h < 0 ? "tone-late strong" : h < 24 ? "tone-at-risk" : "";
      return (
        <span className={`num ${cls}`} title={due ?? undefined}>
          {formatDateTime(due, "dd MMM HH:mm")}
          {h !== null ? <span className="text-faint"> ({h < 0 ? "-" : ""}{formatHours(Math.abs(h), 0)})</span> : null}
        </span>
      );
    },
    sortValue: (o) => orderDueDate(o) ?? "9999",
  };
}

export function projectedColumn(): Column<OrderListItem> {
  return {
    key: "projected",
    header: "Projected",
    cell: (o) => {
      const late = o.projected_lateness_hours ?? 0;
      return (
        <span className={`num ${late > 0 ? "tone-late" : ""}`}>
          {formatDateTime(o.projected_completion, "dd MMM HH:mm")}
          {late > 0 ? ` +${formatHours(late, 0)}` : ""}
        </span>
      );
    },
    sortValue: (o) => o.projected_completion ?? "9999",
  };
}

export function quantityColumn(): Column<OrderListItem> {
  return {
    key: "qty",
    header: "Qty",
    numeric: true,
    width: 70,
    cell: (o) => `${formatNumber(o.quantity - o.completed_quantity - o.cancelled_quantity)}/${formatNumber(o.quantity)}`,
    sortValue: (o) => o.quantity,
  };
}

export function valueColumn(): Column<OrderListItem> {
  return {
    key: "value",
    header: "Value",
    numeric: true,
    width: 90,
    cell: (o) => formatCurrency(o.order_value),
    sortValue: (o) => o.order_value ?? -1,
  };
}

export function machineColumn(): Column<OrderListItem> {
  return {
    key: "machine",
    header: "Machine",
    cell: (o) => <span className="mono">{o.scheduled_machine_id ?? o.required_machine_id ?? "—"}</span>,
    sortValue: (o) => o.scheduled_machine_id ?? o.required_machine_id ?? "zzz",
    filterValue: (o) => `${o.scheduled_machine_id ?? ""} ${o.required_machine_id ?? ""} ${o.machine_group ?? ""}`,
  };
}
