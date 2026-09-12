"""Factor ``machine_availability`` (spec Phase 4 §8).

Looks at the earliest ``next_free`` instant among the machines eligible for
the order's next operation:

* free now (``≤ now``) → ``available_now_score``;
* free within ``available_within_hours`` → linear between the now-score and
  ``available_soon_score``;
* later → ``available_soon_score`` decaying hyperbolically
  (``x within / wait``) down to ``none_available_score`` — continuous and
  monotone without another threshold;
* no eligible machine (or none with a known return) → ``none_available_score``.

When exactly one machine can make the part and it is free, the configured
``single_machine_bonus`` is added ("increase urgency when that machine
becomes available").
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from app.domain.config import FACTOR_NAMES, MachineAvailabilityScoring
from app.domain.models import Order
from app.domain.results import FactorScore
from app.engines.priority.base import PriorityContext, make_factor_score
from app.engines.priority.context_ext import factor_weight
from app.engines.priority.factors.common import describe_hours, interpolate

KEY = "machine_availability"


def availability_score(cfg: MachineAvailabilityScoring, wait_hours: float) -> float:
    """Raw score for a machine becoming free in ``wait_hours`` (≤0 = free now)."""
    if wait_hours <= 0:
        return cfg.available_now_score
    within = cfg.available_within_hours
    if within <= 0:
        return max(cfg.none_available_score, cfg.available_soon_score)
    if wait_hours <= within:
        return interpolate([(0.0, cfg.available_now_score), (within, cfg.available_soon_score)], wait_hours)
    return max(cfg.none_available_score, cfg.available_soon_score * within / wait_hours)


class MachineAvailability:
    key = KEY
    name = FACTOR_NAMES[KEY]
    kind: Literal["bonus", "penalty"] = "bonus"

    def score(self, order: Order, ctx: PriorityContext) -> FactorScore:
        cfg = ctx.profile.machine_availability
        weight = factor_weight(ctx, self.key)
        machines = ctx.eligible_machines.get(order.order_id, [])
        details: dict[str, Any] = {"eligible_machine_ids": [m.machine_id for m in machines]}
        if not machines:
            return make_factor_score(self, cfg.none_available_score, weight, "No eligible machine", details)
        known: list[tuple[datetime, str]] = [
            (ctx.machine_next_free[m.machine_id], m.machine_id)
            for m in machines
            if m.machine_id in ctx.machine_next_free
        ]
        if not known:
            return make_factor_score(
                self,
                cfg.none_available_score,
                weight,
                f"{len(machines)} eligible machine(s) but availability unknown",
                details,
            )
        earliest, machine_id = min(known)
        wait = (earliest - ctx.now).total_seconds() / 3600.0
        raw = availability_score(cfg, wait)
        details.update(
            {"earliest_machine_id": machine_id, "earliest_free_at": earliest, "wait_hours": max(0.0, wait)}
        )
        single = len(machines) == 1
        if wait <= 0:
            reason = f"Machine {machine_id} available now"
        else:
            reason = f"Earliest machine {machine_id} free in {describe_hours(wait)}"
        if single:
            reason = f"Only machine {machine_id} can make this part; " + (
                "it is free now" if wait <= 0 else f"free in {describe_hours(wait)}"
            )
            if wait <= 0 and cfg.single_machine_bonus:
                raw += cfg.single_machine_bonus
                details["single_machine_bonus"] = cfg.single_machine_bonus
        return make_factor_score(self, raw, weight, reason, details)


__all__ = ["KEY", "MachineAvailability", "availability_score"]
