"""Production readiness: why an order can or cannot run right now (spec Phase 3/4).

:func:`assess_order` evaluates one order against the snapshot at instant
``at`` and returns every :class:`~app.domain.results.Blocker` it finds plus
non-blocking *notes* (unknown material, unknown tooling reference, ...) that
explain why a check could not be made. :func:`readiness_state` collapses the
blockers into a single :class:`~app.domain.enums.ReadinessState` using the
fixed precedence ``READINESS_PRECEDENCE`` (a held order is "on hold" even if
it also lacks material).

Checks, in order:

1. closed / cancelled / nothing pending  → OTHER_CONSTRAINT (short-circuits)
2. hold flag, ON_HOLD status, active HOLD_ORDER override → ON_HOLD
3. quality HOLD/FAILED                    → QUALITY_HOLD
4. drawing not approved                   → WAITING_APPROVAL
5. material: ERP status, else free quantity vs pending x per-unit → WAITING_MATERIAL
6. tooling not usable / not yet available → WAITING_TOOLING
7. dependency orders / prerequisite op    → WAITING_PREVIOUS_OPERATION
8. no eligible machine or none available now → MACHINE_UNAVAILABLE

Missing ERP data never blocks (it is flagged in notes and by the Data
Quality Engine); only positive evidence does. ``resolves_at`` is filled
whenever the ERP gives a date (material receipt, tooling return, maintenance
end, estimated end of the prerequisite) so the scheduler can place the order
after its blocker clears.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.core.clock import ensure_utc
from app.domain.config import SchedulingConfig
from app.domain.enums import MaterialStatus, OrderStatus, OverrideType, QualityStatus, ReadinessState
from app.domain.models import Machine, Operation, Order, PriorityOverride
from app.domain.results import Blocker, EligibilityResult
from app.domain.snapshot import PlanningSnapshot
from app.engines.constraints.base import HardConstraint
from app.engines.constraints.eligibility import CandidateIndex, candidate_machines, evaluate_eligibility
from app.engines.constraints.hard import default_hard_constraints, required_tooling_ids

#: Highest-precedence state first; READY when no blocker exists.
READINESS_PRECEDENCE: tuple[ReadinessState, ...] = (
    ReadinessState.ON_HOLD,
    ReadinessState.QUALITY_HOLD,
    ReadinessState.WAITING_APPROVAL,
    ReadinessState.WAITING_MATERIAL,
    ReadinessState.WAITING_TOOLING,
    ReadinessState.MACHINE_UNAVAILABLE,
    ReadinessState.WAITING_PREVIOUS_OPERATION,
    ReadinessState.OTHER_CONSTRAINT,
    ReadinessState.READY,
)
_PRECEDENCE_RANK: dict[ReadinessState, int] = {s: i for i, s in enumerate(READINESS_PRECEDENCE)}

SYNTHETIC_OPERATION_SUFFIX = "__order_level"


def readiness_state(blockers: Sequence[Blocker]) -> ReadinessState:
    """Single state for a blocker list, by fixed precedence (``READY`` when empty)."""
    if not blockers:
        return ReadinessState.READY
    return min((b.state for b in blockers), key=lambda s: _PRECEDENCE_RANK.get(s, len(_PRECEDENCE_RANK)))


@dataclass(slots=True)
class ReadinessAssessment:
    order_id: str
    state: ReadinessState
    blockers: list[Blocker]
    notes: dict[str, Any] = field(default_factory=dict)
    next_operation_id: str | None = None
    eligible_machine_ids: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return self.state is not ReadinessState.READY

    @property
    def blocking_reasons(self) -> list[str]:
        return [b.message for b in self.blockers]


@dataclass(slots=True)
class ReadinessIndex:
    """Snapshot-wide look-ups built once so bulk readiness is O(orders + operations)."""

    snapshot: PlanningSnapshot
    at: datetime
    hard: Sequence[HardConstraint]
    candidates: CandidateIndex
    overrides_by_order: dict[str, list[PriorityOverride]]

    @classmethod
    def build(
        cls, snapshot: PlanningSnapshot, at: datetime, hard: Sequence[HardConstraint] | None = None
    ) -> ReadinessIndex:
        at = ensure_utc(at)
        overrides: dict[str, list[PriorityOverride]] = defaultdict(list)
        for override in snapshot.overrides:
            if override.is_active_at(at):
                overrides[override.order_id].append(override)
        return cls(
            snapshot=snapshot,
            at=at,
            hard=list(hard) if hard is not None else default_hard_constraints(),
            candidates=CandidateIndex(snapshot),
            overrides_by_order=dict(overrides),
        )


# --------------------------------------------------------------------- helpers


def synthesize_operation(order: Order) -> Operation:
    """Order-level stand-in when the ERP supplied no routing for the order."""
    return Operation(
        operation_id=f"{order.order_id}{SYNTHETIC_OPERATION_SUFFIX}",
        order_id=order.order_id,
        sequence=1,
        operation_type=order.process_type,
        machine_group=order.machine_group,
        machine_id=order.required_machine_id,
        setup_minutes=order.estimated_setup_minutes,
        cycle_minutes_per_unit=order.estimated_cycle_minutes_per_unit,
        quantity=order.quantity,
        completed_quantity=order.completed_quantity + order.cancelled_quantity,
        material_id=order.required_material_id,
        tooling_ids=set(order.tooling_requirement),
    )


def next_operation(order: Order, snapshot: PlanningSnapshot) -> tuple[Operation, bool]:
    """Next pending operation, or a synthesized one; the flag says which."""
    op = snapshot.next_operation_for_order(order.order_id)
    if op is not None:
        return op, False
    return synthesize_operation(order), True


def machine_available_at(machine: Machine, at: datetime) -> datetime:
    """Earliest instant ``>= at`` the machine is neither in downtime nor before ``available_from``."""
    t = ensure_utc(at)
    if machine.available_from is not None:
        t = max(t, ensure_utc(machine.available_from))
    windows = sorted(
        ((ensure_utc(w.start), ensure_utc(w.end)) for w in machine.all_downtime), key=lambda w: w[0]
    )
    for start, end in windows:
        if start <= t < end:
            t = end
    return t


def _hold_override(order: Order, index: ReadinessIndex) -> PriorityOverride | None:
    """Latest HOLD_ORDER override unless a later RELEASE_HOLD supersedes it."""
    latest: PriorityOverride | None = None
    for override in index.overrides_by_order.get(order.order_id, ()):
        if override.override_type not in (OverrideType.HOLD_ORDER, OverrideType.RELEASE_HOLD):
            continue
        if latest is None or (override.created_at, override.override_id) > (
            latest.created_at,
            latest.override_id,
        ):
            latest = override
    return latest if latest is not None and latest.override_type == OverrideType.HOLD_ORDER else None


def _closed_blocker(order: Order) -> Blocker | None:
    if not order.order_status.is_open:
        return Blocker(
            ReadinessState.OTHER_CONSTRAINT,
            f"order is {order.order_status.value}; nothing to schedule",
            details={"order_status": order.order_status.value},
        )
    if order.pending_quantity <= 0:
        return Blocker(
            ReadinessState.OTHER_CONSTRAINT,
            "no pending quantity (completed or cancelled)",
            details={
                "quantity": order.quantity,
                "completed": order.completed_quantity,
                "cancelled": order.cancelled_quantity,
            },
        )
    return None


def _hold_blocker(order: Order, index: ReadinessIndex) -> Blocker | None:
    if order.on_hold or order.order_status == OrderStatus.ON_HOLD:
        reason = order.hold_reason or "no reason recorded"
        return Blocker(
            ReadinessState.ON_HOLD, f"order on hold: {reason}", details={"hold_reason": order.hold_reason}
        )
    override = _hold_override(order, index)
    if override is not None:
        return Blocker(
            ReadinessState.ON_HOLD,
            f"held by {override.created_by}: {override.reason}",
            resolves_at=override.expires_at,
            details={"override_id": override.override_id},
        )
    return None


def _quality_blocker(order: Order) -> Blocker | None:
    if order.quality_status in (QualityStatus.HOLD, QualityStatus.FAILED):
        return Blocker(
            ReadinessState.QUALITY_HOLD,
            f"quality status {order.quality_status.value}",
            details={"quality_status": order.quality_status.value},
        )
    return None


def _approval_blocker(order: Order) -> Blocker | None:
    if order.drawing_approved:
        return None
    return Blocker(
        ReadinessState.WAITING_APPROVAL, "drawing approval pending", details={"drawing_approved": False}
    )


def _material_blocker(
    order: Order, op: Operation, snapshot: PlanningSnapshot, notes: dict[str, Any]
) -> Blocker | None:
    material_id = op.material_id or order.required_material_id
    material = snapshot.materials.get(material_id) if material_id is not None else None
    status = order.material_status
    if status in (MaterialStatus.UNAVAILABLE, MaterialStatus.ON_ORDER):
        return Blocker(
            ReadinessState.WAITING_MATERIAL,
            f"material {material_id or '(unspecified)'} is {status.value}",
            resolves_at=material.expected_receipt_date if material is not None else None,
            details={"material_id": material_id, "material_status": status.value},
        )
    if status == MaterialStatus.AVAILABLE:
        return None  # ERP confirmed availability (reservations may already be this order's)
    if material_id is None:
        notes["material_unknown"] = True
        return None
    if material is None:
        notes["material_not_in_snapshot"] = material_id
        return None
    per_unit = op.material_quantity_per_unit
    if per_unit is None:
        notes["material_quantity_per_unit_unknown"] = material_id
        return None
    required = op.pending_quantity * per_unit
    free = material.free_quantity
    if required <= free:
        return None
    covered_by_incoming = material.incoming_quantity > 0 and free + material.incoming_quantity >= required
    resolves_at = material.expected_receipt_date if covered_by_incoming else None
    message = f"material {material_id}: need {required:g} {material.unit}, {free:g} free"
    if material.incoming_quantity > 0:
        message += f", {material.incoming_quantity:g} incoming"
    return Blocker(
        ReadinessState.WAITING_MATERIAL,
        message,
        resolves_at=resolves_at,
        details={
            "material_id": material_id,
            "required_quantity": required,
            "free_quantity": free,
            "incoming_quantity": material.incoming_quantity,
            "material_status": status.value,
        },
    )


def _tooling_blockers(
    order: Order, op: Operation, snapshot: PlanningSnapshot, at: datetime, notes: dict[str, Any]
) -> list[Blocker]:
    blockers: list[Blocker] = []
    unknown: list[str] = []
    for tooling_id in sorted(required_tooling_ids(op, order)):
        tool = snapshot.tooling.get(tooling_id)
        if tool is None:
            unknown.append(tooling_id)
            continue
        available_from = ensure_utc(tool.available_from) if tool.available_from is not None else None
        if not tool.is_usable:
            why = "unavailable" if not tool.available else "life exhausted"
            if tool.maintenance_status and tool.maintenance_status != "ok":
                why += f" ({tool.maintenance_status})"
            blockers.append(
                Blocker(
                    ReadinessState.WAITING_TOOLING,
                    f"tooling {tooling_id} {why}",
                    resolves_at=available_from
                    if available_from is not None and available_from > at
                    else None,
                    details={
                        "tooling_id": tooling_id,
                        "available": tool.available,
                        "maintenance_status": tool.maintenance_status,
                    },
                )
            )
        elif available_from is not None and available_from > at:
            blockers.append(
                Blocker(
                    ReadinessState.WAITING_TOOLING,
                    f"tooling {tooling_id} available from {available_from.isoformat()}",
                    resolves_at=available_from,
                    details={"tooling_id": tooling_id},
                )
            )
    if unknown:
        notes["unknown_tooling_ids"] = unknown
    return blockers


def _dependency_blockers(
    order: Order, op: Operation, snapshot: PlanningSnapshot, notes: dict[str, Any]
) -> list[Blocker]:
    blockers: list[Blocker] = []
    unknown_orders: list[str] = []
    for dep_id in sorted(order.depends_on_order_ids):
        dep = snapshot.orders.get(dep_id)
        if dep is None:
            unknown_orders.append(dep_id)
            continue
        if dep.order_status == OrderStatus.CANCELLED:
            blockers.append(
                Blocker(
                    ReadinessState.OTHER_CONSTRAINT,
                    f"dependency order {dep_id} is cancelled",
                    details={"order_id": dep_id},
                )
            )
        elif dep.is_open:
            pending = snapshot.pending_operations_for_order(dep_id)
            ends = [o.estimated_end for o in pending]
            resolves_at = (
                max(e for e in ends if e is not None)
                if pending and all(e is not None for e in ends)
                else None
            )
            blockers.append(
                Blocker(
                    ReadinessState.WAITING_PREVIOUS_OPERATION,
                    f"waiting for order {dep_id} "
                    f"({dep.order_status.value}, {len(pending)} operation(s) pending)",
                    resolves_at=resolves_at,
                    details={"order_id": dep_id, "pending_operations": len(pending)},
                )
            )
    if unknown_orders:
        notes["unknown_dependency_order_ids"] = unknown_orders
    if op.prerequisite_operation_id is not None:
        pre = snapshot.operations.get(op.prerequisite_operation_id)
        if pre is None:
            notes["unknown_prerequisite_operation_id"] = op.prerequisite_operation_id
        elif not pre.is_done:
            blockers.append(
                Blocker(
                    ReadinessState.WAITING_PREVIOUS_OPERATION,
                    f"prerequisite operation {pre.operation_id} (order {pre.order_id}) "
                    f"is {pre.operation_status.value}",
                    resolves_at=pre.estimated_end,
                    details={"operation_id": pre.operation_id, "order_id": pre.order_id},
                )
            )
    return blockers


def _machine_blocker(
    order: Order, op: Operation, index: ReadinessIndex, config: SchedulingConfig, notes: dict[str, Any]
) -> tuple[Blocker | None, EligibilityResult]:
    snapshot, at = index.snapshot, index.at
    candidates = candidate_machines(op, order, snapshot, index.candidates)
    result = evaluate_eligibility(
        op, snapshot, at, config, index.hard, order=order, index=index.candidates, candidates=candidates
    )
    if not result.eligible_machine_ids:
        reasons = Counter(v.constraint_key for vs in result.rejected.values() for v in vs)
        returns: list[datetime] = []
        for violations in result.rejected.values():
            if all(v.constraint_key == "machine_operable" for v in violations):
                resolves = violations[0].details.get("resolves_at")
                if isinstance(resolves, datetime):
                    returns.append(resolves)
        summary = ", ".join(f"{k} ({n})" for k, n in sorted(reasons.items()))
        message = (
            f"no eligible machine: {candidates.detail}"
            if not candidates.machines
            else f"no eligible machine among {len(candidates.machines)} candidate(s): {summary}"
        )
        blocker = Blocker(
            ReadinessState.MACHINE_UNAVAILABLE,
            message,
            resolves_at=min(returns) if returns else None,
            details={
                "candidate_basis": candidates.basis,
                "candidates": len(candidates.machines),
                "rejections": dict(reasons),
            },
        )
        return blocker, result
    availability = {
        machine_id: machine_available_at(snapshot.machines[machine_id], at)
        for machine_id in result.eligible_machine_ids
    }
    if all(t > at for t in availability.values()):
        earliest_id, earliest = min(availability.items(), key=lambda kv: (kv[1], kv[0]))
        return (
            Blocker(
                ReadinessState.MACHINE_UNAVAILABLE,
                f"all {len(availability)} eligible machine(s) down/unavailable; "
                f"earliest {earliest_id} at {earliest.isoformat()}",
                resolves_at=earliest,
                details={"eligible_machine_ids": list(availability), "earliest_machine_id": earliest_id},
            ),
            result,
        )
    notes["machines_available_now"] = sorted(m for m, t in availability.items() if t <= at)
    return None, result


# ------------------------------------------------------------------ assessment


def assess_order(
    order: Order,
    snapshot: PlanningSnapshot,
    at: datetime,
    config: SchedulingConfig,
    *,
    hard: Sequence[HardConstraint] | None = None,
    index: ReadinessIndex | None = None,
) -> ReadinessAssessment:
    """Full readiness picture of one order (blockers, notes, eligible machines)."""
    if index is None:
        index = ReadinessIndex.build(snapshot, at, hard)
    at = index.at
    notes: dict[str, Any] = {}
    closed = _closed_blocker(order)
    if closed is not None:
        return ReadinessAssessment(order.order_id, ReadinessState.OTHER_CONSTRAINT, [closed], notes)
    blockers: list[Blocker] = []
    for blocker in (_hold_blocker(order, index), _quality_blocker(order), _approval_blocker(order)):
        if blocker is not None:
            blockers.append(blocker)
    op, synthetic = next_operation(order, snapshot)
    if synthetic:
        notes["no_operations"] = True
    material = _material_blocker(order, op, snapshot, notes)
    if material is not None:
        blockers.append(material)
    blockers.extend(_tooling_blockers(order, op, snapshot, at, notes))
    blockers.extend(_dependency_blockers(order, op, snapshot, notes))
    machine_blocker, eligibility = _machine_blocker(order, op, index, config, notes)
    if machine_blocker is not None:
        blockers.append(machine_blocker)
    return ReadinessAssessment(
        order_id=order.order_id,
        state=readiness_state(blockers),
        blockers=blockers,
        notes=notes,
        next_operation_id=None if synthetic else op.operation_id,
        eligible_machine_ids=list(eligibility.eligible_machine_ids),
    )


def compute_blockers(
    order: Order,
    snapshot: PlanningSnapshot,
    at: datetime,
    config: SchedulingConfig,
    *,
    hard: Sequence[HardConstraint] | None = None,
    index: ReadinessIndex | None = None,
) -> list[Blocker]:
    """Blockers preventing ``order`` from running at ``at`` (empty = READY)."""
    return assess_order(order, snapshot, at, config, hard=hard, index=index).blockers


__all__ = [
    "READINESS_PRECEDENCE",
    "ReadinessAssessment",
    "ReadinessIndex",
    "assess_order",
    "compute_blockers",
    "machine_available_at",
    "next_operation",
    "readiness_state",
    "synthesize_operation",
]
