"""Machine candidate narrowing and hard-constraint evaluation.

Shared by :class:`~app.engines.constraints.engine.ConstraintEngine` and the
readiness rules so that "which machines can run this operation" is computed
by exactly one code path.

Candidate narrowing keeps the work per operation proportional to the
*plausible* machines rather than the whole plant: an explicit ERP list, a
required machine, a machine group or (by default) the machines that support
the operation's process type. Order-level fields (required machine, machine
group) only narrow the order's *primary* operation(s) — see
:func:`~app.engines.constraints.hard.is_primary_operation`. Every candidate is
then checked against every hard constraint so a rejected machine carries
*all* of its reasons.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from app.domain.config import SchedulingConfig
from app.domain.enums import ProcessType
from app.domain.models import Machine, Operation, Order
from app.domain.results import EligibilityResult, Violation
from app.domain.snapshot import PlanningSnapshot
from app.engines.constraints.base import ConstraintContext, HardConstraint
from app.engines.constraints.hard import is_primary_operation


def machine_sort_key(machine: Machine) -> tuple[int, str]:
    """Deterministic ranking used everywhere: preferred rank, then id."""
    return (machine.preferred_rank, machine.machine_id)


@dataclass(slots=True)
class CandidateIndex:
    """Per-snapshot cache of machines by process type (sorted deterministically)."""

    snapshot: PlanningSnapshot
    _by_process: dict[ProcessType, list[Machine]] = field(default_factory=dict, repr=False)

    def for_process(self, process: ProcessType) -> list[Machine]:
        cached = self._by_process.get(process)
        if cached is None:
            cached = self.snapshot.machines_for_process(process)
            self._by_process[process] = cached
        return list(cached)


@dataclass(slots=True)
class CandidateSet:
    machines: list[Machine]
    basis: str  # "explicit_list" | "required_machine" | "machine_group" | "process"
    detail: str


def candidate_machines(
    op: Operation,
    order: Order | None,
    snapshot: PlanningSnapshot,
    index: CandidateIndex | None = None,
) -> CandidateSet:
    """Plausible machines for ``op`` before hard constraints are applied."""
    if op.eligible_machine_ids:
        machines = [snapshot.machines[m] for m in sorted(op.eligible_machine_ids) if m in snapshot.machines]
        missing = sorted(op.eligible_machine_ids - set(snapshot.machines))
        detail = f"explicit list of {len(op.eligible_machine_ids)} machine(s)"
        if missing:
            detail += f", unknown: {', '.join(missing)}"
        return CandidateSet(sorted(machines, key=machine_sort_key), "explicit_list", detail)
    primary = order is not None and is_primary_operation(op, order)
    if order is not None and primary and order.required_machine_id is not None:
        machine = snapshot.machines.get(order.required_machine_id)
        detail = f"order requires machine {order.required_machine_id}"
        if machine is None:
            detail += " (unknown machine)"
        return CandidateSet([machine] if machine else [], "required_machine", detail)
    group = op.machine_group or (order.machine_group if order is not None and primary else None)
    if group is not None:
        machines = snapshot.machines_in_group(group)
        detail = f"machine group {group!r}" + ("" if machines else " (no machines in group)")
        return CandidateSet(machines, "machine_group", detail)
    machines = (
        index.for_process(op.operation_type) if index else snapshot.machines_for_process(op.operation_type)
    )
    detail = f"machines supporting {op.operation_type.value}" + ("" if machines else " (none)")
    return CandidateSet(machines, "process", detail)


def check_hard(
    op: Operation, machine: Machine, ctx: ConstraintContext, hard: Sequence[HardConstraint]
) -> list[Violation]:
    """All violations of ``machine`` for ``op`` (empty when eligible)."""
    violations: list[Violation] = []
    for constraint in hard:
        violation = constraint.check(op, machine, ctx)
        if violation is not None:
            violations.append(violation)
    return violations


def evaluate_eligibility(
    op: Operation,
    snapshot: PlanningSnapshot,
    at: datetime,
    config: SchedulingConfig,
    hard: Sequence[HardConstraint],
    *,
    order: Order | None = None,
    index: CandidateIndex | None = None,
    candidates: CandidateSet | None = None,
) -> EligibilityResult:
    """Apply every hard constraint to each candidate machine.

    ``eligible_machine_ids`` is ordered by ``(preferred_rank, machine_id)``;
    ``rejected`` is keyed by machine id in the same order.
    """
    if order is None:
        order = snapshot.orders.get(op.order_id)
    if candidates is None:
        candidates = candidate_machines(op, order, snapshot, index)
    ctx = ConstraintContext(snapshot=snapshot, at=at, config=config, order=order)
    eligible: list[str] = []
    rejected: dict[str, list[Violation]] = {}
    for machine in candidates.machines:
        violations = check_hard(op, machine, ctx, hard)
        if violations:
            rejected[machine.machine_id] = violations
        else:
            eligible.append(machine.machine_id)
    return EligibilityResult(operation_id=op.operation_id, eligible_machine_ids=eligible, rejected=rejected)


__all__ = [
    "CandidateIndex",
    "CandidateSet",
    "candidate_machines",
    "check_hard",
    "evaluate_eligibility",
    "machine_sort_key",
]
