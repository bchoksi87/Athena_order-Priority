"""Post-factor adjustments (spec Phases 9, 15, 16, 17): ERP priority, customer rule,
aging, starvation, expedite and planner overrides.

Each function returns a :class:`~app.domain.results.PriorityAdjustment` (or
none) with the reason that produced the points; the engine applies them in
this fixed order and the explanation renders them verbatim. Snapshot-wide
look-ups (active expedites/overrides) are built once per evaluation pass in
:class:`AdjustmentInputs` so the per-order work stays O(1).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

import structlog

from app.core.clock import ensure_utc
from app.domain.config import PriorityProfile
from app.domain.enums import OverrideType
from app.domain.models import CustomerRule, Expedite, Order, PriorityOverride
from app.domain.results import PriorityAdjustment
from app.engines.priority.base import PriorityContext
from app.engines.priority.factors.common import describe_hours

log = structlog.get_logger(__name__)

HOURS_PER_DAY = 24.0
FORCE_NEXT_SCORE = 100.0


@dataclass(slots=True)
class AdjustmentInputs:
    """Per-pass look-ups shared by every order's adjustments."""

    now: datetime
    profile: PriorityProfile
    customer_rules: Mapping[str, CustomerRule] = field(default_factory=dict)
    expedites: Mapping[str, Expedite] = field(default_factory=dict)  # order_id -> strongest active
    overrides: Mapping[str, list[PriorityOverride]] = field(default_factory=dict)

    @classmethod
    def from_context(cls, ctx: PriorityContext) -> AdjustmentInputs:
        return cls(
            now=ctx.now,
            profile=ctx.profile,
            customer_rules=ctx.customer_rules,
            expedites=ctx.snapshot.active_expedites(ctx.now),
            overrides=ctx.snapshot.active_overrides(ctx.now),
        )


def waiting_days(order: Order, now: datetime) -> float | None:
    """Days since the order was received (else ordered); ``None`` when neither date exists."""
    start = order.received_date or order.order_date
    if start is None:
        return None
    return max(0.0, (now - ensure_utc(start)).total_seconds() / 3600.0 / HOURS_PER_DAY)


def erp_priority_adjustment(order: Order, profile: PriorityProfile) -> PriorityAdjustment | None:
    if order.erp_priority is None:
        return None
    points = profile.erp_priority_points.get(str(order.erp_priority))
    if points is None or points == 0:
        return None
    return PriorityAdjustment("erp_priority", points, f"ERP priority {order.erp_priority}")


def customer_rule_adjustment(order: Order, rules: Mapping[str, CustomerRule]) -> PriorityAdjustment | None:
    rule = rules.get(order.customer_id)
    if rule is None or not rule.active or rule.priority_boost_points == 0:
        return None
    return PriorityAdjustment(
        "customer_rule",
        rule.priority_boost_points,
        f"Customer rule for {order.customer_id}" + (f": {rule.notes}" if rule.notes else ""),
        source_id=order.customer_id,
    )


def aging_adjustment(order: Order, profile: PriorityProfile, now: datetime) -> PriorityAdjustment | None:
    """``points_per_day`` for every day waited beyond ``start_after_days``, capped at ``max_points``."""
    cfg = profile.aging
    if not cfg.enabled:
        return None
    days = waiting_days(order, now)
    if days is None or days <= cfg.start_after_days:
        return None
    points = min(cfg.max_points, (days - cfg.start_after_days) * cfg.points_per_day)
    if points <= 0:
        return None
    reason = f"Waiting {days:.0f} days ({days - cfg.start_after_days:.0f} beyond {cfg.start_after_days:g})"
    if points >= cfg.max_points:
        reason += f", capped at {cfg.max_points:g}"
    return PriorityAdjustment("aging", points, reason)


def starvation_adjustment(order: Order, profile: PriorityProfile, now: datetime) -> PriorityAdjustment | None:
    """Starvation boost once an order has waited ``max_wait_days`` or longer."""
    cfg = profile.fairness
    if not cfg.enabled or cfg.starvation_boost_points <= 0:
        return None
    days = waiting_days(order, now)
    if days is None or days < cfg.max_wait_days:
        return None
    return PriorityAdjustment(
        "fairness",
        cfg.starvation_boost_points,
        f"Starvation prevention: waiting {days:.0f} days (threshold {cfg.max_wait_days:g})",
    )


def expedite_adjustment(
    order: Order, expedites: Mapping[str, Expedite], profile: PriorityProfile, now: datetime
) -> PriorityAdjustment | None:
    expedite = expedites.get(order.order_id)
    if expedite is None or not expedite.is_active_at(now):
        return None
    points = min(max(0.0, expedite.boost_points), profile.expedite.max_boost_points)
    remaining = (ensure_utc(expedite.expires_at) - now).total_seconds() / 3600.0
    reason = f"Expedited by {expedite.created_by}: {expedite.reason} (expires in {describe_hours(remaining)})"
    if expedite.boost_points > points:
        reason += f", capped at {points:g}"
    return PriorityAdjustment("expedite", points, reason, source_id=expedite.expedite_id)


def override_adjustments(
    order: Order, overrides: Mapping[str, list[PriorityOverride]], running_score: float
) -> tuple[list[PriorityAdjustment], bool]:
    """Planner overrides in creation order; returns ``(adjustments, forced_next)``.

    INCREASE/DECREASE add a delta, SET_PRIORITY replaces the running score,
    FORCE_NEXT pins the score to 100 (applied last, wins over everything).
    Hold / release / move / machine-lock overrides are not priority changes
    and are ignored here (the constraint engine handles them).
    """
    active = sorted(
        overrides.get(order.order_id, ()), key=lambda o: (ensure_utc(o.created_at), o.override_id)
    )
    adjustments: list[PriorityAdjustment] = []
    score = running_score
    forced: PriorityOverride | None = None
    for override in active:
        kind = override.override_type
        who = f"by {override.created_by}: {override.reason}"
        if kind is OverrideType.FORCE_NEXT:
            forced = override
            continue
        if kind in (OverrideType.INCREASE_PRIORITY, OverrideType.DECREASE_PRIORITY):
            if override.value is None:
                log.warning("priority.override.no_value", override_id=override.override_id, type=kind.value)
                continue
            delta = abs(override.value) if kind is OverrideType.INCREASE_PRIORITY else -abs(override.value)
            verb = "raised" if delta >= 0 else "lowered"
            adjustments.append(
                PriorityAdjustment(
                    "override", delta, f"Priority {verb} {who}", source_id=override.override_id
                )
            )
            score += delta
        elif kind is OverrideType.SET_PRIORITY:
            if override.value is None:
                log.warning("priority.override.no_value", override_id=override.override_id, type=kind.value)
                continue
            delta = override.value - score
            adjustments.append(
                PriorityAdjustment(
                    "override",
                    delta,
                    f"Priority set to {override.value:g} {who}",
                    source_id=override.override_id,
                )
            )
            score += delta
    if forced is not None:
        delta = FORCE_NEXT_SCORE - score
        adjustments.append(
            PriorityAdjustment(
                "override",
                delta,
                f"Forced next by {forced.created_by}: {forced.reason}",
                source_id=forced.override_id,
            )
        )
    return adjustments, forced is not None


__all__ = [
    "FORCE_NEXT_SCORE",
    "AdjustmentInputs",
    "aging_adjustment",
    "customer_rule_adjustment",
    "erp_priority_adjustment",
    "expedite_adjustment",
    "override_adjustments",
    "starvation_adjustment",
    "waiting_days",
]
