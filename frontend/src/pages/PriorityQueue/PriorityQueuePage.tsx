/**
 * Priority Queue — "What should we produce next?" (spec Phase 8). Server-side paging,
 * sorting and filtering over GET /orders; a right-hand "Why?" drawer answers the second
 * question without leaving the page; quick actions open the audited action dialog.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useCustomers } from "@/api/customers";
import { useMachines } from "@/api/machines";
import { useOrderExplanation, useOrders } from "@/api/orders";
import { useExpedites } from "@/api/overrides";
import type { OrderListItem, OrderListQuery, OrderStatus, ProcessType, ReadinessState, RiskLevel, SortOrder } from "@/api/types";
import { useAuth } from "@/app/auth";
import { routes } from "@/app/nav";
import { ColumnPicker } from "@/components/ColumnPicker";
import { DataTable, type Column, type SortDirection } from "@/components/DataTable";
import { Drawer } from "@/components/Drawer";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { ExplanationLines } from "@/components/ExplanationLines";
import { FilterBar, type FilterField } from "@/components/FilterBar";
import { KpiCard } from "@/components/KpiCard";
import { LoadingState } from "@/components/LoadingState";
import { MenuButton } from "@/components/MenuButton";
import { PageHeader } from "@/components/PageHeader";
import { Pager } from "@/components/Pager";
import { RiskBadge } from "@/components/RiskBadge";
import { Section } from "@/components/Section";
import { ReadinessPill } from "@/components/StatusPill";
import { isApiError } from "@/api/client";
import { ORDER_STATUSES, PROCESS_TYPES, READINESS_LABELS, RISK_LEVELS, STORAGE_KEYS, humanize } from "@/lib/constants";
import { downloadText, toCsv } from "@/lib/csv";
import { formatCurrency, formatHours, formatNumber } from "@/lib/formatters";
import { formatDateTime, toUtcIso } from "@/lib/time";
import { useLocalStorage } from "@/lib/useLocalStorage";
import { useSearchState } from "@/lib/useSearchState";

import {
  ORDER_COLUMN_SORT_KEYS,
  customerColumn,
  dueColumn,
  latenessColumn,
  machineColumn,
  marginColumn,
  materialColumn,
  orderIdColumn,
  orderRowClass,
  partColumn,
  processColumn,
  projectedColumn,
  quantityColumn,
  rankColumn,
  readinessColumn,
  riskColumn,
  scoreColumn,
  statusColumn,
  valueColumn,
} from "../shared/orderColumns";
import { OrderActionDialog } from "../shared/OrderActionDialog";
import { visibleOrderActions, type OrderActionKind } from "../shared/orderActionSpecs";

const FILTER_KEYS = [
  "search",
  "customer_id",
  "machine_group",
  "process_type",
  "machine_id",
  "risk",
  "readiness",
  "status",
  "on_hold",
  "due_from",
  "due_to",
  "open_only",
  "sort",
  "order",
  "page",
] as const;

/** Default order: the engine's rank from the last run (unranked orders last); ties in score keep their rank. */
const DEFAULT_SORT = "rank";

/** Column key → API sort key, so header clicks become server-side sorts. */
function sortForColumn(key: string): string | undefined {
  return ORDER_COLUMN_SORT_KEYS[key];
}

function columnForSort(sortKey: string): string | undefined {
  return Object.entries(ORDER_COLUMN_SORT_KEYS).find(([, v]) => v === sortKey)?.[0];
}

function endOfDayIso(date: string): string | undefined {
  if (!date) return undefined;
  const d = new Date(`${date}T23:59:59`);
  return Number.isNaN(d.getTime()) ? undefined : toUtcIso(d);
}

function startOfDayIso(date: string): string | undefined {
  if (!date) return undefined;
  const d = new Date(`${date}T00:00:00`);
  return Number.isNaN(d.getTime()) ? undefined : toUtcIso(d);
}

/** Drawer body: the engine's explanation lines for one order. */
function WhyPanel({ orderId, now }: { orderId: string; now: Date }) {
  const explanation = useOrderExplanation(orderId);
  if (explanation.isPending) return <LoadingState compact label="Loading explanation" />;
  if (explanation.isError) {
    const notScored = isApiError(explanation.error) && explanation.error.isNotFound;
    return notScored ? (
      <EmptyState compact title="Not scored yet" message="No priority result is stored for this order. Run the priority engine (generate a schedule) to compute scores and explanations." />
    ) : (
      <ErrorState compact error={explanation.error} onRetry={() => void explanation.refetch()} />
    );
  }
  const x = explanation.data;
  return (
    <div className="col gap-3" data-testid="why-panel">
      <div className="row row-wrap gap-3">
        <div className="explain-score num" data-testid="why-score">
          {Math.round(x.score)}
        </div>
        <div className="col gap-1 text-xs text-muted">
          <div className="row">
            <RiskBadge level={x.risk_level} />
            <ReadinessPill state={x.readiness} size="sm" />
            {x.forced_next ? <span className="pill pill-hold pill-sm">FORCED NEXT</span> : null}
            {x.rank !== null ? <span className="num">rank #{x.rank}</span> : null}
          </div>
          <div>
            Profile {x.profile_id} v{x.profile_version} · computed {formatDateTime(x.computed_at, "dd MMM HH:mm")} ({formatHours((now.getTime() - new Date(x.computed_at).getTime()) / 3_600_000, 1)} ago)
          </div>
        </div>
      </div>
      {x.blocked ? (
        <div className="explain-blockers">
          <strong>Blocked — {READINESS_LABELS[x.readiness] ?? x.readiness}</strong>
          {x.blocking_reasons.length > 0 ? (
            <ul>
              {x.blocking_reasons.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
      <ExplanationLines lines={x.lines} score={x.score} />
    </div>
  );
}

/** Full ranked queue with server-side paging/sorting/filters, a "Why?" drawer and audited quick actions. */
export default function PriorityQueuePage() {
  const navigate = useNavigate();
  const { hasMinRole } = useAuth();
  const now = useMemo(() => new Date(), []);
  const { values, set, reset } = useSearchState(FILTER_KEYS);
  const [pageSize, setPageSize] = useLocalStorage<number>(STORAGE_KEYS.queuePageSize, 50);
  const [hiddenColumns, setHiddenColumns] = useLocalStorage<string[]>(STORAGE_KEYS.queueColumns, ["value", "margin", "material"]);
  const [whyOrderId, setWhyOrderId] = useState<string | null>(null);
  const [pending, setPending] = useState<{ orderId: string; action: OrderActionKind; onHold: boolean } | null>(null);
  const [searchDraft, setSearchDraft] = useState(values.search);
  const [urlSearch, setUrlSearch] = useState(values.search);
  // When the URL changes underneath us (back button, global search) adopt its value during render.
  if (values.search !== urlSearch) {
    setUrlSearch(values.search);
    setSearchDraft(values.search);
  }

  // Debounce the search box so every keystroke does not hit the server.
  useEffect(() => {
    if (searchDraft === values.search) return;
    const id = window.setTimeout(() => {
      set("search", searchDraft);
      set("page", "");
    }, 300);
    return () => window.clearTimeout(id);
  }, [searchDraft, values.search, set]);

  const page = Math.max(1, Number(values.page) || 1);
  const sortKey = values.sort || DEFAULT_SORT;
  const sortOrder: SortOrder = values.order === "asc" || values.order === "desc" ? values.order : sortKey === "priority" || sortKey === "order_value" || sortKey === "margin" ? "desc" : "asc";

  const query = useMemo<OrderListQuery>(
    () => ({
      page,
      page_size: pageSize,
      sort: sortKey,
      order: sortOrder,
      search: values.search || undefined,
      customer_id: values.customer_id || undefined,
      machine_group: values.machine_group || undefined,
      process_type: (values.process_type || undefined) as ProcessType | undefined,
      machine_id: values.machine_id || undefined,
      risk: (values.risk || undefined) as RiskLevel | undefined,
      readiness: (values.readiness || undefined) as ReadinessState | undefined,
      status: (values.status || undefined) as OrderStatus | undefined,
      on_hold: values.on_hold === "" ? undefined : values.on_hold === "true",
      due_from: startOfDayIso(values.due_from),
      due_to: endOfDayIso(values.due_to),
      open_only: values.open_only === "false" ? false : undefined,
    }),
    [page, pageSize, sortKey, sortOrder, values],
  );

  const orders = useOrders(query);
  const filtersActive = FILTER_KEYS.some((k) => k !== "sort" && k !== "order" && k !== "page" && values[k] !== "");
  // Summary strip from API totals; the unfiltered "open" count is the main query's total unless a filter narrows it.
  const openTotal = useOrders({ page_size: 1 }, filtersActive);
  const totals = {
    open: filtersActive ? openTotal : orders,
    overdue: useOrders({ page_size: 1, due_to: toUtcIso(now) }),
    held: useOrders({ page_size: 1, on_hold: true }),
  };
  const expedites = useExpedites();
  const customers = useCustomers({ page_size: 500 });
  const machines = useMachines({});

  const machineGroups = useMemo(() => Array.from(new Set((machines.data ?? []).map((m) => m.machine_group))).sort(), [machines.data]);
  const rows = useMemo(() => orders.data?.items ?? [], [orders.data]);
  const blockedOnPage = rows.filter((o) => o.blocked).length;
  const lateOnPage = rows.filter((o) => (o.projected_lateness_hours ?? 0) > 0).length;

  const filterFields = useMemo<FilterField[]>(
    () => [
      { key: "customer_id", label: "Customer", kind: "select", width: 220, options: (customers.data?.items ?? []).map((c) => ({ value: c.customer_id, label: c.customer_name })) },
      { key: "due_from", label: "Due from", kind: "date", width: 150 },
      { key: "due_to", label: "Due to", kind: "date", width: 150 },
      { key: "machine_group", label: "Machine group", kind: "select", options: machineGroups.map((g) => ({ value: g, label: g })) },
      { key: "process_type", label: "Process", kind: "select", options: PROCESS_TYPES.map((p) => ({ value: p, label: humanize(p) })) },
      { key: "machine_id", label: "Machine", kind: "select", width: 200, options: (machines.data ?? []).map((m) => ({ value: m.machine_id, label: `${m.machine_id} · ${m.machine_name}` })) },
      { key: "risk", label: "Risk", kind: "select", options: RISK_LEVELS.map((r) => ({ value: r, label: r })) },
      { key: "readiness", label: "Readiness", kind: "select", options: Object.entries(READINESS_LABELS).map(([value, label]) => ({ value, label })) },
      { key: "status", label: "Status", kind: "select", options: ORDER_STATUSES.map((s) => ({ value: s, label: humanize(s) })) },
      {
        key: "on_hold",
        label: "Hold",
        kind: "select",
        options: [
          { value: "true", label: "On hold only" },
          { value: "false", label: "Exclude held" },
        ],
      },
      {
        key: "open_only",
        label: "Scope",
        kind: "select",
        options: [{ value: "false", label: "Include closed orders" }],
      },
    ],
    [customers.data, machineGroups, machines.data],
  );

  const onFilterChange = useCallback(
    (key: string, value: string) => {
      set(key, value);
      set("page", "");
    },
    [set],
  );

  const openAction = useCallback((o: OrderListItem, action: OrderActionKind) => setPending({ orderId: o.order_id, action, onHold: o.on_hold }), []);

  const columns = useMemo<Column<OrderListItem>[]>(() => {
    const base: Column<OrderListItem>[] = [
      rankColumn(),
      orderIdColumn(),
      customerColumn(),
      partColumn(),
      dueColumn(now),
      quantityColumn(),
      processColumn(),
      machineColumn(),
      scoreColumn(),
      riskColumn(),
      readinessColumn(),
      statusColumn(),
      projectedColumn(),
      latenessColumn(),
      valueColumn(),
      marginColumn(),
      materialColumn(),
    ];
    const actions: Column<OrderListItem> = {
      key: "actions",
      header: "",
      width: 120,
      align: "right",
      cell: (o) => {
        const items = visibleOrderActions(hasMinRole, o.on_hold).map((a) => ({ key: a.kind, label: a.label, danger: a.danger, onSelect: () => openAction(o, a.kind) }));
        return (
          <span className="row gap-1" style={{ justifyContent: "flex-end" }} onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
            <button type="button" className={`btn btn-sm${whyOrderId === o.order_id ? " btn-primary" : ""}`} onClick={() => setWhyOrderId(o.order_id)} aria-label={`Why is ${o.order_id} prioritised?`}>
              Why?
            </button>
            {items.length > 0 ? <MenuButton label="⋯" ariaLabel={`Actions for ${o.order_id}`} items={items} /> : null}
          </span>
        );
      },
    };
    return [...base, actions];
  }, [now, hasMinRole, openAction, whyOrderId]);

  const tableSort = useMemo<{ key: string; direction: SortDirection } | null>(() => {
    const col = columnForSort(sortKey);
    return col ? { key: col, direction: sortOrder } : null;
  }, [sortKey, sortOrder]);

  const onSortChange = (next: { key: string; direction: SortDirection } | null) => {
    if (!next) {
      set("sort", "");
      set("order", "");
    } else {
      const apiKey = sortForColumn(next.key);
      if (!apiKey) return;
      set("sort", apiKey === DEFAULT_SORT ? "" : apiKey);
      set("order", next.direction);
    }
    set("page", "");
  };

  const exportCsv = () => {
    const visible = columns.filter((c) => c.key !== "actions" && !hiddenColumns.includes(c.key));
    const csv = toCsv(rows, [
      ...visible.map((c) => ({
        header: typeof c.header === "string" ? c.header || c.key : c.key,
        value: (o: OrderListItem) => {
          switch (c.key) {
            case "rank":
              return o.rank;
            case "order_id":
              return o.order_id;
            case "customer":
              return o.customer_name ?? o.customer_id;
            case "part":
              return o.part_name ?? o.part_id;
            case "due":
              return o.due_date;
            case "qty":
              return o.pending_quantity;
            case "process":
              return o.process_type;
            case "machine":
              return o.scheduled_machine_id ?? o.required_machine_id ?? o.machine_group;
            case "score":
              return o.priority_score;
            case "risk":
              return o.risk_level;
            case "readiness":
              return o.readiness;
            case "status":
              return o.order_status;
            case "projected":
              return o.projected_completion ?? o.expected_completion;
            case "lateness":
              return o.projected_lateness_hours ?? o.expected_lateness_hours;
            case "value":
              return o.order_value;
            case "margin":
              return o.estimated_margin;
            case "material":
              return o.required_material_id;
            default:
              return c.sortValue ? c.sortValue(o) : "";
          }
        },
      })),
    ]);
    downloadText(`priority-queue-page${page}-${formatDateTime(now, "yyyyMMdd-HHmm")}.csv`, csv);
  };

  const columnOptions = columns.filter((c) => c.key !== "actions").map((c) => ({ key: c.key, label: typeof c.header === "string" && c.header ? c.header : humanize(c.key), locked: c.key === "order_id" }));
  const scored = rows.some((o) => o.priority_score !== null);

  return (
    <div className="page" data-testid="priority-queue-page">
      <PageHeader
        eyebrow="Plan"
        title="Priority Queue"
        subtitle="What should we produce next? Every open order ranked by the priority engine. Click a row for the order; press Why? for the score breakdown."
        actions={
          <>
            <ColumnPicker columns={columnOptions} hidden={hiddenColumns} onChange={setHiddenColumns} />
            <button type="button" className="btn btn-sm" onClick={exportCsv} disabled={rows.length === 0}>
              Export CSV
            </button>
            <button type="button" className="btn btn-sm" onClick={() => void orders.refetch()} disabled={orders.isFetching}>
              {orders.isFetching ? "Refreshing…" : "Refresh"}
            </button>
          </>
        }
      />

      <div className="grid grid-kpi" data-testid="queue-summary">
        <KpiCard label="Open orders" value={formatNumber(totals.open.data?.meta.total ?? null)} loading={totals.open.isPending} tone="neutral" hint="open_only=true" />
        <KpiCard label="Overdue" value={formatNumber(totals.overdue.data?.meta.total ?? null)} loading={totals.overdue.isPending} tone={(totals.overdue.data?.meta.total ?? 0) > 0 ? "late" : "ready"} hint="due date in the past" />
        <KpiCard label="On hold" value={formatNumber(totals.held.data?.meta.total ?? null)} loading={totals.held.isPending} tone="hold" />
        <KpiCard label="Expedited" value={expedites.isError ? "—" : formatNumber(expedites.data?.length ?? null)} loading={expedites.isPending} tone="running" hint="active expedites" />
        <KpiCard label="Blocked (this page)" value={formatNumber(blockedOnPage)} loading={orders.isPending} tone={blockedOnPage > 0 ? "blocked" : "ready"} hint={scored ? `${lateOnPage} projected late` : "not scored yet"} />
        <KpiCard label="Matching filters" value={formatNumber(orders.data?.meta.total ?? null)} loading={orders.isPending} tone="neutral" />
      </div>

      <FilterBar fields={filterFields} values={values} onChange={onFilterChange} onReset={reset} search={{ value: searchDraft, onChange: setSearchDraft, placeholder: "Order, ERP ref, part, customer…" }} />

      <Section
        title="Ranked orders"
        count={orders.data ? `${orders.data.meta.total} · sorted by ${sortKey} ${sortOrder}` : undefined}
        actions={!scored && rows.length > 0 ? <span className="badge badge-writeback">not scored — run the priority engine</span> : null}
        flush
      >
        {orders.isPending ? (
          <LoadingState label="Loading queue" />
        ) : orders.isError ? (
          <ErrorState error={orders.error} onRetry={() => void orders.refetch()} />
        ) : rows.length === 0 ? (
          <EmptyState title="No orders match" message="Loosen the filters, include closed orders, or sync the ERP." />
        ) : (
          <>
            <DataTable
              rows={rows}
              columns={columns}
              rowKey={(o) => o.order_id}
              selectedKey={whyOrderId}
              onRowClick={(o) => navigate(routes.orderDetail(encodeURIComponent(o.order_id)))}
              rowClassName={(o) => orderRowClass(o, now)}
              sort={tableSort}
              onSortChange={onSortChange}
              hiddenColumns={hiddenColumns}
              paginate={false}
              hideFooter
              dense
              ariaLabel="Priority queue"
            />
            <Pager meta={orders.data.meta} page={page} pageSize={pageSize} shown={rows.length} busy={orders.isFetching} onPageChange={(p) => set("page", p <= 1 ? "" : String(p))} onPageSizeChange={(s) => { setPageSize(s); set("page", ""); }} />
          </>
        )}
      </Section>

      <Drawer
        open={whyOrderId !== null}
        title={
          <>
            <span className="text-muted">Why is</span>
            <span className="mono">{whyOrderId}</span>
            <span className="text-muted">prioritised?</span>
          </>
        }
        onClose={() => setWhyOrderId(null)}
        actions={
          whyOrderId ? (
            <>
              {(() => {
                const row = rows.find((o) => o.order_id === whyOrderId);
                const items = row ? visibleOrderActions(hasMinRole, row.on_hold).map((a) => ({ key: a.kind, label: a.label, danger: a.danger, onSelect: () => openAction(row, a.kind) })) : [];
                return items.length > 0 ? <MenuButton label="Actions" items={items} /> : null;
              })()}
              <button type="button" className="btn btn-sm btn-primary" onClick={() => navigate(routes.orderDetail(encodeURIComponent(whyOrderId)))}>
                Open order
              </button>
            </>
          ) : null
        }
      >
        {whyOrderId ? (
          <>
            {(() => {
              const row = rows.find((o) => o.order_id === whyOrderId);
              return row ? (
                <dl className="kv mb-4">
                  <dt>Customer</dt>
                  <dd>{row.customer_name ?? row.customer_id}</dd>
                  <dt>Part</dt>
                  <dd>{row.part_name ?? row.part_id}</dd>
                  <dt>Due</dt>
                  <dd className="num">{formatDateTime(row.due_date, "dd MMM yyyy HH:mm")}</dd>
                  <dt>Value</dt>
                  <dd className="num">{formatCurrency(row.order_value)}</dd>
                </dl>
              ) : null;
            })()}
            <WhyPanel orderId={whyOrderId} now={now} />
          </>
        ) : null}
      </Drawer>

      {pending ? <OrderActionDialog orderId={pending.orderId} action={pending.action} onClose={() => setPending(null)} /> : null}
    </div>
  );
}
