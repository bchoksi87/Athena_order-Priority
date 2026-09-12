"""Order-level alert rules (spec Phase 20): likely late, overdue, SLA breach risk,
production behind schedule and starvation.

Each rule is a pure function ``rule(ctx) -> list[Alert]`` over
:class:`AlertContext`; thresholds come from ``AlertConfig`` (and the priority
profile's SLA scoring for SLA breach risk). Reasons quote the same numbers
the rule compared, so the text can never disagree with the decision.
"""

from __future__ import annotations

from datetime import datetime

from app.core.clock import ensure_utc
from app.domain.enums import AlertSeverity, AlertType, OperationStatus, RiskLevel
from app.domain.models import Order
from app.domain.results import Alert
from app.engines.analytics.alert_context import AlertContext
from app.engines.analytics.common import order_started, waiting_since

SLA_FACTOR_KEY = "sla_risk"


def _fmt_hours(hours: float) -> str:
    hours = abs(hours)
    return f"{hours / 24:.1f} days" if hours >= 48 else f"{hours:.1f} h"


def _fmt_when(t: datetime | None) -> str:
    return ensure_utc(t).strftime("%Y-%m-%d %H:%M UTC") if t is not None else "unknown"


def _order_label(order: Order, ctx: AlertContext) -> str:
    return f"Order {order.order_id} ({ctx.customer_name(order.customer_id)})"


def _order_action(order: Order, ctx: AlertContext) -> str:
    entries = ctx.entries.get(order.order_id)
    if entries:
        return (
            f"Expedite or re-sequence order {order.order_id} ahead of lower-priority work on "
            f"{ctx.machine_name(entries[0].machine_id)}, or agree a revised delivery date with the customer"
        )
    priority = ctx.priorities.get(order.order_id)
    if priority is not None and priority.blocked and priority.blocking_reasons:
        return f"Clear the blocker ({priority.blocking_reasons[0]}) and expedite order {order.order_id}"
    return f"Expedite order {order.order_id} or agree a revised delivery date with the customer"


def likely_late_alerts(ctx: AlertContext) -> list[Alert]:
    """Projected late (HIGH) or slack below ``likely_late_slack_hours`` (WARNING); overdue orders excluded."""
    out: list[Alert] = []
    for item in ctx.risk.items:
        order = ctx.snapshot.orders.get(item.order_id)
        if order is None or order.due_date is None:
            continue
        if item.hours_until_due is not None and item.hours_until_due < 0:
            continue  # overdue rule owns it
        slack = item.slack_hours
        lateness = item.projected_lateness_hours
        if lateness is not None and lateness > 0:
            severity = AlertSeverity.HIGH
            reason = (
                f"Projected completion {_fmt_when(item.projected_completion)} is {_fmt_hours(lateness)} "
                f"after the due date {_fmt_when(order.due_date)} ({item.basis})"
            )
        elif slack is not None and slack < ctx.config.likely_late_slack_hours:
            severity = AlertSeverity.WARNING
            reason = (
                f"Only {_fmt_hours(slack)} of slack before the due date {_fmt_when(order.due_date)} "
                f"(threshold {ctx.config.likely_late_slack_hours:g} h, basis {item.basis})"
            )
        else:
            continue
        out.append(
            ctx.make(
                AlertType.ORDER_LIKELY_LATE,
                severity,
                f"{_order_label(order, ctx)} likely to miss due date",
                reason,
                _order_action(order, ctx),
                entity=order.order_id,
                order_id=order.order_id,
                details={
                    "projected_lateness_hours": lateness,
                    "slack_hours": slack,
                    "basis": item.basis,
                    "risk_level": item.risk_level.value,
                },
            )
        )
    return out


def overdue_alerts(ctx: AlertContext) -> list[Alert]:
    """Every open order whose due date is already in the past (CRITICAL)."""
    out: list[Alert] = []
    for item in ctx.risk.items:
        if item.hours_until_due is None or item.hours_until_due >= 0:
            continue
        order = ctx.snapshot.orders.get(item.order_id)
        if order is None:
            continue
        reason = f"Due {_fmt_when(order.due_date)}, {_fmt_hours(item.hours_until_due)} ago"
        if item.projected_completion is not None:
            reason += f"; projected completion {_fmt_when(item.projected_completion)} ({item.basis})"
        else:
            reason += "; no completion projection"
        out.append(
            ctx.make(
                AlertType.ORDER_OVERDUE,
                AlertSeverity.CRITICAL,
                f"{_order_label(order, ctx)} is overdue",
                reason,
                f"Inform {ctx.customer_name(order.customer_id)} of a revised delivery date; "
                + _order_action(order, ctx),
                entity=order.order_id,
                order_id=order.order_id,
                details={"hours_overdue": -item.hours_until_due, "revenue": item.revenue},
            )
        )
    return out


def sla_breach_alerts(ctx: AlertContext) -> list[Alert]:
    """SLA risk factor raw score at or above the profile's imminent score (CRITICAL when breached)."""
    cfg = ctx.profile.sla
    out: list[Alert] = []
    for order_id in sorted(ctx.priorities):
        priority = ctx.priorities[order_id]
        order = ctx.snapshot.orders.get(order_id)
        if order is None or not order.is_open:
            continue
        factor = next((f for f in priority.factors if f.key == SLA_FACTOR_KEY), None)
        if factor is None or factor.raw_score < cfg.imminent_score:
            continue
        remaining = factor.details.get("remaining_hours")
        breached = factor.raw_score >= cfg.breach_score or (
            isinstance(remaining, (int, float)) and remaining <= 0
        )
        severity = AlertSeverity.CRITICAL if breached else AlertSeverity.HIGH
        sla_hours = factor.details.get("sla_hours")
        out.append(
            ctx.make(
                AlertType.SLA_BREACH_RISK,
                severity,
                f"{_order_label(order, ctx)} SLA {'breached' if breached else 'breach imminent'}",
                f"{factor.reason} (SLA risk score {factor.raw_score:.0f} >= {cfg.imminent_score:g})",
                f"Expedite order {order_id} to protect the {sla_hours:g} h SLA"
                if isinstance(sla_hours, (int, float))
                else f"Expedite order {order_id} to protect its SLA",
                entity=order_id,
                order_id=order_id,
                details={"raw_score": factor.raw_score, "remaining_hours": remaining, "sla_hours": sla_hours},
            )
        )
    return out


def behind_schedule_alerts(ctx: AlertContext) -> list[Alert]:
    """Operations started later than planned, or scheduled starts in the past with no start recorded."""
    threshold = ctx.config.behind_schedule_minutes
    out: list[Alert] = []
    for order in ctx.snapshot.open_orders():
        worst: tuple[float, str, str | None] | None = None  # (delay minutes, description, machine)
        for op in ctx.snapshot.operations_for_order(order.order_id):
            if op.actual_start is not None and op.estimated_start is not None:
                delay = (ensure_utc(op.actual_start) - ensure_utc(op.estimated_start)).total_seconds() / 60.0
                if delay > threshold and (worst is None or delay > worst[0]):
                    worst = (
                        delay,
                        f"operation {op.operation_id} started {_fmt_hours(delay / 60)} after its "
                        f"planned start {_fmt_when(op.estimated_start)}",
                        op.machine_id,
                    )
        for entry in ctx.entries.get(order.order_id, ()):
            entry_op = ctx.snapshot.operations.get(entry.operation_id)
            if entry_op is None or entry_op.actual_start is not None:
                continue
            if entry_op.operation_status in (OperationStatus.IN_PROGRESS, OperationStatus.COMPLETED):
                continue
            delay = (ctx.now - ensure_utc(entry.start)).total_seconds() / 60.0
            if delay > threshold and (worst is None or delay > worst[0]):
                worst = (
                    delay,
                    f"operation {entry_op.operation_id} was scheduled to start {_fmt_when(entry.start)} on "
                    f"{ctx.machine_name(entry.machine_id)} but has not started "
                    f"({_fmt_hours(delay / 60)} ago)",
                    entry.machine_id,
                )
        if worst is None:
            continue
        item = ctx.risk_by_order.get(order.order_id)
        severity = AlertSeverity.HIGH if item is not None and item.at_risk else AlertSeverity.WARNING
        out.append(
            ctx.make(
                AlertType.PRODUCTION_BEHIND_SCHEDULE,
                severity,
                f"{_order_label(order, ctx)} is behind schedule",
                f"{worst[1][0].upper()}{worst[1][1:]} (threshold {threshold:g} min)",
                f"Confirm progress on the shop floor and update the ERP status; "
                f"re-plan order {order.order_id} if the delay persists",
                entity=order.order_id,
                order_id=order.order_id,
                machine_id=worst[2],
                details={"delay_minutes": worst[0], "at_risk": item.at_risk if item is not None else None},
            )
        )
    return out


def starvation_alerts(ctx: AlertContext) -> list[Alert]:
    """Open orders waiting at least ``starvation_days`` since receipt without any production start."""
    out: list[Alert] = []
    for order in ctx.snapshot.open_orders():
        since = waiting_since(order)
        if since is None or order_started(order, ctx.snapshot.operations_for_order(order.order_id)):
            continue
        days = (ctx.now - since).total_seconds() / 86400.0
        if days < ctx.config.starvation_days:
            continue
        priority = ctx.priorities.get(order.order_id)
        score = f", priority score {priority.score:.0f}" if priority is not None else ""
        due = f", due {_fmt_when(order.due_date)}" if order.due_date is not None else ", no due date"
        severity = AlertSeverity.HIGH
        if priority is not None and priority.risk_level is RiskLevel.CRITICAL:
            severity = AlertSeverity.CRITICAL
        out.append(
            ctx.make(
                AlertType.STARVATION,
                severity,
                f"{_order_label(order, ctx)} has been waiting {days:.0f} days",
                f"Received {_fmt_when(since)}, waiting {days:.1f} days without starting "
                f"(threshold {ctx.config.starvation_days:g} days){score}{due}",
                f"Release order {order.order_id} manually or raise its priority "
                "so that fairness aging can take effect",
                entity=order.order_id,
                order_id=order.order_id,
                details={"waiting_days": days},
            )
        )
    return out


__all__ = [
    "SLA_FACTOR_KEY",
    "behind_schedule_alerts",
    "likely_late_alerts",
    "overdue_alerts",
    "sla_breach_alerts",
    "starvation_alerts",
]
