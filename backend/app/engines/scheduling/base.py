"""Scheduler interface (see docs/DESIGN_CONTRACT.md §6.4)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Protocol

from app.domain.config import SchedulingConfig
from app.domain.results import PriorityResult, ScheduleResult
from app.domain.snapshot import PlanningSnapshot

if TYPE_CHECKING:
    from app.engines.calendar.calendar import MachineCalendar
    from app.engines.constraints.engine import ConstraintEngine


class Scheduler(Protocol):
    name: str
    version: str

    def schedule(
        self,
        snapshot: PlanningSnapshot,
        priorities: Mapping[str, PriorityResult],
        config: SchedulingConfig,
        calendars: Mapping[str, MachineCalendar],
        constraints: ConstraintEngine,
    ) -> ScheduleResult: ...
