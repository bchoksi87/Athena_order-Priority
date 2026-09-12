"""Factor ``downstream_impact`` (spec Phase 4 §11): value of what waits on this order.

``ctx.downstream_value`` (order value of every open order that transitively
depends on this one) is ranked as a percentile; an order on a critical
chain — a dependent that cannot meet its own due date unless this order
starts immediately — scores 100 regardless. Orders nothing depends on score
0 with the reason "No dependent orders".
"""

from __future__ import annotations

from typing import Any, Literal

from app.domain.config import FACTOR_NAMES
from app.domain.models import Order
from app.domain.results import FactorScore
from app.engines.priority.base import PriorityContext, make_factor_score
from app.engines.priority.context_ext import extended, factor_weight

KEY = "downstream_impact"
CRITICAL_PATH_SCORE = 100.0


class DownstreamImpact:
    key = KEY
    name = FACTOR_NAMES[KEY]
    kind: Literal["bonus", "penalty"] = "bonus"

    def score(self, order: Order, ctx: PriorityContext) -> FactorScore:
        weight = factor_weight(ctx, self.key)
        value = ctx.downstream_value.get(order.order_id, 0.0)
        dependents = ctx.snapshot.dependents_of(order.order_id)
        ext = extended(ctx)
        details: dict[str, Any] = {"downstream_value": value, "direct_dependents": sorted(dependents)}
        critical = ext.critical_path.get(order.order_id) if ext is not None else None
        if critical:
            details["critical_path"] = critical
            return make_factor_score(
                self, CRITICAL_PATH_SCORE, weight, f"On critical path: {critical}", details
            )
        if not dependents and value <= 0:
            return make_factor_score(self, 0.0, weight, "No dependent orders", details)
        percentile = ext.downstream_percentile.get(order.order_id) if ext is not None else None
        if percentile is None:
            population_max = ext.population_max.get("downstream_value") if ext is not None else None
            percentile = value / population_max if population_max else (1.0 if value > 0 else 0.0)
        details["percentile"] = percentile
        reason = f"{len(dependents)} dependent order(s) worth {value:,.0f}"
        return make_factor_score(self, 100.0 * percentile, weight, reason, details)


__all__ = ["KEY", "DownstreamImpact"]
