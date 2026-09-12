"""Capacity-load measurement of a synthetic dataset (the generator's calibration yardstick).

``measure_load`` compares the machine-hours the open order book still needs
with the hours the machine calendars offer over a window, per machine group
and plant-wide. The generator is tuned against it (see the reasoning in
:mod:`synthetic.generator`) and a unit test guards the result, so a change to
timings, quantities or machine counts that pushes the plant back into an
impossible load fails fast.

Required hours of an operation = setup + cycle x pending quantity / group
efficiency, where the group efficiency is the median ``Machine.efficiency``
of the group (the scheduler divides run time by the chosen machine's
efficiency). Injected data-quality defects are neutralised (an impossible
cycle time counts as zero) so the figure reflects the plant, not the noise.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.domain.models import Machine, Operation, Order
from app.domain.snapshot import PlanningSnapshot
from app.engines.calendar.builder import build_calendars
from synthetic.plant import GROUPS_BY_PROCESS

#: Cycle minutes per unit above this are treated as an injected defect, not load.
_IMPLAUSIBLE_CYCLE_MINUTES = 24 * 60.0


@dataclass(slots=True)
class GroupLoad:
    """Required versus available hours of one machine group over the window."""

    group: str
    machines: int
    efficiency: float
    available_hours: float
    required_hours: float

    @property
    def load_pct(self) -> float:
        return 100.0 * self.required_hours / self.available_hours if self.available_hours > 0 else 0.0


@dataclass(slots=True)
class LoadReport:
    """Plant-wide and per-group load over ``window_days`` from ``as_of``."""

    as_of: datetime
    window_days: int
    groups: dict[str, GroupLoad] = field(default_factory=dict)

    @property
    def available_hours(self) -> float:
        return sum(g.available_hours for g in self.groups.values())

    @property
    def required_hours(self) -> float:
        return sum(g.required_hours for g in self.groups.values())

    @property
    def load_pct(self) -> float:
        return 100.0 * self.required_hours / self.available_hours if self.available_hours > 0 else 0.0

    def bottlenecks(self, threshold_pct: float = 100.0) -> list[GroupLoad]:
        """Groups at or above ``threshold_pct`` load, most loaded first."""
        return sorted(
            (g for g in self.groups.values() if g.load_pct >= threshold_pct),
            key=lambda g: (-g.load_pct, g.group),
        )


def _group_of(op: Operation, snapshot: PlanningSnapshot) -> str | None:
    if op.machine_group and snapshot.machines_in_group(op.machine_group):
        return op.machine_group
    for group in GROUPS_BY_PROCESS.get(op.operation_type, ()):
        if snapshot.machines_in_group(group):
            return group
    return None


def _operation_hours(op: Operation, efficiency: float) -> float:
    cycle = op.cycle_minutes_per_unit or 0.0
    if cycle > _IMPLAUSIBLE_CYCLE_MINUTES:
        cycle = 0.0
    quantity = max(0.0, op.pending_quantity)
    return ((op.setup_minutes or 0.0) + cycle * quantity / (efficiency or 1.0)) / 60.0


def _due_within(order: Order, as_of: datetime, window: timedelta) -> bool:
    due = order.due_date
    return due is None or due <= as_of + window


def measure_load(
    snapshot: PlanningSnapshot,
    window_days: int = 30,
    orders: Iterable[Order] | None = None,
) -> LoadReport:
    """Required hours of the open work due inside the window versus calendar hours.

    All open orders count (blocked and data-quality-flagged ones included: the
    work is still demand); ``orders`` restricts the population when given.
    """
    as_of = snapshot.as_of
    window = timedelta(days=window_days)
    calendars = build_calendars(snapshot)
    by_group: dict[str, list[Machine]] = {}
    for machine in snapshot.machines.values():
        by_group.setdefault(machine.machine_group, []).append(machine)
    report = LoadReport(as_of=as_of, window_days=window_days)
    for group, machines in sorted(by_group.items()):
        report.groups[group] = GroupLoad(
            group=group,
            machines=len(machines),
            efficiency=statistics.median(m.efficiency for m in machines),
            available_hours=sum(
                calendars[m.machine_id].available_hours(as_of, as_of + window) for m in machines
            ),
            required_hours=0.0,
        )
    population = snapshot.open_orders() if orders is None else [o for o in orders if o.is_open]
    for order in population:
        if not _due_within(order, as_of, window):
            continue
        for op in snapshot.pending_operations_for_order(order.order_id):
            group = _group_of(op, snapshot)
            if group is None:
                continue
            load = report.groups[group]
            load.required_hours += _operation_hours(op, load.efficiency)
    return report


__all__ = ["GroupLoad", "LoadReport", "measure_load"]
