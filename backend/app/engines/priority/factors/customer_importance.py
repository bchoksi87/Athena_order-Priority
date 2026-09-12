"""Factor ``customer_importance`` (spec Phase 4 "Customer priority", Phase 15).

Score = tier score (customer-rule override wins over the master-data tier),
blended with the customer's revenue and profitability percentiles according
to ``CustomerScoring.revenue_weight`` / ``profitability_weight`` (missing
percentiles drop out of the blend instead of pulling the score down), plus
the strategic-account bonus and escalation points, capped at ``max_score``.
An order whose customer is unknown gets the lowest tier score with an
explicit data-quality reason.
"""

from __future__ import annotations

from typing import Any, Literal

from app.domain.config import FACTOR_NAMES, CustomerScoring
from app.domain.enums import CustomerTier
from app.domain.models import Customer, CustomerRule, Order
from app.domain.results import FactorScore
from app.engines.priority.base import PriorityContext, make_factor_score
from app.engines.priority.context_ext import factor_weight

KEY = "customer_importance"


def tier_score(cfg: CustomerScoring, tier: CustomerTier | None) -> float:
    if tier is not None and tier.value in cfg.tier_scores:
        return cfg.tier_scores[tier.value]
    return min(cfg.tier_scores.values()) if cfg.tier_scores else 0.0


def effective_tier(customer: Customer | None, rule: CustomerRule | None) -> tuple[CustomerTier | None, str]:
    if rule is not None and rule.active and rule.tier_override is not None:
        return rule.tier_override, "customer_rule"
    if customer is not None:
        return customer.customer_tier, "customer"
    return None, "unknown"


class CustomerImportance:
    key = KEY
    name = FACTOR_NAMES[KEY]
    kind: Literal["bonus", "penalty"] = "bonus"

    def score(self, order: Order, ctx: PriorityContext) -> FactorScore:
        cfg = ctx.profile.customer
        weight = factor_weight(ctx, self.key)
        customer = ctx.snapshot.customers.get(order.customer_id)
        rule = ctx.customer_rules.get(order.customer_id)
        tier, tier_source = effective_tier(customer, rule)
        base = tier_score(cfg, tier)
        details: dict[str, Any] = {
            "tier": tier.value if tier else None,
            "tier_source": tier_source,
            "tier_score": base,
        }
        if customer is None:
            details["customer_id"] = order.customer_id
            return make_factor_score(
                self, base, weight, f"Unknown customer {order.customer_id} (data quality issue)", details
            )
        # Blend the tier score with revenue / profitability rank; absent ranks drop out.
        parts: list[tuple[float, float]] = [
            (max(0.0, 1.0 - cfg.revenue_weight - cfg.profitability_weight), base)
        ]
        rev = ctx.customer_revenue_percentile.get(customer.customer_id)
        prof = ctx.customer_profitability_percentile.get(customer.customer_id)
        if rev is not None and cfg.revenue_weight > 0:
            parts.append((cfg.revenue_weight, 100.0 * rev))
        if prof is not None and cfg.profitability_weight > 0:
            parts.append((cfg.profitability_weight, 100.0 * prof))
        total_weight = sum(w for w, _ in parts)
        blended = sum(w * v for w, v in parts) / total_weight if total_weight > 0 else base
        strategic = cfg.strategic_flag_bonus if customer.strategic_customer_flag else 0.0
        escalation = cfg.escalation_points_per_level * max(0, customer.escalation_level)
        raw = min(cfg.max_score, blended + strategic + escalation)
        details.update(
            {
                "revenue_percentile": rev,
                "profitability_percentile": prof,
                "blended_score": blended,
                "strategic_bonus": strategic,
                "escalation_level": customer.escalation_level,
                "escalation_points": escalation,
                "erp_customer_priority": customer.customer_priority,
            }
        )
        tier_label = tier.value if tier else "unknown"
        bits = [f"{tier_label} customer {customer.customer_name}"]
        if tier_source == "customer_rule":
            bits[0] += " (tier set by customer rule)"
        if rev is not None:
            bits.append(f"revenue top {100 - 100 * rev:.0f}%")
        if strategic:
            bits.append("strategic account")
        if escalation:
            bits.append(f"escalation level {customer.escalation_level}")
        return make_factor_score(self, raw, weight, ", ".join(bits), details)


__all__ = ["KEY", "CustomerImportance", "effective_tier", "tier_score"]
