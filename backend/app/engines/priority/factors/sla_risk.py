"""Factor ``sla_risk``: risk of breaching a contractual turnaround (spec Phase 4/15).

SLA hours are resolved in precedence order ``order.sla_hours → customer rule
→ customer.sla_hours → profile default``; without one the factor scores 0
("No SLA"). The SLA clock starts at ``received_date`` (else ``order_date``)
and the remaining share ``remaining / sla_hours`` maps through
``(0, breach) → (imminent_ratio, imminent) → (watch_ratio, watch) → (1, safe)``.
When a projected completion is known the remaining time is measured to
*that* instant instead of "now", so an order that will finish after its SLA
deadline is already scored as a breach.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Literal

from app.core.clock import ensure_utc
from app.domain.config import FACTOR_NAMES, SlaRiskConfig
from app.domain.models import Customer, CustomerRule, Order
from app.domain.results import FactorScore
from app.engines.priority.base import PriorityContext, make_factor_score
from app.engines.priority.context_ext import factor_weight
from app.engines.priority.factors.common import describe_hours, interpolate

KEY = "sla_risk"


def resolve_sla_hours(
    order: Order, customer: Customer | None, rule: CustomerRule | None, cfg: SlaRiskConfig
) -> tuple[float | None, str]:
    """``(sla_hours, source)`` where source names the level that supplied it."""
    if order.sla_hours is not None and order.sla_hours > 0:
        return order.sla_hours, "order"
    if rule is not None and rule.active and rule.sla_hours is not None and rule.sla_hours > 0:
        return rule.sla_hours, "customer_rule"
    if customer is not None and customer.sla_hours is not None and customer.sla_hours > 0:
        return customer.sla_hours, "customer"
    if cfg.default_sla_hours is not None and cfg.default_sla_hours > 0:
        return cfg.default_sla_hours, "profile_default"
    return None, "none"


def sla_risk_score(cfg: SlaRiskConfig, remaining_ratio: float) -> float:
    """Raw risk score for the share of the SLA window still remaining (≤0 = breached)."""
    if remaining_ratio <= 0:
        return cfg.breach_score
    anchors = [
        (0.0, cfg.breach_score),
        (cfg.imminent_ratio, cfg.imminent_score),
        (cfg.watch_ratio, cfg.watch_score),
        (1.0, cfg.safe_score),
    ]
    return interpolate(anchors, remaining_ratio)


class SlaRisk:
    key = KEY
    name = FACTOR_NAMES[KEY]
    kind: Literal["bonus", "penalty"] = "bonus"

    def score(self, order: Order, ctx: PriorityContext) -> FactorScore:
        cfg = ctx.profile.sla
        weight = factor_weight(ctx, self.key)
        customer = ctx.snapshot.customers.get(order.customer_id)
        rule = ctx.customer_rules.get(order.customer_id)
        sla_hours, source = resolve_sla_hours(order, customer, rule, cfg)
        details: dict[str, Any] = {"sla_hours": sla_hours, "sla_source": source}
        if sla_hours is None:
            return make_factor_score(self, 0.0, weight, "No SLA", details)
        start = order.received_date or order.order_date
        if start is None:
            details["elapsed_hours"] = None
            return make_factor_score(
                self,
                cfg.watch_score,
                weight,
                f"SLA {sla_hours:g} h but elapsed time unknown (no received/order date)",
                details,
            )
        start = ensure_utc(start)
        deadline = start + timedelta(hours=sla_hours)
        elapsed = (ctx.now - start).total_seconds() / 3600.0
        remaining = sla_hours - elapsed
        projected = ctx.projected_completion.get(order.order_id)
        if projected is not None:
            remaining = min(remaining, (deadline - projected).total_seconds() / 3600.0)
            details["projected_completion"] = projected
        ratio = remaining / sla_hours
        details.update(
            {
                "elapsed_hours": elapsed,
                "remaining_hours": remaining,
                "remaining_ratio": ratio,
                "deadline": deadline,
            }
        )
        raw = sla_risk_score(cfg, ratio)
        if remaining <= 0:
            reason = f"SLA {sla_hours:g} h breached" + (
                f" by {describe_hours(remaining)}" if remaining < 0 else ""
            )
            if projected is not None and deadline > ctx.now:
                reason = (
                    f"SLA {sla_hours:g} h: projected completion misses deadline "
                    f"by {describe_hours(remaining)}"
                )
        else:
            reason = f"SLA {sla_hours:g} h: {describe_hours(remaining)} remaining ({ratio:.0%})"
        return make_factor_score(self, raw, weight, reason, details)


__all__ = ["KEY", "SlaRisk", "resolve_sla_hours", "sla_risk_score"]
