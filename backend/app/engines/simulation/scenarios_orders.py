"""Order scenarios: urgent orders, outsourcing, due dates, holds, expedites (spec Phase 7).

* ``urgent_orders``   — inject new orders (inline routing) or clones of
  existing orders with a new due date; each may carry an expedite so it
  scores as urgent, not merely as "new".
* ``outsource``       — remove pending quantity from named orders or from a
  machine group's queue: the quantity is booked as *cancelled* on the order
  (so it leaves the open workload) and recorded as outsourced in the order's
  simulation notes. Partial outsourcing shrinks the pending operations too.
* ``due_date_change`` — sets the *revised* delivery date (the effective due
  date precedence is revised > promised > requested).
* ``hold_orders``     — planner hold (readiness → ON_HOLD).
* ``expedite_orders`` — active expedite from the snapshot instant.
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.clock import ensure_utc
from app.core.errors import ValidationError
from app.domain.enums import OperationStatus, OrderStatus, ProcessType
from app.domain.models import Expedite, Operation, Order
from app.domain.snapshot import PlanningSnapshot
from app.engines.constraints.eligibility import CandidateIndex, candidate_machines
from app.engines.constraints.readiness import next_operation
from app.engines.simulation.base import (
    ScenarioBase,
    ScenarioEffect,
    add_note,
    fmt_dt,
    require_order,
)

SIM_ORDER_PREFIX = "SIM-URGENT"
SIM_USER = "simulation"


# ------------------------------------------------------------ urgent orders


class RouteStep(BaseModel):
    """One operation of an inline urgent order."""

    model_config = ConfigDict(extra="forbid")
    process: ProcessType
    machine_group: str | None = None
    machine_id: str | None = None
    setup_minutes: float | None = Field(default=None, ge=0)
    cycle_minutes_per_unit: float | None = Field(default=None, ge=0)
    material_id: str | None = None
    setup_family: str | None = None


class UrgentOrderSpec(BaseModel):
    """Either ``clone_of`` an existing order (with ``due``) or a fully inline order."""

    model_config = ConfigDict(extra="forbid")
    order_id: str | None = None
    clone_of: str | None = None
    customer_id: str | None = None
    part_id: str | None = None
    part_family: str | None = None
    quantity: float | None = Field(default=None, gt=0)
    due: datetime | None = None
    route: list[RouteStep] = Field(default_factory=list)
    order_value: float | None = None
    estimated_margin: float | None = None
    material_id: str | None = None
    expedite: bool = True
    boost_points: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _complete(self) -> UrgentOrderSpec:
        if self.clone_of is None:
            missing = [
                name
                for name, value in (
                    ("customer_id", self.customer_id),
                    ("part_id", self.part_id),
                    ("quantity", self.quantity),
                    ("due", self.due),
                )
                if value is None
            ]
            if missing:
                raise ValueError(f"inline urgent order needs {', '.join(missing)} (or 'clone_of')")
            if not self.route:
                raise ValueError("inline urgent order needs at least one route step")
        return self


class UrgentOrdersScenario(ScenarioBase):
    """ "What if 3 new urgent orders arrive?" — orders appear as NEW at the snapshot instant."""

    kind: Literal["urgent_orders"] = "urgent_orders"
    orders: list[UrgentOrderSpec] = Field(min_length=1)

    def _mutate(self, snapshot: PlanningSnapshot, effect: ScenarioEffect) -> None:
        now = ensure_utc(snapshot.as_of)
        for index, spec in enumerate(self.orders, start=1):
            order_id = spec.order_id or self._next_id(snapshot, index)
            if order_id in snapshot.orders:
                raise ValidationError(
                    f"urgent_orders: order {order_id!r} already exists", details={"order_id": order_id}
                )
            if spec.clone_of is not None:
                order, ops = self._clone(snapshot, spec, order_id, now)
            else:
                order, ops = self._inline(snapshot, spec, order_id, now)
            snapshot.orders[order_id] = order
            for op in ops:
                snapshot.operations[op.operation_id] = op
            if spec.expedite:
                boost = spec.boost_points
                if boost is None:
                    boost = effect.profile.expedite.default_boost_points
                hours = effect.profile.expedite.default_duration_hours
                snapshot.expedites.append(
                    Expedite(
                        expedite_id=f"sim-exp-{order_id}",
                        order_id=order_id,
                        created_by=SIM_USER,
                        created_at=now,
                        reason="simulated urgent order",
                        boost_points=boost,
                        starts_at=now,
                        expires_at=now + timedelta(hours=hours),
                    )
                )
                add_note(order.attributes, f"expedited +{boost:g} points for {hours:g} h")
            effect.touch_orders(order_id)
            source = f" (clone of {spec.clone_of})" if spec.clone_of else ""
            due = fmt_dt(order.due_date) if order.due_date is not None else "no due date"
            effect.note(
                f"Order {order_id}{source}: qty {order.quantity:g}, due {due}, {len(ops)} operation(s)"
            )

    @staticmethod
    def _next_id(snapshot: PlanningSnapshot, index: int) -> str:
        candidate = f"{SIM_ORDER_PREFIX}-{index}"
        suffix = index
        while candidate in snapshot.orders:
            suffix += 1
            candidate = f"{SIM_ORDER_PREFIX}-{suffix}"
        return candidate

    @staticmethod
    def _clone(
        snapshot: PlanningSnapshot, spec: UrgentOrderSpec, order_id: str, now: datetime
    ) -> tuple[Order, list[Operation]]:
        source = require_order(snapshot, spec.clone_of or "", "urgent_orders")
        order: Order = copy.deepcopy(source)
        order.order_id = order_id
        order.order_line_id = None
        order.external_order_ref = None
        order.order_date = now
        order.received_date = now
        order.order_status = OrderStatus.NEW
        order.completed_quantity = 0.0
        order.cancelled_quantity = 0.0
        order.on_hold = False
        order.hold_reason = None
        order.depends_on_order_ids = set()
        if spec.quantity is not None:
            order.quantity = spec.quantity
        if spec.customer_id is not None:
            order.customer_id = spec.customer_id
        if spec.due is not None:
            order.requested_delivery_date = ensure_utc(spec.due)
            order.promised_delivery_date = None
            order.revised_delivery_date = None
        if spec.order_value is not None:
            order.order_value = spec.order_value
        if spec.estimated_margin is not None:
            order.estimated_margin = spec.estimated_margin
        add_note(order.attributes, f"simulated clone of {source.order_id}")
        ops: list[Operation] = []
        for src_op in snapshot.operations_for_order(source.order_id):
            op: Operation = copy.deepcopy(src_op)
            op.operation_id = f"{order_id}-{src_op.sequence}"
            op.order_id = order_id
            op.quantity = order.quantity
            op.completed_quantity = 0.0
            op.operation_status = OperationStatus.PENDING
            op.prerequisite_operation_id = (
                f"{order_id}-{snapshot.operations[src_op.prerequisite_operation_id].sequence}"
                if src_op.prerequisite_operation_id in snapshot.operations
                else None
            )
            op.estimated_start = op.estimated_end = op.actual_start = op.actual_end = None
            ops.append(op)
        return order, ops

    @staticmethod
    def _inline(
        snapshot: PlanningSnapshot, spec: UrgentOrderSpec, order_id: str, now: datetime
    ) -> tuple[Order, list[Operation]]:
        if spec.customer_id not in snapshot.customers:
            raise ValidationError(
                f"urgent_orders: unknown customer {spec.customer_id!r}",
                details={"customer_id": spec.customer_id},
            )
        quantity = float(spec.quantity or 0.0)
        route = [step.process for step in spec.route]
        order = Order(
            order_id=order_id,
            customer_id=spec.customer_id or "",
            part_id=spec.part_id or "",
            part_family=spec.part_family,
            order_date=now,
            received_date=now,
            requested_delivery_date=ensure_utc(spec.due) if spec.due else None,
            quantity=quantity,
            order_status=OrderStatus.NEW,
            order_value=spec.order_value,
            estimated_margin=spec.estimated_margin,
            process_type=route[0] if route else ProcessType.OTHER,
            manufacturing_route=route,
            required_material_id=spec.material_id,
        )
        add_note(order.attributes, "simulated urgent order")
        ops: list[Operation] = []
        for i, step in enumerate(spec.route, start=1):
            ops.append(
                Operation(
                    operation_id=f"{order_id}-{i * 10}",
                    order_id=order_id,
                    sequence=i * 10,
                    operation_type=step.process,
                    machine_group=step.machine_group,
                    machine_id=step.machine_id,
                    setup_minutes=step.setup_minutes,
                    cycle_minutes_per_unit=step.cycle_minutes_per_unit,
                    quantity=quantity,
                    material_id=step.material_id or spec.material_id,
                    setup_family=step.setup_family,
                )
            )
        return order, ops

    def describe(self) -> str:
        n = len(self.orders)
        return f"{n} urgent order{'s' if n != 1 else ''} arrive{'s' if n == 1 else ''}"


# --------------------------------------------------------------- outsource


class OutsourceScenario(ScenarioBase):
    """Outsource whole orders, or ``quantity`` pieces queued for ``machine_group``.

    Group mode takes the least urgent orders first (latest due date, undated
    first) so the plant keeps the work it must finish soonest; the last order
    may be outsourced partially.
    """

    kind: Literal["outsource"] = "outsource"
    order_ids: list[str] = Field(default_factory=list)
    machine_group: str | None = None
    quantity: float | None = Field(default=None, gt=0)
    supplier: str = "external supplier"

    @model_validator(mode="after")
    def _mode(self) -> OutsourceScenario:
        if not self.order_ids and (self.machine_group is None or self.quantity is None):
            raise ValueError("outsource needs 'order_ids' or both 'machine_group' and 'quantity'")
        return self

    def _mutate(self, snapshot: PlanningSnapshot, effect: ScenarioEffect) -> None:
        total = 0.0
        for order_id in self.order_ids:
            order = require_order(snapshot, order_id, self.kind)
            total += self._outsource(snapshot, order, order.pending_quantity, effect)
        if self.machine_group is not None and self.quantity is not None:
            remaining = float(self.quantity)
            for order in self._group_queue(snapshot, self.machine_group):
                if remaining <= 0:
                    break
                qty = min(order.pending_quantity, remaining)
                if qty <= 0:
                    continue
                remaining -= self._outsource(snapshot, order, qty, effect)
            total += float(self.quantity) - remaining
            if remaining > 0:
                effect.note(
                    f"Only {float(self.quantity) - remaining:g} of {self.quantity:g} pieces were queued for "
                    f"{self.machine_group}; nothing more to outsource"
                )
        effect.note(f"{total:g} piece(s) outsourced to {self.supplier}")

    @staticmethod
    def _group_queue(snapshot: PlanningSnapshot, group: str) -> list[Order]:
        """Open orders whose next operation can run in ``group`` (least urgent first)."""
        index = CandidateIndex(snapshot)
        queue: list[Order] = []
        for order in snapshot.open_orders():
            op, _synthetic = next_operation(order, snapshot)
            candidates = candidate_machines(op, order, snapshot, index).machines
            if any(m.machine_group == group for m in candidates):
                queue.append(order)
        # least urgent first: undated orders, then latest due date, id as tie-break
        queue.sort(
            key=lambda o: (
                o.due_date is not None,
                -ensure_utc(o.due_date).timestamp() if o.due_date is not None else 0.0,
                o.order_id,
            )
        )
        return queue

    def _outsource(
        self, snapshot: PlanningSnapshot, order: Order, qty: float, effect: ScenarioEffect
    ) -> float:
        qty = min(qty, order.pending_quantity)
        if qty <= 0:
            return 0.0
        order.cancelled_quantity += qty
        for op in snapshot.pending_operations_for_order(order.order_id):
            op.quantity = max(op.completed_quantity, op.quantity - qty)
        outsourced = float(order.attributes.get("outsourced_quantity", 0.0) or 0.0) + qty
        order.attributes["outsourced_quantity"] = outsourced
        order.attributes["outsourced_to"] = self.supplier
        add_note(order.attributes, f"{qty:g} piece(s) outsourced to {self.supplier}")
        effect.touch_orders(order.order_id)
        remaining = order.pending_quantity
        effect.note(
            f"Order {order.order_id}: {qty:g} outsourced"
            + (f", {remaining:g} still in-house" if remaining > 0 else ", fully outsourced")
        )
        return qty

    def describe(self) -> str:
        if self.order_ids:
            return f"Outsource {len(self.order_ids)} order(s) to {self.supplier}"
        return f"Outsource {self.quantity:g} pieces from {self.machine_group} to {self.supplier}"


# ------------------------------------------------------- due date / holds


class DueDateChangeScenario(ScenarioBase):
    """ "What if Customer X's order must be completed tomorrow?" — revised delivery date."""

    kind: Literal["due_date_change"] = "due_date_change"
    order_id: str
    new_due: datetime

    def _mutate(self, snapshot: PlanningSnapshot, effect: ScenarioEffect) -> None:
        order = require_order(snapshot, self.order_id, self.kind)
        previous = order.due_date
        order.revised_delivery_date = ensure_utc(self.new_due)
        was = fmt_dt(previous) if previous is not None else "none"
        add_note(order.attributes, f"due date {was} → {fmt_dt(self.new_due)}")
        effect.touch_orders(self.order_id)
        effect.note(f"Order {self.order_id} due {was} → {fmt_dt(self.new_due)}")

    def describe(self) -> str:
        return f"Order {self.order_id} due on {fmt_dt(self.new_due)}"


class HoldOrdersScenario(ScenarioBase):
    kind: Literal["hold_orders"] = "hold_orders"
    order_ids: list[str] = Field(min_length=1)
    reason: str = "simulated hold"

    def _mutate(self, snapshot: PlanningSnapshot, effect: ScenarioEffect) -> None:
        for order_id in self.order_ids:
            order = require_order(snapshot, order_id, self.kind)
            order.on_hold = True
            order.hold_reason = self.reason
            add_note(order.attributes, f"on hold: {self.reason}")
            effect.touch_orders(order_id)
        effect.note(f"{len(self.order_ids)} order(s) put on hold: {self.reason}")

    def describe(self) -> str:
        return f"Hold {len(self.order_ids)} order(s)"


class ExpediteOrdersScenario(ScenarioBase):
    """Expedite orders with ``boost_points`` for ``hours`` (profile defaults when omitted)."""

    kind: Literal["expedite_orders"] = "expedite_orders"
    order_ids: list[str] = Field(min_length=1)
    boost_points: float | None = Field(default=None, ge=0)
    hours: float | None = Field(default=None, gt=0)
    reason: str = "simulated expedite"

    def _mutate(self, snapshot: PlanningSnapshot, effect: ScenarioEffect) -> None:
        now = ensure_utc(snapshot.as_of)
        rules = effect.profile.expedite
        boost = self.boost_points if self.boost_points is not None else rules.default_boost_points
        hours = self.hours if self.hours is not None else rules.default_duration_hours
        for order_id in self.order_ids:
            order = require_order(snapshot, order_id, self.kind)
            snapshot.expedites.append(
                Expedite(
                    expedite_id=f"sim-exp-{order_id}",
                    order_id=order_id,
                    created_by=SIM_USER,
                    created_at=now,
                    reason=self.reason,
                    boost_points=boost,
                    starts_at=now,
                    expires_at=now + timedelta(hours=hours),
                )
            )
            add_note(order.attributes, f"expedited +{boost:g} points for {hours:g} h")
            effect.touch_orders(order_id)
        effect.note(f"{len(self.order_ids)} order(s) expedited +{boost:g} points for {hours:g} h")

    def describe(self) -> str:
        return f"Expedite {len(self.order_ids)} order(s)"


__all__ = [
    "DueDateChangeScenario",
    "ExpediteOrdersScenario",
    "HoldOrdersScenario",
    "OutsourceScenario",
    "RouteStep",
    "UrgentOrderSpec",
    "UrgentOrdersScenario",
]
