"""Hand-built fixture builders for engine tests.

Shared by every engine test module. Rules for extension (see the engine task
brief): append new helpers, never rename or change the signature of existing
ones. Every builder returns a plain domain object with sensible defaults so a
test only spells out the fields it is about.

``NOW`` is Monday 2026-09-07 08:00 UTC (13:30 IST): a working-hours instant
in the default calendar.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any

from app.domain.config import PriorityProfile
from app.domain.enums import (
    CustomerTier,
    LockType,
    MachineStatus,
    MaterialStatus,
    OperationStatus,
    OrderStatus,
    OverrideType,
    ProcessType,
    QualityStatus,
)
from app.domain.models import (
    CalendarSpec,
    Customer,
    CustomerRule,
    Expedite,
    Machine,
    Material,
    Operation,
    Order,
    PriorityOverride,
    ScheduleLock,
    Shift,
    TimeWindow,
    Tooling,
)
from app.domain.snapshot import PlanningSnapshot

if TYPE_CHECKING:
    from app.domain.results import PriorityResult, ScheduleEntry, ScheduleResult, UnscheduledItem
from app.engines.priority.base import PriorityContext

NOW = datetime(2026, 9, 7, 8, 0, tzinfo=UTC)
ALL_WEEK = (0, 1, 2, 3, 4, 5, 6)
WEEKDAYS = (0, 1, 2, 3, 4)


def at(days: float = 0, hours: float = 0, minutes: float = 0, base: datetime = NOW) -> datetime:
    """``base`` shifted by the given offsets (UTC-aware)."""
    return base + timedelta(days=days, hours=hours, minutes=minutes)


def window(start: datetime, hours: float, reason: str = "") -> TimeWindow:
    return TimeWindow(start, start + timedelta(hours=hours), reason)


def make_customer(customer_id: str = "C1", **overrides: Any) -> Customer:
    fields: dict[str, Any] = {
        "customer_id": customer_id,
        "customer_name": f"Customer {customer_id}",
        "customer_tier": CustomerTier.STANDARD,
    }
    fields.update(overrides)
    return Customer(**fields)


def make_machine(
    machine_id: str = "CNC-01",
    process_type: ProcessType = ProcessType.CNC_MACHINING,
    machine_group: str = "CNC",
    **overrides: Any,
) -> Machine:
    fields: dict[str, Any] = {
        "machine_id": machine_id,
        "machine_name": machine_id,
        "machine_type": machine_group,
        "process_type": process_type,
        "machine_group": machine_group,
        "status": MachineStatus.AVAILABLE,
    }
    fields.update(overrides)
    return Machine(**fields)


def make_material(
    material_id: str = "AL-6061", available_quantity: float = 100.0, **overrides: Any
) -> Material:
    fields: dict[str, Any] = {
        "material_id": material_id,
        "material_name": material_id,
        "material_type": "metal",
        "available_quantity": available_quantity,
    }
    fields.update(overrides)
    return Material(**fields)


def make_tooling(tooling_id: str = "T-01", **overrides: Any) -> Tooling:
    fields: dict[str, Any] = {"tooling_id": tooling_id, "tooling_name": tooling_id}
    fields.update(overrides)
    return Tooling(**fields)


def make_operation(
    order_id: str = "O1",
    sequence: int = 1,
    operation_type: ProcessType = ProcessType.CNC_MACHINING,
    operation_id: str | None = None,
    **overrides: Any,
) -> Operation:
    fields: dict[str, Any] = {
        "operation_id": operation_id or f"{order_id}-op{sequence}",
        "order_id": order_id,
        "sequence": sequence,
        "operation_type": operation_type,
        "quantity": 10.0,
        "setup_minutes": 30.0,
        "cycle_minutes_per_unit": 6.0,
        "operation_status": OperationStatus.PENDING,
    }
    fields.update(overrides)
    return Operation(**fields)


def make_order(order_id: str = "O1", customer_id: str = "C1", **overrides: Any) -> Order:
    fields: dict[str, Any] = {
        "order_id": order_id,
        "customer_id": customer_id,
        "part_id": f"P-{order_id}",
        "quantity": 10.0,
        "order_status": OrderStatus.RELEASED,
        "material_status": MaterialStatus.UNKNOWN,
        "quality_status": QualityStatus.NONE,
        "process_type": ProcessType.CNC_MACHINING,
        "requested_delivery_date": at(days=5),
        "order_value": 10_000.0,
    }
    fields.update(overrides)
    return Order(**fields)


def make_order_with_ops(
    order_id: str = "O1",
    operation_types: Sequence[ProcessType] = (ProcessType.CNC_MACHINING,),
    customer_id: str = "C1",
    op_overrides: Sequence[dict[str, Any]] | None = None,
    **order_overrides: Any,
) -> tuple[Order, list[Operation]]:
    """An order plus one operation per entry in ``operation_types`` (sequence 1..n)."""
    order = make_order(order_id, customer_id, **order_overrides)
    ops: list[Operation] = []
    for i, op_type in enumerate(operation_types, start=1):
        fields: dict[str, Any] = {"quantity": order.quantity}
        if op_overrides and len(op_overrides) >= i:
            fields.update(op_overrides[i - 1])
        ops.append(make_operation(order_id, i, op_type, **fields))
    return order, ops


def make_calendar_spec(
    calendar_id: str = "CAL-DAY",
    timezone: str = "UTC",
    shifts: Sequence[Shift] | None = None,
    holidays: Iterable[date] = (),
    overtime_windows: Iterable[TimeWindow] = (),
    extra_working_days: Iterable[date] = (),
) -> CalendarSpec:
    """Default: one 08:00-16:00 shift Monday-Friday in ``timezone``."""
    if shifts is None:
        shifts = [Shift("day", time(8, 0), time(16, 0), WEEKDAYS)]
    return CalendarSpec(
        calendar_id=calendar_id,
        name=calendar_id,
        timezone=timezone,
        shifts=list(shifts),
        holidays=list(holidays),
        overtime_windows=list(overtime_windows),
        extra_working_days=list(extra_working_days),
    )


def make_24x7_spec(calendar_id: str = "CAL-24x7") -> CalendarSpec:
    return make_calendar_spec(calendar_id, shifts=[Shift("24x7", time(0, 0), time(0, 0), ALL_WEEK)])


def make_lock(
    order_id: str,
    machine_id: str | None = None,
    lock_type: LockType = LockType.ORDER,
    lock_id: str = "L1",
    **overrides: Any,
) -> ScheduleLock:
    fields: dict[str, Any] = {
        "lock_id": lock_id,
        "lock_type": lock_type,
        "created_by": "planner",
        "created_at": at(hours=-1),
        "reason": "test lock",
        "order_id": order_id,
        "machine_id": machine_id,
    }
    fields.update(overrides)
    return ScheduleLock(**fields)


def make_override(
    order_id: str,
    override_type: OverrideType,
    override_id: str = "OV1",
    **overrides: Any,
) -> PriorityOverride:
    fields: dict[str, Any] = {
        "override_id": override_id,
        "order_id": order_id,
        "override_type": override_type,
        "created_by": "manager",
        "created_at": at(hours=-1),
        "reason": "test override",
    }
    fields.update(overrides)
    return PriorityOverride(**fields)


def make_expedite(
    order_id: str, boost_points: float = 30.0, expedite_id: str = "E1", **overrides: Any
) -> Expedite:
    fields: dict[str, Any] = {
        "expedite_id": expedite_id,
        "order_id": order_id,
        "created_by": "manager",
        "created_at": at(hours=-1),
        "reason": "test expedite",
        "boost_points": boost_points,
        "starts_at": at(hours=-1),
        "expires_at": at(hours=4),
    }
    fields.update(overrides)
    return Expedite(**fields)


def make_snapshot(
    orders: Iterable[Order] = (),
    operations: Iterable[Operation] = (),
    machines: Iterable[Machine] = (),
    customers: Iterable[Customer] = (),
    materials: Iterable[Material] = (),
    tooling: Iterable[Tooling] = (),
    calendars: Iterable[CalendarSpec] = (),
    default_calendar_id: str | None = None,
    locks: Iterable[ScheduleLock] = (),
    overrides: Iterable[PriorityOverride] = (),
    expedites: Iterable[Expedite] = (),
    as_of: datetime = NOW,
) -> PlanningSnapshot:
    """Snapshot from iterables; indexes are rebuilt so query helpers work immediately."""
    orders = list(orders)
    customers = list(customers)
    known_customers = {c.customer_id for c in customers}
    for order in orders:  # every order gets a customer unless the test supplied one
        if order.customer_id not in known_customers:
            customers.append(make_customer(order.customer_id))
            known_customers.add(order.customer_id)
    snapshot = PlanningSnapshot(
        as_of=as_of,
        customers={c.customer_id: c for c in customers},
        orders={o.order_id: o for o in orders},
        operations={op.operation_id: op for op in operations},
        machines={m.machine_id: m for m in machines},
        materials={m.material_id: m for m in materials},
        tooling={t.tooling_id: t for t in tooling},
        calendars={c.calendar_id: c for c in calendars},
        default_calendar_id=default_calendar_id,
        locks=list(locks),
        overrides=list(overrides),
        expedites=list(expedites),
        snapshot_id="snap-test",
        source="test",
    )
    snapshot.rebuild_indexes()
    return snapshot


__all__ = [
    "ALL_WEEK",
    "NOW",
    "WEEKDAYS",
    "at",
    "make_24x7_spec",
    "make_calendar_spec",
    "make_customer",
    "make_expedite",
    "make_lock",
    "make_machine",
    "make_material",
    "make_operation",
    "make_order",
    "make_order_with_ops",
    "make_override",
    "make_snapshot",
    "make_tooling",
    "window",
]


# ---------------------------------------------------------------------------
# Dated orders and fully specified routings (added by the data quality engine
# tests; generic enough for any engine that needs a "clean" order).
# ---------------------------------------------------------------------------


def make_dated_order(
    order_id: str = "O1",
    due_in_days: float | None = 7.0,
    now: datetime = NOW,
    **overrides: Any,
) -> Order:
    """Open order placed 3 days before ``now`` and promised ``due_in_days`` after it.

    ``due_in_days=None`` leaves the order without any delivery date.
    """
    fields: dict[str, Any] = {
        "order_date": now - timedelta(days=3),
        "received_date": now - timedelta(days=3),
        "requested_delivery_date": None,
        "promised_delivery_date": None if due_in_days is None else now + timedelta(days=due_in_days),
    }
    fields.update(overrides)
    return make_order(order_id, **fields)


MATERIAL_STEPS: frozenset[ProcessType] = frozenset(
    {ProcessType.CNC_MACHINING, ProcessType.ADDITIVE_3D_PRINTING}
)


def make_routing(
    order: Order,
    steps: Sequence[ProcessType] = (ProcessType.CNC_MACHINING,),
    **op_overrides: Any,
) -> list[Operation]:
    """One fully specified operation per step.

    Sequence 10/20/..., id ``{order_id}-{seq}``, quantity from the order,
    machine group ``"CNC"`` for machining and the upper-cased process name
    otherwise, material ``"MAT1"`` on material-consuming steps only.
    """
    ops: list[Operation] = []
    for i, process in enumerate(steps, start=1):
        seq = 10 * i
        fields: dict[str, Any] = {
            "quantity": order.quantity,
            "machine_group": "CNC" if process == ProcessType.CNC_MACHINING else process.value.upper(),
            "material_id": "MAT1" if process in MATERIAL_STEPS else None,
            "setup_minutes": 30.0,
            "cycle_minutes_per_unit": 5.0,
        }
        fields.update(op_overrides)
        ops.append(
            make_operation(order.order_id, seq, process, operation_id=f"{order.order_id}-{seq}", **fields)
        )
    return ops


def make_order_with_routing(
    order_id: str = "O1",
    steps: Sequence[ProcessType] = (ProcessType.CNC_MACHINING,),
    op_overrides: dict[str, Any] | None = None,
    **order_overrides: Any,
) -> tuple[Order, list[Operation]]:
    """``make_dated_order`` + ``make_routing`` in one call."""
    order = make_dated_order(order_id, **order_overrides)
    return order, make_routing(order, steps, **(op_overrides or {}))


__all__ += ["MATERIAL_STEPS", "make_dated_order", "make_order_with_routing", "make_routing"]


# ---------------------------------------------------------------------------
# Priority engine helpers (added by the priority engine tests).
# ---------------------------------------------------------------------------


def make_customer_rule(customer_id: str = "C1", **overrides: Any) -> CustomerRule:
    fields: dict[str, Any] = {"customer_id": customer_id}
    fields.update(overrides)
    return CustomerRule(**fields)


def make_profile(weights: dict[str, float] | None = None, **overrides: Any) -> PriorityProfile:
    """Default profile; ``weights`` overrides raw percent weights by factor key (others unchanged)."""
    profile = PriorityProfile(**overrides)
    if weights:
        profile = profile.model_copy(
            update={
                "weights": [
                    w.model_copy(update={"weight": weights.get(w.key, w.weight)}) for w in profile.weights
                ]
            }
        )
    return profile


def make_context(
    snapshot: PlanningSnapshot | None = None,
    profile: PriorityProfile | None = None,
    now: datetime = NOW,
    **fields: Any,
) -> PriorityContext:
    """A base ``PriorityContext`` with hand-supplied look-ups (no builder involved)."""
    return PriorityContext(
        snapshot=snapshot if snapshot is not None else make_snapshot(),
        profile=profile if profile is not None else PriorityProfile(),
        now=now,
        **fields,
    )


__all__ += ["make_context", "make_customer_rule", "make_profile"]


# ---------------------------------------------------------------------------
# Priority results and small plants (added by the scheduling engine tests).
# ---------------------------------------------------------------------------


def make_priority(order_id: str, score: float = 50.0, **overrides: Any) -> PriorityResult:
    """A ready, unblocked PriorityResult with the given score (no factor breakdown)."""
    from app.domain.enums import ReadinessState, RiskLevel
    from app.domain.results import PriorityResult

    fields: dict[str, Any] = {
        "order_id": order_id,
        "score": score,
        "base_score": score,
        "factors": [],
        "adjustments": [],
        "readiness": ReadinessState.READY,
        "blocked": False,
        "blocking_reasons": [],
        "risk_level": RiskLevel.LOW,
        "explanation": f"score {score:g}",
        "profile_id": "PriorityProfile-A",
        "profile_version": 1,
        "computed_at": NOW,
    }
    fields.update(overrides)
    return PriorityResult(**fields)


def make_priorities(scores: dict[str, float], **overrides: Any) -> dict[str, PriorityResult]:
    """Priority results for several orders; ranks follow descending score."""
    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return {oid: make_priority(oid, score, rank=i + 1, **overrides) for i, (oid, score) in enumerate(ordered)}


__all__ += ["make_priorities", "make_priority"]


# ---------------------------------------------------------------------------
# Schedule results (added by the analytics tests).
# ---------------------------------------------------------------------------


def make_entry(
    order_id: str,
    machine_id: str = "CNC-01",
    start: datetime = NOW,
    run_minutes: float = 60.0,
    setup_minutes: float = 0.0,
    operation_id: str | None = None,
    sequence_on_machine: int = 1,
    **overrides: Any,
) -> ScheduleEntry:
    """A schedule entry occupying ``[start - setup, start + run)`` on ``machine_id``."""
    from app.domain.results import ScheduleEntry

    fields: dict[str, Any] = {
        "entry_id": f"ent_{operation_id or f'{order_id}-op1'}",
        "machine_id": machine_id,
        "order_id": order_id,
        "operation_id": operation_id or f"{order_id}-op1",
        "sequence_on_machine": sequence_on_machine,
        "setup_start": start - timedelta(minutes=setup_minutes),
        "start": start,
        "end": start + timedelta(minutes=run_minutes),
        "setup_minutes": setup_minutes,
        "run_minutes": run_minutes,
        "quantity": 10.0,
        "priority_score": 50.0,
        "placement_reason": "test placement",
    }
    fields.update(overrides)
    return ScheduleEntry(**fields)


def make_schedule(
    entries: Iterable[ScheduleEntry] = (),
    unscheduled: Iterable[UnscheduledItem] = (),
    now: datetime = NOW,
    horizon_days: float = 14.0,
    **overrides: Any,
) -> ScheduleResult:
    """A ScheduleResult with the given entries; metrics are left at their defaults."""
    from app.domain.results import ScheduleResult

    fields: dict[str, Any] = {
        "algorithm": "test",
        "algorithm_version": "0",
        "profile_id": "PriorityProfile-A",
        "profile_version": 1,
        "config_version": 1,
        "generated_at": now,
        "horizon_start": now,
        "horizon_end": now + timedelta(days=horizon_days),
        "entries": list(entries),
        "unscheduled": list(unscheduled),
    }
    fields.update(overrides)
    return ScheduleResult(**fields)


def make_unscheduled(
    order_id: str, reason_code: str = "no_eligible_machine", **overrides: Any
) -> UnscheduledItem:
    from app.domain.results import UnscheduledItem

    fields: dict[str, Any] = {
        "order_id": order_id,
        "operation_id": f"{order_id}-op1",
        "reason_code": reason_code,
        "reason": f"test {reason_code}",
    }
    fields.update(overrides)
    return UnscheduledItem(**fields)


__all__ += ["make_entry", "make_schedule", "make_unscheduled"]


# ---------------------------------------------------------------------------
# Small plants, schedule entries and results (added by the simulation /
# replanning engine tests).
# ---------------------------------------------------------------------------


def make_plant_snapshot(
    n_orders: int = 6,
    *,
    machine_ids: Sequence[str] = ("CNC-01", "CNC-02"),
    due_hours: Sequence[float] | None = None,
    material_id: str | None = "AL",
    customers: Sequence[str] = ("C0", "C1", "C2"),
    now: datetime = NOW,
    **order_overrides: Any,
) -> PlanningSnapshot:
    """``n_orders`` single-op CNC orders (setup 30, cycle 6 x qty 10) on CNC machines, 08-16 Mon-Fri.

    Order ``O{i}`` belongs to customer ``customers[i % len]``, is due
    ``due_hours[i]`` hours after ``now`` (default ``6 + 2 i``) and is worth
    ``100 000 x (i + 1)``.
    """
    orders: list[Order] = []
    operations: list[Operation] = []
    for i in range(n_orders):
        due = due_hours[i] if due_hours is not None and i < len(due_hours) else 6.0 + 2.0 * i
        fields: dict[str, Any] = {
            "requested_delivery_date": at(hours=due, base=now),
            "quantity": 10.0,
            "order_value": 100_000.0 * (i + 1),
            "estimated_margin": 20_000.0 * (i + 1),
        }
        fields.update(order_overrides)
        order, ops = make_order_with_ops(
            f"O{i}",
            (ProcessType.CNC_MACHINING,),
            customer_id=customers[i % len(customers)],
            op_overrides=[{"setup_minutes": 30.0, "cycle_minutes_per_unit": 6.0, "material_id": material_id}],
            **fields,
        )
        orders.append(order)
        operations.extend(ops)
    machines = [
        make_machine(mid, calendar_id="CAL", preferred_rank=rank) for rank, mid in enumerate(machine_ids)
    ]
    return make_snapshot(
        orders=orders,
        operations=operations,
        machines=machines,
        customers=[make_customer(c) for c in customers],
        materials=[make_material(material_id)] if material_id else (),
        calendars=[make_calendar_spec("CAL")],
        default_calendar_id="CAL",
        as_of=now,
    )


def make_quality_schedule(
    entries: Sequence[ScheduleEntry] = (),
    quality_score: float | None = None,
    **overrides: Any,
) -> ScheduleResult:
    """``make_schedule`` plus an optional quality score (metrics stay default)."""
    from app.domain.results import ScheduleQuality

    result = make_schedule(entries, **overrides)
    if quality_score is not None:
        result.quality = ScheduleQuality(
            score=quality_score, components={}, weights={}, summary=f"quality {quality_score:g}"
        )
    return result


__all__ += ["make_plant_snapshot", "make_quality_schedule"]
