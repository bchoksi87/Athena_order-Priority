"""ConstraintEngine: façade over hard constraints, soft constraints and readiness (§6.2).

The engine owns no snapshot state; every call receives the snapshot and the
instant ``at`` so the same engine instance serves live planning, what-if
clones and tests deterministically. Bulk helpers (``readiness_map``,
``assessment_map``) build a :class:`ReadinessIndex` once and reuse it for
every order, keeping the pass linear in orders + operations (times the
number of plausible machines per operation).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

import structlog

from app.core.clock import ensure_utc
from app.domain.config import SchedulingConfig
from app.domain.enums import ReadinessState
from app.domain.models import Machine, Operation, Order
from app.domain.results import Blocker, EligibilityResult, Penalty, Violation
from app.domain.snapshot import PlanningSnapshot
from app.engines.constraints.base import ConstraintContext, HardConstraint, SoftConstraint
from app.engines.constraints.eligibility import CandidateIndex, check_hard, evaluate_eligibility
from app.engines.constraints.readiness import ReadinessAssessment, ReadinessIndex, assess_order

log = structlog.get_logger(__name__)


class ConstraintEngine:
    """Answers "which machines?", "at what preference cost?" and "is the order ready?"."""

    def __init__(
        self,
        hard: Sequence[HardConstraint],
        soft: Sequence[SoftConstraint],
        config: SchedulingConfig,
    ) -> None:
        self.hard: list[HardConstraint] = list(hard)
        self.soft: list[SoftConstraint] = list(soft)
        self.config = config

    # ---------------------------------------------------------------- hard
    def check_machine(self, op: Operation, machine: Machine, ctx: ConstraintContext) -> list[Violation]:
        """All hard-constraint violations of ``machine`` for ``op`` (empty = eligible)."""
        return check_hard(op, machine, ctx, self.hard)

    def eligible_machines(
        self,
        op: Operation,
        snapshot: PlanningSnapshot,
        at: datetime,
        order: Order | None = None,
        index: CandidateIndex | None = None,
    ) -> EligibilityResult:
        """Eligible machines ordered by ``(preferred_rank, machine_id)`` plus every rejection reason."""
        return evaluate_eligibility(
            op, snapshot, ensure_utc(at), self.config, self.hard, order=order, index=index
        )

    # ---------------------------------------------------------------- soft
    def soft_penalties(self, op: Operation, machine: Machine, ctx: ConstraintContext) -> list[Penalty]:
        penalties: list[Penalty] = []
        for constraint in self.soft:
            penalty = constraint.penalty(op, machine, ctx)
            if penalty is not None:
                penalties.append(penalty)
        return penalties

    def soft_cost(self, op: Operation, machine: Machine, ctx: ConstraintContext) -> float:
        """Total preference cost in minute-equivalents (bonuses are negative)."""
        return sum(p.cost for p in self.soft_penalties(op, machine, ctx))

    # ----------------------------------------------------------- readiness
    def readiness_index(self, snapshot: PlanningSnapshot, at: datetime) -> ReadinessIndex:
        return ReadinessIndex.build(snapshot, ensure_utc(at), self.hard)

    def assess(
        self, order: Order, snapshot: PlanningSnapshot, at: datetime, index: ReadinessIndex | None = None
    ) -> ReadinessAssessment:
        return assess_order(order, snapshot, ensure_utc(at), self.config, hard=self.hard, index=index)

    def order_blockers(self, order: Order, snapshot: PlanningSnapshot, at: datetime) -> list[Blocker]:
        return self.assess(order, snapshot, at).blockers

    def readiness(self, order: Order, snapshot: PlanningSnapshot, at: datetime) -> ReadinessState:
        return self.assess(order, snapshot, at).state

    def assessment_map(self, snapshot: PlanningSnapshot, at: datetime) -> dict[str, ReadinessAssessment]:
        """Readiness of every order in the snapshot (sorted by order id), sharing one index."""
        index = self.readiness_index(snapshot, at)
        result = {
            order_id: assess_order(snapshot.orders[order_id], snapshot, index.at, self.config, index=index)
            for order_id in sorted(snapshot.orders)
        }
        states = [a.state.value for a in result.values()]
        log.debug(
            "constraints.readiness_map",
            orders=len(result),
            ready=states.count(ReadinessState.READY.value),
            blocked=len(result) - states.count(ReadinessState.READY.value),
        )
        return result

    def readiness_map(
        self, snapshot: PlanningSnapshot, at: datetime
    ) -> dict[str, tuple[ReadinessState, list[Blocker]]]:
        return {oid: (a.state, a.blockers) for oid, a in self.assessment_map(snapshot, at).items()}

    # -------------------------------------------------------------- explain
    def describe(self) -> dict[str, Any]:
        return {
            "hard_constraints": [c.key for c in self.hard],
            "soft_constraints": [c.key for c in self.soft],
            "config_id": self.config.config_id,
            "config_version": self.config.version,
        }


__all__ = ["ConstraintEngine"]
