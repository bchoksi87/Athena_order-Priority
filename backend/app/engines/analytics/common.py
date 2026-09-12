"""Helpers shared by the analytics modules (KPIs, capacity, bottlenecks, OTD, alerts).

Everything here is a pure function of the snapshot / results passed in: no
wall-clock reads, no business constants. The helpers exist so that every
analytics view answers questions such as "which resource does this order's
next operation target?", "has this order started?" or "what is the plant's
local date?" in exactly one way, which keeps the numbers on the dashboard,
the bottleneck panel and the alert list mutually consistent.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog

from app.core.clock import ensure_utc
from app.domain.enums import (
    CLOSED_ORDER_STATUSES,
    AlertSeverity,
    OperationStatus,
    OrderStatus,
    ProcessType,
    ReadinessState,
    RiskLevel,
)
from app.domain.models import Machine, Operation, Order
from app.domain.results import PriorityResult, ScheduleEntry, ScheduleResult
from app.domain.snapshot import PlanningSnapshot

log = structlog.get_logger(__name__)

Dimension = Literal["machine", "machine_group", "process", "department"]
DIMENSIONS: tuple[Dimension, ...] = ("machine", "machine_group", "process", "department")

#: Department fallback when neither ``Machine.location`` nor ``attributes["department"]`` is set.
UNASSIGNED_DEPARTMENT = "unassigned"

RISK_RANK: dict[RiskLevel, int] = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}

ALERT_SEVERITY_RANK: dict[AlertSeverity, int] = {
    AlertSeverity.INFO: 0,
    AlertSeverity.WARNING: 1,
    AlertSeverity.HIGH: 2,
    AlertSeverity.CRITICAL: 3,
}

#: Closed statuses that count as *delivered* for on-time-delivery history (cancelled is not).
DELIVERED_STATUSES: frozenset[OrderStatus] = frozenset(CLOSED_ORDER_STATUSES - {OrderStatus.CANCELLED})

#: Order statuses that mean production has already started even without operation timestamps.
STARTED_STATUSES: frozenset[OrderStatus] = frozenset(
    {OrderStatus.IN_PRODUCTION, OrderStatus.PARTIALLY_COMPLETED, OrderStatus.QUALITY_INSPECTION}
)


# ------------------------------------------------------------------ time helpers


def hours_between(a: datetime, b: datetime) -> float:
    """Signed hours from ``a`` to ``b``."""
    return (ensure_utc(b) - ensure_utc(a)).total_seconds() / 3600.0


def plant_timezone(snapshot: PlanningSnapshot) -> tzinfo:
    """Timezone of the plant: the default calendar's zone, else UTC (logged)."""
    spec = snapshot.calendars.get(snapshot.default_calendar_id) if snapshot.default_calendar_id else None
    if spec is None or not spec.timezone:
        return UTC
    try:
        return ZoneInfo(spec.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("analytics.unknown_timezone", timezone=spec.timezone, fallback="UTC")
        return UTC


def local_date(t: datetime, tz: tzinfo) -> date:
    return ensure_utc(t).astimezone(tz).date()


def local_midnight(d: date, tz: tzinfo) -> datetime:
    """UTC instant of local midnight starting day ``d`` in ``tz``."""
    return datetime(d.year, d.month, d.day, tzinfo=tz).astimezone(UTC)


def week_start(d: date) -> date:
    """Monday of the ISO week containing ``d``."""
    return d - timedelta(days=d.weekday())


# --------------------------------------------------------------- order helpers


def order_revenue(order: Order) -> float:
    return order.order_value or 0.0


def order_margin(order: Order) -> float:
    return order.estimated_margin or 0.0


def waiting_since(order: Order) -> datetime | None:
    """Instant the order entered the plant: received date, else order date."""
    start = order.received_date or order.order_date
    return ensure_utc(start) if start is not None else None


def order_started(order: Order, ops: Iterable[Operation]) -> bool:
    """True once any operation started (timestamps, status, completed quantity) or the order status is."""
    if order.order_status in STARTED_STATUSES:
        return True
    for op in ops:
        if op.actual_start is not None or op.actual_end is not None:
            return True
        if op.operation_status in (OperationStatus.IN_PROGRESS, OperationStatus.COMPLETED):
            return True
        if op.completed_quantity > 0:
            return True
    return order.completed_quantity > 0


def next_pending_operation(order: Order, snapshot: PlanningSnapshot) -> Operation | None:
    return snapshot.next_operation_for_order(order.order_id)


def operation_material_id(op: Operation | None, order: Order) -> str | None:
    if op is not None and op.material_id:
        return op.material_id
    return order.required_material_id


def operation_tooling_ids(op: Operation | None, order: Order) -> set[str]:
    ids = set(order.tooling_requirement)
    if op is not None:
        ids |= op.tooling_ids
    return ids


def readiness_for(order: Order, priorities: Mapping[str, PriorityResult]) -> ReadinessState | None:
    """Readiness from the priority result, else inferred from ERP status fields (``None`` = unknown)."""
    result = priorities.get(order.order_id)
    if result is not None:
        return result.readiness
    if order.on_hold:
        return ReadinessState.ON_HOLD
    if order.order_status is OrderStatus.MATERIAL_WAITING:
        return ReadinessState.WAITING_MATERIAL
    if order.order_status is OrderStatus.TOOLING_WAITING:
        return ReadinessState.WAITING_TOOLING
    if not order.drawing_approved:
        return ReadinessState.WAITING_APPROVAL
    return None


# ------------------------------------------------------------ duration helpers


def estimate_operation_minutes(op: Operation, order: Order) -> float | None:
    """Setup + run minutes for the pending quantity of ``op`` from ERP data only.

    Uses the operation's own cycle/setup, then the order-level estimates. An
    in-progress operation needs no further setup. ``None`` when no cycle time
    is known anywhere (the Data Quality engine reports that separately).
    """
    cycle = op.cycle_minutes_per_unit
    if cycle is None:
        cycle = order.estimated_cycle_minutes_per_unit
    if cycle is None:
        return None
    setup = op.setup_minutes if op.setup_minutes is not None else order.estimated_setup_minutes
    setup_minutes = max(0.0, setup or 0.0)
    if op.operation_status is OperationStatus.IN_PROGRESS:
        setup_minutes = 0.0
    return setup_minutes + max(0.0, cycle) * op.pending_quantity


def estimate_order_remaining_minutes(order: Order, snapshot: PlanningSnapshot) -> float | None:
    """Remaining production minutes over pending operations, else the order-level estimate."""
    pending = snapshot.pending_operations_for_order(order.order_id)
    total = 0.0
    if pending:
        for op in pending:
            minutes = estimate_operation_minutes(op, order)
            if minutes is None:
                total = -1.0
                break
            total += minutes
        if total >= 0:
            return total
    if order.estimated_total_production_minutes is not None:
        total = max(0.0, order.estimated_total_production_minutes)
        if order.quantity > 0:
            total *= order.pending_quantity / order.quantity
        return total
    if order.estimated_cycle_minutes_per_unit is not None:
        setup = max(0.0, order.estimated_setup_minutes or 0.0)
        return setup + max(0.0, order.estimated_cycle_minutes_per_unit) * order.pending_quantity
    return None


# ------------------------------------------------------------ resource helpers


def machine_department(machine: Machine) -> str:
    """``location``, else ``attributes["department"]``, else ``UNASSIGNED_DEPARTMENT``."""
    if machine.location:
        return machine.location
    dept = machine.attributes.get("department")
    if isinstance(dept, str) and dept:
        return dept
    return UNASSIGNED_DEPARTMENT


def machine_key(machine: Machine, dimension: Dimension) -> str:
    if dimension == "machine":
        return machine.machine_id
    if dimension == "machine_group":
        return machine.machine_group
    if dimension == "process":
        return machine.process_type.value
    return machine_department(machine)


def _unique(values: Iterable[str]) -> str | None:
    distinct = sorted(set(values))
    return distinct[0] if len(distinct) == 1 else None


class ResourceResolver:
    """Maps operations to the resource key they will consume along a dimension.

    Built once per snapshot; the machine-group and process member look-ups are
    cached so resolving thousands of operations stays O(operations).

    Resolution order (``process`` is always the operation type):

    1. the order's required machine or the operation's assigned machine;
    2. an explicit eligible-machine list, when its members agree on the key;
    3. the routing's machine group (its members must agree for other keys);
    4. the machines supporting the operation's process type, when they agree.

    Anything else is ``None`` and callers report the demand as unallocated
    instead of guessing.
    """

    def __init__(self, snapshot: PlanningSnapshot) -> None:
        self.snapshot = snapshot
        self._group_key: dict[tuple[str, Dimension], str | None] = {}
        self._process_key: dict[tuple[str, Dimension], str | None] = {}

    def _key_for_group(self, group: str, dimension: Dimension) -> str | None:
        cache_key = (group, dimension)
        if cache_key not in self._group_key:
            members = self.snapshot.machines_in_group(group)
            self._group_key[cache_key] = (
                group
                if dimension == "machine_group"
                else (_unique(machine_key(m, dimension) for m in members) if members else None)
            )
        return self._group_key[cache_key]

    def _key_for_process(self, process: ProcessType, dimension: Dimension) -> str | None:
        cache_key = (process.value, dimension)
        if cache_key not in self._process_key:
            members = self.snapshot.machines_for_process(process)
            self._process_key[cache_key] = (
                _unique(machine_key(m, dimension) for m in members) if members else None
            )
        return self._process_key[cache_key]

    def resolve(self, op: Operation, order: Order, dimension: Dimension) -> str | None:
        if dimension == "process":
            return op.operation_type.value
        machines = self.snapshot.machines
        assigned = order.required_machine_id or op.machine_id
        if assigned is not None and assigned in machines:
            return machine_key(machines[assigned], dimension)
        if op.eligible_machine_ids:
            known = [machines[m] for m in sorted(op.eligible_machine_ids) if m in machines]
            if known:
                return _unique(machine_key(m, dimension) for m in known)
        group = op.machine_group or order.machine_group
        if group is not None:
            return self._key_for_group(group, dimension)
        return self._key_for_process(op.operation_type, dimension)


def resolve_operation_resource(
    op: Operation, order: Order, snapshot: PlanningSnapshot, dimension: Dimension
) -> str | None:
    """One-off resolution (see :class:`ResourceResolver`); build a resolver for bulk work."""
    return ResourceResolver(snapshot).resolve(op, order, dimension)


# ------------------------------------------------------------ schedule helpers


def entries_by_order(schedule: ScheduleResult | None) -> dict[str, list[ScheduleEntry]]:
    out: dict[str, list[ScheduleEntry]] = {}
    if schedule is None:
        return out
    for entry in schedule.entries:
        out.setdefault(entry.order_id, []).append(entry)
    for entries in out.values():
        entries.sort(key=lambda e: (e.start, e.operation_id))
    return out


def unscheduled_reasons(schedule: ScheduleResult | None) -> dict[str, str]:
    """``order_id -> reason_code`` for orders with an ``UnscheduledItem`` (first item wins)."""
    out: dict[str, str] = {}
    if schedule is None:
        return out
    for item in schedule.unscheduled:
        out.setdefault(item.order_id, item.reason_code)
    return out


def fully_scheduled_completion(
    schedule: ScheduleResult | None,
    by_order: Mapping[str, list[ScheduleEntry]] | None = None,
) -> dict[str, datetime]:
    """Completion instants of orders with entries and no ``UnscheduledItem`` (metrics semantics)."""
    if schedule is None:
        return {}
    by_order = by_order if by_order is not None else entries_by_order(schedule)
    failed = unscheduled_reasons(schedule)
    return {
        order_id: max(e.end for e in entries)
        for order_id, entries in by_order.items()
        if order_id not in failed and entries
    }


def label_for_process(process_value: str) -> str:
    return process_value.replace("_", " ").title().replace("3D", "3D").replace("Cnc", "CNC")


__all__ = [
    "ALERT_SEVERITY_RANK",
    "DELIVERED_STATUSES",
    "DIMENSIONS",
    "RISK_RANK",
    "STARTED_STATUSES",
    "UNASSIGNED_DEPARTMENT",
    "Dimension",
    "ResourceResolver",
    "entries_by_order",
    "estimate_operation_minutes",
    "estimate_order_remaining_minutes",
    "fully_scheduled_completion",
    "hours_between",
    "label_for_process",
    "local_date",
    "local_midnight",
    "machine_department",
    "machine_key",
    "next_pending_operation",
    "operation_material_id",
    "operation_tooling_ids",
    "order_margin",
    "order_revenue",
    "order_started",
    "plant_timezone",
    "readiness_for",
    "resolve_operation_resource",
    "unscheduled_reasons",
    "waiting_since",
    "week_start",
]
