"""Factor ``margin``: contribution margin where reliable data exists (spec Phase 4 §5).

Uses ``estimated_margin`` (falling back to ``actual_margin``) ranked as a
percentile within the open-order population. Unknown margin scores 0 with an
explicit reason so the explanation shows the data gap instead of hiding it.
"""

from __future__ import annotations

from typing import Any, Literal

from app.domain.config import FACTOR_NAMES
from app.domain.models import Order
from app.domain.results import FactorScore
from app.engines.priority.base import PriorityContext, make_factor_score
from app.engines.priority.context_ext import factor_weight

KEY = "margin"


def order_margin(order: Order) -> tuple[float | None, str]:
    if order.estimated_margin is not None:
        return order.estimated_margin, "estimated"
    if order.actual_margin is not None:
        return order.actual_margin, "actual"
    return None, "unknown"


class Margin:
    key = KEY
    name = FACTOR_NAMES[KEY]
    kind: Literal["bonus", "penalty"] = "bonus"

    def score(self, order: Order, ctx: PriorityContext) -> FactorScore:
        weight = factor_weight(ctx, self.key)
        margin, source = order_margin(order)
        details: dict[str, Any] = {"margin": margin, "margin_source": source}
        if margin is None:
            return make_factor_score(self, 0.0, weight, "Margin unknown", details)
        percentile = ctx.margin_percentile.get(order.order_id)
        details["percentile"] = percentile
        if percentile is None:
            return make_factor_score(
                self, 0.0, weight, f"Margin {margin:,.0f} (no population to rank against)", details
            )
        raw = 100.0 * percentile
        reason = (
            f"{source.capitalize()} margin {margin:,.0f} (top {100 - 100 * percentile:.0f}% of open orders)"
        )
        if margin < 0:
            reason = f"Negative {source} margin {margin:,.0f}"
        return make_factor_score(self, raw, weight, reason, details)


__all__ = ["KEY", "Margin", "order_margin"]
