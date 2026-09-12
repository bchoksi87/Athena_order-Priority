"""Profile preview (spec Phase 14): what would change if the weights changed.

"Changing Due Date weight from 30% to 40% would move 27 orders into the top
50." Both profiles are evaluated on the same snapshot; when only the factor
weights differ the expensive context (readiness, projections, percentiles)
is built once and reused, otherwise it is rebuilt for the second profile.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from app.domain.config import FACTOR_NAMES, PriorityProfile
from app.domain.models import CustomerRule
from app.domain.results import PriorityResult
from app.domain.snapshot import PlanningSnapshot
from app.engines.priority.engine import PriorityEngine

_NON_WEIGHT_FIELDS = {"weights", "profile_id", "name", "version"}


@dataclass(slots=True)
class ProfileComparison:
    profile_a_id: str
    profile_b_id: str
    top_n: int
    orders_evaluated: int
    top_n_a: list[str]
    top_n_b: list[str]
    entered_top_n: list[str]  # in B's top N but not A's (ordered by B rank)
    left_top_n: list[str]  # in A's top N but not B's (ordered by A rank)
    rank_deltas: dict[str, int]  # order_id -> rank_b - rank_a (negative = moved up)
    score_deltas: dict[str, float]  # order_id -> score_b - score_a
    orders_changed_rank: int
    mean_abs_score_delta: float
    max_abs_score_delta: float
    weight_changes: dict[str, tuple[float, float]]  # key -> (weight_a, weight_b), raw percents
    summary: str
    details: dict[str, Any] = field(default_factory=dict)


def raw_weights(profile: PriorityProfile) -> dict[str, float]:
    """Raw (percent) weights as configured; disabled factors count as 0."""
    return {w.key: (w.weight if w.enabled else 0.0) for w in profile.weights}


def weight_changes(a: PriorityProfile, b: PriorityProfile) -> dict[str, tuple[float, float]]:
    wa, wb = raw_weights(a), raw_weights(b)
    return {
        k: (wa.get(k, 0.0), wb.get(k, 0.0))
        for k in sorted(set(wa) | set(wb))
        if wa.get(k, 0.0) != wb.get(k, 0.0)
    }


def only_weights_differ(a: PriorityProfile, b: PriorityProfile) -> bool:
    return a.model_dump(exclude=_NON_WEIGHT_FIELDS) == b.model_dump(exclude=_NON_WEIGHT_FIELDS)


def _top(results: Mapping[str, PriorityResult], top_n: int) -> list[str]:
    return [r.order_id for r in sorted(results.values(), key=lambda r: r.rank or 0) if r.rank is not None][
        :top_n
    ]


def _summary(
    changes: dict[str, tuple[float, float]],
    a: PriorityProfile,
    b: PriorityProfile,
    entered: int,
    left: int,
    top_n: int,
) -> str:
    if changes:
        parts = [
            f"{FACTOR_NAMES.get(k, k)} weight from {wa:g}% to {wb:g}%"
            for k, (wa, wb) in list(changes.items())[:3]
        ]
        if len(changes) > 3:
            parts.append(f"{len(changes) - 3} more weight(s)")
        what = "Changing " + " and ".join(parts)
    else:
        what = f"Switching from profile {a.profile_id!r} to {b.profile_id!r}"
    return f"{what} would move {entered} order(s) into the top {top_n} and {left} out"


def compare_profiles(
    snapshot: PlanningSnapshot,
    profile_a: PriorityProfile,
    profile_b: PriorityProfile,
    engine: PriorityEngine,
    top_n: int = 50,
    customer_rules: Mapping[str, CustomerRule] | None = None,
) -> ProfileComparison:
    ctx_a = engine.build_context(snapshot, profile_a, customer_rules)
    if only_weights_differ(profile_a, profile_b):
        ctx_b = replace(ctx_a, profile=profile_b, weights=profile_b.weight_map())
    else:
        ctx_b = engine.build_context(snapshot, profile_b, customer_rules)
    results_a = engine.evaluate(snapshot, profile_a, customer_rules, ctx=ctx_a)
    results_b = engine.evaluate(snapshot, profile_b, customer_rules, ctx=ctx_b)
    top_a, top_b = _top(results_a, top_n), _top(results_b, top_n)
    set_a, set_b = set(top_a), set(top_b)
    entered = [oid for oid in top_b if oid not in set_a]
    left = [oid for oid in top_a if oid not in set_b]
    rank_deltas = {
        oid: (results_b[oid].rank or 0) - (results_a[oid].rank or 0)
        for oid in sorted(results_a)
        if oid in results_b
    }
    score_deltas = {oid: results_b[oid].score - results_a[oid].score for oid in rank_deltas}
    abs_deltas = [abs(d) for d in score_deltas.values()]
    changes = weight_changes(profile_a, profile_b)
    return ProfileComparison(
        profile_a_id=profile_a.profile_id,
        profile_b_id=profile_b.profile_id,
        top_n=top_n,
        orders_evaluated=len(rank_deltas),
        top_n_a=top_a,
        top_n_b=top_b,
        entered_top_n=entered,
        left_top_n=left,
        rank_deltas=rank_deltas,
        score_deltas=score_deltas,
        orders_changed_rank=sum(1 for d in rank_deltas.values() if d != 0),
        mean_abs_score_delta=sum(abs_deltas) / len(abs_deltas) if abs_deltas else 0.0,
        max_abs_score_delta=max(abs_deltas, default=0.0),
        weight_changes=changes,
        summary=_summary(changes, profile_a, profile_b, len(entered), len(left), top_n),
        details={"context_reused": only_weights_differ(profile_a, profile_b)},
    )


__all__ = ["ProfileComparison", "compare_profiles", "only_weights_differ", "raw_weights", "weight_changes"]
