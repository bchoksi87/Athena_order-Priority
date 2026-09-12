"""PriorityEngine (DESIGN_CONTRACT §6.1): weighted factor scoring plus adjustments.

Per open order::

    base_score  = Σ factor.points            (weight x raw, penalties negative)
    score       = base_score
                + ERP priority + customer rule + aging + starvation + expedite
                + overrides (INCREASE/DECREASE delta, SET_PRIORITY, FORCE_NEXT → 100)
    score       = clamp(score, 0, 100), then the optional blocked-order cap

Every factor in the engine is evaluated even when its profile weight is 0 so
the explanation can show "(not weighted)"; the weight comes from
``profile.weight_map()`` (bonus weights normalised to 1.0). Risk level is
derived from projected lateness / slack against ``RiskThresholds``. The
ranking pass (with the fairness top-N share rule) sets ``rank`` and the
explanation text is rendered last from the computed lists.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

import structlog

from app.core.clock import Clock
from app.domain.config import PriorityProfile, RiskThresholds
from app.domain.enums import ReadinessState, RiskLevel
from app.domain.models import CustomerRule, Order
from app.domain.results import FactorScore, PriorityAdjustment, PriorityResult
from app.domain.snapshot import PlanningSnapshot
from app.engines.priority.adjustments import (
    AdjustmentInputs,
    aging_adjustment,
    customer_rule_adjustment,
    erp_priority_adjustment,
    expedite_adjustment,
    override_adjustments,
    starvation_adjustment,
)
from app.engines.priority.base import PriorityContext, PriorityFactor
from app.engines.priority.context import PriorityContextBuilder
from app.engines.priority.context_ext import ExtendedPriorityContext
from app.engines.priority.explanation import SCORE_MAX, SCORE_MIN, render_explanation
from app.engines.priority.ranking import rank_results

log = structlog.get_logger(__name__)

MINUTES_PER_HOUR = 60.0


def assess_risk(
    order: Order, ctx: PriorityContext, thresholds: RiskThresholds
) -> tuple[RiskLevel, float | None, datetime | None, float | None, str]:
    """``(risk, hours_until_due, projected_completion, projected_lateness_hours, basis)``.

    CRITICAL when overdue or projected late beyond ``critical_lateness_hours``;
    otherwise slack (``due - projected completion``, else ``time to due -
    remaining work``, else time to due alone) against the HIGH / MEDIUM
    thresholds. No due date at all → LOW (nothing to be late for).
    """
    hours = order.hours_until_due(ctx.now)
    projected = ctx.projected_completion.get(order.order_id)
    due = order.due_date
    lateness = (
        (projected - due).total_seconds() / 3600.0 if projected is not None and due is not None else None
    )
    if hours is None:
        return RiskLevel.LOW, None, projected, None, "no_due_date"
    if hours < 0:
        return RiskLevel.CRITICAL, hours, projected, lateness, "overdue"
    if lateness is not None:
        if lateness > thresholds.critical_lateness_hours:
            return RiskLevel.CRITICAL, hours, projected, lateness, "projected_late"
        slack, basis = -lateness, "projected_slack"
    else:
        remaining = ctx.remaining_minutes.get(order.order_id)
        if remaining is not None:
            slack, basis = hours - remaining / MINUTES_PER_HOUR, "remaining_slack"
            if slack < thresholds.critical_lateness_hours:
                return RiskLevel.CRITICAL, hours, projected, lateness, "cannot_finish_in_time"
        else:
            slack, basis = hours, "time_to_due_only"
    if slack < thresholds.high_slack_hours:
        return RiskLevel.HIGH, hours, projected, lateness, basis
    if slack < thresholds.medium_slack_hours:
        return RiskLevel.MEDIUM, hours, projected, lateness, basis
    return RiskLevel.LOW, hours, projected, lateness, basis


class PriorityEngine:
    """Scores and ranks every open order of a snapshot against a :class:`PriorityProfile`."""

    def __init__(
        self,
        factors: Sequence[PriorityFactor],
        clock: Clock,
        builder: PriorityContextBuilder | None = None,
    ) -> None:
        keys = [f.key for f in factors]
        if len(keys) != len(set(keys)):
            raise ValueError(f"duplicate factor keys: {sorted(k for k in keys if keys.count(k) > 1)}")
        self.factors: list[PriorityFactor] = list(factors)
        self.clock = clock
        self._builder = builder

    # ---------------------------------------------------------------- setup
    @property
    def builder(self) -> PriorityContextBuilder:
        if self._builder is None:
            self._builder = PriorityContextBuilder(clock=self.clock)
        return self._builder

    def build_context(
        self,
        snapshot: PlanningSnapshot,
        profile: PriorityProfile,
        customer_rules: Mapping[str, CustomerRule] | None = None,
    ) -> ExtendedPriorityContext:
        return self.builder.build(snapshot, profile, customer_rules)

    # ------------------------------------------------------------- evaluate
    def evaluate(
        self,
        snapshot: PlanningSnapshot,
        profile: PriorityProfile,
        customer_rules: Mapping[str, CustomerRule] | None = None,
        ctx: PriorityContext | None = None,
    ) -> dict[str, PriorityResult]:
        """Ranked :class:`PriorityResult` per *open* order (keyed by order id)."""
        if ctx is None:
            ctx = self.build_context(snapshot, profile, customer_rules)
        self._warn_unknown_weights(profile)
        inputs = AdjustmentInputs.from_context(ctx)
        results = {
            order.order_id: self._evaluate(order, ctx, inputs, rendered=False)
            for order in snapshot.open_orders()
        }
        rank_results(results, snapshot, profile.fairness)
        for result in results.values():
            result.explanation = render_explanation(result)
        log.info(
            "priority.evaluated",
            orders=len(results),
            blocked=sum(1 for r in results.values() if r.blocked),
            forced=sum(1 for r in results.values() if r.forced_next),
            profile=profile.profile_id,
            profile_version=profile.version,
        )
        return results

    def evaluate_order(self, order: Order, ctx: PriorityContext) -> PriorityResult:
        """Score one order (no rank; ranking needs the whole population)."""
        return self._evaluate(order, ctx, AdjustmentInputs.from_context(ctx), rendered=True)

    # ------------------------------------------------------------ internals
    def _warn_unknown_weights(self, profile: PriorityProfile) -> None:
        known = {f.key for f in self.factors}
        unknown = sorted(w.key for w in profile.weights if w.key not in known and w.enabled and w.weight > 0)
        if unknown:
            log.warning("priority.profile.unknown_factors", profile=profile.profile_id, keys=unknown)

    def _evaluate(
        self, order: Order, ctx: PriorityContext, inputs: AdjustmentInputs, *, rendered: bool
    ) -> PriorityResult:
        profile = ctx.profile
        factors: list[FactorScore] = [factor.score(order, ctx) for factor in self.factors]
        base_score = sum(f.points for f in factors)
        adjustments: list[PriorityAdjustment] = []
        score = base_score
        for adjustment in (
            erp_priority_adjustment(order, profile),
            customer_rule_adjustment(order, inputs.customer_rules),
            aging_adjustment(order, profile, inputs.now),
            starvation_adjustment(order, profile, inputs.now),
            expedite_adjustment(order, inputs.expedites, profile, inputs.now),
        ):
            if adjustment is not None:
                adjustments.append(adjustment)
                score += adjustment.points
        overrides, forced_next = override_adjustments(order, inputs.overrides, score)
        adjustments.extend(overrides)
        score += sum(a.points for a in overrides)
        score = max(SCORE_MIN, min(SCORE_MAX, score))

        readiness = ctx.readiness.get(order.order_id, ReadinessState.READY)
        blockers = ctx.blockers.get(order.order_id, [])
        blocked = readiness is not ReadinessState.READY
        if blocked and profile.blocked_order_cap is not None and not forced_next:
            score = min(score, profile.blocked_order_cap)
        risk, hours, projected, lateness, _basis = assess_risk(order, ctx, profile.risk)

        result = PriorityResult(
            order_id=order.order_id,
            score=score,
            base_score=base_score,
            factors=factors,
            adjustments=adjustments,
            readiness=readiness,
            blocked=blocked,
            blocking_reasons=[b.message for b in blockers],
            risk_level=risk,
            explanation="",
            profile_id=profile.profile_id,
            profile_version=profile.version,
            computed_at=self.clock.now(),
            hours_until_due=hours,
            projected_completion=projected,
            projected_lateness_hours=lateness,
            forced_next=forced_next,
        )
        if rendered:
            result.explanation = render_explanation(result)
        return result

    def describe(self) -> dict[str, Any]:
        return {"factors": [f.key for f in self.factors]}


__all__ = ["PriorityEngine", "assess_risk"]
