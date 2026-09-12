"""Ranking and the fairness top-N share rule (spec Phase 17).

Orders are ranked by ``(forced_next desc, score desc, due date asc, order_id)``
— a total, deterministic order. The fairness pass then walks the ranking
and lets no customer occupy more than ``max_top_n_share_per_customer`` of the
first ``top_n`` slots: a customer's excess (lowest-scoring) orders are
demoted to just below the top-N block, and other customers' orders move up.
Scores are never changed by this pass — only ``rank`` — and each demoted
order receives a zero-point ``fairness`` adjustment that documents it.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING

from app.domain.config import FairnessConfig
from app.domain.results import PriorityAdjustment, PriorityResult

if TYPE_CHECKING:
    from app.domain.snapshot import PlanningSnapshot


def ranking_key(
    result: PriorityResult, due: datetime | None
) -> tuple[bool, float, bool, datetime | None, str]:
    """Sort key: forced first, then score desc, then earliest due date (None last), then id."""
    return (not result.forced_next, -result.score, due is None, due, result.order_id)


def max_slots_per_customer(fairness: FairnessConfig) -> int:
    """Top-N slots one customer may hold (at least one, so a single customer is never excluded)."""
    return max(1, math.floor(fairness.top_n * fairness.max_top_n_share_per_customer))


def rank_results(
    results: dict[str, PriorityResult], snapshot: PlanningSnapshot, fairness: FairnessConfig
) -> list[str]:
    """Assign ``rank`` to every result in place; returns the ordered ids."""
    ordered = sorted(
        results.values(),
        key=lambda r: ranking_key(
            r, snapshot.orders[r.order_id].due_date if r.order_id in snapshot.orders else None
        ),
    )
    if fairness.enabled and fairness.top_n > 0 and 0 < fairness.max_top_n_share_per_customer < 1:
        ordered = _apply_top_n_share(ordered, snapshot, fairness)
    for position, result in enumerate(ordered, start=1):
        result.rank = position
    return [r.order_id for r in ordered]


def _apply_top_n_share(
    ordered: list[PriorityResult], snapshot: PlanningSnapshot, fairness: FairnessConfig
) -> list[PriorityResult]:
    limit = max_slots_per_customer(fairness)
    counts: dict[str, int] = defaultdict(int)
    kept: list[PriorityResult] = []
    demoted: list[PriorityResult] = []
    rest: list[PriorityResult] = []
    for result in ordered:
        if len(kept) >= fairness.top_n:
            rest.append(result)
            continue
        order = snapshot.orders.get(result.order_id)
        customer_id = order.customer_id if order is not None else ""
        if result.forced_next or counts[customer_id] < limit:
            kept.append(result)
            counts[customer_id] += 1
        else:
            demoted.append(result)
            result.adjustments.append(
                PriorityAdjustment(
                    "fairness",
                    0.0,
                    f"Fairness cap: customer {customer_id} already holds {limit} of the top "
                    f"{fairness.top_n} slots; ranked below them (score unchanged)",
                    source_id=customer_id,
                )
            )
    return kept + demoted + rest


__all__ = ["max_slots_per_customer", "rank_results", "ranking_key"]
