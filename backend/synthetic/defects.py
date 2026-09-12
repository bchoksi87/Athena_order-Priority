"""Data-quality defect injection for the synthetic generator.

The Data Quality Engine (spec Phase 21) must have something to find, so a
configurable share of orders is corrupted in ERP-typical ways. Every injected
defect is tagged in ``order.attributes["injected_defect"]`` so tests and
benchmarks can verify detection recall.
"""

from __future__ import annotations

import copy
import random
from collections import Counter
from dataclasses import dataclass, field

from app.domain.models import Operation, Order

DEFECT_KINDS: tuple[str, ...] = (
    "missing_due_date",
    "missing_cycle_time",
    "missing_machine_group",
    "negative_quantity",
    "impossible_cycle_time",
    "duplicate_order_ref",
    "unknown_material_ref",
)

_IMPOSSIBLE_CYCLE_MINUTES = 5_000.0  # > 3 days per unit


@dataclass(slots=True)
class DefectInjectionResult:
    """What was injected, for the generation statistics."""

    counts: dict[str, int] = field(default_factory=dict)
    affected_order_ids: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def inject_defects(
    rng: random.Random,
    orders: list[Order],
    operations: list[Operation],
    ratio: float,
) -> DefectInjectionResult:
    """Corrupt ``ratio`` of the open orders in place (duplicates are appended).

    Defects are spread evenly across :data:`DEFECT_KINDS`; the same order never
    receives two defects so that each detection can be attributed.
    """
    result = DefectInjectionResult(counts=dict.fromkeys(DEFECT_KINDS, 0))
    if ratio <= 0 or not orders:
        return result
    ops_by_order: dict[str, list[Operation]] = {}
    for op in operations:
        ops_by_order.setdefault(op.order_id, []).append(op)
    open_orders = [o for o in orders if o.is_open]
    target = min(len(open_orders), max(1, round(len(orders) * ratio)))
    victims = rng.sample(open_orders, target)
    counter: Counter[str] = Counter()
    for index, order in enumerate(victims):
        kind = DEFECT_KINDS[index % len(DEFECT_KINDS)]
        ops = ops_by_order.get(order.order_id, [])
        if kind == "duplicate_order_ref":
            duplicate, dup_ops = _duplicate(order, ops)
            orders.append(duplicate)
            operations.extend(dup_ops)
        else:
            _apply(kind, order, ops, rng)
        order.attributes["injected_defect"] = kind
        counter[kind] += 1
        result.affected_order_ids.append(order.order_id)
    result.counts.update(counter)
    return result


def _apply(kind: str, order: Order, ops: list[Operation], rng: random.Random) -> None:
    if kind == "missing_due_date":
        order.requested_delivery_date = None
        order.promised_delivery_date = None
        order.revised_delivery_date = None
    elif kind == "missing_cycle_time":
        victim = rng.choice(ops) if ops else None
        if victim is not None:
            victim.cycle_minutes_per_unit = None
            victim.machine_cycle_minutes = {}
        order.estimated_cycle_minutes_per_unit = None
        order.estimated_total_production_minutes = None
    elif kind == "missing_machine_group":
        if ops:
            ops[0].machine_group = None
            ops[0].machine_id = None
            ops[0].eligible_machine_ids = set()
        order.machine_group = None
        order.required_machine_id = None
    elif kind == "negative_quantity":
        order.quantity = -abs(order.quantity or 1.0)
        order.completed_quantity = 0.0
        for op in ops:
            op.quantity = order.quantity
            op.completed_quantity = 0.0
    elif kind == "impossible_cycle_time":
        if ops:
            ops[0].cycle_minutes_per_unit = _IMPOSSIBLE_CYCLE_MINUTES
            ops[0].machine_cycle_minutes = {}
            order.estimated_cycle_minutes_per_unit = _IMPOSSIBLE_CYCLE_MINUTES
            order.estimated_total_production_minutes = _IMPOSSIBLE_CYCLE_MINUTES * max(order.quantity, 1.0)
    elif kind == "unknown_material_ref":
        unknown = f"MAT-UNKNOWN-{rng.randint(100, 999)}"
        order.required_material_id = unknown
        for op in ops:
            if op.material_id is not None:
                op.material_id = unknown


def _duplicate(order: Order, ops: list[Operation]) -> tuple[Order, list[Operation]]:
    """A second ERP row with the same order/line reference (classic double entry)."""
    duplicate = copy.deepcopy(order)
    duplicate.order_id = f"{order.order_id}-DUP"
    duplicate.attributes["injected_defect"] = "duplicate_order_ref"
    duplicate.attributes["duplicate_of"] = order.order_id
    dup_ops: list[Operation] = []
    for op in ops:
        clone = copy.deepcopy(op)
        clone.operation_id = f"{op.operation_id}-DUP"
        clone.order_id = duplicate.order_id
        if clone.prerequisite_operation_id is not None:
            clone.prerequisite_operation_id = f"{clone.prerequisite_operation_id}-DUP"
        dup_ops.append(clone)
    return duplicate, dup_ops


__all__ = ["DEFECT_KINDS", "DefectInjectionResult", "inject_defects"]
