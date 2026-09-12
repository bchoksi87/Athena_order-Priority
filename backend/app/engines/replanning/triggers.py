"""Replanning triggers (spec Phase 11: "continuously monitor ...").

:func:`detect_events` compares two snapshots (the one the current schedule
was built from and the freshly synced one) and emits one
:class:`ReplanEvent` per change the spec lists: new orders, completed
orders, machine downtime / recovery, material arrivals, quality failures,
rework, production delays, customer priority changes and due-date changes.
Events are plain data: the :class:`~app.engines.replanning.engine.ReplanningEngine`
decides what they mean, and services persist / notify.

Mapping to :class:`~app.domain.enums.ReplanTriggerType` (the enum is frozen):

* due-date changes, ERP/customer priority changes, customer master changes,
  new customer rules and expedites → ``CUSTOMER_PRIORITY_CHANGE`` (they all
  change *what the customer wants first*; ``details["change"]`` says which);
* new planner overrides / locks → ``MANUAL``;
* ``CONFIG_CHANGE`` and ``SCHEDULED`` are not detectable from snapshots —
  services build them with :func:`manual_event`.

Production delay = an operation whose ``estimated_end`` drifted later than
``alerts.behind_schedule_minutes`` (default :class:`AlertConfig`), whose
actual start is later than its previous estimate by that much, or which is
still running past its previous estimated end. The comparison is
O(orders + operations + machines + materials + customers).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.core.clock import ensure_utc
from app.domain.config import AlertConfig
from app.domain.enums import (
    MachineStatus,
    MaterialStatus,
    OperationStatus,
    OrderStatus,
    OverrideType,
    QualityStatus,
    ReplanTriggerType,
)
from app.domain.models import Machine, Operation, Order
from app.domain.snapshot import PlanningSnapshot

MINUTES_PER_HOUR = 60.0


@dataclass(slots=True)
class ReplanEvent:
    """One observed change that may warrant a replan."""

    type: ReplanTriggerType
    entity_type: str  # "order" | "operation" | "machine" | "material" | "customer" | "system"
    entity_id: str
    occurred_at: datetime
    message: str
    order_id: str | None = None
    machine_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "occurred_at": self.occurred_at.isoformat(),
            "message": self.message,
            "order_id": self.order_id,
            "machine_id": self.machine_id,
            "details": dict(self.details),
        }


def manual_event(
    occurred_at: datetime,
    message: str,
    *,
    type: ReplanTriggerType = ReplanTriggerType.MANUAL,
    entity_type: str = "system",
    entity_id: str = "planner",
    **details: Any,
) -> ReplanEvent:
    """Event a service raises itself (planner request, config change, scheduled run)."""
    return ReplanEvent(type, entity_type, entity_id, ensure_utc(occurred_at), message, details=details)


# ----------------------------------------------------------------- helpers


def _fmt(dt: datetime | None) -> str:
    return ensure_utc(dt).strftime("%Y-%m-%d %H:%M") if dt is not None else "none"


def _minutes(a: datetime, b: datetime) -> float:
    return (ensure_utc(b) - ensure_utc(a)).total_seconds() / 60.0


def _same_dt(a: datetime | None, b: datetime | None) -> bool:
    if a is None or b is None:
        return a is b
    return ensure_utc(a) == ensure_utc(b)


class _Collector:
    def __init__(self, at: datetime) -> None:
        self.at = ensure_utc(at)
        self.events: list[ReplanEvent] = []

    def add(
        self,
        type: ReplanTriggerType,
        entity_type: str,
        entity_id: str,
        message: str,
        *,
        order_id: str | None = None,
        machine_id: str | None = None,
        **details: Any,
    ) -> None:
        self.events.append(
            ReplanEvent(type, entity_type, entity_id, self.at, message, order_id, machine_id, details)
        )


# ------------------------------------------------------------------ orders


def _order_events(prev: PlanningSnapshot, cur: PlanningSnapshot, out: _Collector) -> None:
    for order_id in sorted(cur.orders):
        order = cur.orders[order_id]
        old = prev.orders.get(order_id)
        if old is None:
            if order.is_open:
                out.add(
                    ReplanTriggerType.NEW_ORDER,
                    "order",
                    order_id,
                    f"New order {order_id} (qty {order.pending_quantity:g}, due {_fmt(order.due_date)})",
                    order_id=order_id,
                    due_date=order.due_date,
                    overdue=order.due_date is not None and ensure_utc(order.due_date) < out.at,
                    expedited=order_id in cur.active_expedites(out.at),
                    forced_next=_forced_next(cur, order_id),
                    customer_id=order.customer_id,
                    order_value=order.order_value,
                )
            continue
        _order_change_events(old, order, cur, out)
    for order_id in sorted(prev.orders):
        if order_id not in cur.orders and prev.orders[order_id].is_open:
            out.add(
                ReplanTriggerType.ORDER_COMPLETED,
                "order",
                order_id,
                f"Order {order_id} no longer in the ERP feed (closed or deleted)",
                order_id=order_id,
                reason="missing",
            )


def _forced_next(snapshot: PlanningSnapshot, order_id: str) -> bool:
    return any(
        o.override_type == OverrideType.FORCE_NEXT
        for o in snapshot.active_overrides(snapshot.as_of).get(order_id, ())
    )


def _order_change_events(old: Order, new: Order, cur: PlanningSnapshot, out: _Collector) -> None:
    oid = new.order_id
    if old.is_open and not new.is_open:
        cancelled = new.order_status == OrderStatus.CANCELLED
        verb = "cancelled" if cancelled else "completed"
        out.add(
            ReplanTriggerType.ORDER_COMPLETED,
            "order",
            oid,
            f"Order {oid} {verb} ({old.order_status.value} → {new.order_status.value})",
            order_id=oid,
            reason="cancelled" if cancelled else "completed",
            previous_status=old.order_status.value,
            status=new.order_status.value,
        )
        return
    if not new.is_open:
        return
    if old.quality_status != new.quality_status and new.quality_status in (
        QualityStatus.FAILED,
        QualityStatus.HOLD,
    ):
        out.add(
            ReplanTriggerType.QUALITY_FAILURE,
            "order",
            oid,
            f"Order {oid} quality {old.quality_status.value} → {new.quality_status.value}",
            order_id=oid,
            previous=old.quality_status.value,
            status=new.quality_status.value,
        )
    if (old.order_status != OrderStatus.REWORK and new.order_status == OrderStatus.REWORK) or (
        old.quality_status != QualityStatus.REWORK and new.quality_status == QualityStatus.REWORK
    ):
        out.add(
            ReplanTriggerType.REWORK,
            "order",
            oid,
            f"Order {oid} needs rework ({new.pending_quantity:g} pending)",
            order_id=oid,
            pending_quantity=new.pending_quantity,
        )
    if not _same_dt(old.due_date, new.due_date):
        out.add(
            ReplanTriggerType.CUSTOMER_PRIORITY_CHANGE,
            "order",
            oid,
            f"Order {oid} due date {_fmt(old.due_date)} → {_fmt(new.due_date)}",
            order_id=oid,
            change="due_date",
            previous=old.due_date,
            current=new.due_date,
            earlier=(
                old.due_date is not None
                and new.due_date is not None
                and ensure_utc(new.due_date) < ensure_utc(old.due_date)
            ),
        )
    for attr in ("erp_priority", "customer_priority", "commercial_priority", "technical_priority"):
        a, b = getattr(old, attr), getattr(new, attr)
        if a != b:
            out.add(
                ReplanTriggerType.CUSTOMER_PRIORITY_CHANGE,
                "order",
                oid,
                f"Order {oid} {attr} {a} → {b}",
                order_id=oid,
                change=attr,
                previous=a,
                current=b,
            )
    if old.material_status != MaterialStatus.AVAILABLE and new.material_status == MaterialStatus.AVAILABLE:
        out.add(
            ReplanTriggerType.MATERIAL_ARRIVED,
            "order",
            oid,
            f"Order {oid} material now available (was {old.material_status.value})",
            order_id=oid,
            material_id=new.required_material_id,
        )


# -------------------------------------------------------------- operations


def _operation_events(
    prev: PlanningSnapshot, cur: PlanningSnapshot, alerts: AlertConfig, out: _Collector
) -> None:
    threshold = max(0.0, alerts.behind_schedule_minutes)
    for op_id in sorted(cur.operations):
        op = cur.operations[op_id]
        old = prev.operations.get(op_id)
        order = cur.orders.get(op.order_id)
        if old is None or order is None or not order.is_open:
            continue
        if old.operation_status != OperationStatus.REWORK and op.operation_status == OperationStatus.REWORK:
            out.add(
                ReplanTriggerType.REWORK,
                "operation",
                op_id,
                f"Operation {op_id} of {op.order_id} sent to rework",
                order_id=op.order_id,
                machine_id=op.machine_id,
            )
        delay = _delay_minutes(old, op, out.at)
        if delay is not None and delay > threshold:
            out.add(
                ReplanTriggerType.PRODUCTION_DELAY,
                "operation",
                op_id,
                f"Operation {op_id} of {op.order_id} running {delay / MINUTES_PER_HOUR:.1f} h behind plan",
                order_id=op.order_id,
                machine_id=op.machine_id,
                delay_minutes=delay,
                overdue=order.due_date is not None
                and _projected_end(op) is not None
                and ensure_utc(_projected_end(op) or out.at) > ensure_utc(order.due_date),
            )


def _projected_end(op: Operation) -> datetime | None:
    return op.actual_end or op.estimated_end


def _delay_minutes(old: Operation, new: Operation, at: datetime) -> float | None:
    """Positive drift of the operation's timeline between the two snapshots (minutes)."""
    if new.is_done:
        return None
    drifts: list[float] = []
    if old.estimated_end is not None and new.estimated_end is not None:
        drifts.append(_minutes(old.estimated_end, new.estimated_end))
    if old.estimated_start is not None and new.actual_start is not None and old.actual_start is None:
        drifts.append(_minutes(old.estimated_start, new.actual_start))
    if (
        new.actual_start is not None
        and new.actual_end is None
        and old.estimated_end is not None
        and ensure_utc(old.estimated_end) < at
        and _same_dt(old.estimated_end, new.estimated_end)
    ):
        drifts.append(_minutes(old.estimated_end, at))  # still running past its planned end
    positive = [d for d in drifts if d > 0]
    return max(positive) if positive else None


# ---------------------------------------------------------------- machines


def _downtime_keys(machine: Machine) -> set[tuple[datetime, datetime]]:
    return {(ensure_utc(w.start), ensure_utc(w.end)) for w in machine.all_downtime}


def _machine_events(prev: PlanningSnapshot, cur: PlanningSnapshot, out: _Collector) -> None:
    for machine_id in sorted(cur.machines):
        machine = cur.machines[machine_id]
        old = prev.machines.get(machine_id)
        if old is None:
            if machine.status.is_operable:
                out.add(
                    ReplanTriggerType.MACHINE_UP,
                    "machine",
                    machine_id,
                    f"New machine {machine_id} ({machine.machine_group})",
                    machine_id=machine_id,
                    reason="new_machine",
                )
            continue
        if old.status.is_operable and not machine.status.is_operable:
            out.add(
                ReplanTriggerType.MACHINE_DOWN,
                "machine",
                machine_id,
                f"Machine {machine_id} {old.status.value} → {machine.status.value}",
                machine_id=machine_id,
                previous_status=old.status.value,
                status=machine.status.value,
            )
        elif not old.status.is_operable and machine.status.is_operable:
            out.add(
                ReplanTriggerType.MACHINE_UP,
                "machine",
                machine_id,
                f"Machine {machine_id} {old.status.value} → {machine.status.value}",
                machine_id=machine_id,
                previous_status=old.status.value,
                status=machine.status.value,
            )
        new_windows = sorted(_downtime_keys(machine) - _downtime_keys(old))
        future = [(s, e) for s, e in new_windows if e > out.at]
        if future:
            s, e = future[0]
            out.add(
                ReplanTriggerType.MACHINE_DOWN,
                "machine",
                machine_id,
                f"Machine {machine_id}: new downtime {_fmt(s)} → {_fmt(e)}"
                + (f" (+{len(future) - 1} more)" if len(future) > 1 else ""),
                machine_id=machine_id,
                windows=[(a.isoformat(), b.isoformat()) for a, b in future],
                start=s,
                end=e,
            )
        removed = sorted(_downtime_keys(old) - _downtime_keys(machine))
        removed_future = [(s, e) for s, e in removed if e > out.at]
        if removed_future and machine.status.is_operable:
            s, e = removed_future[0]
            out.add(
                ReplanTriggerType.MACHINE_UP,
                "machine",
                machine_id,
                f"Machine {machine_id}: downtime {_fmt(s)} → {_fmt(e)} cancelled",
                machine_id=machine_id,
                start=s,
                end=e,
            )
    for machine_id in sorted(prev.machines):
        if machine_id not in cur.machines and prev.machines[machine_id].status != MachineStatus.OFFLINE:
            out.add(
                ReplanTriggerType.MACHINE_DOWN,
                "machine",
                machine_id,
                f"Machine {machine_id} removed from the ERP feed",
                machine_id=machine_id,
                reason="removed",
            )


# --------------------------------------------------------------- materials


def _material_events(prev: PlanningSnapshot, cur: PlanningSnapshot, out: _Collector) -> None:
    for material_id in sorted(cur.materials):
        material = cur.materials[material_id]
        old = prev.materials.get(material_id)
        if old is None:
            continue
        gained = material.available_quantity - old.available_quantity
        if gained > 0:
            out.add(
                ReplanTriggerType.MATERIAL_ARRIVED,
                "material",
                material_id,
                f"Material {material_id}: +{gained:g} {material.unit} received "
                f"({material.free_quantity:g} free)",
                received_quantity=gained,
                free_quantity=material.free_quantity,
            )


# --------------------------------------------------------------- customers


def _customer_events(prev: PlanningSnapshot, cur: PlanningSnapshot, out: _Collector) -> None:
    for customer_id in sorted(cur.customers):
        customer = cur.customers[customer_id]
        old = prev.customers.get(customer_id)
        if old is None:
            continue
        changes: dict[str, tuple[Any, Any]] = {}
        for attr in ("customer_tier", "customer_priority", "strategic_customer_flag", "escalation_level"):
            a, b = getattr(old, attr), getattr(customer, attr)
            if a != b:
                changes[attr] = (a.value if hasattr(a, "value") else a, b.value if hasattr(b, "value") else b)
        if changes:
            text = ", ".join(f"{k} {a} → {b}" for k, (a, b) in sorted(changes.items()))
            out.add(
                ReplanTriggerType.CUSTOMER_PRIORITY_CHANGE,
                "customer",
                customer_id,
                f"Customer {customer_id}: {text}",
                change="customer_master",
                changes=changes,
            )
    for customer_id in sorted(set(prev.customer_rules) | set(cur.customer_rules)):
        a, b = prev.customer_rules.get(customer_id), cur.customer_rules.get(customer_id)
        if a == b:
            continue
        what = "removed" if b is None else "added" if a is None else "changed"
        out.add(
            ReplanTriggerType.CUSTOMER_PRIORITY_CHANGE,
            "customer",
            customer_id,
            f"Customer rule for {customer_id} {what}",
            change="customer_rule",
            previous_boost=a.priority_boost_points if a else None,
            boost=b.priority_boost_points if b else None,
            previous_tier=a.tier_override.value if a and a.tier_override else None,
            tier=b.tier_override.value if b and b.tier_override else None,
        )


# ---------------------------------------------------------------- overlays


def _overlay_events(prev: PlanningSnapshot, cur: PlanningSnapshot, out: _Collector) -> None:
    old_exp = {e.expedite_id for e in prev.expedites}
    for expedite in sorted(cur.expedites, key=lambda e: e.expedite_id):
        if expedite.expedite_id not in old_exp and expedite.is_active_at(out.at):
            out.add(
                ReplanTriggerType.CUSTOMER_PRIORITY_CHANGE,
                "order",
                expedite.order_id,
                f"Order {expedite.order_id} expedited (+{expedite.boost_points:g} points)",
                order_id=expedite.order_id,
                change="expedite",
                expedite_id=expedite.expedite_id,
                boost_points=expedite.boost_points,
            )
    old_ovr = {o.override_id for o in prev.overrides}
    for override in sorted(cur.overrides, key=lambda o: o.override_id):
        if override.override_id not in old_ovr and override.is_active_at(out.at):
            out.add(
                ReplanTriggerType.MANUAL,
                "order",
                override.order_id,
                f"Planner override {override.override_type.value} on {override.order_id}",
                order_id=override.order_id,
                override_id=override.override_id,
                override_type=override.override_type.value,
                value=override.value,
            )
    old_locks = {lk.lock_id for lk in prev.locks}
    for lock in sorted(cur.locks, key=lambda lk: lk.lock_id):
        if lock.lock_id not in old_locks and lock.active:
            out.add(
                ReplanTriggerType.MANUAL,
                "order" if lock.order_id else "machine",
                lock.order_id or lock.machine_id or lock.lock_id,
                f"Planner lock {lock.lock_type.value} ({lock.lock_id})",
                order_id=lock.order_id,
                machine_id=lock.machine_id,
                lock_id=lock.lock_id,
                lock_type=lock.lock_type.value,
            )


# ------------------------------------------------------------------ public

_TYPE_ORDER = {t: i for i, t in enumerate(ReplanTriggerType)}


def event_sort_key(event: ReplanEvent) -> tuple[int, str, str]:
    return (_TYPE_ORDER.get(event.type, len(_TYPE_ORDER)), event.entity_type, event.entity_id)


def detect_events(
    previous_snapshot: PlanningSnapshot,
    current_snapshot: PlanningSnapshot,
    *,
    alerts: AlertConfig | None = None,
) -> list[ReplanEvent]:
    """Every change between the two snapshots that the spec lists as a replan trigger."""
    alerts = alerts if alerts is not None else AlertConfig()
    out = _Collector(current_snapshot.as_of)
    _order_events(previous_snapshot, current_snapshot, out)
    _operation_events(previous_snapshot, current_snapshot, alerts, out)
    _machine_events(previous_snapshot, current_snapshot, out)
    _material_events(previous_snapshot, current_snapshot, out)
    _customer_events(previous_snapshot, current_snapshot, out)
    _overlay_events(previous_snapshot, current_snapshot, out)
    return sorted(out.events, key=event_sort_key)


__all__ = ["ReplanEvent", "detect_events", "event_sort_key", "manual_event"]
