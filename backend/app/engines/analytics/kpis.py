"""Executive KPIs (spec Phase 8) computed from one snapshot + results.

Every field of :class:`ExecutiveKpis` is derived here, from the same helpers
the other analytics views use, so the dashboard tiles agree with the
bottleneck panel and the alert list:

* counts of open orders / pending quantity come straight from the snapshot;
* "due today / tomorrow / this week" compare the due date's *plant-local*
  date (timezone of the default calendar) with the local date of ``now``;
  "this week" is the rest of the current ISO week (today .. Sunday);
  "overdue" is any open order whose due instant is before ``now``;
* blocked counts read the readiness of each open order (priority result,
  else inferred from ERP status fields);
* at-risk orders and money at risk come from :mod:`analytics.risk`;
* machine utilisation is the schedule's occupied share of calendar working
  time over the horizon (recomputed from the entries so it never depends on
  a service having filled ``ScheduleResult.metrics``); without a schedule it
  falls back to the ERP's trailing ``Machine.utilization`` when present;
* capacity utilisation = (scheduled + unscheduled estimated demand) /
  available hours over the horizon from :mod:`analytics.capacity`;
* historical OTD = customers' ``historical_on_time_delivery`` weighted by
  their number of open orders (``None`` when no customer reports one);
* expected OTD = share of dated, projected orders finishing on time (schedule
  placements when a schedule exists, else the priority engine's projection).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime, timedelta

import structlog

from app.core.clock import ensure_utc
from app.domain.config import SchedulingConfig
from app.domain.enums import ReadinessState
from app.domain.results import ExecutiveKpis, PriorityResult, ScheduleEntry, ScheduleResult
from app.domain.snapshot import PlanningSnapshot
from app.engines.analytics.capacity import compute_capacity
from app.engines.analytics.common import local_date, plant_timezone, readiness_for, week_start
from app.engines.analytics.risk import RiskReport, orders_at_risk
from app.engines.calendar.calendar import MachineCalendar
from app.engines.scheduling.metrics import machine_utilization

log = structlog.get_logger(__name__)


def historical_otd_pct(snapshot: PlanningSnapshot) -> float | None:
    """Customers' historical OTD weighted by open order count; ``None`` when unknown."""
    open_counts: dict[str, int] = defaultdict(int)
    for order in snapshot.open_orders():
        open_counts[order.customer_id] += 1
    weighted = weight = 0.0
    for customer_id in sorted(open_counts):
        customer = snapshot.customers.get(customer_id)
        if customer is None or customer.historical_on_time_delivery is None:
            continue
        n = open_counts[customer_id]
        weighted += max(0.0, min(1.0, customer.historical_on_time_delivery)) * n
        weight += n
    return 100.0 * weighted / weight if weight > 0 else None


def expected_otd_pct(risk: RiskReport, has_schedule: bool) -> float | None:
    """Share of dated orders with a projection that finish on time (schedule basis when available)."""
    basis = "schedule" if has_schedule else "priority"
    dated = [i for i in risk.items if i.basis == basis and i.projected_lateness_hours is not None]
    if not dated:
        return None
    return (
        100.0
        * sum(1 for i in dated if i.projected_lateness_hours is not None and i.projected_lateness_hours <= 0)
        / len(dated)
    )


def schedule_machine_utilization(
    schedule: ScheduleResult | None,
    snapshot: PlanningSnapshot,
    calendars: Mapping[str, MachineCalendar],
    now: datetime,
    horizon_days: float,
) -> float | None:
    """Occupied share of calendar working time over the horizon; ERP trailing utilisation as fallback."""
    if schedule is not None and calendars:
        by_machine: dict[str, list[ScheduleEntry]] = defaultdict(list)
        for entry in schedule.entries:
            by_machine[entry.machine_id].append(entry)
        _, overall = machine_utilization(by_machine, calendars, now, now + timedelta(days=horizon_days))
        return overall
    trailing = [m.utilization for m in snapshot.machines.values() if m.utilization is not None]
    if not trailing:
        return None
    return 100.0 * sum(max(0.0, min(1.0, u)) for u in trailing) / len(trailing)


def compute_executive_kpis(
    snapshot: PlanningSnapshot,
    priorities: Mapping[str, PriorityResult],
    schedule: ScheduleResult | None,
    now: datetime,
    config: SchedulingConfig,
    calendars: Mapping[str, MachineCalendar],
) -> ExecutiveKpis:
    """All Phase 8 executive KPIs (see module docstring for each definition)."""
    now = ensure_utc(now)
    tz = plant_timezone(snapshot)
    today = local_date(now, tz)
    tomorrow = today + timedelta(days=1)
    week_end = week_start(today) + timedelta(days=6)

    open_orders = snapshot.open_orders()
    due_today = due_tomorrow = due_week = overdue = 0
    pending_qty = 0.0
    blocked: dict[ReadinessState, int] = defaultdict(int)
    blocked_total = 0
    for order in open_orders:
        pending_qty += order.pending_quantity
        due = order.due_date
        if due is not None:
            due = ensure_utc(due)
            if due < now:
                overdue += 1
            else:
                due_day = local_date(due, tz)
                if due_day == today:
                    due_today += 1
                elif due_day == tomorrow:
                    due_tomorrow += 1
                if today <= due_day <= week_end:
                    due_week += 1
        state = readiness_for(order, priorities)
        if state is not None and state is not ReadinessState.READY:
            blocked[state] += 1
            blocked_total += 1

    risk = orders_at_risk(priorities, schedule, snapshot, config, now)
    scheduled = sum(1 for i in risk.items if i.scheduled)
    unscheduled = len(open_orders) - scheduled
    capacity = compute_capacity(
        snapshot, schedule, calendars, now, config.horizon_days, "machine_group", "week"
    )

    kpis = ExecutiveKpis(
        total_open_orders=len(open_orders),
        total_pending_quantity=pending_qty,
        orders_due_today=due_today,
        orders_due_tomorrow=due_tomorrow,
        orders_due_this_week=due_week,
        overdue_orders=overdue,
        at_risk_orders=risk.at_risk_orders,
        on_time_delivery_pct=historical_otd_pct(snapshot),
        expected_on_time_delivery_pct=expected_otd_pct(risk, schedule is not None),
        machine_utilization_pct=schedule_machine_utilization(
            schedule, snapshot, calendars, now, config.horizon_days
        ),
        capacity_utilization_pct=capacity.utilization_pct,
        revenue_at_risk=risk.revenue_at_risk,
        margin_at_risk=risk.margin_at_risk,
        blocked_by_material=blocked[ReadinessState.WAITING_MATERIAL],
        blocked_by_tooling=blocked[ReadinessState.WAITING_TOOLING],
        blocked_by_machine=blocked[ReadinessState.MACHINE_UNAVAILABLE],
        waiting_for_approval=blocked[ReadinessState.WAITING_APPROVAL],
        blocked_total=blocked_total,
        scheduled_orders=scheduled,
        unscheduled_orders=unscheduled,
        as_of=now,
    )
    log.debug(
        "analytics.kpis",
        open_orders=kpis.total_open_orders,
        overdue=kpis.overdue_orders,
        at_risk=kpis.at_risk_orders,
        blocked=kpis.blocked_total,
    )
    return kpis


__all__ = ["compute_executive_kpis", "expected_otd_pct", "historical_otd_pct", "schedule_machine_utilization"]
