import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useOrderExplanation, useOrders } from "@/api/orders";
import type { OrderListItem, OrderListQuery, ReadinessState, RiskLevel } from "@/api/types";
import { routes } from "@/app/nav";
import { AsyncContent } from "@/components/AsyncContent";
import { DataTable, type Column } from "@/components/DataTable";
import { EmptyState } from "@/components/EmptyState";
import { ExplanationPanel } from "@/components/ExplanationPanel";
import { FilterBar, type FilterField } from "@/components/FilterBar";
import { LoadingState } from "@/components/LoadingState";
import { PageHeader } from "@/components/PageHeader";
import { Section } from "@/components/Section";
import { READINESS_LABELS } from "@/lib/constants";
import { useSearchState } from "@/lib/useSearchState";

import {
  customerColumn,
  dueColumn,
  machineColumn,
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

const FILTER_KEYS = ["search", "readiness", "risk_level", "process_type", "blocked"] as const;
const PAGE_LIMIT = 500;

const filterFields: FilterField[] = [
  {
    key: "readiness",
    label: "Readiness",
    kind: "select",
    options: Object.entries(READINESS_LABELS).map(([value, label]) => ({ value, label })),
  },
  {
    key: "risk_level",
    label: "Risk",
    kind: "select",
    options: ["critical", "high", "medium", "low"].map((v) => ({ value: v, label: v })),
  },
  { key: "process_type", label: "Process", kind: "text", placeholder: "cnc_machining" },
  {
    key: "blocked",
    label: "Blocked",
    kind: "select",
    options: [
      { value: "true", label: "Blocked only" },
      { value: "false", label: "Ready only" },
    ],
  },
];

/** Full ranked queue with filters and a side panel answering "why?" for the selected order. */
export default function PriorityQueuePage() {
  const navigate = useNavigate();
  const now = useMemo(() => new Date(), []);
  const { values, set, reset } = useSearchState(FILTER_KEYS);
  const [selected, setSelected] = useState<string | null>(null);

  const query = useMemo<OrderListQuery>(
    () => ({
      sort: "-priority_score",
      limit: PAGE_LIMIT,
      search: values.search || undefined,
      readiness: (values.readiness || undefined) as ReadinessState | undefined,
      risk_level: (values.risk_level || undefined) as RiskLevel | undefined,
      process_type: (values.process_type || undefined) as OrderListQuery["process_type"],
      blocked: values.blocked === "" ? undefined : values.blocked === "true",
    }),
    [values],
  );
  const orders = useOrders(query);
  const explanation = useOrderExplanation(selected ?? undefined);

  const columns = useMemo<Column<OrderListItem>[]>(
    () => [
      rankColumn(),
      orderIdColumn(),
      partColumn(),
      customerColumn(),
      processColumn(),
      scoreColumn(),
      readinessColumn(),
      riskColumn(),
      statusColumn(),
      dueColumn(now),
      projectedColumn(),
      quantityColumn(),
      valueColumn(),
      machineColumn(),
    ],
    [now],
  );

  return (
    <div className="page">
      <PageHeader
        eyebrow="Plan"
        title="Priority Queue"
        subtitle="Every open order ranked by the priority engine. Select a row to see the score breakdown; open the order for actions."
      />
      <FilterBar
        fields={filterFields}
        values={values}
        onChange={set}
        onReset={reset}
        search={{ value: values.search, onChange: (v) => set("search", v), placeholder: "Order, part, customer…" }}
      />
      <div className="grid grid-main-side">
        <Section title="Ranked orders" count={orders.data ? `${orders.data.items.length} of ${orders.data.meta.total}` : undefined} flush>
          <AsyncContent query={orders} isEmpty={(p) => p.items.length === 0} emptyTitle="No orders match" emptyMessage="Loosen the filters or sync the ERP.">
            {(page) => (
              <DataTable
                rows={page.items}
                columns={columns}
                rowKey={(o) => o.order_id}
                selectedKey={selected}
                onRowClick={(o) => setSelected(o.order_id)}
                rowClassName={(o) => orderRowClass(o, now)}
                initialSort={{ key: "score", direction: "desc" }}
                filters
                pageSize={50}
                totalCount={page.meta.total}
                dense
              />
            )}
          </AsyncContent>
        </Section>
        <Section
          title={selected ? `Why ${selected}?` : "Why?"}
          actions={
            selected ? (
              <button type="button" className="btn btn-sm" onClick={() => navigate(routes.orderDetail(encodeURIComponent(selected)))}>
                Open order
              </button>
            ) : null
          }
        >
          {!selected ? (
            <EmptyState compact title="Select an order" message="The explanation is produced by the same engine run that computed the score." />
          ) : explanation.isPending ? (
            <LoadingState compact label="Loading explanation" />
          ) : explanation.isError ? (
            <EmptyState compact title="No explanation available" message="Run the priority engine (generate a schedule) to compute scores." />
          ) : (
            <ExplanationPanel result={explanation.data} />
          )}
        </Section>
      </div>
    </div>
  );
}
