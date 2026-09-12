"""Baseline-vs-scenario comparison (spec Phase 7: "the system should show ...").

Everything in :class:`ScheduleDiff` is derived from the two
:class:`ScheduleResult`s, their metrics and the two snapshots — no number is
estimated separately from the schedules that were actually built:

* an order is *affected* when its completion moved by more than
  ``move_tolerance_minutes``, it changed machine, its late flag flipped, or it
  is scheduled in only one of the two plans (new urgent order, outsourced
  order, order that no longer fits);
* *moved machine* / *resequenced* look at the order's first scheduled
  operation (the one the shop floor sees next);
* *additional overtime* = hours of scheduled work that fall outside the
  calendar's regular shifts (i.e. inside overtime windows or on extra working
  days) in the scenario minus the same in the baseline, so "Saturday becomes
  a working day" and "extra shift" both show up as overtime;
* revenue / margin at risk, utilisation, lateness and setup hours come from
  :class:`ScheduleMetrics`;
* bottlenecks come from ``bottleneck_fn`` when injected (the analytics
  engine) and otherwise from a utilisation-based top-3 machine-group list.

The management summary sentence is rendered from the same numbers.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime

from app.core.clock import ensure_utc
from app.domain.models import CalendarSpec
from app.domain.results import OrderDelta, PriorityResult, ScheduleDiff, ScheduleEntry, ScheduleResult
from app.domain.snapshot import PlanningSnapshot
from app.engines.calendar.builder import build_calendar, resolve_calendar_spec
from app.engines.calendar.calendar import ShiftPattern
from app.engines.simulation.money import format_money

BottleneckFn = Callable[[ScheduleResult, PlanningSnapshot], list[str]]

DEFAULT_MOVE_TOLERANCE_MINUTES = 1.0
DEFAULT_TOP_BOTTLENECKS = 3
MINUTES_PER_HOUR = 60.0


@dataclass(slots=True)
class OrderView:
    """What one schedule says about one order (None fields = not scheduled)."""

    order_id: str
    scheduled: bool
    completion: datetime | None
    machine_id: str | None
    sequence: int | None
    due: datetime | None
    late: bool


def order_views(result: ScheduleResult, snapshot: PlanningSnapshot) -> dict[str, OrderView]:
    """Per-order completion / first machine / late flag for ``result``."""
    first: dict[str, ScheduleEntry] = {}
    completion: dict[str, datetime] = {}
    for entry in result.entries:
        cur = first.get(entry.order_id)
        if cur is None or (entry.start, entry.operation_id) < (cur.start, cur.operation_id):
            first[entry.order_id] = entry
        done = completion.get(entry.order_id)
        if done is None or entry.end > done:
            completion[entry.order_id] = entry.end
    unscheduled = {u.order_id for u in result.unscheduled}
    views: dict[str, OrderView] = {}
    for order_id, entry in first.items():
        scheduled = order_id not in unscheduled
        order = snapshot.orders.get(order_id)
        due = ensure_utc(order.due_date) if order is not None and order.due_date is not None else None
        done = ensure_utc(completion[order_id]) if scheduled else None
        views[order_id] = OrderView(
            order_id=order_id,
            scheduled=scheduled,
            completion=done,
            machine_id=entry.machine_id,
            sequence=entry.sequence_on_machine,
            due=due,
            late=done is not None and due is not None and done > due,
        )
    for order_id in unscheduled - set(first):
        order = snapshot.orders.get(order_id)
        due = ensure_utc(order.due_date) if order is not None and order.due_date is not None else None
        views[order_id] = OrderView(order_id, False, None, None, None, due, False)
    return views


def _score(value: PriorityResult | float | None) -> float | None:
    if value is None:
        return None
    return value.score if isinstance(value, PriorityResult) else float(value)


def _delta_hours(a: datetime | None, b: datetime | None) -> float | None:
    if a is None or b is None:
        return None
    return (b - a).total_seconds() / 3600.0


# ----------------------------------------------------------------- overtime


def _base_spec(spec: CalendarSpec) -> CalendarSpec:
    """The spec's regular shifts only (no overtime windows, no extra working days)."""
    return CalendarSpec(
        calendar_id=f"{spec.calendar_id}#regular",
        name=spec.name,
        timezone=spec.timezone,
        shifts=list(spec.shifts),
        holidays=list(spec.holidays),
    )


def overtime_hours(result: ScheduleResult, snapshot: PlanningSnapshot) -> float:
    """Hours of scheduled work outside the regular shifts (overtime windows / extra days)."""
    by_machine: dict[str, list[ScheduleEntry]] = defaultdict(list)
    for entry in result.entries:
        by_machine[entry.machine_id].append(entry)
    patterns: dict[str, tuple[ShiftPattern, ShiftPattern]] = {}
    total = 0.0
    for machine_id in sorted(by_machine):
        machine = snapshot.machines.get(machine_id)
        if machine is None:
            continue
        spec, _ = resolve_calendar_spec(machine, snapshot)
        pair = patterns.get(spec.calendar_id)
        if pair is None or pair[0].spec is not spec:
            pair = (ShiftPattern(spec), ShiftPattern(_base_spec(spec)))
            patterns[spec.calendar_id] = pair
        full = build_calendar(machine, spec, pattern=pair[0])
        regular = build_calendar(machine, pair[1].spec, pattern=pair[1])
        for entry in by_machine[machine_id]:
            worked = full.working_minutes_between(entry.setup_start, entry.end)
            base = regular.working_minutes_between(entry.setup_start, entry.end)
            total += max(0.0, worked - base)
    return total / MINUTES_PER_HOUR


# -------------------------------------------------------------- bottlenecks


def default_bottlenecks(
    result: ScheduleResult, snapshot: PlanningSnapshot, top_n: int = DEFAULT_TOP_BOTTLENECKS
) -> list[str]:
    """Top-N machine groups by mean scheduled utilisation, rendered as ``"CNC (92%)"``."""
    groups: dict[str, list[float]] = defaultdict(list)
    for machine_id, pct in result.metrics.machine_utilization_pct.items():
        machine = snapshot.machines.get(machine_id)
        if machine is not None:
            groups[machine.machine_group].append(pct)
    ranked = sorted(
        ((sum(v) / len(v), group) for group, v in groups.items() if v),
        key=lambda item: (-item[0], item[1]),
    )
    return [f"{group} ({pct:.0f}%)" for pct, group in ranked[:top_n] if pct > 0]


# --------------------------------------------------------------------- diff


def diff_schedules(
    baseline: ScheduleResult,
    scenario: ScheduleResult,
    snapshot_before: PlanningSnapshot,
    snapshot_after: PlanningSnapshot,
    baseline_priorities: Mapping[str, PriorityResult | float] | None = None,
    scenario_priorities: Mapping[str, PriorityResult | float] | None = None,
    bottleneck_fn: BottleneckFn | None = None,
    *,
    currency: str = "INR",
    move_tolerance_minutes: float = DEFAULT_MOVE_TOLERANCE_MINUTES,
) -> ScheduleDiff:
    """Every field of :class:`ScheduleDiff` for ``baseline`` → ``scenario``."""
    before = order_views(baseline, snapshot_before)
    after = order_views(scenario, snapshot_after)
    b_prio = baseline_priorities or {}
    s_prio = scenario_priorities or {}
    tolerance = move_tolerance_minutes * 60.0

    deltas: list[OrderDelta] = []
    moved_machine = resequenced = newly_late = newly_on_time = 0
    for order_id in sorted(set(before) | set(after)):
        b = before.get(order_id)
        a = after.get(order_id)
        b_done = b.completion if b is not None else None
        a_done = a.completion if a is not None else None
        b_machine = b.machine_id if b is not None else None
        a_machine = a.machine_id if a is not None else None
        b_late = b.late if b is not None else False
        a_late = a.late if a is not None else False
        b_sched = b.scheduled if b is not None else False
        a_sched = a.scheduled if a is not None else False
        delta = _delta_hours(b_done, a_done)
        machine_changed = b_machine is not None and a_machine is not None and b_machine != a_machine
        completion_moved = (b_done is None) != (a_done is None) or (
            delta is not None and abs(delta) * 3600.0 > tolerance
        )
        if not (completion_moved or machine_changed or b_late != a_late or b_sched != a_sched):
            continue
        if machine_changed:
            moved_machine += 1
        elif (
            b is not None
            and a is not None
            and b_machine == a_machine
            and b.sequence is not None
            and a.sequence is not None
            and b.sequence != a.sequence
        ):
            resequenced += 1
        if a_late and not b_late:
            newly_late += 1
        if b_late and not a_late and a_sched:
            newly_on_time += 1
        deltas.append(
            OrderDelta(
                order_id=order_id,
                baseline_completion=b_done,
                scenario_completion=a_done,
                delta_hours=delta,
                baseline_late=b_late,
                scenario_late=a_late,
                baseline_machine_id=b_machine,
                scenario_machine_id=a_machine,
                baseline_score=_score(b_prio.get(order_id)),
                scenario_score=_score(s_prio.get(order_id)),
            )
        )
    deltas.sort(key=lambda d: (d.delta_hours is None, -abs(d.delta_hours or 0.0), d.order_id))

    bottlenecks = bottleneck_fn if bottleneck_fn is not None else default_bottlenecks
    mb, ma = baseline.metrics, scenario.metrics
    overtime_before = overtime_hours(baseline, snapshot_before)
    overtime_after = overtime_hours(scenario, snapshot_after)
    diff = ScheduleDiff(
        orders_affected=len(deltas),
        orders_moved_machine=moved_machine,
        orders_resequenced=resequenced,
        orders_newly_late=newly_late,
        orders_newly_on_time=newly_on_time,
        late_orders_before=mb.late_orders,
        late_orders_after=ma.late_orders,
        on_time_pct_before=mb.on_time_pct,
        on_time_pct_after=ma.on_time_pct,
        avg_lateness_before=mb.avg_lateness_hours,
        avg_lateness_after=ma.avg_lateness_hours,
        utilization_before=mb.overall_utilization_pct,
        utilization_after=ma.overall_utilization_pct,
        setup_hours_before=mb.total_setup_hours,
        setup_hours_after=ma.total_setup_hours,
        revenue_at_risk_before=mb.revenue_at_risk,
        revenue_at_risk_after=ma.revenue_at_risk,
        margin_at_risk_before=mb.margin_at_risk,
        margin_at_risk_after=ma.margin_at_risk,
        additional_overtime_hours=overtime_after - overtime_before,
        bottlenecks_before=list(bottlenecks(baseline, snapshot_before)),
        bottlenecks_after=list(bottlenecks(scenario, snapshot_after)),
        order_deltas=deltas,
    )
    diff.summary = render_summary(diff, currency)
    return diff


def render_summary(diff: ScheduleDiff, currency: str = "INR") -> str:
    """ "17 orders affected; late orders 12 → 15; on-time 91.3% → 88.0%; ..." from the diff's numbers."""
    n = diff.orders_affected
    parts = [
        f"{n} order{'s' if n != 1 else ''} affected",
        f"late orders {diff.late_orders_before} → {diff.late_orders_after}",
        f"on-time {diff.on_time_pct_before:.1f}% → {diff.on_time_pct_after:.1f}%",
        f"utilisation {diff.utilization_before:.0f}% → {diff.utilization_after:.0f}%",
        f"revenue at risk {format_money(diff.revenue_at_risk_before, currency)} → "
        f"{format_money(diff.revenue_at_risk_after, currency)}",
    ]
    if abs(diff.additional_overtime_hours) >= 0.05:
        parts.append(f"additional overtime {diff.additional_overtime_hours:+.1f} h")
    if diff.bottlenecks_before != diff.bottlenecks_after:
        before = ", ".join(diff.bottlenecks_before) or "none"
        after = ", ".join(diff.bottlenecks_after) or "none"
        parts.append(f"bottlenecks {before} → {after}")
    return "; ".join(parts)


__all__ = [
    "DEFAULT_MOVE_TOLERANCE_MINUTES",
    "BottleneckFn",
    "OrderView",
    "default_bottlenecks",
    "diff_schedules",
    "order_views",
    "overtime_hours",
    "render_summary",
]
