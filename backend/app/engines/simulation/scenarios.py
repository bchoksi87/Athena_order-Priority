"""The ``Scenario`` union (DESIGN_CONTRACT §6.5) and helpers to parse / apply lists of scenarios.

``Scenario`` is a Pydantic discriminated union on ``kind``; API schemas can
embed it directly. :func:`parse_scenario` turns a dict into the right model,
:func:`apply_scenarios` applies a list in order on one clone of the snapshot
and returns the accumulated effects (used by :class:`SimulationEngine`).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Annotated, Any

from pydantic import Field, TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from app.core.errors import ValidationError
from app.domain.config import PriorityProfile, SchedulingConfig
from app.domain.snapshot import PlanningSnapshot
from app.engines.simulation.base import ScenarioBase, ScenarioEffect
from app.engines.simulation.scenarios_orders import (
    DueDateChangeScenario,
    ExpediteOrdersScenario,
    HoldOrdersScenario,
    OutsourceScenario,
    RouteStep,
    UrgentOrderSpec,
    UrgentOrdersScenario,
)
from app.engines.simulation.scenarios_priority import PrioritizeCustomerScenario, WeightChangeScenario
from app.engines.simulation.scenarios_resources import (
    AddMachineScenario,
    ExtraShiftScenario,
    ExtraWorkingDayScenario,
    MachineDownScenario,
    MaterialArrivalScenario,
    MaterialDelayScenario,
)

Scenario = Annotated[
    MachineDownScenario
    | UrgentOrdersScenario
    | AddMachineScenario
    | ExtraWorkingDayScenario
    | ExtraShiftScenario
    | OutsourceScenario
    | MaterialDelayScenario
    | MaterialArrivalScenario
    | PrioritizeCustomerScenario
    | WeightChangeScenario
    | DueDateChangeScenario
    | HoldOrdersScenario
    | ExpediteOrdersScenario,
    Field(discriminator="kind"),
]

SCENARIO_KINDS: tuple[str, ...] = (
    "machine_down",
    "urgent_orders",
    "add_machine",
    "extra_working_day",
    "extra_shift",
    "outsource",
    "material_delay",
    "material_arrival",
    "prioritize_customer",
    "weight_change",
    "due_date_change",
    "hold_orders",
    "expedite_orders",
)

_ADAPTER: TypeAdapter[Any] = TypeAdapter(Scenario)


def parse_scenario(data: Mapping[str, Any] | ScenarioBase) -> ScenarioBase:
    """Dict (with ``kind``) → concrete scenario; raises :class:`ValidationError` on bad input."""
    if isinstance(data, ScenarioBase):
        return data
    try:
        parsed = _ADAPTER.validate_python(dict(data))
    except PydanticValidationError as exc:
        raise ValidationError(
            "invalid scenario", details={"errors": exc.errors(include_url=False), "kind": data.get("kind")}
        ) from exc
    assert isinstance(parsed, ScenarioBase)
    return parsed


def parse_scenarios(items: Iterable[Mapping[str, Any] | ScenarioBase]) -> list[ScenarioBase]:
    return [parse_scenario(item) for item in items]


def apply_scenarios(
    snapshot: PlanningSnapshot,
    scenarios: Sequence[ScenarioBase],
    profile: PriorityProfile,
    config: SchedulingConfig,
) -> tuple[PlanningSnapshot, PriorityProfile, SchedulingConfig, list[ScenarioEffect]]:
    """Apply ``scenarios`` in order on one clone; the input snapshot is never mutated."""
    clone = snapshot.clone()
    effects: list[ScenarioEffect] = []
    for scenario in scenarios:
        effect = scenario.apply_to(clone, profile, config)
        profile, config = effect.profile, effect.config
        effects.append(effect)
    return clone, profile, config, effects


__all__ = [
    "SCENARIO_KINDS",
    "AddMachineScenario",
    "DueDateChangeScenario",
    "ExpediteOrdersScenario",
    "ExtraShiftScenario",
    "ExtraWorkingDayScenario",
    "HoldOrdersScenario",
    "MachineDownScenario",
    "MaterialArrivalScenario",
    "MaterialDelayScenario",
    "OutsourceScenario",
    "PrioritizeCustomerScenario",
    "RouteStep",
    "Scenario",
    "ScenarioBase",
    "ScenarioEffect",
    "UrgentOrderSpec",
    "UrgentOrdersScenario",
    "WeightChangeScenario",
    "apply_scenarios",
    "parse_scenario",
    "parse_scenarios",
]
