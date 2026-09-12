"""Priority scenarios: customer prioritisation and weight changes (spec Phases 7, 14).

* ``prioritize_customer`` — "What if I prioritize Customer A?": a customer
  rule (boost points and/or tier override) is written into the snapshot; the
  priority engine's customer-rule adjustment and customer-importance factor
  pick it up through ``snapshot.customer_rules``.
* ``weight_change`` — "What if we increase the priority of high-margin
  orders?": partial weight overrides by factor key, or a whole replacement
  profile. Returns a *new* :class:`PriorityProfile` (Pydantic models are not
  mutated); the snapshot is untouched.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from app.core.errors import ValidationError
from app.domain.config import FACTOR_KEYS, FactorWeight, PriorityProfile
from app.domain.enums import CustomerTier
from app.domain.models import CustomerRule
from app.domain.snapshot import PlanningSnapshot
from app.engines.simulation.base import ScenarioBase, ScenarioEffect


class PrioritizeCustomerScenario(ScenarioBase):
    kind: Literal["prioritize_customer"] = "prioritize_customer"
    customer_id: str
    boost_points: float | None = None
    tier_override: CustomerTier | None = None
    sla_hours: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _something(self) -> PrioritizeCustomerScenario:
        if self.boost_points is None and self.tier_override is None and self.sla_hours is None:
            raise ValueError("prioritize_customer needs 'boost_points', 'tier_override' or 'sla_hours'")
        return self

    def _mutate(self, snapshot: PlanningSnapshot, effect: ScenarioEffect) -> None:
        if self.customer_id not in snapshot.customers:
            raise ValidationError(
                f"prioritize_customer: unknown customer {self.customer_id!r}",
                details={"customer_id": self.customer_id},
            )
        existing = snapshot.customer_rules.get(self.customer_id)
        rule = CustomerRule(
            customer_id=self.customer_id,
            sla_hours=self.sla_hours
            if self.sla_hours is not None
            else (existing.sla_hours if existing else None),
            tier_override=(
                self.tier_override
                if self.tier_override is not None
                else (existing.tier_override if existing else None)
            ),
            priority_boost_points=(
                self.boost_points
                if self.boost_points is not None
                else (existing.priority_boost_points if existing else 0.0)
            ),
            notes="simulated customer rule",
            active=True,
        )
        snapshot.customer_rules[self.customer_id] = rule
        orders = [o.order_id for o in snapshot.open_orders() if o.customer_id == self.customer_id]
        effect.touch_orders(*orders)
        parts: list[str] = []
        if self.boost_points is not None:
            parts.append(f"+{self.boost_points:g} points")
        if self.tier_override is not None:
            parts.append(f"tier {self.tier_override.value}")
        if self.sla_hours is not None:
            parts.append(f"SLA {self.sla_hours:g} h")
        effect.note(f"Customer {self.customer_id}: {', '.join(parts)} ({len(orders)} open order(s))")

    def describe(self) -> str:
        parts: list[str] = []
        if self.boost_points is not None:
            parts.append(f"+{self.boost_points:g} points")
        if self.tier_override is not None:
            parts.append(f"tier → {self.tier_override.value}")
        if self.sla_hours is not None:
            parts.append(f"SLA {self.sla_hours:g} h")
        return f"Prioritize customer {self.customer_id} ({', '.join(parts)})"


class WeightChangeScenario(ScenarioBase):
    """Partial weight overrides (percent by factor key) and/or a full replacement profile."""

    kind: Literal["weight_change"] = "weight_change"
    weights: dict[str, float] = Field(default_factory=dict)
    profile: PriorityProfile | None = None

    @model_validator(mode="after")
    def _valid(self) -> WeightChangeScenario:
        if not self.weights and self.profile is None:
            raise ValueError("weight_change needs 'weights' and/or 'profile'")
        unknown = sorted(k for k in self.weights if k not in FACTOR_KEYS)
        if unknown:
            raise ValueError(f"unknown factor keys: {unknown}")
        negative = sorted(k for k, v in self.weights.items() if v < 0)
        if negative:
            raise ValueError(f"negative weights for: {negative}")
        return self

    def apply_profile(self, profile: PriorityProfile) -> PriorityProfile:
        """The profile the scenario yields for ``profile`` (pure; no snapshot needed)."""
        base = self.profile if self.profile is not None else profile
        if not self.weights:
            return base
        present = {w.key for w in base.weights}
        weights: list[FactorWeight] = []
        for w in base.weights:
            if w.key in self.weights:
                value = self.weights[w.key]
                weights.append(w.model_copy(update={"weight": value, "enabled": w.enabled or value > 0}))
            else:
                weights.append(w)
        for key in FACTOR_KEYS:
            if key in self.weights and key not in present:
                weights.append(FactorWeight(key=key, weight=self.weights[key]))  # type: ignore[arg-type]
        updated = base.model_copy(update={"weights": weights, "name": f"{base.name} [what-if]"})
        try:
            return PriorityProfile.model_validate(updated.model_dump())
        except ValueError as exc:  # e.g. every weight set to zero
            raise ValidationError(f"weight_change: invalid profile: {exc}") from exc

    def _mutate(self, snapshot: PlanningSnapshot, effect: ScenarioEffect) -> None:
        before = effect.profile
        after = self.apply_profile(before)
        effect.profile = after
        before_map, after_map = before.weight_map(), after.weight_map()
        for key in FACTOR_KEYS:
            a, b = before_map.get(key, 0.0) * 100.0, after_map.get(key, 0.0) * 100.0
            if abs(a - b) > 1e-9:
                effect.note(f"{key}: {a:.0f}% → {b:.0f}%")
        if self.profile is not None:
            effect.note(f"profile replaced by {self.profile.profile_id} v{self.profile.version}")

    def describe(self) -> str:
        if self.weights:
            changes = ", ".join(f"{k}={v:g}" for k, v in sorted(self.weights.items()))
            return f"Change priority weights ({changes})"
        return f"Use priority profile {self.profile.profile_id if self.profile else '?'}"


__all__ = ["PrioritizeCustomerScenario", "WeightChangeScenario"]
