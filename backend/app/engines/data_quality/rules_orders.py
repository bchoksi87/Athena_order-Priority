"""Order-level data quality rules (spec Phase 21).

Rules here look at ``Order`` records (and, for status/quantity consistency,
their operations). Scheduling-relevant checks (missing due date, missing
customer) only consider *open* orders — a shipped order without a due date is
history, not a planning problem. Data-integrity checks (invalid dates, negative
quantities, duplicates, incorrect status) consider every order because those
records are wrong regardless of status and the ERP team needs the full list.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from typing import Any

from app.domain.config import DataQualityConfig
from app.domain.enums import (
    DataQualityCode,
    DataQualitySeverity,
    OperationStatus,
    OrderStatus,
)
from app.domain.models import Operation, Order
from app.domain.results import DataQualityIssue
from app.domain.snapshot import PlanningSnapshot
from app.engines.data_quality.base import (
    ENTITY_ORDER,
    DataQualityContext,
    fmt_ids,
    is_blank,
    is_naive,
)

#: Average Gregorian year, used to turn ``max_due_date_years_ahead`` into a timedelta.
_DAYS_PER_YEAR = 365.25

_ORDER_DATE_FIELDS: tuple[str, ...] = (
    "order_date",
    "received_date",
    "requested_delivery_date",
    "promised_delivery_date",
    "revised_delivery_date",
)
_DUE_DATE_FIELDS: frozenset[str] = frozenset(
    {"requested_delivery_date", "promised_delivery_date", "revised_delivery_date"}
)
_OPERATION_DATE_FIELDS: tuple[str, ...] = ("estimated_start", "estimated_end", "actual_start", "actual_end")


def effective_due_field(order: Order) -> str | None:
    """Name of the field ``Order.due_date`` resolves to (revised > promised > requested)."""
    if order.revised_delivery_date is not None:
        return "revised_delivery_date"
    if order.promised_delivery_date is not None:
        return "promised_delivery_date"
    if order.requested_delivery_date is not None:
        return "requested_delivery_date"
    return None


class MissingDueDateRule:
    """Open orders without any delivery date cannot be ranked by urgency."""

    key = "missing_due_date"
    entity = ENTITY_ORDER

    def run(
        self, snapshot: PlanningSnapshot, config: DataQualityConfig, ctx: DataQualityContext
    ) -> Iterable[DataQualityIssue]:
        for order in ctx.open_orders:
            if order.due_date is None:
                yield ctx.order_issue(
                    order,
                    DataQualityCode.MISSING_DUE_DATE,
                    f"Order {order.order_id} has no requested, promised or revised delivery date",
                    field_name="promised_delivery_date",
                    recommendation=(
                        "Enter the promised delivery date on the sales order line in the ERP "
                        "(the requested date is used as a fallback). Without it due-date urgency "
                        "and SLA risk cannot be computed."
                    ),
                )


class InvalidDateRule:
    """Dates that are naive, inverted or implausibly far in the future.

    Naive datetimes are flagged first (and the field is then skipped) because
    comparing them with aware values raises ``TypeError`` in every engine.
    """

    key = "invalid_date"
    entity = ENTITY_ORDER

    def run(
        self, snapshot: PlanningSnapshot, config: DataQualityConfig, ctx: DataQualityContext
    ) -> Iterable[DataQualityIssue]:
        horizon = ctx.as_of + timedelta(days=config.max_due_date_years_ahead * _DAYS_PER_YEAR)
        for order in ctx.all_orders:
            yield from self._check_order(order, ctx, horizon)
        for op in snapshot.operations.values():
            yield from self._check_operation(op, ctx)

    def _check_order(
        self, order: Order, ctx: DataQualityContext, horizon: datetime
    ) -> Iterable[DataQualityIssue]:
        due_field = effective_due_field(order)
        aware: dict[str, datetime] = {}
        for name in _ORDER_DATE_FIELDS:
            value: datetime | None = getattr(order, name)
            if value is None:
                continue
            if is_naive(value):
                is_due = name == due_field
                yield ctx.order_issue(
                    order,
                    DataQualityCode.INVALID_DATE,
                    f"Order {order.order_id}.{name} has no timezone ({value.isoformat()})",
                    field_name=name,
                    recommendation="Export ERP timestamps with an explicit timezone (UTC preferred).",
                    details={"value": value.isoformat(), "problem": "naive_datetime"},
                    severity=ctx.policy.for_code(DataQualityCode.MISSING_DUE_DATE) if is_due else None,
                )
                continue
            aware[name] = value

        if due_field is None or due_field not in aware:
            return
        due = aware[due_field]
        anchor_name = "order_date" if "order_date" in aware else "received_date"
        anchor = aware.get(anchor_name)
        if anchor is not None and due < anchor:
            yield ctx.order_issue(
                order,
                DataQualityCode.INVALID_DATE,
                f"Order {order.order_id} is due ({due.date()}) before it was {anchor_name.split('_')[0]}ed "
                f"({anchor.date()})",
                field_name=due_field,
                recommendation=f"Correct {due_field} or {anchor_name} in the ERP; the due date cannot "
                f"precede it.",
                details={
                    "due": due.isoformat(),
                    anchor_name: anchor.isoformat(),
                    "problem": "due_before_order",
                },
            )
        if due > horizon:
            years = ctx.config.max_due_date_years_ahead
            yield ctx.order_issue(
                order,
                DataQualityCode.INVALID_DATE,
                f"Order {order.order_id} is due on {due.date()}, more than {years:g} years after "
                f"{ctx.as_of.date()}",
                field_name=due_field,
                recommendation="Check the year of the delivery date in the ERP (likely a typo).",
                details={"due": due.isoformat(), "max_years_ahead": years, "problem": "due_too_far"},
            )

    def _check_operation(self, op: Operation, ctx: DataQualityContext) -> Iterable[DataQualityIssue]:
        aware: dict[str, datetime] = {}
        for name in _OPERATION_DATE_FIELDS:
            value: datetime | None = getattr(op, name)
            if value is None:
                continue
            if is_naive(value):
                yield ctx.operation_issue(
                    op,
                    DataQualityCode.INVALID_DATE,
                    f"Operation {op.operation_id}.{name} has no timezone ({value.isoformat()})",
                    field_name=name,
                    recommendation="Export ERP timestamps with an explicit timezone (UTC preferred).",
                    details={"value": value.isoformat(), "problem": "naive_datetime"},
                )
                continue
            aware[name] = value
        for start_name, end_name in (("actual_start", "actual_end"), ("estimated_start", "estimated_end")):
            start, end = aware.get(start_name), aware.get(end_name)
            if start is not None and end is not None and end < start:
                yield ctx.operation_issue(
                    op,
                    DataQualityCode.INVALID_DATE,
                    f"Operation {op.operation_id} {end_name} ({end.isoformat()}) is before "
                    f"{start_name} ({start.isoformat()})",
                    field_name=end_name,
                    recommendation=f"Correct {start_name}/{end_name} on the production confirmation "
                    f"in the ERP.",
                    details={
                        start_name: start.isoformat(),
                        end_name: end.isoformat(),
                        "problem": "end_before_start",
                    },
                )
        actual_start = aware.get("actual_start")
        if actual_start is not None and actual_start > ctx.as_of:
            yield ctx.operation_issue(
                op,
                DataQualityCode.INVALID_DATE,
                f"Operation {op.operation_id} reports an actual start in the future "
                f"({actual_start.isoformat()})",
                field_name="actual_start",
                recommendation="Check the shop-floor clock / confirmation timestamp in the ERP.",
                details={"actual_start": actual_start.isoformat(), "problem": "start_in_future"},
            )


class NegativeQuantityRule:
    """Quantities below zero (blocking) and completed > ordered (warning)."""

    key = "negative_quantity"
    entity = ENTITY_ORDER

    def run(
        self, snapshot: PlanningSnapshot, config: DataQualityConfig, ctx: DataQualityContext
    ) -> Iterable[DataQualityIssue]:
        for order in ctx.all_orders:
            yield from self._check_order(order, ctx)
        for op in snapshot.operations.values():
            yield from self._check_operation(op, ctx)

    def _check_order(self, order: Order, ctx: DataQualityContext) -> Iterable[DataQualityIssue]:
        for name in ("quantity", "completed_quantity", "cancelled_quantity"):
            value: float = getattr(order, name)
            if value < 0:
                yield ctx.order_issue(
                    order,
                    DataQualityCode.NEGATIVE_QUANTITY,
                    f"Order {order.order_id}.{name} is negative ({value:g})",
                    field_name=name,
                    recommendation=f"Correct {name} on the order line in the ERP; quantities must be >= 0.",
                    details={"value": value},
                )
        if order.quantity >= 0 and order.completed_quantity + order.cancelled_quantity > order.quantity:
            yield ctx.order_issue(
                order,
                DataQualityCode.NEGATIVE_QUANTITY,
                f"Order {order.order_id} completed + cancelled quantity "
                f"({order.completed_quantity:g} + {order.cancelled_quantity:g}) exceeds ordered "
                f"quantity ({order.quantity:g})",
                field_name="completed_quantity",
                recommendation="Reconcile production confirmations against the ordered quantity in the ERP.",
                details={
                    "quantity": order.quantity,
                    "completed_quantity": order.completed_quantity,
                    "cancelled_quantity": order.cancelled_quantity,
                },
                severity=DataQualitySeverity.WARNING,
            )
        if order.quantity == 0 and order.order_status.is_open:
            yield ctx.order_issue(
                order,
                DataQualityCode.NEGATIVE_QUANTITY,
                f"Order {order.order_id} is {order.order_status.value} with quantity 0",
                field_name="quantity",
                recommendation="Enter the ordered quantity or close the line in the ERP; "
                "zero-quantity lines are ignored by the scheduler.",
                details={"quantity": 0.0, "order_status": order.order_status.value},
                severity=DataQualitySeverity.WARNING,
            )

    def _check_operation(self, op: Operation, ctx: DataQualityContext) -> Iterable[DataQualityIssue]:
        for name in ("quantity", "completed_quantity"):
            value: float = getattr(op, name)
            if value < 0:
                yield ctx.operation_issue(
                    op,
                    DataQualityCode.NEGATIVE_QUANTITY,
                    f"Operation {op.operation_id}.{name} is negative ({value:g})",
                    field_name=name,
                    recommendation=f"Correct {name} on the routing step in the ERP; quantities must be >= 0.",
                    details={"value": value},
                )
        if op.quantity >= 0 and op.completed_quantity > op.quantity:
            yield ctx.operation_issue(
                op,
                DataQualityCode.NEGATIVE_QUANTITY,
                f"Operation {op.operation_id} completed quantity ({op.completed_quantity:g}) exceeds "
                f"its quantity ({op.quantity:g})",
                field_name="completed_quantity",
                recommendation="Reconcile the operation confirmation against the routing quantity "
                "in the ERP.",
                details={"quantity": op.quantity, "completed_quantity": op.completed_quantity},
                severity=DataQualitySeverity.WARNING,
            )


class DuplicateOrderRule:
    """Two internal lines for one ERP line, or look-alike open lines.

    Exact key: ``external_order_ref + order_line_id`` across *all* orders (a
    sync defect, whatever the status). Fuzzy key: same customer, part,
    quantity and due *day* across *open* orders (probably keyed twice).
    """

    key = "duplicate_order"
    entity = ENTITY_ORDER

    def run(
        self, snapshot: PlanningSnapshot, config: DataQualityConfig, ctx: DataQualityContext
    ) -> Iterable[DataQualityIssue]:
        exact: dict[tuple[str, str], list[str]] = defaultdict(list)
        for order in ctx.all_orders:
            if not is_blank(order.external_order_ref) and not is_blank(order.order_line_id):
                exact[(str(order.external_order_ref), str(order.order_line_id))].append(order.order_id)
        yield from self._report(exact, "external_order_ref", "exact_erp_line", ctx)

        fuzzy: dict[tuple[str, str, float, str], list[str]] = defaultdict(list)
        for order in ctx.open_orders:
            due = order.due_date
            if due is None or is_naive(due):
                continue
            fuzzy[(order.customer_id, order.part_id, order.quantity, due.date().isoformat())].append(
                order.order_id
            )
        yield from self._report(fuzzy, "part_id", "same_customer_part_qty_due", ctx)

    @staticmethod
    def _report(
        groups: Mapping[Any, list[str]],
        field_name: str,
        kind: str,
        ctx: DataQualityContext,
    ) -> Iterable[DataQualityIssue]:
        for key, ids in groups.items():
            if len(ids) < 2:
                continue
            group = sorted(ids)
            for order_id in group:
                order = ctx.snapshot.orders[order_id]
                others = [i for i in group if i != order_id]
                yield ctx.order_issue(
                    order,
                    DataQualityCode.DUPLICATE_ORDER,
                    f"Order {order_id} looks like a duplicate of {fmt_ids(others)} "
                    f"({kind.replace('_', ' ')})",
                    field_name=field_name,
                    recommendation="Confirm with sales whether both lines are wanted; cancel the duplicate "
                    "in the ERP or give each line a distinct order line id.",
                    details={"duplicate_of": others, "match": kind, "key": list(key)},
                )


class IncorrectStatusRule:
    """Statuses contradicting quantities or operation progress."""

    key = "incorrect_status"
    entity = ENTITY_ORDER

    _DONE_STATUSES = frozenset({OrderStatus.COMPLETED, OrderStatus.PACKED, OrderStatus.SHIPPED})

    def run(
        self, snapshot: PlanningSnapshot, config: DataQualityConfig, ctx: DataQualityContext
    ) -> Iterable[DataQualityIssue]:
        for order in ctx.all_orders:
            ops = ctx.operations_for(order.order_id)
            status = order.order_status
            if status in self._DONE_STATUSES and order.pending_quantity > 0:
                yield ctx.order_issue(
                    order,
                    DataQualityCode.INCORRECT_STATUS,
                    f"Order {order.order_id} is {status.value} but {order.pending_quantity:g} units are "
                    f"still pending",
                    field_name="order_status",
                    recommendation="Either confirm the remaining quantity or reopen the order in the ERP.",
                    details={"order_status": status.value, "pending_quantity": order.pending_quantity},
                )
            if (
                status == OrderStatus.IN_PRODUCTION
                and ops
                and not any(op.operation_status == OperationStatus.IN_PROGRESS for op in ops)
            ):
                yield ctx.order_issue(
                    order,
                    DataQualityCode.INCORRECT_STATUS,
                    f"Order {order.order_id} is in_production but none of its {len(ops)} operations "
                    f"is in progress",
                    field_name="order_status",
                    recommendation="Start the current operation on the shop floor terminal or correct "
                    "the order status in the ERP.",
                    details={"order_status": status.value, "operation_count": len(ops)},
                )
            if status.is_open and status != OrderStatus.ON_HOLD and ops and all(op.is_done for op in ops):
                yield ctx.order_issue(
                    order,
                    DataQualityCode.INCORRECT_STATUS,
                    f"Order {order.order_id} is {status.value} but every routing step is already done",
                    field_name="order_status",
                    recommendation="Close the order (or confirm the remaining quantity) in the ERP.",
                    details={"order_status": status.value, "operation_count": len(ops)},
                )
            for op in ops:
                yield from self._check_operation(op, ctx)

    @staticmethod
    def _check_operation(op: Operation, ctx: DataQualityContext) -> Iterable[DataQualityIssue]:
        if op.operation_status == OperationStatus.COMPLETED and op.completed_quantity < op.quantity:
            yield ctx.operation_issue(
                op,
                DataQualityCode.INCORRECT_STATUS,
                f"Operation {op.operation_id} is completed but only {op.completed_quantity:g} of "
                f"{op.quantity:g} units are confirmed",
                field_name="operation_status",
                recommendation="Confirm the remaining quantity or set the step back to in_progress "
                "in the ERP.",
                details={
                    "operation_status": op.operation_status.value,
                    "quantity": op.quantity,
                    "completed_quantity": op.completed_quantity,
                },
            )
        elif (
            not op.operation_status.is_done
            and op.quantity > 0
            and op.completed_quantity >= op.quantity
            and op.operation_status != OperationStatus.ON_HOLD
        ):
            yield ctx.operation_issue(
                op,
                DataQualityCode.INCORRECT_STATUS,
                f"Operation {op.operation_id} is {op.operation_status.value} although all "
                f"{op.quantity:g} units are confirmed",
                field_name="operation_status",
                recommendation="Close the routing step in the ERP so the next step becomes ready.",
                details={
                    "operation_status": op.operation_status.value,
                    "quantity": op.quantity,
                    "completed_quantity": op.completed_quantity,
                },
            )


class MissingCustomerRule:
    """Open orders whose customer is blank, unknown or inactive."""

    key = "missing_customer"
    entity = ENTITY_ORDER

    def run(
        self, snapshot: PlanningSnapshot, config: DataQualityConfig, ctx: DataQualityContext
    ) -> Iterable[DataQualityIssue]:
        customers = snapshot.customers
        for order in ctx.open_orders:
            if is_blank(order.customer_id):
                yield ctx.order_issue(
                    order,
                    DataQualityCode.MISSING_CUSTOMER,
                    f"Order {order.order_id} has no customer",
                    field_name="customer_id",
                    recommendation="Link the order line to a customer account in the ERP; customer "
                    "importance, SLA and fairness rules fall back to defaults until then.",
                    details={"customer_id": order.customer_id},
                )
                continue
            customer = customers.get(order.customer_id)
            if customer is None:
                yield ctx.order_issue(
                    order,
                    DataQualityCode.MISSING_CUSTOMER,
                    f"Order {order.order_id} references customer {order.customer_id!r} which is not in the "
                    f"customer master",
                    field_name="customer_id",
                    recommendation="Include the customer in the ERP customer export or correct the "
                    "customer id on the order line.",
                    details={"customer_id": order.customer_id},
                )
            elif not customer.active:
                yield ctx.order_issue(
                    order,
                    DataQualityCode.MISSING_CUSTOMER,
                    f"Order {order.order_id} belongs to inactive customer {order.customer_id}",
                    field_name="customer_id",
                    recommendation="Reactivate the customer account or cancel the order in the ERP.",
                    details={"customer_id": order.customer_id, "active": False},
                    severity=DataQualitySeverity.WARNING,
                )


__all__ = [
    "DuplicateOrderRule",
    "IncorrectStatusRule",
    "InvalidDateRule",
    "MissingCustomerRule",
    "MissingDueDateRule",
    "NegativeQuantityRule",
    "effective_due_field",
]
