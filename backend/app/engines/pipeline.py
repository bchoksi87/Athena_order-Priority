"""PlanningPipeline: the pure-engine planning flow, with no persistence.

One :meth:`PlanningPipeline.run` turns a :class:`PlanningSnapshot` plus a
:class:`SystemConfig` into everything the services, API and CLI need::

    data quality → calendars → constraint engine → priority context + results
    → schedule (previous entries feed the frozen window) → quality → analytics
    (executive KPIs, weekly capacity per machine group, bottlenecks, alerts)

Every stage is timed (``PipelineResult.timings``, seconds) and logged with the
``run_id`` bound, so a slow run can be attributed to one engine. Orders that
carry a *blocking* data-quality issue are evaluated by the priority engine
(they still appear in the queue, with their blockers) but are withheld from
the scheduler; they come back as ``UnscheduledItem(reason_code="data_quality")``
so the schedule accounts for every open order.

The pipeline owns no state between runs and reads no clock but the injected
one, so a frozen clock makes ``run`` reproducible (only ``run_id`` differs).
:meth:`PlanningPipeline.simulate` hands the same priority engine, scheduler,
constraint factory and calendar builder to the :class:`SimulationEngine`, so a
what-if baseline is the plan ``run`` would produce.
"""

from __future__ import annotations

import inspect
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog

from app.core.clock import Clock, ensure_utc
from app.core.errors import NotFoundError
from app.core.ids import new_id
from app.domain.config import DataQualityConfig, SchedulingConfig, SystemConfig
from app.domain.results import (
    Alert,
    Bottleneck,
    DataQualityIssue,
    ExecutiveKpis,
    MachineRecommendation,
    PriorityResult,
    ScheduleEntry,
    ScheduleResult,
    SimulationResult,
    UnscheduledItem,
)
from app.domain.snapshot import PlanningSnapshot
from app.engines.analytics import compute_capacity, compute_executive_kpis, evaluate_alerts, find_bottlenecks
from app.engines.analytics.capacity import CapacityReport
from app.engines.calendar import MachineCalendar, build_calendars
from app.engines.constraints import ConstraintEngine, default_constraint_engine
from app.engines.data_quality import DataQualityEngine, DataQualityReport
from app.engines.priority import PriorityContextBuilder, PriorityEngine, PriorityFactor, default_factors
from app.engines.scheduling import (
    Scheduler,
    SchedulerRegistry,
    compute_metrics,
    compute_quality,
    default_registry,
)
from app.engines.simulation import ScenarioBase, SimulationEngine

log = structlog.get_logger(__name__)

DATA_QUALITY_REASON_CODE = "data_quality"
_NO_PRIORITY_REASON_CODE = "no_priority"
_MAX_ISSUES_IN_REASON = 3


# ------------------------------------------------------------------ results


@dataclass(slots=True)
class PipelineResult:
    """Everything one planning run produced (see module docstring)."""

    run_id: str
    generated_at: datetime
    snapshot: PlanningSnapshot
    dq_report: DataQualityReport
    calendars: Mapping[str, MachineCalendar]
    priorities: dict[str, PriorityResult]
    schedule: ScheduleResult
    kpis: ExecutiveKpis
    capacity: CapacityReport
    bottlenecks: list[Bottleneck]
    alerts: list[Alert]
    timings: dict[str, float]  # stage -> seconds (plus "total")
    profile_id: str
    profile_version: int
    config_id: str
    config_version: int
    scheduler_name: str
    scheduler_version: str
    dq_excluded_order_ids: list[str] = field(default_factory=list)

    @property
    def total_seconds(self) -> float:
        return self.timings.get("total", sum(self.timings.values()))

    def summary(self) -> dict[str, Any]:
        """Headline numbers for logs, the CLI and the optimisation-run record."""
        metrics = self.schedule.metrics
        return {
            "run_id": self.run_id,
            "generated_at": self.generated_at.isoformat(),
            "scheduler": f"{self.scheduler_name} {self.scheduler_version}",
            "profile": f"{self.profile_id} v{self.profile_version}",
            "config": f"{self.config_id} v{self.config_version}",
            "orders_considered": len(self.priorities),
            "orders_scheduled": metrics.scheduled_orders,
            "orders_unscheduled": metrics.unscheduled_orders,
            "orders_blocked_by_data_quality": len(self.dq_excluded_order_ids),
            "entries": len(self.schedule.entries),
            "on_time_pct": metrics.on_time_pct,
            "quality_score": self.schedule.quality.score if self.schedule.quality is not None else None,
            "alerts": len(self.alerts),
            "bottlenecks": len(self.bottlenecks),
            "timings": {k: round(v, 4) for k, v in self.timings.items()},
        }


@dataclass(slots=True)
class OrderExplanation:
    """Spec Phase 34/35 view of one order: score breakdown, placement and machine choice."""

    order_id: str
    priority: PriorityResult
    entries: list[ScheduleEntry]
    recommendation: MachineRecommendation | None
    unscheduled: list[UnscheduledItem]
    data_quality: list[DataQualityIssue]
    text: str


def explain_order(result: PipelineResult, order_id: str) -> OrderExplanation:
    """Priority result, schedule entries and machine recommendation of ``order_id``.

    The text is assembled from the objects returned alongside it (the priority
    explanation rendered by the priority engine, the scheduler's placement
    reasons, the data-quality messages) — never written independently.
    """
    priority = result.priorities.get(order_id)
    if priority is None:
        known = order_id in result.snapshot.orders
        raise NotFoundError(
            f"no priority result for order {order_id!r}" + (" (order is not open)" if known else ""),
            details={"order_id": order_id, "run_id": result.run_id},
        )
    entries = result.schedule.entries_for_order(order_id)
    unscheduled = [u for u in result.schedule.unscheduled if u.order_id == order_id]
    issues = result.dq_report.issues_for(order_id)
    lines = [priority.explanation]
    if entries:
        lines.append("Schedule:")
        lines.extend(
            f"{e.operation_id} on {e.machine_id} {e.setup_start.isoformat()} → {e.end.isoformat()}"
            f"{' [locked]' if e.locked else ''}: {e.placement_reason}"
            for e in entries
        )
    for item in unscheduled:
        lines.append(f"Not scheduled ({item.reason_code}): {item.reason}")
    for issue in issues:
        lines.append(f"Data quality ({issue.severity.value}, {issue.code.value}): {issue.message}")
    return OrderExplanation(
        order_id=order_id,
        priority=priority,
        entries=entries,
        recommendation=result.schedule.machine_recommendations.get(order_id),
        unscheduled=unscheduled,
        data_quality=issues,
        text="\n".join(lines),
    )


# ----------------------------------------------------------------- adapters


class SchedulerAdapter:
    """The pipeline's scheduling step over any registry :class:`Scheduler`.

    * ``previous_entries``: the contract signature (§6.4) has none; the rule-based
      scheduler accepts it as a keyword. The adapter forwards it when the wrapped
      scheduler supports it and warns (instead of failing) when it does not.
    * data-quality gate: with ``data_quality=(engine, config)`` the adapter runs
      the Data Quality Engine on the snapshot (or takes a ``dq_report`` computed by
      the caller), withholds the orders with blocking issues from the scheduler and
      reports them as ``data_quality`` unscheduled items. The simulation engine
      calls this adapter for baseline and what-if plans alike, so both apply the
      same gate as :meth:`PlanningPipeline.run`.
    """

    def __init__(
        self,
        scheduler: Scheduler,
        data_quality: tuple[DataQualityEngine, DataQualityConfig] | None = None,
    ) -> None:
        self.inner = scheduler
        self.name = scheduler.name
        self.version = scheduler.version
        self.data_quality = data_quality
        self._accepts_previous = "previous_entries" in inspect.signature(scheduler.schedule).parameters

    def schedule(
        self,
        snapshot: PlanningSnapshot,
        priorities: Mapping[str, PriorityResult],
        config: SchedulingConfig,
        calendars: Mapping[str, MachineCalendar],
        constraints: ConstraintEngine,
        *,
        previous_entries: Sequence[ScheduleEntry] | None = None,
        dq_report: DataQualityReport | None = None,
    ) -> ScheduleResult:
        if dq_report is None and self.data_quality is not None:
            engine, dq_config = self.data_quality
            dq_report = engine.run(snapshot, dq_config)
        excluded = list(dq_report.unschedulable_order_ids) if dq_report is not None else []
        schedulable = priorities
        if excluded:
            withheld = set(excluded)
            schedulable = {oid: r for oid, r in priorities.items() if oid not in withheld}
        if previous_entries and self._accepts_previous:
            result = self.inner.schedule(  # type: ignore[call-arg]
                snapshot, schedulable, config, calendars, constraints, previous_entries=previous_entries
            )
        else:
            if previous_entries:
                log.warning("pipeline.previous_entries_ignored", scheduler=self.name)
            result = self.inner.schedule(snapshot, schedulable, config, calendars, constraints)
        if dq_report is not None and excluded:
            withhold_data_quality_blocked(result, dq_report, excluded, priorities)
        return result


def withhold_data_quality_blocked(
    schedule: ScheduleResult,
    report: DataQualityReport,
    excluded_order_ids: Sequence[str],
    priorities: Mapping[str, PriorityResult],
) -> None:
    """Relabel the scheduler's ``no_priority`` items of DQ-excluded orders as ``data_quality`` items.

    The pipeline withholds those orders' priority results from the scheduler,
    which reports them as ``no_priority``; the schedule must instead say *why*
    (the blocking issues). Orders already reported for another reason (e.g.
    ``status_not_schedulable``) keep that item. Metrics are unaffected: the
    set of unscheduled orders does not change, only the reason code.
    """
    excluded = set(excluded_order_ids)
    kept = [
        u
        for u in schedule.unscheduled
        if not (u.order_id in excluded and u.reason_code == _NO_PRIORITY_REASON_CODE)
    ]
    reported = {u.order_id for u in kept}
    for order_id in sorted(excluded):
        if order_id in reported:
            continue
        issues = report.blocking_issues_for(order_id)
        messages = "; ".join(i.message for i in issues[:_MAX_ISSUES_IN_REASON])
        if len(issues) > _MAX_ISSUES_IN_REASON:
            messages += f" (+{len(issues) - _MAX_ISSUES_IN_REASON} more)"
        priority = priorities.get(order_id)
        kept.append(
            UnscheduledItem(
                order_id,
                None,
                DATA_QUALITY_REASON_CODE,
                f"blocked by data quality ({len(issues)} issue(s)): {messages}",
                priority.readiness if priority is not None else None,
            )
        )
    schedule.unscheduled = sorted(kept, key=lambda u: (u.order_id, u.operation_id or ""))


@contextmanager
def _stage(timings: dict[str, float], logger: Any, name: str) -> Iterator[None]:
    started = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - started
        timings[name] = elapsed
        logger.info("pipeline.stage", stage=name, seconds=round(elapsed, 4))


# ----------------------------------------------------------------- pipeline


class PlanningPipeline:
    """Orchestrates the engines for one snapshot (see module docstring)."""

    def __init__(
        self,
        clock: Clock,
        factors: Sequence[PriorityFactor] | None = None,
        scheduler_name: str = "rule_based",
        registry: SchedulerRegistry | None = None,
        data_quality_engine: DataQualityEngine | None = None,
    ) -> None:
        self.clock = clock
        self.registry = registry if registry is not None else default_registry(clock)
        self.scheduler = SchedulerAdapter(self.registry.create(scheduler_name, clock))
        self.priority_engine = PriorityEngine(
            list(factors) if factors is not None else default_factors(), clock
        )
        self.data_quality_engine = (
            data_quality_engine if data_quality_engine is not None else DataQualityEngine()
        )

    # ---------------------------------------------------------------- run
    def run(
        self,
        snapshot: PlanningSnapshot,
        system_config: SystemConfig,
        previous_entries: Sequence[ScheduleEntry] | None = None,
    ) -> PipelineResult:
        """Full planning flow on ``snapshot``; ``previous_entries`` feed the frozen lock window."""
        run_id = new_id("run")
        now = ensure_utc(self.clock.now())
        profile, scheduling = system_config.priority_profile, system_config.scheduling
        logger = log.bind(run_id=run_id)
        logger.info(
            "pipeline.start",
            scheduler=self.scheduler.name,
            profile=profile.profile_id,
            profile_version=profile.version,
            config_version=scheduling.version,
            previous_entries=len(previous_entries or ()),
            **snapshot.summary(),
        )
        timings: dict[str, float] = {}
        started = time.perf_counter()

        with _stage(timings, logger, "data_quality"):
            dq_report = self.data_quality_engine.run(snapshot, system_config.data_quality)
            excluded = list(dq_report.unschedulable_order_ids)
        with _stage(timings, logger, "calendars"):
            calendars = build_calendars(snapshot)
        with _stage(timings, logger, "constraints"):
            constraints = default_constraint_engine(scheduling)
        with _stage(timings, logger, "priority_context"):
            builder = PriorityContextBuilder(
                constraint_engine=constraints, calendars=calendars, clock=self.clock
            )
            ctx = builder.build(snapshot, profile, now=now)
        with _stage(timings, logger, "priority"):
            priorities = self.priority_engine.evaluate(snapshot, profile, ctx=ctx)
        with _stage(timings, logger, "schedule"):
            schedule = self.scheduler.schedule(
                snapshot,
                priorities,
                scheduling,
                calendars,
                constraints,
                previous_entries=previous_entries,
                dq_report=dq_report,
            )
            schedule.run_id = run_id
        with _stage(timings, logger, "quality"):
            if schedule.quality is None:  # a scheduler that did not finalise its result
                schedule.metrics = compute_metrics(schedule, snapshot, calendars, scheduling)
            schedule.quality = compute_quality(schedule, scheduling)
        with _stage(timings, logger, "kpis"):
            kpis = compute_executive_kpis(snapshot, priorities, schedule, now, scheduling, calendars)
        with _stage(timings, logger, "capacity"):
            capacity = compute_capacity(
                snapshot, schedule, calendars, now, scheduling.horizon_days, "machine_group", "week"
            )
        with _stage(timings, logger, "bottlenecks"):
            bottlenecks = find_bottlenecks(
                snapshot, priorities, schedule, calendars, now, scheduling, system_config.alerts
            )
        with _stage(timings, logger, "alerts"):
            alerts = evaluate_alerts(
                snapshot,
                priorities,
                schedule,
                bottlenecks,
                dq_report,
                now,
                system_config.alerts,
                scheduling_config=scheduling,
                capacity=capacity,
                profile=profile,
            )
        timings["total"] = time.perf_counter() - started

        result = PipelineResult(
            run_id=run_id,
            generated_at=now,
            snapshot=snapshot,
            dq_report=dq_report,
            calendars=calendars,
            priorities=priorities,
            schedule=schedule,
            kpis=kpis,
            capacity=capacity,
            bottlenecks=bottlenecks,
            alerts=alerts,
            timings=timings,
            profile_id=profile.profile_id,
            profile_version=profile.version,
            config_id=scheduling.config_id,
            config_version=scheduling.version,
            scheduler_name=self.scheduler.name,
            scheduler_version=self.scheduler.version,
            dq_excluded_order_ids=excluded,
        )
        logger.info("pipeline.done", **result.summary())
        return result

    # ----------------------------------------------------------- simulate
    def simulate(
        self,
        snapshot: PlanningSnapshot,
        scenarios: Sequence[ScenarioBase | Mapping[str, Any]],
        system_config: SystemConfig,
        previous_entries: Sequence[ScheduleEntry] | None = None,
    ) -> SimulationResult:
        """What-if run with the pipeline's own components (baseline == what :meth:`run` schedules).

        Baseline and what-if plans go through the same data-quality gate as
        :meth:`run`. The simulation engine evaluates priorities at
        ``snapshot.as_of``; with a clock frozen at that instant the baseline
        entries equal ``run``'s.
        """
        gated = SchedulerAdapter(
            self.scheduler.inner, data_quality=(self.data_quality_engine, system_config.data_quality)
        )
        engine = SimulationEngine(
            self.priority_engine,
            gated,
            constraint_engine_factory=default_constraint_engine,
            calendar_builder=build_calendars,
            clock=self.clock,
            currency=system_config.currency,
        )
        return engine.run(
            snapshot, scenarios, system_config.priority_profile, system_config.scheduling, previous_entries
        )

    # ------------------------------------------------------------ explain
    def explain_order(self, result: PipelineResult, order_id: str) -> OrderExplanation:
        return explain_order(result, order_id)

    def describe(self) -> dict[str, Any]:
        return {
            "scheduler": self.scheduler.name,
            "scheduler_version": self.scheduler.version,
            "schedulers_available": self.registry.names(),
            "priority_factors": self.priority_engine.describe().get("factors", []),
            "data_quality_rules": [r.key for r in self.data_quality_engine.rules],
        }


__all__ = [
    "DATA_QUALITY_REASON_CODE",
    "OrderExplanation",
    "PipelineResult",
    "PlanningPipeline",
    "SchedulerAdapter",
    "explain_order",
    "withhold_data_quality_blocked",
]
