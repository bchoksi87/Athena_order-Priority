"""Orders at risk: one consistent answer for KPIs, bottlenecks, OTD and alerts.

Why a shared helper: "revenue at risk" on the executive dashboard, per
bottleneck resource and in the alert list must agree, so the decision of
*which* orders count as late / at risk / unscheduled is made once here.

Rules (all from the schedule when there is one, else from the priority
projection, else from the due date alone):

* projected lateness = completion - due date (signed hours); completion is the
  end of the order's last schedule entry when the order is fully scheduled
  (has entries and no ``UnscheduledItem``), else the priority engine's
  ``projected_completion``;
* ``late`` when projected lateness > 0, or when the order is already overdue
  and no projection exists (the lower bound ``now - due`` is reported);
* ``at_risk`` when late, or slack < ``config.at_risk_slack_hours``, or the
  priority engine rated the order HIGH/CRITICAL, or the schedule could not
  place it (an in-scope ``UnscheduledItem``);
* money at risk = revenue / margin of late orders plus in-scope unscheduled
  orders — the same definition as ``scheduling.metrics``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from app.core.clock import ensure_utc
from app.domain.config import SchedulingConfig
from app.domain.enums import RiskLevel
from app.domain.models import Order
from app.domain.results import PriorityResult, ScheduleResult
from app.domain.snapshot import PlanningSnapshot
from app.engines.analytics.common import (
    RISK_RANK,
    entries_by_order,
    fully_scheduled_completion,
    hours_between,
    order_margin,
    order_revenue,
    unscheduled_reasons,
)

#: Unscheduled reason codes describing orders outside the scheduling scope (mirrors scheduling.metrics).
OUT_OF_SCOPE_CODES: frozenset[str] = frozenset({"status_not_schedulable"})


@dataclass(slots=True)
class OrderRisk:
    order_id: str
    risk_level: RiskLevel
    projected_completion: datetime | None
    projected_lateness_hours: float | None  # signed; positive = late
    hours_until_due: float | None
    revenue: float
    margin: float
    scheduled: bool  # fully placed in the given schedule
    unscheduled_reason: str | None
    late: bool
    at_risk: bool
    basis: str  # "schedule" | "priority" | "due_date" | "unknown"
    reason: str
    customer_id: str = ""

    @property
    def slack_hours(self) -> float | None:
        if self.projected_lateness_hours is not None:
            return -self.projected_lateness_hours
        return self.hours_until_due


@dataclass(slots=True)
class RiskReport:
    as_of: datetime
    items: list[OrderRisk] = field(default_factory=list)  # sorted, most severe first
    revenue_at_risk: float = 0.0
    margin_at_risk: float = 0.0
    late_orders: int = 0
    at_risk_orders: int = 0
    unscheduled_orders: int = 0
    overdue_orders: int = 0

    @property
    def by_order(self) -> dict[str, OrderRisk]:
        return {item.order_id: item for item in self.items}

    def money_at_risk_ids(self) -> list[str]:
        """Orders contributing to revenue / margin at risk (late or in-scope unscheduled)."""
        return [i.order_id for i in self.items if _counts_as_money_at_risk(i)]


def _counts_as_money_at_risk(item: OrderRisk) -> bool:
    if item.late:
        return True
    return item.unscheduled_reason is not None and item.unscheduled_reason not in OUT_OF_SCOPE_CODES


def _max_level(a: RiskLevel, b: RiskLevel) -> RiskLevel:
    return a if RISK_RANK[a] >= RISK_RANK[b] else b


def _min_level(a: RiskLevel, b: RiskLevel) -> RiskLevel:
    return a if RISK_RANK[a] <= RISK_RANK[b] else b


def _describe_hours(hours: float) -> str:
    hours = abs(hours)
    if hours >= 48:
        return f"{hours / 24:.1f} days"
    return f"{hours:.1f} h"


def assess_order_risk(
    order: Order,
    priority: PriorityResult | None,
    completion: datetime | None,
    unscheduled_reason: str | None,
    has_schedule: bool,
    now: datetime,
    config: SchedulingConfig,
) -> OrderRisk:
    """Risk of one order from the schedule placement, the priority projection or the due date."""
    due = ensure_utc(order.due_date) if order.due_date is not None else None
    hours_until_due = hours_between(now, due) if due is not None else None
    revenue, margin = order_revenue(order), order_margin(order)
    scheduled = completion is not None

    if completion is not None:
        basis, projected = "schedule", ensure_utc(completion)
    elif priority is not None and priority.projected_completion is not None:
        basis, projected = "priority", ensure_utc(priority.projected_completion)
    else:
        basis, projected = ("due_date" if due is not None else "unknown"), None

    lateness: float | None = None
    if projected is not None and due is not None:
        lateness = hours_between(due, projected)
    elif basis == "priority" and priority is not None and priority.projected_lateness_hours is not None:
        lateness = priority.projected_lateness_hours
    elif due is not None and hours_until_due is not None and hours_until_due < 0:
        lateness = -hours_until_due  # overdue with no projection: lower bound

    late = lateness is not None and lateness > 0
    in_scope_unscheduled = unscheduled_reason is not None and unscheduled_reason not in OUT_OF_SCOPE_CODES
    slack = -lateness if lateness is not None else None
    tight = slack is not None and slack < config.at_risk_slack_hours
    priority_level = priority.risk_level if priority is not None else RiskLevel.LOW

    if late:
        level = RiskLevel.CRITICAL
    elif tight or in_scope_unscheduled:
        level = _max_level(RiskLevel.HIGH, priority_level)
    elif scheduled:
        # The schedule places it comfortably; a naive priority projection may not override that.
        level = _min_level(priority_level, RiskLevel.MEDIUM)
    else:
        level = priority_level
    at_risk = late or tight or in_scope_unscheduled or RISK_RANK[level] >= RISK_RANK[RiskLevel.HIGH]

    if late and lateness is not None:
        reason = f"Projected {_describe_hours(lateness)} late ({basis})"
        if basis == "due_date":
            reason = f"Overdue by {_describe_hours(lateness)} with no projection"
    elif in_scope_unscheduled:
        reason = f"Not placed in the schedule ({unscheduled_reason})"
    elif tight and slack is not None:
        reason = f"Only {_describe_hours(slack)} slack before due date ({basis})"
    elif due is None:
        reason = "No due date"
    elif lateness is not None:
        reason = f"{_describe_hours(-lateness)} slack ({basis})"
    else:
        reason = f"Due in {_describe_hours(hours_until_due or 0.0)}; no completion projection"
    if priority is not None and priority.blocked and priority.blocking_reasons:
        reason += f"; blocked: {priority.blocking_reasons[0]}"

    return OrderRisk(
        order_id=order.order_id,
        risk_level=level,
        projected_completion=projected,
        projected_lateness_hours=lateness,
        hours_until_due=hours_until_due,
        revenue=revenue,
        margin=margin,
        scheduled=scheduled and has_schedule,
        unscheduled_reason=unscheduled_reason,
        late=late,
        at_risk=at_risk,
        basis=basis,
        reason=reason,
        customer_id=order.customer_id,
    )


def risk_sort_key(item: OrderRisk) -> tuple[int, float, float, str]:
    lateness = item.projected_lateness_hours if item.projected_lateness_hours is not None else float("-inf")
    return (-RISK_RANK[item.risk_level], -lateness, -item.revenue, item.order_id)


def orders_at_risk(
    priorities: Mapping[str, PriorityResult],
    schedule: ScheduleResult | None,
    snapshot: PlanningSnapshot,
    config: SchedulingConfig,
    now: datetime | None = None,
) -> RiskReport:
    """Risk of every open order, sorted most severe first, with money-at-risk totals.

    ``now`` defaults to the schedule's horizon start (else ``snapshot.as_of``)
    so "overdue" means the same thing as inside the scheduler.
    """
    if now is None:
        now = schedule.horizon_start if schedule is not None else snapshot.as_of
    now = ensure_utc(now)
    completions = fully_scheduled_completion(schedule, entries_by_order(schedule))
    failed = unscheduled_reasons(schedule)
    report = RiskReport(as_of=now)
    for order in snapshot.open_orders():
        item = assess_order_risk(
            order,
            priorities.get(order.order_id),
            completions.get(order.order_id),
            failed.get(order.order_id),
            schedule is not None,
            now,
            config,
        )
        report.items.append(item)
        if item.late:
            report.late_orders += 1
        if item.at_risk:
            report.at_risk_orders += 1
        if item.unscheduled_reason is not None:
            report.unscheduled_orders += 1
        if item.hours_until_due is not None and item.hours_until_due < 0:
            report.overdue_orders += 1
        if _counts_as_money_at_risk(item):
            report.revenue_at_risk += item.revenue
            report.margin_at_risk += item.margin
    report.items.sort(key=risk_sort_key)
    return report


__all__ = [
    "OUT_OF_SCOPE_CODES",
    "OrderRisk",
    "RiskReport",
    "assess_order_risk",
    "orders_at_risk",
    "risk_sort_key",
]
