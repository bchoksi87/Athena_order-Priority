"""Factor ``setup_efficiency`` (spec Phase 4 §9 "Setup optimization").

For the order's next operation, the setup needed on each eligible machine is
estimated with the constraint engine's :func:`estimate_setup` (so the
scheduler, the soft constraints and this factor agree on the number). The
*best* machine (least setup; ties broken by basis then id) decides the
score: same setup family → ``same_setup_score``, same material →
``same_material_score``, full changeover → ``changeover_score``, machine
state unknown → ``unknown_score``.

Large setups are penalised inside the same factor (bonus kind only, so the
canonical key stays unique): above ``large_setup_minutes`` the score is
scaled down by ``large_setup_penalty_score / 100 x excess``, where ``excess``
is how far past the threshold the setup is (relative to the threshold,
capped at 1). The reason then says "Large setup required (120 min)".
"""

from __future__ import annotations

from typing import Any, Literal

from app.domain.config import FACTOR_NAMES, SetupEfficiencyScoring
from app.domain.models import Machine, Order
from app.domain.results import FactorScore
from app.engines.constraints.readiness import next_operation
from app.engines.constraints.soft import SetupEstimate, estimate_setup
from app.engines.priority.base import PriorityContext, make_factor_score
from app.engines.priority.context_ext import factor_weight, scheduling_config

KEY = "setup_efficiency"
BASIS_RANK: dict[str, int] = {"same_family": 0, "same_material": 1, "changeover": 2, "unknown": 3}


def basis_score(cfg: SetupEfficiencyScoring, basis: str) -> float:
    return {
        "same_family": cfg.same_setup_score,
        "same_material": cfg.same_material_score,
        "changeover": cfg.changeover_score,
    }.get(basis, cfg.unknown_score)


def large_setup_factor(cfg: SetupEfficiencyScoring, minutes: float) -> float:
    """Multiplier (0..1) applied to the raw score for setups above the large-setup threshold."""
    threshold = cfg.large_setup_minutes
    if threshold <= 0 or minutes <= threshold:
        return 1.0
    excess = min(1.0, (minutes - threshold) / threshold)
    return max(0.0, 1.0 - excess * cfg.large_setup_penalty_score / 100.0)


def best_setup(
    order: Order, machines: list[Machine], ctx: PriorityContext
) -> tuple[Machine, SetupEstimate] | None:
    """Eligible machine with the smallest estimated setup for the order's next operation."""
    if not machines:
        return None
    op, _synthetic = next_operation(order, ctx.snapshot)
    config = scheduling_config(ctx)
    best: tuple[tuple[float, int, str], Machine, SetupEstimate] | None = None
    for machine in machines:
        est = estimate_setup(op, order, machine, None, config)
        key = (est.minutes, BASIS_RANK.get(est.basis, 9), machine.machine_id)
        if best is None or key < best[0]:
            best = (key, machine, est)
    return (best[1], best[2]) if best is not None else None


class SetupEfficiency:
    key = KEY
    name = FACTOR_NAMES[KEY]
    kind: Literal["bonus", "penalty"] = "bonus"

    def score(self, order: Order, ctx: PriorityContext) -> FactorScore:
        cfg = ctx.profile.setup
        weight = factor_weight(ctx, self.key)
        machines = ctx.eligible_machines.get(order.order_id, [])
        choice = best_setup(order, machines, ctx)
        if choice is None:
            return make_factor_score(
                self, cfg.unknown_score, weight, "No eligible machine to assess setup", {}
            )
        machine, est = choice
        raw = basis_score(cfg, est.basis)
        factor = large_setup_factor(cfg, est.minutes)
        details: dict[str, Any] = {
            "machine_id": machine.machine_id,
            "large_setup_factor": factor,
            **est.to_details(),
        }
        reason = {
            "same_family": f"Same setup family on {machine.machine_id} ({est.reason})",
            "same_material": f"Same material on {machine.machine_id} ({est.reason})",
            "changeover": f"Changeover on {machine.machine_id}: {est.minutes:g} min setup",
        }.get(est.basis, f"Machine state unknown on {machine.machine_id}: {est.minutes:g} min setup assumed")
        if factor < 1.0:
            raw *= factor
            reason = f"Large setup required ({est.minutes:g} min) on {machine.machine_id}; {est.reason}"
        return make_factor_score(self, raw, weight, reason, details)


__all__ = ["KEY", "SetupEfficiency", "basis_score", "best_setup", "large_setup_factor"]
