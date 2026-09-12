"""Factor ``order_value``: higher-value orders score higher (spec Phase 4 §4).

Three scalings, chosen by ``OrderValueConfig.scaling``:

* ``percentile`` (default) - rank within the open-order population, so the
  score is robust to outliers and currency;
* ``linear`` - ``value / cap`` where ``cap`` is ``cap_value`` or the largest
  open-order value;
* ``log`` - ``log1p(value) / log1p(cap)``, compressing the long tail.

All scalings start at ``min_score`` so a known-but-tiny value still beats an
unknown one only marginally; unknown values get ``min_score`` and a reason.
Fairness against starvation is handled by the engine's aging/starvation
adjustments, not here.
"""

from __future__ import annotations

import math
from typing import Any, Literal

from app.domain.config import FACTOR_NAMES, OrderValueConfig
from app.domain.models import Order
from app.domain.results import FactorScore
from app.engines.priority.base import PriorityContext, make_factor_score
from app.engines.priority.context_ext import extended, factor_weight

KEY = "order_value"


def scaled_value_score(
    cfg: OrderValueConfig, value: float, percentile: float | None, population_max: float | None
) -> tuple[float, str]:
    """Raw score and the scaling actually applied (falls back to percentile when no cap is known)."""
    span = 100.0 - cfg.min_score
    cap = cfg.cap_value if cfg.cap_value is not None and cfg.cap_value > 0 else population_max
    if cfg.scaling in ("linear", "log") and cap is not None and cap > 0:
        v = max(0.0, min(value, cap))
        ratio = v / cap if cfg.scaling == "linear" else math.log1p(v) / math.log1p(cap)
        return cfg.min_score + span * ratio, cfg.scaling
    if percentile is None:
        return cfg.min_score, "unranked"
    return cfg.min_score + span * percentile, "percentile"


class OrderValue:
    key = KEY
    name = FACTOR_NAMES[KEY]
    kind: Literal["bonus", "penalty"] = "bonus"

    def score(self, order: Order, ctx: PriorityContext) -> FactorScore:
        cfg = ctx.profile.order_value
        weight = factor_weight(ctx, self.key)
        value = order.order_value
        details: dict[str, Any] = {"order_value": value, "scaling": cfg.scaling}
        if value is None:
            return make_factor_score(self, cfg.min_score, weight, "Order value unknown", details)
        ext = extended(ctx)
        population_max = ext.population_max.get("order_value") if ext is not None else None
        percentile = ctx.order_value_percentile.get(order.order_id)
        raw, applied = scaled_value_score(cfg, value, percentile, population_max)
        details.update(
            {"percentile": percentile, "population_max": population_max, "applied_scaling": applied}
        )
        if applied == "percentile" and percentile is not None:
            reason = f"Order value {value:,.0f} (top {100 - 100 * percentile:.0f}% of open orders)"
        elif applied == "unranked":
            reason = f"Order value {value:,.0f} (no population to rank against)"
        else:
            cap = cfg.cap_value if cfg.cap_value is not None else population_max
            reason = f"Order value {value:,.0f} ({applied} scale, cap {cap or 0:,.0f})"
        return make_factor_score(self, raw, weight, reason, details)


__all__ = ["KEY", "OrderValue", "scaled_value_score"]
