/** Order table column presets shared by Control Tower, Priority Queue and Machine Detail. */
import type { OrderListItem, OrderSortKey } from "@/api/types";
import type { Column } from "@/components/DataTable";
import { RiskBadge } from "@/components/RiskBadge";
import { ScoreBar } from "@/components/ScoreBar";
import { OrderStatusPill, ReadinessPill, StatusPill } from "@/components/StatusPill";
import { RISK_RANK, humanize } from "@/lib/constants";
import { formatCurrency, formatHours, formatNumber } from "@/lib/formatters";
import { formatDateTime, formatRelative, hoursUntil } from "@/lib/time";

export function orderDueDate(o: OrderListItem): string | null {
  return o.due_date;
}

export function pendingQuantity(o: OrderListItem): number {
  return o.pending_quantity;
}

/** Exception highlighting class for an order row (late > blocked > at-risk > hold). */
export function orderRowClass(o: OrderListItem, now: Date = new Date()): string | undefined {
  const h = hoursUntil(o.due_date, now);
  if ((o.projected_lateness_hours ?? 0) > 0 || (h !== null && h < 0)) return "row-late";
  if (o.blocked || (o.readiness && o.readiness !== "ready")) return "row-blocked";
  if (o.risk_level === "high" || o.risk_level === "critical") return "row-at-risk";
  if (o.on_hold) return "row-hold";
  return undefined;
}

/** Server sort key for the columns that GET /orders can sort by. */
export const ORDER_COLUMN_SORT_KEYS: Record<string, OrderSortKey> = {
  rank: "rank",
  order_id: "order_id",
  customer: "customer",
  due: "due_date",
  qty: "quantity",
  score: "priority",
  risk: "risk",
  status: "status",
  value: "order_value",
  margin: "margin",
};

export function rankColumn(): Column<OrderListItem> {
  return {
    key: "rank",
    header: "#",
    width: 44,
    numeric: true,
    cell: (o) => (o.rank === null ? <span className="text-faint">—</span> : o.rank),
    sortValue: (o) => o.rank ?? Number.MAX_SAFE_INTEGER,
    title: "Rank in the priority queue (from the last engine run)",
  };
}

export function orderIdColumn(): Column<OrderListItem> {
  return {
    key: "order_id",
    header: "Order",
    cell: (o) => (
      <span className="mono strong text-nowrap">
        {o.order_id}
        {o.forced_next ? (
          <span className="pill pill-hold pill-sm" style={{ marginLeft: 6 }} title="Forced next by a planner override">
            NEXT
          </span>
        ) : null}
        {o.on_hold ? (
          <span className="pill pill-hold pill-sm" style={{ marginLeft: 6 }} title={o.hold_reason ?? "On hold"}>
            HOLD
          </span>
        ) : null}
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
      <span className="truncate" style={{ maxWidth: 180, display: "inline-block", verticalAlign: "middle" }} title={`${o.part_id}${o.part_name ? ` · ${o.part_name}` : ""}${o.part_family ? ` · ${o.part_family}` : ""}`}>
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
    cell: (o) => (
      <span className="truncate" style={{ maxWidth: 180, display: "inline-block", verticalAlign: "middle" }} title={`${o.customer_name ?? ""} (${o.customer_id})`}>
        {o.customer_name ?? o.customer_id}
      </span>
    ),
    sortValue: (o) => o.customer_name ?? o.customer_id,
    filterValue: (o) => `${o.customer_id} ${o.customer_name ?? ""}`,
  };
}

export function processColumn(): Column<OrderListItem> {
  return {
    key: "process",
    header: "Process",
    cell: (o) => (
      <span title={o.machine_group ? `Machine group ${o.machine_group}` : undefined}>
        {humanize(o.process_type)}
        {o.machine_group ? <span className="text-faint mono"> · {o.machine_group}</span> : null}
      </span>
    ),
    sortValue: (o) => o.process_type,
    filterValue: (o) => `${o.process_type} ${o.machine_group ?? ""}`,
  };
}

export function scoreColumn(): Column<OrderListItem> {
  return {
    key: "score",
    header: "Priority",
    width: 132,
    cell: (o) => <ScoreBar value={o.priority_score} width={120} ariaLabel={`Priority score of ${o.order_id}`} />,
    sortValue: (o) => o.priority_score ?? -1,
    title: "Priority score 0–100 from the priority engine",
  };
}

export function readinessColumn(): Column<OrderListItem> {
  return {
    key: "readiness",
    header: "Readiness",
    cell: (o) =>
      o.readiness ? (
        <ReadinessPill state={o.readiness} size="sm" />
      ) : o.blocked ? (
        <ReadinessPill state="other_constraint" size="sm" />
      ) : (
        <StatusPill tone="neutral" size="sm" dot={false}>
          not evaluated
        </StatusPill>
      ),
    sortValue: (o) => o.readiness ?? "zzz",
    filterValue: (o) => o.readiness ?? "",
  };
}

export function riskColumn(): Column<OrderListItem> {
  return {
    key: "risk",
    header: "Risk",
    width: 84,
    cell: (o) => <RiskBadge level={o.risk_level} />,
    sortValue: (o) => (o.risk_level ? RISK_RANK[o.risk_level] : 0),
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

/** Due date: absolute + relative; overdue in red, < 24 h in amber. */
export function dueColumn(now: Date = new Date()): Column<OrderListItem> {
  return {
    key: "due",
    header: "Due",
    cell: (o) => {
      const due = o.due_date;
      const h = hoursUntil(due, now);
      if (!due || h === null) return <span className="text-faint">no due date</span>;
      const cls = h < 0 ? "tone-late strong" : h < 24 ? "tone-at-risk" : "";
      return (
        <span className={`num text-nowrap ${cls}`} title={due}>
          {formatDateTime(due, "dd MMM HH:mm")}
          <span className={h < 0 ? "" : "text-faint"}> · {h < 0 ? `overdue ${formatHours(Math.abs(h), 0)}` : formatRelative(due, now)}</span>
        </span>
      );
    },
    sortValue: (o) => o.due_date ?? "9999",
  };
}

export function projectedColumn(): Column<OrderListItem> {
  return {
    key: "projected",
    header: "Expected completion",
    cell: (o) => {
      const when = o.projected_completion ?? o.expected_completion;
      if (!when) return <span className="text-faint">not scheduled</span>;
      const late = o.projected_lateness_hours ?? o.expected_lateness_hours ?? 0;
      return <span className={`num text-nowrap ${late > 0 ? "tone-late" : ""}`}>{formatDateTime(when, "dd MMM HH:mm")}</span>;
    },
    sortValue: (o) => o.projected_completion ?? o.expected_completion ?? "9999",
  };
}

export function latenessColumn(): Column<OrderListItem> {
  return {
    key: "lateness",
    header: "Lateness",
    numeric: true,
    width: 84,
    cell: (o) => {
      const late = o.projected_lateness_hours ?? o.expected_lateness_hours;
      if (late === null || late === undefined) return <span className="text-faint">—</span>;
      if (late <= 0) return <span className="tone-ready">on time</span>;
      return <span className="tone-late strong">+{formatHours(late, 1)}</span>;
    },
    sortValue: (o) => o.projected_lateness_hours ?? o.expected_lateness_hours ?? -1,
    title: "Projected lateness in hours (positive = late)",
  };
}

export function quantityColumn(): Column<OrderListItem> {
  return {
    key: "qty",
    header: "Qty pending",
    numeric: true,
    width: 92,
    cell: (o) => (
      <span title={`${formatNumber(o.completed_quantity)} of ${formatNumber(o.quantity)} completed`}>
        {formatNumber(o.pending_quantity)}
        <span className="text-faint">/{formatNumber(o.quantity)}</span>
      </span>
    ),
    sortValue: (o) => o.pending_quantity,
  };
}

export function valueColumn(): Column<OrderListItem> {
  return {
    key: "value",
    header: "Value",
    numeric: true,
    width: 92,
    cell: (o) => formatCurrency(o.order_value),
    sortValue: (o) => o.order_value ?? -1,
  };
}

export function marginColumn(): Column<OrderListItem> {
  return {
    key: "margin",
    header: "Margin",
    numeric: true,
    width: 92,
    cell: (o) => formatCurrency(o.estimated_margin),
    sortValue: (o) => o.estimated_margin ?? -1,
  };
}

/** Scheduled machine when placed, otherwise the ERP-required machine or the machine group as a hint. */
export function machineColumn(): Column<OrderListItem> {
  return {
    key: "machine",
    header: "Machine",
    cell: (o) =>
      o.scheduled_machine_id ? (
        <span className="mono" title={`Scheduled on ${o.scheduled_machine_id} (schedule v${o.schedule_version ?? "?"})`}>
          {o.scheduled_machine_id}
        </span>
      ) : o.required_machine_id ? (
        <span className="mono text-muted" title="Required by the ERP routing; not yet scheduled">
          {o.required_machine_id}
        </span>
      ) : (
        <span className="text-faint" title="Not yet scheduled">
          {o.machine_group ? `group ${o.machine_group}` : "—"}
        </span>
      ),
    sortValue: (o) => o.scheduled_machine_id ?? o.required_machine_id ?? "zzz",
    filterValue: (o) => `${o.scheduled_machine_id ?? ""} ${o.required_machine_id ?? ""} ${o.machine_group ?? ""}`,
  };
}

export function materialColumn(): Column<OrderListItem> {
  return {
    key: "material",
    header: "Material",
    cell: (o) => (
      <span className={o.material_status === "available" ? "" : o.material_status === "unknown" ? "text-faint" : "tone-blocked"} title={humanize(o.material_status)}>
        {o.required_material_id ?? "—"}
        <span className="text-faint"> · {humanize(o.material_status)}</span>
      </span>
    ),
    sortValue: (o) => o.material_status,
    filterValue: (o) => `${o.required_material_id ?? ""} ${o.material_status}`,
  };
}
