"""Hard constraints: a machine either can or cannot run an operation (§6.2).

Each constraint is a tiny stateless class implementing
:class:`~app.engines.constraints.base.HardConstraint`. ``check`` returns a
:class:`~app.domain.results.Violation` (with the data that produced it, for
explanations) or ``None``. Constraints are conservative about *missing* ERP
data: a rule only fires when both sides of the comparison are known, so
incomplete master data widens eligibility (and is reported by the Data
Quality Engine) instead of silently blocking orders.

Order of evaluation is irrelevant for correctness; the engine evaluates all
constraints so that an explanation lists every reason a machine was rejected.

Order-level requirements (``Order.required_machine_id``, ``Order.machine_group``,
``Order.tooling_requirement``) describe the order's *primary* process
(``Order.process_type``, the first step of its route). They constrain only the
operations of that process type — a deburring or packing step of a CNC order
is never pinned to the CNC machine (see :func:`is_primary_operation`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.clock import ensure_utc
from app.domain.enums import LockType, OperationStatus, OverrideType, ProcessType
from app.domain.models import Machine, Operation, Order
from app.domain.results import Violation
from app.domain.snapshot import PlanningSnapshot
from app.engines.constraints.base import ConstraintContext, HardConstraint

# Keys of ``Order.attributes`` / ``Machine.attributes`` read by these rules.
PART_SIZE_ATTRIBUTE = "part_size_mm"
PART_DIMENSION_ATTRIBUTES = ("part_length_mm", "part_width_mm", "part_height_mm")
MIN_BATCH_ATTRIBUTE = "min_batch_qty"
MAX_BATCH_ATTRIBUTE = "max_batch_qty"


def _order_of(op: Operation, ctx: ConstraintContext) -> Order | None:
    if ctx.order is not None and ctx.order.order_id == op.order_id:
        return ctx.order
    return ctx.snapshot.orders.get(op.order_id)


def is_primary_operation(op: Operation, order: Order | None) -> bool:
    """True when ``op`` performs the order's primary process, i.e. the one the order-level
    machine / group / tooling requirements describe.

    Orders whose process type is unknown (``OTHER``) treat every operation as primary so
    that an ERP requirement is never silently dropped on incomplete master data.
    """
    if order is None:
        return True
    return order.process_type is ProcessType.OTHER or op.operation_type == order.process_type


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def part_dimensions(order: Order) -> tuple[float, float, float] | None:
    """Part dimensions from ``order.attributes`` (either a 3-tuple or three scalar keys)."""
    raw = order.attributes.get(PART_SIZE_ATTRIBUTE)
    if isinstance(raw, list | tuple) and len(raw) == 3:
        dims = [_as_float(v) for v in raw]
    else:
        dims = [_as_float(order.attributes.get(k)) for k in PART_DIMENSION_ATTRIBUTES]
    if any(d is None or d < 0 for d in dims):
        return None
    return (float(dims[0] or 0.0), float(dims[1] or 0.0), float(dims[2] or 0.0))


def pinned_machine_for_order(
    snapshot: PlanningSnapshot, order_id: str, at: datetime
) -> tuple[str, str] | None:
    """Machine pinned to ``order_id`` by an active lock or override: ``(machine_id, source)``.

    Locks win over overrides; among several the most recent decision wins so
    that a planner's latest action is honoured deterministically.
    """
    at = ensure_utc(at)
    best: tuple[datetime, str, str] | None = None
    for lock in snapshot.locks:
        if not lock.active or lock.order_id != order_id or lock.machine_id is None:
            continue
        if lock.lock_type not in (LockType.ORDER, LockType.MACHINE):
            continue
        if lock.window is not None and lock.window.end <= at:
            continue
        candidate = (ensure_utc(lock.created_at), lock.machine_id, f"lock:{lock.lock_id}")
        if best is None or candidate[0] > best[0]:
            best = candidate
    if best is not None:
        return best[1], best[2]
    for override in snapshot.overrides:
        if override.order_id != order_id or override.target_machine_id is None:
            continue
        if override.override_type != OverrideType.LOCK_MACHINE_ASSIGNMENT or not override.is_active_at(at):
            continue
        candidate = (
            ensure_utc(override.created_at),
            override.target_machine_id,
            f"override:{override.override_id}",
        )
        if best is None or candidate[0] > best[0]:
            best = candidate
    return (best[1], best[2]) if best is not None else None


class ProcessCapability:
    """The machine must support the operation's process type."""

    key = "process_capability"

    def check(self, op: Operation, machine: Machine, ctx: ConstraintContext) -> Violation | None:
        if machine.supports_process(op.operation_type):
            return None
        return Violation(
            self.key,
            f"{machine.machine_id} cannot perform {op.operation_type.value} "
            f"(machine process: {machine.process_type.value})",
            {"required": op.operation_type.value, "machine_process": machine.process_type.value},
        )


class ExplicitEligibility:
    """Explicit machine lists from the ERP.

    * ``op.eligible_machine_ids`` (non-empty) restricts to that list;
    * ``order.required_machine_id`` restricts the order's primary operation(s) to that machine;
    * ``op.machine_id`` is binding only while the operation is *in progress*
      (work physically on that machine); otherwise it is the ERP-preferred
      machine and handled as a soft preference (:class:`~soft.PreferredMachine`).
    """

    key = "explicit_eligibility"

    def check(self, op: Operation, machine: Machine, ctx: ConstraintContext) -> Violation | None:
        if op.eligible_machine_ids and machine.machine_id not in op.eligible_machine_ids:
            return Violation(
                self.key,
                f"{machine.machine_id} is not in the operation's eligible machine list",
                {"eligible_machine_ids": sorted(op.eligible_machine_ids)},
            )
        order = _order_of(op, ctx)
        if (
            order is not None
            and order.required_machine_id is not None
            and order.required_machine_id != machine.machine_id
            and is_primary_operation(op, order)
        ):
            return Violation(
                self.key,
                f"order requires machine {order.required_machine_id}",
                {"required_machine_id": order.required_machine_id},
            )
        if (
            op.machine_id is not None
            and op.machine_id != machine.machine_id
            and op.operation_status == OperationStatus.IN_PROGRESS
        ):
            return Violation(
                self.key,
                f"operation is in progress on {op.machine_id} and cannot move",
                {"assigned_machine_id": op.machine_id, "operation_status": op.operation_status.value},
            )
        return None


class MachineGroup:
    """Machine must belong to the required group unless an explicit list exists."""

    key = "machine_group"

    def check(self, op: Operation, machine: Machine, ctx: ConstraintContext) -> Violation | None:
        if op.eligible_machine_ids:
            return None
        order = _order_of(op, ctx)
        group = op.machine_group or (
            order.machine_group if order is not None and is_primary_operation(op, order) else None
        )
        if group is None or machine.machine_group == group:
            return None
        return Violation(
            self.key,
            f"{machine.machine_id} is in group {machine.machine_group}, required {group}",
            {"required_group": group, "machine_group": machine.machine_group},
        )


class MaterialCompatibility:
    """Machine ↔ material compatibility, checked from both sides when known."""

    key = "material_compatibility"

    def check(self, op: Operation, machine: Machine, ctx: ConstraintContext) -> Violation | None:
        order = _order_of(op, ctx)
        material_id = op.material_id or (order.required_material_id if order is not None else None)
        if material_id is None:
            return None
        if machine.compatible_materials and material_id not in machine.compatible_materials:
            return Violation(
                self.key,
                f"{machine.machine_id} is not compatible with material {material_id}",
                {"material_id": material_id, "side": "machine.compatible_materials"},
            )
        material = ctx.snapshot.materials.get(material_id)
        if (
            material is not None
            and material.compatible_machine_ids
            and machine.machine_id not in material.compatible_machine_ids
        ):
            return Violation(
                self.key,
                f"material {material_id} is not released for {machine.machine_id}",
                {"material_id": material_id, "side": "material.compatible_machine_ids"},
            )
        return None


def required_tooling_ids(op: Operation, order: Order | None) -> set[str]:
    """Tooling required by the operation.

    Falls back to the order-level ``tooling_requirement`` only for the order's primary
    operation(s): an end mill required by the CNC step is not needed at deburring.
    """
    if op.tooling_ids:
        return set(op.tooling_ids)
    if order is not None and is_primary_operation(op, order):
        return set(order.tooling_requirement)
    return set()


class ToolingCompatibility:
    """Every required tool with a known compatibility list must fit the machine."""

    key = "tooling_compatibility"

    def check(self, op: Operation, machine: Machine, ctx: ConstraintContext) -> Violation | None:
        tooling_ids = required_tooling_ids(op, _order_of(op, ctx))
        incompatible: list[str] = []
        for tooling_id in sorted(tooling_ids):
            tool = ctx.snapshot.tooling.get(tooling_id)
            if tool is None or not tool.compatible_machine_ids:
                continue  # unknown tool or no restriction: cannot judge, do not block
            if machine.machine_id not in tool.compatible_machine_ids:
                incompatible.append(tooling_id)
        if not incompatible:
            return None
        return Violation(
            self.key,
            f"tooling {', '.join(incompatible)} cannot be used on {machine.machine_id}",
            {"incompatible_tooling_ids": incompatible},
        )


def maintenance_end_after(machine: Machine, at: datetime) -> datetime | None:
    """Earliest end of any downtime window still running or ahead of ``at``."""
    ends = [ensure_utc(w.end) for w in machine.all_downtime if ensure_utc(w.end) > at]
    return min(ends) if ends else None


class MachineOperable:
    """Machine status must allow work.

    A DOWN / OFFLINE / MAINTENANCE machine is eligible only when one of its
    downtime windows tells when it comes back (the calendar removes the window
    and the scheduler starts it at the return; readiness reports the wait).
    With no scheduled return the machine is rejected.
    """

    key = "machine_operable"

    def check(self, op: Operation, machine: Machine, ctx: ConstraintContext) -> Violation | None:
        if machine.status.is_operable:
            return None
        if maintenance_end_after(machine, ensure_utc(ctx.at)) is not None:
            return None
        details: dict[str, Any] = {"status": machine.status.value}
        return Violation(
            self.key, f"{machine.machine_id} is {machine.status.value} with no scheduled return", details
        )


class PartSize:
    """Part must fit the machine envelope (orientation-agnostic: sorted dimensions)."""

    key = "part_size"

    def check(self, op: Operation, machine: Machine, ctx: ConstraintContext) -> Violation | None:
        if machine.max_part_size_mm is None:
            return None
        order = _order_of(op, ctx)
        if order is None:
            return None
        dims = part_dimensions(order)
        if dims is None:
            return None
        part = sorted(dims, reverse=True)
        envelope = sorted(machine.max_part_size_mm, reverse=True)
        if all(p <= e for p, e in zip(part, envelope, strict=True)):
            return None
        return Violation(
            self.key,
            f"part {dims} mm exceeds {machine.machine_id} envelope {machine.max_part_size_mm} mm",
            {"part_size_mm": list(dims), "max_part_size_mm": list(machine.max_part_size_mm)},
        )


class QuantityRestriction:
    """``machine.attributes[min_batch_qty|max_batch_qty]`` bound the pending quantity."""

    key = "quantity_restriction"

    def check(self, op: Operation, machine: Machine, ctx: ConstraintContext) -> Violation | None:
        qty = op.pending_quantity
        minimum = _as_float(machine.attributes.get(MIN_BATCH_ATTRIBUTE))
        maximum = _as_float(machine.attributes.get(MAX_BATCH_ATTRIBUTE))
        if minimum is not None and qty > 0 and qty < minimum:
            return Violation(
                self.key,
                f"quantity {qty:g} below {machine.machine_id} minimum batch {minimum:g}",
                {"quantity": qty, "min_batch_qty": minimum},
            )
        if maximum is not None and qty > maximum:
            return Violation(
                self.key,
                f"quantity {qty:g} above {machine.machine_id} maximum batch {maximum:g}",
                {"quantity": qty, "max_batch_qty": maximum},
            )
        return None


class LockedMachineAssignment:
    """An active order/machine lock or LOCK_MACHINE_ASSIGNMENT override pins the machine.

    The pin applies to the operations the pinned machine can perform; the other
    steps of a routed order (e.g. deburring after a locked CNC step) stay free.
    """

    key = "locked_machine_assignment"

    def check(self, op: Operation, machine: Machine, ctx: ConstraintContext) -> Violation | None:
        pinned = pinned_machine_for_order(ctx.snapshot, op.order_id, ctx.at)
        if pinned is None or pinned[0] == machine.machine_id:
            return None
        target = ctx.snapshot.machines.get(pinned[0])
        if target is not None and not target.supports_process(op.operation_type):
            return None  # the lock concerns another step of the route
        return Violation(
            self.key,
            f"order {op.order_id} is locked to {pinned[0]} ({pinned[1]})",
            {"pinned_machine_id": pinned[0], "source": pinned[1]},
        )


def default_hard_constraints() -> list[HardConstraint]:
    """The shipped hard-constraint set, in explanation order."""
    return [
        ProcessCapability(),
        ExplicitEligibility(),
        MachineGroup(),
        MaterialCompatibility(),
        ToolingCompatibility(),
        MachineOperable(),
        PartSize(),
        QuantityRestriction(),
        LockedMachineAssignment(),
    ]


__all__ = [
    "ExplicitEligibility",
    "LockedMachineAssignment",
    "MachineGroup",
    "MachineOperable",
    "MaterialCompatibility",
    "PartSize",
    "ProcessCapability",
    "QuantityRestriction",
    "ToolingCompatibility",
    "default_hard_constraints",
    "is_primary_operation",
    "maintenance_end_after",
    "part_dimensions",
    "pinned_machine_for_order",
    "required_tooling_ids",
]
