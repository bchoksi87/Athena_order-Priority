"""Shared building blocks for what-if scenarios (spec Phase 7).

Every scenario is a Pydantic model discriminated on ``kind``. Two entry points:

* :meth:`ScenarioBase.apply` — the contract method: clone the snapshot, apply
  the scenario, return ``(snapshot, profile, config)``. The caller's snapshot
  is never touched.
* :meth:`ScenarioBase.apply_to` — the in-place variant on a snapshot the
  caller has *already* cloned. It returns a :class:`ScenarioEffect` with the
  notes and touched entity ids so the simulation engine (which applies a list
  of scenarios to one clone) can report what each scenario changed without
  deep-copying thousands of orders once per scenario.

Scenarios raise :class:`~app.core.errors.ValidationError` for references that
do not exist in the snapshot (unknown machine, order, material, calendar) —
they are user input, and a silent no-op would make the "what happens if"
answer meaningless.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.core.clock import ensure_utc
from app.core.errors import ValidationError
from app.domain.config import PriorityProfile, SchedulingConfig
from app.domain.models import Order
from app.domain.snapshot import PlanningSnapshot

#: Key under ``Order.attributes`` / ``Machine.attributes`` where scenarios leave
#: a trail of what they changed (readable by explanations and the UI).
SIMULATION_NOTES_KEY = "simulation_notes"


@dataclass(slots=True)
class ScenarioEffect:
    """What one scenario changed on the (cloned) snapshot, profile and config."""

    kind: str
    description: str
    profile: PriorityProfile
    config: SchedulingConfig
    notes: list[str] = field(default_factory=list)
    affected_order_ids: list[str] = field(default_factory=list)
    affected_machine_ids: list[str] = field(default_factory=list)

    def note(self, text: str) -> None:
        self.notes.append(text)

    def touch_orders(self, *order_ids: str) -> None:
        for oid in order_ids:
            if oid not in self.affected_order_ids:
                self.affected_order_ids.append(oid)

    def touch_machines(self, *machine_ids: str) -> None:
        for mid in machine_ids:
            if mid not in self.affected_machine_ids:
                self.affected_machine_ids.append(mid)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "description": self.description,
            "notes": list(self.notes),
            "affected_order_ids": list(self.affected_order_ids),
            "affected_machine_ids": list(self.affected_machine_ids),
        }


class ScenarioBase(BaseModel):
    """Base of every scenario: ``kind`` discriminator, ``apply``, ``describe``."""

    model_config = ConfigDict(extra="forbid")
    #: Discriminator; every concrete scenario narrows it to a ``Literal``.
    kind: str = ""

    #: Optional planner-facing label ("CNC-07 spindle failure").
    label: str | None = None

    # ------------------------------------------------------------ contract
    def apply(
        self, snapshot: PlanningSnapshot, profile: PriorityProfile, config: SchedulingConfig
    ) -> tuple[PlanningSnapshot, PriorityProfile, SchedulingConfig]:
        """Apply on a clone; the given ``snapshot`` is left untouched."""
        clone = snapshot.clone()
        effect = self.apply_to(clone, profile, config)
        return clone, effect.profile, effect.config

    def apply_to(
        self, snapshot: PlanningSnapshot, profile: PriorityProfile, config: SchedulingConfig
    ) -> ScenarioEffect:
        """Mutate ``snapshot`` (already a clone) in place; return what changed."""
        effect = ScenarioEffect(kind=self.kind, description=self.describe(), profile=profile, config=config)
        self._mutate(snapshot, effect)
        snapshot.rebuild_indexes()
        return effect

    @abstractmethod
    def _mutate(self, snapshot: PlanningSnapshot, effect: ScenarioEffect) -> None: ...

    @abstractmethod
    def describe(self) -> str: ...

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready representation (``kind`` included) for :class:`SimulationResult`."""
        data = self.model_dump(mode="json")
        data["kind"] = self.kind
        return data


# ----------------------------------------------------------------- helpers


def require_order(snapshot: PlanningSnapshot, order_id: str, scenario: str) -> Order:
    order = snapshot.orders.get(order_id)
    if order is None:
        raise ValidationError(
            f"{scenario}: unknown order {order_id!r}", details={"order_id": order_id, "scenario": scenario}
        )
    return order


def require_machine_id(snapshot: PlanningSnapshot, machine_id: str, scenario: str) -> str:
    if machine_id not in snapshot.machines:
        raise ValidationError(
            f"{scenario}: unknown machine {machine_id!r}",
            details={"machine_id": machine_id, "scenario": scenario},
        )
    return machine_id


def require_material_id(snapshot: PlanningSnapshot, material_id: str, scenario: str) -> str:
    if material_id not in snapshot.materials:
        raise ValidationError(
            f"{scenario}: unknown material {material_id!r}",
            details={"material_id": material_id, "scenario": scenario},
        )
    return material_id


def add_note(target: dict[str, Any], text: str) -> None:
    """Append ``text`` to the entity's simulation-notes list (created on demand)."""
    notes = target.get(SIMULATION_NOTES_KEY)
    if not isinstance(notes, list):
        notes = []
        target[SIMULATION_NOTES_KEY] = notes
    notes.append(text)


def utc_or_none(value: datetime | None) -> datetime | None:
    return ensure_utc(value) if value is not None else None


def fmt_dt(value: datetime) -> str:
    return ensure_utc(value).strftime("%Y-%m-%d %H:%M")


__all__ = [
    "SIMULATION_NOTES_KEY",
    "ScenarioBase",
    "ScenarioEffect",
    "add_note",
    "fmt_dt",
    "require_machine_id",
    "require_material_id",
    "require_order",
    "utc_or_none",
]
