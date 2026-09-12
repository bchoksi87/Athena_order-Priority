"""Fixtures for engine tests. Independent of ``tests/conftest.py`` by design."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.domain.config import DataQualityConfig, SchedulingConfig
from app.domain.enums import ProcessType
from app.domain.snapshot import PlanningSnapshot
from app.engines.calendar import MachineCalendar
from app.engines.constraints import ConstraintEngine, default_constraint_engine

if TYPE_CHECKING:
    from app.core.clock import FrozenClock
    from app.engines.scheduling.rule_based import RuleBasedScheduler
from tests.engines.factories import (
    NOW,
    make_24x7_spec,
    make_calendar_spec,
    make_customer,
    make_machine,
    make_material,
    make_order_with_routing,
    make_snapshot,
    make_tooling,
)


@pytest.fixture
def scheduling_config() -> SchedulingConfig:
    return SchedulingConfig()


@pytest.fixture
def constraint_engine(scheduling_config: SchedulingConfig) -> ConstraintEngine:
    return default_constraint_engine(scheduling_config)


@pytest.fixture
def day_calendar() -> MachineCalendar:
    """08:00-16:00 UTC, Monday to Friday, no downtime."""
    return MachineCalendar(make_calendar_spec())


@pytest.fixture
def always_calendar() -> MachineCalendar:
    return MachineCalendar(make_24x7_spec())


# ---------------------------------------------------------------- data quality


@pytest.fixture
def dq_config() -> DataQualityConfig:
    return DataQualityConfig()


@pytest.fixture
def clean_snapshot() -> PlanningSnapshot:
    """One valid open order (CNC -> deburring) on a small, fully consistent plant: zero DQ issues."""
    order, ops = make_order_with_routing("O1", steps=(ProcessType.CNC_MACHINING, ProcessType.DEBURRING))
    return make_snapshot(
        orders=[order],
        operations=ops,
        machines=[
            make_machine("M1", ProcessType.CNC_MACHINING, "CNC", calendar_id="CAL1"),
            make_machine("M2", ProcessType.CNC_MACHINING, "CNC", calendar_id="CAL1", preferred_rank=1),
            make_machine("P1", ProcessType.ADDITIVE_3D_PRINTING, "PRINT", calendar_id="CAL1"),
            make_machine("D1", ProcessType.DEBURRING, "DEBURRING", calendar_id="CAL1"),
        ],
        customers=[make_customer("C1")],
        materials=[make_material("MAT1"), make_material("POWDER1")],
        tooling=[make_tooling("T1")],
        calendars=[make_calendar_spec("CAL1")],
        default_calendar_id="CAL1",
        as_of=NOW,
    )


# ------------------------------------------------------------------ scheduling


@pytest.fixture
def clock() -> FrozenClock:
    from app.core.clock import FrozenClock

    return FrozenClock(NOW)


@pytest.fixture
def scheduler(clock: FrozenClock) -> RuleBasedScheduler:
    from app.engines.scheduling.rule_based import RuleBasedScheduler

    return RuleBasedScheduler(clock)
