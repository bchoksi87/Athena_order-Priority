"""Factor ``production_readiness``: ready orders beat blocked ones (spec Phase 4 §7).

The readiness state comes from the constraint engine (via the context) and
maps to a configured score; the reason lists the concrete blockers,
including when they are expected to clear ("Waiting for material AL-7075,
expected 14 Sep"), so the explanation tells the planner what to chase.
"""

from __future__ import annotations

from typing import Any, Literal

from app.domain.config import FACTOR_NAMES, ReadinessScoring
from app.domain.enums import MaterialStatus, ReadinessState
from app.domain.models import Order
from app.domain.results import Blocker, FactorScore
from app.engines.priority.base import PriorityContext, make_factor_score
from app.engines.priority.context_ext import factor_weight
from app.engines.priority.factors.common import describe_when

KEY = "production_readiness"
MAX_BLOCKERS_IN_REASON = 2

STATE_LABELS: dict[ReadinessState, str] = {
    ReadinessState.READY: "Ready to run",
    ReadinessState.WAITING_MATERIAL: "Waiting for material",
    ReadinessState.WAITING_TOOLING: "Waiting for tooling",
    ReadinessState.WAITING_APPROVAL: "Waiting for approval",
    ReadinessState.WAITING_PREVIOUS_OPERATION: "Waiting for previous operation",
    ReadinessState.MACHINE_UNAVAILABLE: "Machine unavailable",
    ReadinessState.QUALITY_HOLD: "Quality hold",
    ReadinessState.ON_HOLD: "On hold",
    ReadinessState.OTHER_CONSTRAINT: "Blocked",
}


def readiness_score(cfg: ReadinessScoring, state: ReadinessState, order: Order) -> float:
    if state is ReadinessState.READY:
        return cfg.ready_score
    if state is ReadinessState.WAITING_MATERIAL:
        return (
            cfg.material_partial_score
            if order.material_status is MaterialStatus.PARTIAL
            else cfg.waiting_material_score
        )
    if state is ReadinessState.WAITING_TOOLING:
        return cfg.waiting_tooling_score
    if state is ReadinessState.WAITING_APPROVAL:
        return cfg.waiting_approval_score
    if state is ReadinessState.WAITING_PREVIOUS_OPERATION:
        return cfg.waiting_previous_operation_score
    if state is ReadinessState.MACHINE_UNAVAILABLE:
        return cfg.machine_unavailable_score
    return cfg.hold_score  # QUALITY_HOLD, ON_HOLD, OTHER_CONSTRAINT


def describe_blockers(state: ReadinessState, blockers: list[Blocker]) -> str:
    label = STATE_LABELS.get(state, state.value)
    if not blockers:
        return label
    parts: list[str] = []
    for blocker in blockers[:MAX_BLOCKERS_IN_REASON]:
        text = blocker.message
        if blocker.resolves_at is not None:
            text += f", expected {describe_when(blocker.resolves_at)}"
        parts.append(text)
    extra = len(blockers) - MAX_BLOCKERS_IN_REASON
    suffix = f" (+{extra} more)" if extra > 0 else ""
    return f"{label}: {'; '.join(parts)}{suffix}"


class ProductionReadiness:
    key = KEY
    name = FACTOR_NAMES[KEY]
    kind: Literal["bonus", "penalty"] = "bonus"

    def score(self, order: Order, ctx: PriorityContext) -> FactorScore:
        cfg = ctx.profile.readiness
        weight = factor_weight(ctx, self.key)
        state = ctx.readiness.get(order.order_id)
        details: dict[str, Any] = {"readiness": state.value if state else None}
        if state is None:
            return make_factor_score(
                self, cfg.ready_score, weight, "Readiness not assessed (assumed ready)", details
            )
        blockers = list(ctx.blockers.get(order.order_id, ()))
        details["blockers"] = [b.message for b in blockers]
        details["resolves_at"] = min(
            (b.resolves_at for b in blockers if b.resolves_at is not None), default=None
        )
        raw = readiness_score(cfg, state, order)
        return make_factor_score(self, raw, weight, describe_blockers(state, blockers), details)


__all__ = ["KEY", "STATE_LABELS", "ProductionReadiness", "describe_blockers", "readiness_score"]
