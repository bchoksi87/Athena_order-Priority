"""SimulationEngine (DESIGN_CONTRACT §6.5, spec Phase 7).

``run`` answers "what happens if ...?" without touching the real plan:

1. **baseline** — priorities and schedule on the untouched snapshot (the
   plan the plant would follow today);
2. **scenario**  — every scenario applied in order on *one clone* of the
   snapshot (profile and config flow through, since ``weight_change`` returns
   a new profile); calendars and the constraint engine are rebuilt from the
   mutated snapshot / config because machines, calendars and materials may
   have changed;
3. **diff**      — :func:`diff_schedules` compares the two plans.

Both plans go through exactly the same code path (:meth:`plan`): the same
priority engine, context builder, constraint engine and scheduler, so a
difference in the output is caused by the scenario and nothing else. The
priority context is built with the *same* calendars and constraint engine
the scheduler uses, so the two engines agree on machine availability and
readiness. Time comes from the injected clock and the snapshot's ``as_of``;
nothing reads the wall clock.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import structlog

from app.core.clock import Clock, ensure_utc
from app.core.ids import new_id
from app.domain.config import PriorityProfile, SchedulingConfig
from app.domain.results import PriorityResult, ScheduleEntry, ScheduleResult, SimulationResult
from app.domain.snapshot import PlanningSnapshot
from app.engines.calendar.builder import build_calendars
from app.engines.calendar.calendar import MachineCalendar
from app.engines.constraints.engine import ConstraintEngine
from app.engines.constraints.registry import default_constraint_engine
from app.engines.priority.context import PriorityContextBuilder
from app.engines.priority.engine import PriorityEngine
from app.engines.simulation.base import ScenarioBase, ScenarioEffect
from app.engines.simulation.diff import BottleneckFn, diff_schedules
from app.engines.simulation.scenarios import apply_scenarios, parse_scenarios

log = structlog.get_logger(__name__)

ConstraintEngineFactory = Callable[[SchedulingConfig], ConstraintEngine]
CalendarBuilder = Callable[[PlanningSnapshot], Mapping[str, MachineCalendar]]


class SimulationScheduler(Protocol):
    """A :class:`~app.engines.scheduling.base.Scheduler` that also accepts ``previous_entries``."""

    name: str
    version: str

    def schedule(
        self,
        snapshot: PlanningSnapshot,
        priorities: Mapping[str, PriorityResult],
        config: SchedulingConfig,
        calendars: Mapping[str, MachineCalendar],
        constraints: ConstraintEngine,
        *,
        previous_entries: Sequence[ScheduleEntry] | None = None,
    ) -> ScheduleResult: ...


@dataclass(slots=True)
class PlanOutcome:
    """Priorities + schedule for one snapshot, with the collaborators that produced them."""

    snapshot: PlanningSnapshot
    profile: PriorityProfile
    config: SchedulingConfig
    priorities: dict[str, PriorityResult]
    schedule: ScheduleResult
    calendars: Mapping[str, MachineCalendar]
    constraints: ConstraintEngine

    @property
    def scores(self) -> dict[str, float]:
        return {oid: r.score for oid, r in sorted(self.priorities.items())}


class SimulationEngine:
    def __init__(
        self,
        priority_engine: PriorityEngine,
        scheduler: SimulationScheduler,
        constraint_engine_factory: ConstraintEngineFactory = default_constraint_engine,
        calendar_builder: CalendarBuilder = build_calendars,
        clock: Clock | None = None,
        *,
        currency: str = "INR",
        bottleneck_fn: BottleneckFn | None = None,
    ) -> None:
        self.priority_engine = priority_engine
        self.scheduler = scheduler
        self.constraint_engine_factory = constraint_engine_factory
        self.calendar_builder = calendar_builder
        self.clock = clock
        self.currency = currency
        self.bottleneck_fn = bottleneck_fn

    # ------------------------------------------------------------------ plan
    def plan(
        self,
        snapshot: PlanningSnapshot,
        profile: PriorityProfile,
        config: SchedulingConfig,
        previous_entries: Sequence[ScheduleEntry] | None = None,
    ) -> PlanOutcome:
        """Priorities + schedule for ``snapshot`` (used for baseline and scenario alike)."""
        constraints = self.constraint_engine_factory(config)
        calendars = self.calendar_builder(snapshot)
        builder = PriorityContextBuilder(constraint_engine=constraints, calendars=calendars, clock=self.clock)
        ctx = builder.build(snapshot, profile)
        priorities = self.priority_engine.evaluate(snapshot, profile, ctx=ctx)
        schedule = self.scheduler.schedule(
            snapshot, priorities, config, calendars, constraints, previous_entries=previous_entries
        )
        return PlanOutcome(snapshot, profile, config, priorities, schedule, calendars, constraints)

    # ------------------------------------------------------------------- run
    def run(
        self,
        snapshot: PlanningSnapshot,
        scenarios: Sequence[ScenarioBase | Mapping[str, Any]],
        profile: PriorityProfile,
        config: SchedulingConfig,
        previous_entries: Sequence[ScheduleEntry] | None = None,
    ) -> SimulationResult:
        """Baseline vs. scenario plans plus their diff; ``snapshot`` is never mutated."""
        parsed = parse_scenarios(scenarios)
        generated_at = ensure_utc(self.clock.now()) if self.clock is not None else ensure_utc(snapshot.as_of)
        simulation_id = new_id("sim")
        log.info(
            "simulation.start",
            simulation_id=simulation_id,
            scenarios=[s.kind for s in parsed],
            orders=len(snapshot.orders),
            machines=len(snapshot.machines),
        )
        baseline = self.plan(snapshot, profile, config, previous_entries)
        what_if, profile_after, config_after, effects = apply_scenarios(snapshot, parsed, profile, config)
        scenario = self.plan(what_if, profile_after, config_after, previous_entries)
        diff = diff_schedules(
            baseline.schedule,
            scenario.schedule,
            snapshot,
            what_if,
            baseline.priorities,
            scenario.priorities,
            self.bottleneck_fn,
            currency=self.currency,
        )
        result = SimulationResult(
            simulation_id=simulation_id,
            scenarios=[_scenario_record(s, e) for s, e in zip(parsed, effects, strict=True)],
            baseline=baseline.schedule,
            scenario=scenario.schedule,
            diff=diff,
            baseline_priorities=baseline.scores,
            scenario_priorities=scenario.scores,
            generated_at=generated_at,
        )
        log.info(
            "simulation.done",
            simulation_id=simulation_id,
            orders_affected=diff.orders_affected,
            late_before=diff.late_orders_before,
            late_after=diff.late_orders_after,
            summary=diff.summary,
        )
        return result

    def describe(self) -> dict[str, Any]:
        return {
            "scheduler": getattr(self.scheduler, "name", type(self.scheduler).__name__),
            "scheduler_version": getattr(self.scheduler, "version", "?"),
            "priority_factors": self.priority_engine.describe().get("factors", []),
            "currency": self.currency,
            "bottleneck_fn": "injected" if self.bottleneck_fn is not None else "default_utilisation_top3",
        }


def _scenario_record(scenario: ScenarioBase, effect: ScenarioEffect) -> dict[str, Any]:
    record = scenario.to_dict()
    record["description"] = effect.description
    record["notes"] = list(effect.notes)
    record["affected_order_ids"] = list(effect.affected_order_ids)
    record["affected_machine_ids"] = list(effect.affected_machine_ids)
    return record


__all__ = [
    "CalendarBuilder",
    "ConstraintEngineFactory",
    "PlanOutcome",
    "SimulationEngine",
    "SimulationScheduler",
]
