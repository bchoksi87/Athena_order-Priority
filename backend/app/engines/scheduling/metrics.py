"""Schedule metrics (spec Phases 6, 18, 36) computed from a finished entry list.

Definitions (all derived from ``ScheduleResult.entries`` / ``unscheduled`` and
the snapshot; nothing is estimated separately):

* an order is *scheduled* when every pending operation has an entry, i.e. it
  has entries and no :class:`UnscheduledItem`; partially placed orders count
  as unscheduled (their completion is unknown);
* completion = end of the order's last entry; lateness = completion - due date;
  ``late`` when positive, ``on time`` otherwise, ``at risk`` when on time with
  slack below ``config.at_risk_slack_hours``; orders without a due date are
  neither late nor at risk;
* ``avg_lateness_hours`` is the mean tardiness of the *late* orders (the number
  a planner expects behind "average lateness 2.4 h"); ``total_tardiness_hours``
  sums positive lateness over all scheduled orders;
* utilization per machine = working minutes occupied by entries inside the
  horizon / working minutes the calendar offers inside the horizon;
* revenue / margin at risk = value of late or unscheduled orders;
* ``wip_orders_avg`` = Σ (time each order spends between its first setup start
  and last end, clipped to the horizon) / horizon length.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime

from app.core.clock import ensure_utc
from app.domain.config import SchedulingConfig
from app.domain.results import ScheduleEntry, ScheduleMetrics, ScheduleResult
from app.domain.snapshot import PlanningSnapshot
from app.engines.calendar.calendar import MachineCalendar

#: Unscheduled reason codes that describe orders outside the scheduling scope
#: (not a failure of the schedule), excluded from the "at risk" money figures.
OUT_OF_SCOPE_CODES: frozenset[str] = frozenset({"status_not_schedulable"})


def _hours(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 3600.0


def order_lateness_hours(completion: datetime, due: datetime | None) -> float | None:
    """Signed lateness in hours (positive = late) or None without a due date."""
    if due is None:
        return None
    return _hours(ensure_utc(due), ensure_utc(completion))


def machine_utilization(
    entries_by_machine: Mapping[str, list[ScheduleEntry]],
    calendars: Mapping[str, MachineCalendar],
    horizon_start: datetime,
    horizon_end: datetime,
) -> tuple[dict[str, float], float]:
    """``(per-machine %, overall %)`` of calendar working time inside the horizon that is occupied."""
    per_machine: dict[str, float] = {}
    total_busy = total_available = 0.0
    for machine_id in sorted(calendars):
        calendar = calendars[machine_id]
        available = calendar.working_minutes_between(horizon_start, horizon_end)
        busy = 0.0
        for entry in entries_by_machine.get(machine_id, ()):
            lo, hi = max(entry.setup_start, horizon_start), min(entry.end, horizon_end)
            if hi > lo:
                busy += calendar.working_minutes_between(lo, hi)
        total_busy += busy
        total_available += available
        if available > 0:
            per_machine[machine_id] = 100.0 * busy / available
        else:
            per_machine[machine_id] = 100.0 if busy > 0 else 0.0
    overall = 100.0 * total_busy / total_available if total_available > 0 else 0.0
    return per_machine, overall


def compute_metrics(
    result: ScheduleResult,
    snapshot: PlanningSnapshot,
    calendars: Mapping[str, MachineCalendar],
    config: SchedulingConfig,
) -> ScheduleMetrics:
    """Aggregate metrics for ``result`` (pure function of entries, unscheduled items and snapshot)."""
    horizon_start, horizon_end = ensure_utc(result.horizon_start), ensure_utc(result.horizon_end)
    horizon_hours = max(_hours(horizon_start, horizon_end), 1e-9)
    by_order: dict[str, list[ScheduleEntry]] = defaultdict(list)
    by_machine: dict[str, list[ScheduleEntry]] = defaultdict(list)
    for entry in result.entries:
        by_order[entry.order_id].append(entry)
        by_machine[entry.machine_id].append(entry)
    unscheduled_ids = {u.order_id for u in result.unscheduled}
    at_risk_money_ids = {u.order_id for u in result.unscheduled if u.reason_code not in OUT_OF_SCOPE_CODES}
    scheduled_ids = sorted(oid for oid in by_order if oid not in unscheduled_ids)

    metrics = ScheduleMetrics(
        scheduled_orders=len(scheduled_ids),
        unscheduled_orders=len(unscheduled_ids),
        scheduled_operations=len(result.entries),
    )
    dated = 0
    late_hours: list[float] = []
    for order_id in scheduled_ids:
        order = snapshot.orders.get(order_id)
        entries = by_order[order_id]
        completion = max(e.end for e in entries)
        lateness = order_lateness_hours(completion, order.due_date) if order is not None else None
        value = (order.order_value or 0.0) if order is not None else 0.0
        margin = (order.estimated_margin or 0.0) if order is not None else 0.0
        metrics.revenue_scheduled += value
        if lateness is None:
            continue
        dated += 1
        if lateness > 0:
            metrics.late_orders += 1
            late_hours.append(lateness)
            metrics.revenue_at_risk += value
            metrics.margin_at_risk += margin
        else:
            metrics.on_time_orders += 1
            if -lateness < config.at_risk_slack_hours:
                metrics.orders_at_risk += 1
    for order_id in sorted(at_risk_money_ids):
        order = snapshot.orders.get(order_id)
        if order is not None:
            metrics.revenue_at_risk += order.order_value or 0.0
            metrics.margin_at_risk += order.estimated_margin or 0.0
    if dated:
        metrics.on_time_pct = 100.0 * metrics.on_time_orders / dated
    else:
        metrics.on_time_pct = 100.0 if scheduled_ids else 0.0
    if late_hours:
        metrics.total_tardiness_hours = sum(late_hours)
        metrics.avg_lateness_hours = metrics.total_tardiness_hours / len(late_hours)
        metrics.max_lateness_hours = max(late_hours)

    metrics.total_run_hours = sum(e.run_minutes for e in result.entries) / 60.0
    metrics.total_setup_hours = sum(e.setup_minutes for e in result.entries) / 60.0
    metrics.setup_count = sum(1 for e in result.entries if e.setup_minutes > 0)
    if result.entries:
        metrics.makespan_hours = max(0.0, _hours(horizon_start, max(e.end for e in result.entries)))
    metrics.machine_utilization_pct, metrics.overall_utilization_pct = machine_utilization(
        by_machine, calendars, horizon_start, horizon_end
    )
    wip_hours = 0.0
    for entries in by_order.values():
        lo = max(min(e.setup_start for e in entries), horizon_start)
        hi = min(max(e.end for e in entries), horizon_end)
        if hi > lo:
            wip_hours += _hours(lo, hi)
    metrics.wip_orders_avg = wip_hours / horizon_hours
    return metrics


__all__ = ["OUT_OF_SCOPE_CODES", "compute_metrics", "machine_utilization", "order_lateness_hours"]
