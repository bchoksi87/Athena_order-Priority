"""Capacity planning (spec Phase 13): required vs available hours per period.

Required hours per (resource key, period):

* **scheduled** demand — every schedule entry's occupied time inside the
  period. The machine's calendar converts the wall-clock span
  ``[setup_start, end)`` into working minutes (an entry that spans a night
  does not "require" the idle night); without a calendar the entry's
  setup + run minutes are spread proportionally over its span;
* **unscheduled** demand — pending operations without an entry (every pending
  operation when no schedule is given, plus blocked / failed orders when
  there is one): their ERP-estimated setup + run minutes are booked into the
  period that contains the order's due date — the *first* period for overdue
  and undated work — on the resource the operation targets (assigned
  machine, explicit eligible list or machine group, see
  ``common.resolve_operation_resource``). Work due after the horizon is not
  required inside it and is reported as ``beyond_horizon_hours`` instead of
  inflating the first period. Demand whose resource cannot be resolved is
  reported as ``unallocated_hours`` instead of being guessed.

Demand that is *unknown* is never counted, only reported: operations without
a cycle time (``missing_cycle_operations``) and operations whose cycle time
exceeds ``DataQualityConfig.max_cycle_minutes_per_unit``
(``implausible_cycle_operations`` - a unit error would otherwise book
thousands of phantom hours and a "585 % load"). Orders in
``exclude_order_ids`` - the orders the planning pipeline withholds from
scheduling because of blocking data-quality issues - contribute no
unscheduled demand either (``excluded_operations``); their scheduled entries,
if any, still count.

Available hours per (key, period) = Σ over the machines mapped to the key of
``MachineCalendar.available_hours(period)``; machines without a calendar, and
DOWN / OFFLINE / MAINTENANCE machines with neither an ``available_from`` return
time nor a downtime window still ahead (``maintenance_end_after`` — the rule
the scheduler applies when it excludes a machine), add nothing and are counted
in ``notes``. A machine with a known return contributes the working time its
calendar keeps after the downtime, exactly the time the scheduler plans on.
Periods are plant-local days or ISO
weeks covering ``[now, now + horizon_days)``; the first period starts at
``now`` (past hours are not available), the last ends at the horizon.

``CapacityReport`` keeps the spec's "Process | Required | Available | Gap"
table shape (``table()`` / ``table_text()``) next to the period rows.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from collections.abc import Collection, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo
from typing import Any, Literal

import structlog

from app.core.clock import ensure_utc
from app.core.errors import ValidationError
from app.domain.config import DataQualityConfig
from app.domain.results import CapacityRow, ScheduleEntry, ScheduleResult
from app.domain.snapshot import PlanningSnapshot
from app.engines.analytics.common import (
    DIMENSIONS,
    Dimension,
    ResourceResolver,
    cycle_is_implausible,
    estimate_operation_minutes,
    local_date,
    local_midnight,
    machine_key,
    operation_cycle_minutes,
    plant_timezone,
    week_start,
)
from app.engines.calendar.calendar import MachineCalendar
from app.engines.constraints.hard import maintenance_end_after

log = structlog.get_logger(__name__)

Period = Literal["day", "week"]
PERIODS: tuple[Period, ...] = ("day", "week")


@dataclass(slots=True)
class CapacityTotals:
    """Required vs available over the whole horizon for one key (one table line)."""

    key: str
    required_hours: float
    available_hours: float
    scheduled_hours: float = 0.0
    estimated_hours: float = 0.0
    shortfall_hours: float = 0.0  # Σ per-period max(0, required - available)

    @property
    def gap_hours(self) -> float:
        return self.available_hours - self.required_hours

    @property
    def utilization_pct(self) -> float:
        if self.available_hours <= 0:
            return 100.0 if self.required_hours > 0 else 0.0
        return 100.0 * self.required_hours / self.available_hours


@dataclass(slots=True)
class CapacityReport:
    """Ordered ``CapacityRow`` list plus per-key totals; iterating yields the rows."""

    dimension: Dimension
    period: Period
    horizon_start: datetime
    horizon_end: datetime
    rows: list[CapacityRow] = field(default_factory=list)
    totals: list[CapacityTotals] = field(default_factory=list)
    unallocated_hours: float = 0.0
    unallocated_operations: int = 0
    estimated_hours: float = 0.0  # unscheduled demand that was allocated
    scheduled_hours: float = 0.0
    missing_cycle_operations: int = 0  # unknown demand: no cycle time anywhere
    implausible_cycle_operations: int = 0  # unknown demand: cycle above the plausibility limit
    excluded_operations: int = 0  # pending operations of orders withheld by data quality
    beyond_horizon_hours: float = 0.0  # unscheduled demand due after the horizon (not required in it)
    beyond_horizon_operations: int = 0
    notes: list[str] = field(default_factory=list)

    def __iter__(self) -> Iterator[CapacityRow]:
        return iter(self.rows)

    def __len__(self) -> int:
        return len(self.rows)

    @property
    def total_required_hours(self) -> float:
        return sum(t.required_hours for t in self.totals)

    @property
    def total_available_hours(self) -> float:
        return sum(t.available_hours for t in self.totals)

    @property
    def gap_hours(self) -> float:
        return self.total_available_hours - self.total_required_hours

    @property
    def utilization_pct(self) -> float | None:
        if self.total_available_hours <= 0:
            return None
        return 100.0 * self.total_required_hours / self.total_available_hours

    def totals_for(self, key: str) -> CapacityTotals | None:
        return next((t for t in self.totals if t.key == key), None)

    def rows_for(self, key: str) -> list[CapacityRow]:
        return [r for r in self.rows if r.key == key]

    def table(self) -> list[dict[str, Any]]:
        """Spec table: one line per key with required, available, gap and utilisation."""
        return [
            {
                self.dimension: t.key,
                "required_hours": t.required_hours,
                "available_hours": t.available_hours,
                "gap_hours": t.gap_hours,
                "utilization_pct": t.utilization_pct,
                "shortfall_hours": t.shortfall_hours,
            }
            for t in self.totals
        ]

    def table_text(self) -> str:
        header = f"{self.dimension.replace('_', ' ').title()} | Required Hrs | Available Hrs | Gap"
        lines = [header]
        for t in self.totals:
            lines.append(f"{t.key} | {t.required_hours:.0f} | {t.available_hours:.0f} | {t.gap_hours:+.0f}")
        return "\n".join(lines)


def period_bounds(
    now: datetime, horizon_days: float, period: Period, tz: tzinfo
) -> list[tuple[datetime, datetime]]:
    """Plant-local day / ISO-week windows covering ``[now, now + horizon_days)``, first clipped to ``now``."""
    now = ensure_utc(now)
    horizon_end = now + timedelta(days=horizon_days)
    if horizon_end <= now:
        return []
    today = local_date(now, tz)
    cursor = today if period == "day" else week_start(today)
    step = timedelta(days=1) if period == "day" else timedelta(days=7)
    bounds: list[tuple[datetime, datetime]] = []
    while True:
        start = local_midnight(cursor, tz)
        end = local_midnight(cursor + step, tz)
        if start >= horizon_end:
            break
        bounds.append((max(start, now), min(end, horizon_end)))
        cursor += step
    return bounds


def _entry_minutes_in(
    entry: ScheduleEntry, lo: datetime, hi: datetime, calendar: MachineCalendar | None
) -> float:
    lo, hi = max(lo, entry.setup_start), min(hi, entry.end)
    if hi <= lo:
        return 0.0
    if calendar is not None:
        return calendar.working_minutes_between(lo, hi)
    span = (entry.end - entry.setup_start).total_seconds()
    if span <= 0:
        return 0.0
    return (entry.setup_minutes + entry.run_minutes) * (hi - lo).total_seconds() / span


def compute_capacity(
    snapshot: PlanningSnapshot,
    schedule: ScheduleResult | None,
    calendars: Mapping[str, MachineCalendar],
    now: datetime,
    horizon_days: float,
    dimension: Dimension = "machine_group",
    period: Period = "week",
    *,
    data_quality: DataQualityConfig | None = None,
    exclude_order_ids: Collection[str] | None = None,
) -> CapacityReport:
    """Required vs available hours per ``dimension`` key and ``period`` (see module docstring).

    ``data_quality`` supplies the cycle-time plausibility limit (defaults to
    :class:`DataQualityConfig` defaults); ``exclude_order_ids`` are orders whose
    unscheduled work is not demand (withheld from scheduling by data quality).
    """
    if dimension not in DIMENSIONS:
        raise ValidationError(
            f"unknown capacity dimension {dimension!r}", details={"allowed": list(DIMENSIONS)}
        )
    if period not in PERIODS:
        raise ValidationError(f"unknown capacity period {period!r}", details={"allowed": list(PERIODS)})
    now = ensure_utc(now)
    tz = plant_timezone(snapshot)
    bounds = period_bounds(now, horizon_days, period, tz)
    horizon_end = bounds[-1][1] if bounds else now
    report = CapacityReport(dimension, period, now, horizon_end)
    if not bounds:
        report.notes.append("empty horizon")
        return report
    starts = [b[0] for b in bounds]

    key_of_machine: dict[str, str] = {}
    available: dict[str, list[float]] = defaultdict(lambda: [0.0] * len(bounds))
    no_calendar: list[str] = []
    inoperable: list[str] = []
    for machine_id in sorted(snapshot.machines):
        machine = snapshot.machines[machine_id]
        key = machine_key(machine, dimension)
        key_of_machine[machine_id] = key
        calendar = calendars.get(machine_id)
        slots = available[key]  # ensures the key appears even with zero demand
        if calendar is None:
            no_calendar.append(machine_id)
            continue
        if (
            not machine.status.is_operable
            and machine.available_from is None
            and maintenance_end_after(machine, now) is None
        ):
            inoperable.append(machine_id)  # down with no known return: the calendar cannot know
            continue
        for i, (lo, hi) in enumerate(bounds):
            slots[i] += calendar.available_hours(lo, hi)
    if no_calendar:
        report.notes.append(f"{len(no_calendar)} machine(s) without calendar contribute no available hours")
    if inoperable:
        report.notes.append(
            f"{len(inoperable)} inoperable machine(s) without a return time contribute no available hours"
        )

    scheduled: dict[str, list[float]] = defaultdict(lambda: [0.0] * len(bounds))
    estimated: dict[str, list[float]] = defaultdict(lambda: [0.0] * len(bounds))
    placed_ops: set[str] = set()
    if schedule is not None:
        for entry in sorted(schedule.entries, key=lambda e: (e.machine_id, e.setup_start, e.entry_id)):
            placed_ops.add(entry.operation_id)
            entry_key = key_of_machine.get(entry.machine_id)
            if entry_key is None:
                unknown = snapshot.machines.get(entry.machine_id)
                entry_key = machine_key(unknown, dimension) if unknown is not None else entry.machine_id
            calendar = calendars.get(entry.machine_id)
            i = max(0, bisect_right(starts, entry.setup_start) - 1)
            while i < len(bounds) and bounds[i][0] < entry.end:
                occupied = _entry_minutes_in(entry, bounds[i][0], bounds[i][1], calendar)
                if occupied > 0:
                    scheduled[entry_key][i] += occupied / 60.0
                    report.scheduled_hours += occupied / 60.0
                i += 1

    max_cycle = (data_quality or DataQualityConfig()).max_cycle_minutes_per_unit
    excluded = frozenset(exclude_order_ids or ())
    resolver = ResourceResolver(snapshot)
    for order in snapshot.open_orders():
        for op in snapshot.pending_operations_for_order(order.order_id):
            if op.operation_id in placed_ops:
                continue
            if order.order_id in excluded:
                report.excluded_operations += 1
                continue
            cycle = operation_cycle_minutes(op, order)
            if cycle is None:
                report.missing_cycle_operations += 1
                continue
            if cycle_is_implausible(cycle, max_cycle):
                report.implausible_cycle_operations += 1
                continue
            estimate = estimate_operation_minutes(op, order, max_cycle_minutes_per_unit=max_cycle)
            if estimate is None:  # pragma: no cover - both checks above already passed
                continue
            resource = resolver.resolve(op, order, dimension)
            if resource is None:
                report.unallocated_hours += estimate / 60.0
                report.unallocated_operations += 1
                continue
            period_index = 0  # overdue and undated work is required now
            if order.due_date is not None:
                due = ensure_utc(order.due_date)
                if due >= horizon_end:
                    report.beyond_horizon_hours += estimate / 60.0
                    report.beyond_horizon_operations += 1
                    continue
                period_index = max(0, bisect_right(starts, due) - 1)
            estimated[resource][period_index] += estimate / 60.0
            report.estimated_hours += estimate / 60.0
    if report.missing_cycle_operations:
        report.notes.append(
            f"{report.missing_cycle_operations} pending operation(s) without cycle time carry no demand"
        )
    if report.implausible_cycle_operations:
        report.notes.append(
            f"{report.implausible_cycle_operations} pending operation(s) with an implausible cycle time "
            f"(> {max_cycle:g} min/unit) carry no demand"
        )
    if report.excluded_operations:
        report.notes.append(
            f"{report.excluded_operations} pending operation(s) of orders withheld by data quality "
            "carry no demand"
        )
    if report.beyond_horizon_operations:
        report.notes.append(
            f"{report.beyond_horizon_operations} pending operation(s) ({report.beyond_horizon_hours:.1f} h) "
            "are due after the horizon and not required inside it"
        )
    if report.unallocated_operations:
        report.notes.append(
            f"{report.unallocated_operations} pending operation(s) ({report.unallocated_hours:.1f} h) "
            f"could not be attributed to a {dimension}"
        )

    keys = sorted(set(available) | set(scheduled) | set(estimated))
    for key in keys:
        avail, sched, est = available[key], scheduled[key], estimated[key]
        totals = CapacityTotals(key, 0.0, 0.0)
        for i, (lo, hi) in enumerate(bounds):
            required = sched[i] + est[i]
            report.rows.append(CapacityRow(dimension, key, lo, hi, required, avail[i]))
            totals.required_hours += required
            totals.available_hours += avail[i]
            totals.scheduled_hours += sched[i]
            totals.estimated_hours += est[i]
            totals.shortfall_hours += max(0.0, required - avail[i])
        report.totals.append(totals)
    log.debug(
        "analytics.capacity",
        dimension=dimension,
        period=period,
        keys=len(keys),
        periods=len(bounds),
        unallocated_hours=round(report.unallocated_hours, 2),
    )
    return report


def capacity_rows(
    snapshot: PlanningSnapshot,
    schedule: ScheduleResult | None,
    calendars: Mapping[str, MachineCalendar],
    now: datetime,
    horizon_days: float,
    dimension: Dimension = "machine_group",
    period: Period = "week",
    *,
    data_quality: DataQualityConfig | None = None,
    exclude_order_ids: Collection[str] | None = None,
) -> list[CapacityRow]:
    """Contract-shaped variant (DESIGN_CONTRACT §6.6): only the ordered rows."""
    return compute_capacity(
        snapshot,
        schedule,
        calendars,
        now,
        horizon_days,
        dimension,
        period,
        data_quality=data_quality,
        exclude_order_ids=exclude_order_ids,
    ).rows


__all__ = [
    "PERIODS",
    "CapacityReport",
    "CapacityTotals",
    "Period",
    "capacity_rows",
    "compute_capacity",
    "period_bounds",
]
