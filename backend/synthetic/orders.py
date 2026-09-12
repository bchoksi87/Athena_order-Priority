"""Order and operation builders for the synthetic generator.

Produces realistic order lines (CNC, additive and assembly kinds) with
consistent operation routes, statuses, material/tooling links and
dependencies. All randomness comes from the injected ``random.Random``.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.domain.enums import (
    CustomerTier,
    MaterialStatus,
    OperationStatus,
    OrderStatus,
    ProcessType,
    QualityStatus,
    ShippingStatus,
)
from app.domain.models import Customer, Machine, Material, Operation, Order, Tooling
from synthetic import catalog
from synthetic.catalog import ScaleProfile

#: Order status mix for the month's order book (weights sum to 1.0). About 18 %
#: of the lines are already completed / packed / shipped: they are history for
#: the OTD and customer screens and carry no load; the remaining ~82 % are the
#: open book the capacity calibration is based on (see ``synthetic.generator``).
STATUS_MIX: tuple[tuple[OrderStatus, float], ...] = (
    (OrderStatus.NEW, 0.09),
    (OrderStatus.RELEASED, 0.205),
    (OrderStatus.PLANNED, 0.12),
    (OrderStatus.SCHEDULED, 0.09),
    (OrderStatus.IN_PRODUCTION, 0.14),
    (OrderStatus.PARTIALLY_COMPLETED, 0.05),
    (OrderStatus.QUALITY_INSPECTION, 0.02),
    (OrderStatus.REWORK, 0.02),
    (OrderStatus.ON_HOLD, 0.035),
    (OrderStatus.MATERIAL_WAITING, 0.03),
    (OrderStatus.TOOLING_WAITING, 0.01),
    (OrderStatus.COMPLETED, 0.06),
    (OrderStatus.PACKED, 0.03),
    (OrderStatus.SHIPPED, 0.09),
)

_TIER_ORDER_WEIGHT: dict[CustomerTier, float] = {
    CustomerTier.STRATEGIC: 6.0,
    CustomerTier.KEY: 3.0,
    CustomerTier.STANDARD: 1.0,
    CustomerTier.LOW: 0.5,
}

_LATHE_FAMILIES = frozenset({"Shaft", "Bushing", "Coupling", "Spacer", "Adapter"})
_FIVE_AXIS_FAMILIES = frozenset({"Impeller", "Manifold", "Valve Body", "Housing", "Nozzle", "Enclosure"})
_FIVE_AXIS_SHARE = 0.8  # share of those families that is actually programmed for the 5-axis cell
#: Material class mix of CNC parts (weights): a mostly aluminium / steel shop
#: with a titanium, engineering-plastic, brass and Inconel minority.
_CNC_MATERIAL_CLASSES: tuple[str, ...] = (
    "aluminium",
    "steel",
    "titanium",
    "polymer",
    "copper_alloy",
    "superalloy",
)
_CNC_MATERIAL_WEIGHTS: tuple[float, ...] = (3.0, 3.0, 1.0, 1.0, 1.0, 0.5)
_AM_SHARE = 0.30  # share of part lines that are 3D-printed rather than machined
_DRAWING_UNAPPROVED_SHARE = 0.02
_ASSEMBLY_SHARE = 0.04
_HOLD_REASONS = ("customer request", "credit hold", "engineering change pending", "awaiting PO amendment")
_DUE_TIME_UTC = timedelta(hours=12)  # 17:30 IST end of working day


@dataclass(slots=True)
class OrderBuildContext:
    """Everything the order factory needs, prepared once per generation run."""

    rng: random.Random
    as_of: datetime
    profile: ScaleProfile
    customers: list[Customer]
    machines: list[Machine]
    materials: list[Material]
    tooling: list[Tooling]
    machines_by_group: dict[str, list[Machine]] = field(default_factory=dict)
    materials_by_class: dict[str, list[Material]] = field(default_factory=dict)
    tooling_by_group: dict[str, list[Tooling]] = field(default_factory=dict)
    customer_weights: list[float] = field(default_factory=list)
    #: material id -> groups with at least one machine that accepts it
    groups_for_material: dict[str, set[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for machine in self.machines:
            self.machines_by_group.setdefault(machine.machine_group, []).append(machine)
            accepted = machine.compatible_materials or {m.material_id for m in self.materials}
            for material_id in accepted:
                self.groups_for_material.setdefault(material_id, set()).add(machine.machine_group)
        for material in self.materials:
            self.materials_by_class.setdefault(material.material_type, []).append(material)
        machine_group = {m.machine_id: m.machine_group for m in self.machines}
        for tool in self.tooling:
            for machine_id in tool.compatible_machine_ids:
                group = machine_group.get(machine_id)
                if group is not None:
                    bucket = self.tooling_by_group.setdefault(group, [])
                    if tool not in bucket:
                        bucket.append(tool)
        self.customer_weights = [_TIER_ORDER_WEIGHT[c.customer_tier] for c in self.customers]

    def group_for(
        self, process: ProcessType, preferred: str | None = None, material_id: str | None = None
    ) -> str | None:
        """First group (preferred first) that has machines and, if given, accepts ``material_id``."""
        candidates = [preferred] if preferred else []
        candidates.extend(catalog.GROUPS_BY_PROCESS.get(process, ()))
        compatible = self.groups_for_material.get(material_id) if material_id is not None else None
        for group in candidates:
            if not group or not self.machines_by_group.get(group):
                continue
            if compatible is not None and group not in compatible:
                continue
            return group
        return None

    def shortage_materials(self, classes: tuple[str, ...] | None = None) -> list[Material]:
        """Materials out of stock with a receipt pending, optionally restricted to ``classes``."""
        pool = (
            self.materials
            if classes is None
            else [m for c in classes for m in self.materials_by_class.get(c, ())]
        )
        return [m for m in pool if m.available_quantity <= 0 and m.incoming_quantity > 0]


def _pick_status(rng: random.Random) -> OrderStatus:
    roll = rng.random()
    cumulative = 0.0
    for status, weight in STATUS_MIX:
        cumulative += weight
        if roll < cumulative:
            return status
    return OrderStatus.RELEASED


def _due_offset_days(rng: random.Random, closed: bool) -> int:
    """Due date offset from ``as_of``: ~8 % overdue, ~11 % due today/tomorrow, rest 2-30 days out."""
    if closed:
        return -rng.randint(0, 10)
    roll = rng.random()
    if roll < 0.08:
        return -rng.randint(1, 6)
    if roll < 0.14:
        return 0
    if roll < 0.19:
        return 1
    return round(rng.triangular(2.0, 30.0, 8.0))


def _quantity(rng: random.Random, kind: str, multiplier: float = 1.0) -> float:
    """Lot sizes: log-normal for CNC (median ~12) and printed parts (median ~6, a few big builds)."""
    if kind == "am":
        pieces = min(60.0, math.exp(rng.gauss(1.8, 0.9)))
    elif kind == "assembly":
        pieces = float(rng.randint(1, 12))
    else:
        pieces = math.exp(rng.gauss(2.5, 1.0))
    return float(max(1, round(pieces * multiplier)))


class OrderFactory:
    """Builds order lines and their operations from an :class:`OrderBuildContext`."""

    def __init__(self, ctx: OrderBuildContext) -> None:
        self._ctx = ctx
        self._rng = ctx.rng
        self._order_seq = 0
        self._op_seq = 0

    # ------------------------------------------------------------------ public
    def build(self) -> tuple[list[Order], list[Operation]]:
        total = self._ctx.profile.orders
        assembly_count = round(total * _ASSEMBLY_SHARE)
        orders: list[Order] = []
        operations: list[Operation] = []
        while len(orders) < total - assembly_count:
            customer = self._pick_customer()
            lines = self._rng.choices((1, 2, 3, 4), weights=(60, 25, 10, 5))[0]
            order_no = self._next_order_no()
            for line_no in range(1, lines + 1):
                if len(orders) >= total - assembly_count:
                    break
                kind = "am" if self._rng.random() < _AM_SHARE else "cnc"
                order, ops = self._build_line(customer, order_no, line_no, kind)
                orders.append(order)
                operations.extend(ops)
        self._build_assemblies(orders, operations, assembly_count)
        return orders, operations

    # ---------------------------------------------------------------- helpers
    def _next_order_no(self) -> str:
        self._order_seq += 1
        stamp = self._ctx.as_of.strftime("%y%m")
        return f"SO{stamp}-{self._order_seq:05d}"

    def _next_op_id(self) -> str:
        self._op_seq += 1
        return f"OP-{self._op_seq:07d}"

    def _pick_customer(self) -> Customer:
        return self._rng.choices(self._ctx.customers, weights=self._ctx.customer_weights)[0]

    def _pick_material(self, kind: str, technology: str | None) -> Material | None:
        if kind == "am" and technology is not None:
            material_class = catalog.AM_TECHNOLOGIES[technology][1]
        else:
            material_class = self._rng.choices(_CNC_MATERIAL_CLASSES, weights=_CNC_MATERIAL_WEIGHTS)[0]
        pool = self._ctx.materials_by_class.get(material_class)
        if not pool:
            pool = self._ctx.materials
        return self._rng.choice(pool) if pool else None

    def _cnc_group(self, family: str, material: Material | None) -> str | None:
        """Turned families go to the lathes, complex families mostly to 5-axis, the rest to 3-axis.

        The preferred group is only used when one of its machines accepts the
        part's material; otherwise the route falls back to the next capable
        group (e.g. a brass impeller is milled on a 3-axis VMC, not a 5-axis
        cell that is qualified for metals only).
        """
        preferred = "CNC3"
        if family in _LATHE_FAMILIES:
            preferred = "LATHE"
        elif family in _FIVE_AXIS_FAMILIES and self._rng.random() < _FIVE_AXIS_SHARE:
            preferred = "CNC5"
        material_id = material.material_id if material is not None else None
        return self._ctx.group_for(ProcessType.CNC_MACHINING, preferred, material_id)

    def _build_line(
        self, customer: Customer, order_no: str, line_no: int, kind: str
    ) -> tuple[Order, list[Operation]]:
        rng = self._rng
        as_of = self._ctx.as_of
        family = rng.choice(catalog.PART_FAMILIES)
        technology = rng.choice(tuple(catalog.AM_TECHNOLOGIES)) if kind == "am" else None
        if technology is not None and not self._ctx.machines_by_group.get(
            catalog.AM_TECHNOLOGIES[technology][0]
        ):
            technology = next(
                (t for t, (g, _) in catalog.AM_TECHNOLOGIES.items() if self._ctx.machines_by_group.get(g)),
                "FDM",
            )
        material = self._pick_material(kind, technology) if kind != "assembly" else None
        quantity = _quantity(rng, kind, self._ctx.profile.lot_multiplier)
        status = _pick_status(rng)
        closed = status in (OrderStatus.COMPLETED, OrderStatus.PACKED, OrderStatus.SHIPPED)
        if status == OrderStatus.MATERIAL_WAITING and material is not None:
            # Swap in an out-of-stock material of the *same class* so the route stays
            # consistent with the machine groups' material compatibility; when none
            # is short the ERP flag alone marks the line as waiting for material.
            shortage = self._ctx.shortage_materials((material.material_type,))
            if shortage:
                material = rng.choice(shortage)
        route: tuple[ProcessType, ...]
        if kind == "assembly":
            route = catalog.ASSEMBLY_ROUTE
            primary_group = self._ctx.group_for(ProcessType.ASSEMBLY)
        elif technology is not None:
            route = rng.choices(catalog.AM_ROUTES, weights=catalog.AM_ROUTE_WEIGHTS)[0]
            primary_group = self._ctx.group_for(
                ProcessType.ADDITIVE_3D_PRINTING, catalog.AM_TECHNOLOGIES[technology][0]
            )
        else:
            route = rng.choices(catalog.CNC_ROUTES, weights=catalog.CNC_ROUTE_WEIGHTS)[0]
            primary_group = self._cnc_group(family, material)
        order_id = f"{order_no}-{line_no:02d}"
        setup_family = f"{material.material_id.removeprefix('MAT-')}-{family}" if material else family
        operations = self._build_operations(order_id, route, primary_group, material, quantity, setup_family)
        self._apply_progress(status, operations, quantity)

        due_day = as_of.date() + timedelta(days=_due_offset_days(rng, closed))
        due = datetime.combine(due_day, datetime.min.time(), tzinfo=as_of.tzinfo) + _DUE_TIME_UTC
        lead = timedelta(days=rng.randint(7, 45))
        order_date = min(due - lead, as_of - timedelta(days=1))
        promised = due + timedelta(days=rng.randint(0, 3)) if rng.random() < 0.2 else due
        revised = promised + timedelta(days=rng.randint(1, 7)) if rng.random() < 0.08 else None

        total_cycle = sum((op.cycle_minutes_per_unit or 0.0) for op in operations)
        total_setup = sum((op.setup_minutes or 0.0) for op in operations)
        unit_cost = float(material.attributes.get("unit_cost", 500.0)) if material else 500.0
        mat_per_unit = operations[0].material_quantity_per_unit or 0.5
        unit_price = max(300.0, (unit_cost * mat_per_unit + total_cycle * 28.0) * rng.uniform(1.8, 4.5))
        order_value = round(unit_price * quantity, 2)
        margin_ratio = rng.uniform(0.08, 0.45)
        completed_qty = quantity if closed else 0.0
        if status == OrderStatus.PARTIALLY_COMPLETED:
            completed_qty = float(int(quantity * rng.uniform(0.2, 0.7)))
        on_hold = status == OrderStatus.ON_HOLD
        drawing_ok = not (
            status in (OrderStatus.NEW, OrderStatus.RELEASED, OrderStatus.PLANNED)
            and rng.random() < _DRAWING_UNAPPROVED_SHARE / 0.48
        )
        order = Order(
            order_id=order_id,
            customer_id=customer.customer_id,
            part_id=f"P-{family[:3].upper()}-{rng.randint(1000, 9999)}",
            order_line_id=str(line_no),
            external_order_ref=order_no,
            part_name=f"{family} {rng.choice(('Rev A', 'Rev B', 'Rev C', 'Mk II'))}",
            part_family=family,
            order_date=order_date,
            received_date=min(order_date + timedelta(hours=rng.randint(1, 30)), as_of),
            requested_delivery_date=due,
            promised_delivery_date=promised,
            revised_delivery_date=revised,
            quantity=quantity,
            completed_quantity=completed_qty,
            order_status=status,
            erp_priority=customer.customer_priority,
            production_status=status.value.upper(),
            material_status=self._material_status(material, quantity, mat_per_unit, status),
            quality_status=self._quality_status(status),
            shipping_status=(
                ShippingStatus.SHIPPED if status == OrderStatus.SHIPPED else ShippingStatus.NOT_SHIPPED
            ),
            order_value=order_value,
            estimated_cost=round(order_value * (1.0 - margin_ratio), 2),
            estimated_margin=round(order_value * margin_ratio, 2),
            process_type=route[0],
            manufacturing_route=list(route),
            machine_group=primary_group,
            required_machine_id=operations[0].machine_id,
            required_material_id=material.material_id if material else None,
            tooling_requirement=set(operations[0].tooling_ids),
            estimated_setup_minutes=total_setup,
            estimated_cycle_minutes_per_unit=total_cycle,
            estimated_total_production_minutes=total_setup + total_cycle * quantity,
            customer_priority=customer.customer_priority,
            technical_priority=rng.randint(1, 5),
            commercial_priority=rng.randint(1, 5),
            lateness_penalty_per_day=(
                round(order_value * rng.uniform(0.005, 0.03), 2)
                if customer.customer_tier in (CustomerTier.STRATEGIC, CustomerTier.KEY) and rng.random() < 0.5
                else None
            ),
            sla_hours=customer.sla_hours,
            special_instructions="Certificate of conformance required" if rng.random() < 0.1 else None,
            drawing_approved=drawing_ok,
            on_hold=on_hold,
            hold_reason=rng.choice(_HOLD_REASONS) if on_hold else None,
            surface_finish=rng.choice(catalog.SURFACE_FINISHES),
            technology=technology or ("ASSEMBLY" if kind == "assembly" else "CNC"),
            attributes={"kind": kind},
        )
        return order, operations

    def _build_operations(
        self,
        order_id: str,
        route: tuple[ProcessType, ...],
        primary_group: str | None,
        material: Material | None,
        quantity: float,
        setup_family: str,
    ) -> list[Operation]:
        rng = self._rng
        ops: list[Operation] = []
        previous: Operation | None = None
        for index, process in enumerate(route):
            if index == 0:
                group = primary_group
            elif process == ProcessType.FINISHING:
                # printed parts are hand-finished either in the AM post cell or the deburr bay
                group = self._ctx.group_for(process, rng.choice(("AMPOST", "DEBURR")))
            else:
                group = self._ctx.group_for(process)
            timing = catalog.PROCESS_TIMING[process]
            cycle = round(
                rng.uniform(timing.cycle_min, timing.cycle_max) / (1.0 + math.log10(max(quantity, 1.0))), 2
            )
            setup = round(rng.uniform(timing.setup_min, timing.setup_max), 1)
            machines = self._ctx.machines_by_group.get(group or "", [])
            tooling_ids: set[str] = set()
            if process == ProcessType.CNC_MACHINING and group in self._ctx.tooling_by_group:
                pool = self._ctx.tooling_by_group[group]
                tooling_ids = {t.tooling_id for t in rng.sample(pool, min(len(pool), rng.randint(1, 2)))}
            machine_id = (
                machines[rng.randrange(len(machines))].machine_id if machines and rng.random() < 0.2 else None
            )
            eligible: set[str] = set()
            if index == 0 and len(machines) > 2 and rng.random() < 0.1:
                eligible = {m.machine_id for m in rng.sample(machines, 2)}
            overrides: dict[str, float] = {}
            if index == 0 and machines and rng.random() < 0.15:
                for machine in rng.sample(machines, min(2, len(machines))):
                    overrides[machine.machine_id] = round(cycle * rng.uniform(0.85, 1.25), 2)
            op = Operation(
                operation_id=self._next_op_id(),
                order_id=order_id,
                sequence=(index + 1) * 10,
                operation_type=process,
                machine_group=group,
                machine_id=machine_id,
                eligible_machine_ids=eligible,
                setup_minutes=setup,
                cycle_minutes_per_unit=cycle,
                machine_cycle_minutes=overrides,
                quantity=quantity,
                operation_status=OperationStatus.PENDING,
                prerequisite_operation_id=previous.operation_id if previous else None,
                material_id=material.material_id if (index == 0 and material) else None,
                material_quantity_per_unit=round(rng.uniform(0.05, 2.5), 3)
                if (index == 0 and material)
                else None,
                tooling_ids=tooling_ids,
                operator_requirement="certified" if process == ProcessType.INSPECTION else None,
                quality_requirement="CMM report" if process == ProcessType.INSPECTION else None,
                setup_family=setup_family if index == 0 else None,
            )
            ops.append(op)
            previous = op
        return ops

    def _apply_progress(self, status: OrderStatus, ops: list[Operation], quantity: float) -> None:
        rng = self._rng
        as_of = self._ctx.as_of
        n = len(ops)

        def complete(op: Operation, hours_ago: float) -> None:
            op.operation_status = OperationStatus.COMPLETED
            op.completed_quantity = quantity
            op.actual_end = as_of - timedelta(hours=hours_ago)
            op.actual_start = op.actual_end - timedelta(
                minutes=(op.setup_minutes or 0) + (op.cycle_minutes_per_unit or 0) * quantity
            )

        if status in (OrderStatus.COMPLETED, OrderStatus.PACKED, OrderStatus.SHIPPED):
            for i, op in enumerate(ops):
                complete(op, hours_ago=float((n - i) * 12 + rng.randint(2, 40)))
            return
        if status in (OrderStatus.RELEASED, OrderStatus.PLANNED):
            ops[0].operation_status = OperationStatus.READY
        elif status == OrderStatus.SCHEDULED:
            ops[0].operation_status = OperationStatus.SCHEDULED
            ops[0].estimated_start = as_of + timedelta(hours=rng.randint(2, 72))
        elif status == OrderStatus.ON_HOLD:
            ops[0].operation_status = OperationStatus.ON_HOLD
        elif status in (OrderStatus.IN_PRODUCTION, OrderStatus.PARTIALLY_COMPLETED):
            done = rng.randint(0, max(0, n - 2))
            for i in range(done):
                complete(ops[i], hours_ago=float((done - i) * 10 + rng.randint(1, 20)))
            current = ops[done]
            current.operation_status = OperationStatus.IN_PROGRESS
            current.completed_quantity = float(int(quantity * rng.uniform(0.1, 0.9)))
            current.actual_start = as_of - timedelta(hours=rng.randint(1, 30))
        elif status in (OrderStatus.QUALITY_INSPECTION, OrderStatus.REWORK):
            inspection = next(
                (i for i, op in enumerate(ops) if op.operation_type == ProcessType.INSPECTION), n - 1
            )
            for i in range(inspection):
                complete(ops[i], hours_ago=float((inspection - i) * 10 + rng.randint(1, 20)))
            target = ops[inspection]
            target.actual_start = as_of - timedelta(hours=rng.randint(1, 12))
            if status == OrderStatus.REWORK:
                target.operation_status = OperationStatus.REWORK
                target.completed_quantity = float(int(quantity * rng.uniform(0.3, 0.8)))
            else:
                target.operation_status = OperationStatus.IN_PROGRESS

    def _material_status(
        self, material: Material | None, quantity: float, per_unit: float, status: OrderStatus
    ) -> MaterialStatus:
        if material is None:
            return MaterialStatus.UNKNOWN
        if status == OrderStatus.MATERIAL_WAITING or material.available_quantity <= 0:
            return MaterialStatus.ON_ORDER if material.incoming_quantity > 0 else MaterialStatus.UNAVAILABLE
        if material.free_quantity < quantity * per_unit:
            return MaterialStatus.PARTIAL
        return MaterialStatus.AVAILABLE

    def _quality_status(self, status: OrderStatus) -> QualityStatus:
        if status == OrderStatus.REWORK:
            return QualityStatus.REWORK
        if status == OrderStatus.QUALITY_INSPECTION:
            return QualityStatus.HOLD if self._rng.random() < 0.5 else QualityStatus.PENDING
        if status in (OrderStatus.COMPLETED, OrderStatus.PACKED, OrderStatus.SHIPPED):
            return QualityStatus.PASSED
        return QualityStatus.NONE

    def _build_assemblies(self, orders: list[Order], operations: list[Operation], count: int) -> None:
        """Assembly orders that depend on 1-3 open part orders of the same customer."""
        by_customer: dict[str, list[Order]] = {}
        for order in orders:
            if order.is_open:
                by_customer.setdefault(order.customer_id, []).append(order)
        eligible = [c for c in self._ctx.customers if len(by_customer.get(c.customer_id, ())) >= 2]
        if not eligible:
            return
        for _ in range(count):
            customer = self._rng.choice(eligible)
            pool = by_customer[customer.customer_id]
            parts = self._rng.sample(pool, self._rng.randint(1, min(3, len(pool))))
            order, ops = self._build_line(customer, self._next_order_no(), 1, "assembly")
            order.depends_on_order_ids = {p.order_id for p in parts}
            if order.order_status not in (OrderStatus.NEW, OrderStatus.RELEASED, OrderStatus.PLANNED):
                # An assembly cannot have progressed while its parts are still open.
                order.order_status = OrderStatus.RELEASED
                order.production_status = OrderStatus.RELEASED.value.upper()
                order.completed_quantity = 0.0
                order.quality_status = QualityStatus.NONE
                order.shipping_status = ShippingStatus.NOT_SHIPPED
                order.on_hold = False
                order.hold_reason = None
                for op in ops:
                    op.operation_status = OperationStatus.PENDING
                    op.completed_quantity = 0.0
                    op.actual_start = op.actual_end = None
                ops[0].operation_status = OperationStatus.READY
            latest_due = max((p.due_date for p in parts if p.due_date is not None), default=None)
            if latest_due is not None and order.due_date is not None and order.due_date < latest_due:
                shift = latest_due + timedelta(days=self._rng.randint(2, 7)) - order.due_date
                if order.requested_delivery_date is not None:
                    order.requested_delivery_date += shift
                if order.promised_delivery_date is not None:
                    order.promised_delivery_date += shift
                if order.revised_delivery_date is not None:
                    order.revised_delivery_date += shift
            orders.append(order)
            operations.extend(ops)


__all__ = ["STATUS_MIX", "OrderBuildContext", "OrderFactory"]
