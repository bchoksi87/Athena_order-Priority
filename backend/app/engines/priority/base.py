"""Priority engine interfaces (see docs/DESIGN_CONTRACT.md §6.1)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

from app.domain.config import PriorityProfile
from app.domain.enums import ReadinessState
from app.domain.models import CustomerRule, Machine, Operation, Order
from app.domain.results import Blocker, FactorScore
from app.domain.snapshot import PlanningSnapshot


@dataclass(slots=True)
class PriorityContext:
    """Everything a factor may look at. Built once per ``evaluate`` call.

    Pre-computed, snapshot-wide statistics (percentiles, machine load) live
    here so individual factors stay O(1) per order.
    """

    snapshot: PlanningSnapshot
    profile: PriorityProfile
    now: datetime
    customer_rules: Mapping[str, CustomerRule] = field(default_factory=dict)
    readiness: Mapping[str, ReadinessState] = field(default_factory=dict)  # order_id -> state
    blockers: Mapping[str, list[Blocker]] = field(default_factory=dict)  # order_id -> blockers
    # order_id -> eligible machines for the order's next operation
    eligible_machines: Mapping[str, list[Machine]] = field(default_factory=dict)
    projected_completion: Mapping[str, datetime | None] = field(default_factory=dict)
    order_value_percentile: Mapping[str, float] = field(default_factory=dict)  # order_id -> 0..1
    margin_percentile: Mapping[str, float] = field(default_factory=dict)
    penalty_percentile: Mapping[str, float] = field(default_factory=dict)
    customer_revenue_percentile: Mapping[str, float] = field(default_factory=dict)  # customer_id -> 0..1
    customer_profitability_percentile: Mapping[str, float] = field(default_factory=dict)
    downstream_value: Mapping[str, float] = field(default_factory=dict)  # order_id -> value of dependents
    machine_next_free: Mapping[str, datetime] = field(default_factory=dict)  # machine_id -> time
    remaining_minutes: Mapping[str, float | None] = field(default_factory=dict)  # order_id -> estimate

    def next_operation(self, order: Order) -> Operation | None:
        return self.snapshot.next_operation_for_order(order.order_id)


class PriorityFactor(Protocol):
    key: str
    name: str
    kind: Literal["bonus", "penalty"]

    def score(self, order: Order, ctx: PriorityContext) -> FactorScore: ...


def make_factor_score(
    factor: PriorityFactor,
    raw_score: float,
    weight: float,
    reason: str,
    details: dict[str, object] | None = None,
) -> FactorScore:
    raw = max(0.0, min(100.0, raw_score))
    points = weight * raw
    if factor.kind == "penalty":
        points = -points
    return FactorScore(
        key=factor.key,
        name=factor.name,
        kind=factor.kind,
        raw_score=raw,
        weight=weight,
        points=points,
        reason=reason,
        details=dict(details or {}),
    )
