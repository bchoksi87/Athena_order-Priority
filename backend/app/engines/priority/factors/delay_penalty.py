"""Factor ``delay_penalty``: the cost of being late (spec Phase 4 §6).

``penalty per day`` = ERP ``lateness_penalty_per_day`` or, failing that,
``default_penalty_per_day_ratio x order_value``; escalated by the customer's
escalation level and the strategic multiplier. The effective penalty is
scaled to a score either against ``reference_penalty`` (absolute) or by
percentile within the open-order population. :func:`effective_penalty_per_day`
is shared with the context builder so the percentile population and the
per-order number come from the same formula.
"""

from __future__ import annotations

from typing import Any, Literal

from app.domain.config import FACTOR_NAMES, DelayPenaltyConfig
from app.domain.models import Customer, Order
from app.domain.results import FactorScore
from app.engines.priority.base import PriorityContext, make_factor_score
from app.engines.priority.context_ext import factor_weight

KEY = "delay_penalty"


def effective_penalty_per_day(
    order: Order, customer: Customer | None, cfg: DelayPenaltyConfig
) -> tuple[float | None, dict[str, Any]]:
    """Escalated lateness penalty per day and the pieces that produced it."""
    details: dict[str, Any] = {}
    if order.lateness_penalty_per_day is not None:
        base, source = max(0.0, order.lateness_penalty_per_day), "erp"
    elif order.order_value is not None:
        base, source = max(0.0, order.order_value) * cfg.default_penalty_per_day_ratio, "default_ratio"
    else:
        return None, {"penalty_source": "none"}
    escalation = customer.escalation_level if customer is not None else 0
    strategic = customer is not None and customer.strategic_customer_flag
    multiplier = (1.0 + cfg.escalation_multiplier_per_level * max(0, escalation)) * (
        cfg.strategic_multiplier if strategic else 1.0
    )
    details.update(
        {
            "penalty_source": source,
            "base_penalty_per_day": base,
            "escalation_level": escalation,
            "strategic": strategic,
            "multiplier": multiplier,
        }
    )
    return base * multiplier, details


class DelayPenalty:
    key = KEY
    name = FACTOR_NAMES[KEY]
    kind: Literal["bonus", "penalty"] = "bonus"

    def score(self, order: Order, ctx: PriorityContext) -> FactorScore:
        cfg = ctx.profile.delay_penalty
        weight = factor_weight(ctx, self.key)
        customer = ctx.snapshot.customers.get(order.customer_id)
        penalty, details = effective_penalty_per_day(order, customer, cfg)
        details["penalty_per_day"] = penalty
        if penalty is None:
            return make_factor_score(
                self, 0.0, weight, "No penalty information (no ERP penalty, no order value)", details
            )
        if penalty <= 0:
            return make_factor_score(self, 0.0, weight, "No lateness penalty", details)
        if cfg.reference_penalty is not None and cfg.reference_penalty > 0:
            raw = 100.0 * min(1.0, penalty / cfg.reference_penalty)
            details["reference_penalty"] = cfg.reference_penalty
            rank = f"{penalty / cfg.reference_penalty:.0%} of reference"
        else:
            percentile = ctx.penalty_percentile.get(order.order_id)
            details["percentile"] = percentile
            raw = 100.0 * percentile if percentile is not None else 0.0
            rank = (
                f"top {100 - 100 * percentile:.0f}% of open orders" if percentile is not None else "unranked"
            )
        source = "contractual" if details.get("penalty_source") == "erp" else "estimated"
        reason = f"{source.capitalize()} penalty {penalty:,.0f}/day ({rank})"
        if details.get("multiplier", 1.0) != 1.0:
            reason += f", escalated x{details['multiplier']:.2f}"
        return make_factor_score(self, raw, weight, reason, details)


__all__ = ["KEY", "DelayPenalty", "effective_penalty_per_day"]
