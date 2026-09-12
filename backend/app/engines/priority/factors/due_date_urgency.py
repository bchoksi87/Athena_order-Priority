"""Factor ``due_date_urgency``: how close the (effective) due date is.

Rule (spec Phase 4, "Due Date Urgency"): urgency rises as the due date
approaches and jumps to ``overdue_score`` once the order is late. The curve
is piecewise-linear between the configured anchors
``(critical_hours, critical_score) → (high_days, high_score) →
(medium_days, medium_score) → (low_days, low_score)``; beyond ``low_days`` the
last slope continues down to ``floor_score`` (a linear tail keeps the
function continuous without inventing another threshold).

When ``use_projected_lateness`` is on and the context carries a projected
completion, the effective horizon is the *slack* ``due - projected
completion`` when that is tighter than the raw time to due date: an order
that will be late even if started now is as urgent as an overdue one.
"""

from __future__ import annotations

from typing import Any, Literal

from app.domain.config import FACTOR_NAMES, DueDateThresholds
from app.domain.models import Order
from app.domain.results import FactorScore
from app.engines.priority.base import PriorityContext, make_factor_score
from app.engines.priority.context_ext import factor_weight
from app.engines.priority.factors.common import describe_hours, interpolate

KEY = "due_date_urgency"
HOURS_PER_DAY = 24.0


def urgency_anchors(cfg: DueDateThresholds) -> list[tuple[float, float]]:
    """Curve anchors as ``(hours, score)`` sorted by hours."""
    return sorted(
        [
            (cfg.critical_hours, cfg.critical_score),
            (cfg.high_days * HOURS_PER_DAY, cfg.high_score),
            (cfg.medium_days * HOURS_PER_DAY, cfg.medium_score),
            (cfg.low_days * HOURS_PER_DAY, cfg.low_score),
        ],
        key=lambda a: a[0],
    )


def urgency_score(cfg: DueDateThresholds, hours: float) -> float:
    """Raw urgency (0..100) for ``hours`` until the effective due date (negative = overdue)."""
    if hours <= 0:
        return cfg.overdue_score
    anchors = urgency_anchors(cfg)
    last_x, last_y = anchors[-1]
    if hours <= last_x:
        return interpolate(anchors, hours)
    prev_x, prev_y = anchors[-2]
    slope = (last_y - prev_y) / (last_x - prev_x) if last_x > prev_x else 0.0
    if slope >= 0:
        return min(last_y, cfg.floor_score)
    return min(last_y, max(cfg.floor_score, last_y + slope * (hours - last_x)))


class DueDateUrgency:
    key = KEY
    name = FACTOR_NAMES[KEY]
    kind: Literal["bonus", "penalty"] = "bonus"

    def score(self, order: Order, ctx: PriorityContext) -> FactorScore:
        cfg = ctx.profile.due_date
        weight = factor_weight(ctx, self.key)
        hours = order.hours_until_due(ctx.now)
        details: dict[str, Any] = {"hours_until_due": hours, "projected_lateness_hours": None}
        if hours is None:
            return make_factor_score(
                self, cfg.floor_score, weight, "No due date (data quality issue)", details
            )
        due = order.due_date
        projected = ctx.projected_completion.get(order.order_id) if cfg.use_projected_lateness else None
        lateness: float | None = None
        effective = hours
        if projected is not None and due is not None:
            lateness = (projected - due).total_seconds() / 3600.0
            details["projected_completion"] = projected
            details["projected_lateness_hours"] = lateness
            effective = min(hours, -lateness)  # slack = due - projected completion
        details["effective_hours"] = effective
        raw = urgency_score(cfg, effective)
        if hours < 0:
            reason = f"Overdue by {describe_hours(hours)}"
        elif lateness is not None and lateness > 0:
            reason = f"Due in {describe_hours(hours)} but projected {describe_hours(lateness)} late"
        elif effective < hours:
            reason = (
                f"Due in {describe_hours(hours)}, projected completion leaves "
                f"{describe_hours(effective)} slack"
            )
        else:
            reason = f"Due in {describe_hours(hours)}"
        return make_factor_score(self, raw, weight, reason, details)


__all__ = ["KEY", "DueDateUrgency", "urgency_anchors", "urgency_score"]
