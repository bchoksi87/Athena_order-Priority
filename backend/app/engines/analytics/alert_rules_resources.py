"""Resource / system alert rules (spec Phase 20): machine downtime, material and
tool shortage, capacity overload, bottleneck, schedule disruption and data quality.

Each rule is a pure function ``rule(ctx) -> list[Alert]`` over
:class:`AlertContext`. Entity keys are stable (machine id, material id,
``<key>@<period start>``, bottleneck ``<type>:<id>``, data-quality code) so
the dedupe key ``<type>:<entity>:<day>`` stays the same across runs on the
same plant day.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from app.core.clock import ensure_utc
from app.domain.enums import (
    AlertSeverity,
    AlertType,
    DataQualitySeverity,
    MachineStatus,
    ReadinessState,
    RiskLevel,
)
from app.domain.models import Machine, Order
from app.domain.results import Alert
from app.engines.analytics.alert_context import AlertContext
from app.engines.analytics.common import (
    local_date,
    next_pending_operation,
    operation_material_id,
    operation_tooling_ids,
    readiness_for,
)

RISK_TO_ALERT: dict[RiskLevel, AlertSeverity] = {
    RiskLevel.LOW: AlertSeverity.INFO,
    RiskLevel.MEDIUM: AlertSeverity.WARNING,
    RiskLevel.HIGH: AlertSeverity.HIGH,
    RiskLevel.CRITICAL: AlertSeverity.CRITICAL,
}

UNKNOWN_RESOURCE = "unknown"


def _fmt_when(t: datetime | None) -> str:
    return ensure_utc(t).strftime("%Y-%m-%d %H:%M UTC") if t is not None else "unknown"


def _fmt_ids(ids: list[str], limit: int = 5) -> str:
    shown = ", ".join(ids[:limit])
    return shown + (f" (+{len(ids) - limit} more)" if len(ids) > limit else "")


# ------------------------------------------------------------ machine downtime


def _machine_impact(ctx: AlertContext, machine: Machine) -> tuple[int, int]:
    """``(scheduled entries on the machine, open orders whose next operation targets it)``."""
    return ctx.entries_on_machine.get(machine.machine_id, 0), ctx.waiting_on_machine.get(
        machine.machine_id, 0
    )


def machine_downtime_alerts(ctx: AlertContext) -> list[Alert]:
    """DOWN/OFFLINE: CRITICAL; MAINTENANCE: WARNING; active downtime: HIGH; upcoming downtime: WARNING."""
    out: list[Alert] = []
    lookahead = ctx.now + timedelta(hours=ctx.downtime_lookahead_hours)
    for machine_id in sorted(ctx.snapshot.machines):
        machine = ctx.snapshot.machines[machine_id]
        severity: AlertSeverity | None = None
        reason = ""
        if machine.status in (MachineStatus.DOWN, MachineStatus.OFFLINE):
            severity = AlertSeverity.CRITICAL
            reason = f"Machine status is {machine.status.value}"
            if machine.available_from is not None:
                reason += f", expected back {_fmt_when(machine.available_from)}"
        elif machine.status is MachineStatus.MAINTENANCE:
            severity = AlertSeverity.WARNING
            reason = "Machine is in maintenance"
            if machine.available_from is not None:
                reason += f", expected back {_fmt_when(machine.available_from)}"
        windows = sorted(machine.all_downtime, key=lambda w: (w.start, w.end))
        active = next((w for w in windows if w.contains(ctx.now)), None)
        upcoming = next((w for w in windows if ctx.now < w.start <= lookahead), None)
        if active is not None and (severity is None or severity is AlertSeverity.WARNING):
            severity = AlertSeverity.HIGH if severity is None else severity
            reason = (reason + "; " if reason else "") + (
                f"downtime{' (' + active.reason + ')' if active.reason else ''} in progress "
                f"until {_fmt_when(active.end)}"
            )
        elif active is None and upcoming is not None and severity is None:
            severity = AlertSeverity.WARNING
            reason = (
                f"Downtime{' (' + upcoming.reason + ')' if upcoming.reason else ''} starts "
                f"{_fmt_when(upcoming.start)} (within {ctx.downtime_lookahead_hours:g} h) "
                f"until {_fmt_when(upcoming.end)}"
            )
        if severity is None:
            continue
        entries, waiting = _machine_impact(ctx, machine)
        impact = f"; {entries} scheduled job(s) and {waiting} waiting order(s) affected"
        alternatives = [
            m.machine_id
            for m in ctx.snapshot.machines_in_group(machine.machine_group)
            if m.machine_id != machine_id and m.status.is_operable
        ]
        action = (
            f"Re-route affected work to {_fmt_ids(alternatives, 3)} in group {machine.machine_group} "
            "and confirm the return time"
            if alternatives
            else f"No alternative machine in group {machine.machine_group}: confirm the return time "
            "and re-plan affected orders"
        )
        out.append(
            ctx.make(
                AlertType.MACHINE_DOWNTIME,
                severity,
                f"Machine {machine.machine_name} downtime",
                reason + impact,
                action,
                entity=machine_id,
                machine_id=machine_id,
                details={
                    "status": machine.status.value,
                    "scheduled_entries": entries,
                    "waiting_orders": waiting,
                },
            )
        )
    return out


# ------------------------------------------------------- material / tooling


def _shortage_alerts(
    ctx: AlertContext,
    kind: str,
    blocked: dict[str, list[Order]],
) -> list[Alert]:
    days = ctx.config.material_shortage_days_ahead
    limit = ctx.now + timedelta(days=days)
    today = local_date(ctx.now, ctx.tz)
    alert_type = AlertType.MATERIAL_SHORTAGE if kind == "material" else AlertType.TOOL_SHORTAGE
    out: list[Alert] = []
    for resource_id in sorted(blocked):
        due_soon = [
            o for o in blocked[resource_id] if o.due_date is not None and ensure_utc(o.due_date) <= limit
        ]
        if not due_soon:
            continue
        urgent = any(local_date(ensure_utc(o.due_date), ctx.tz) <= today for o in due_soon if o.due_date)
        severity = AlertSeverity.CRITICAL if urgent else AlertSeverity.HIGH
        ids = [
            o.order_id
            for o in sorted(due_soon, key=lambda o: (ensure_utc(o.due_date or ctx.now), o.order_id))
        ]
        earliest = min(ensure_utc(o.due_date) for o in due_soon if o.due_date is not None)
        revenue = sum(ctx.risk_by_order[i].revenue for i in ids if i in ctx.risk_by_order)
        if kind == "material":
            material = ctx.snapshot.materials.get(resource_id)
            name = material.material_name if material is not None else resource_id
            stock = (
                f"{material.free_quantity:g} {material.unit} free, {material.incoming_quantity:g} incoming"
                + (
                    f" expected {_fmt_when(material.expected_receipt_date)}"
                    if material.expected_receipt_date
                    else ""
                )
                if material is not None
                else "stock unknown"
            )
            action = (
                f"Expedite the supplier delivery of {name} or release a partial quantity "
                f"to cover {_fmt_ids(ids, 3)}"
            )
        else:
            tool = ctx.snapshot.tooling.get(resource_id)
            name = tool.tooling_name if tool is not None else resource_id
            stock = (
                f"status {tool.maintenance_status}"
                + (f", back {_fmt_when(tool.available_from)}" if tool.available_from else "")
                if tool is not None
                else "tool unknown"
            )
            action = f"Repair, replace or borrow tooling {name} so that {_fmt_ids(ids, 3)} can start"
        out.append(
            ctx.make(
                alert_type,
                severity,
                f"{kind.title()} shortage: {name} blocks {len(due_soon)} order(s) due within {days} days",
                f"{name} ({stock}) blocks {_fmt_ids(ids)}; earliest due {_fmt_when(earliest)}",
                action,
                entity=resource_id,
                details={"blocked_orders": ids, "revenue": revenue, "all_blocked": len(blocked[resource_id])},
            )
        )
    return out


def material_shortage_alerts(ctx: AlertContext) -> list[Alert]:
    """Materials blocking orders due within ``material_shortage_days_ahead`` days (CRITICAL if due today)."""
    blocked: dict[str, list[Order]] = defaultdict(list)
    for order in ctx.snapshot.open_orders():
        if readiness_for(order, ctx.priorities) is not ReadinessState.WAITING_MATERIAL:
            continue
        op = next_pending_operation(order, ctx.snapshot)
        blocked[operation_material_id(op, order) or UNKNOWN_RESOURCE].append(order)
    return _shortage_alerts(ctx, "material", blocked)


def tool_shortage_alerts(ctx: AlertContext) -> list[Alert]:
    """Tooling blocking orders due within ``material_shortage_days_ahead`` days (same ladder as materials)."""
    blocked: dict[str, list[Order]] = defaultdict(list)
    for order in ctx.snapshot.open_orders():
        if readiness_for(order, ctx.priorities) is not ReadinessState.WAITING_TOOLING:
            continue
        op = next_pending_operation(order, ctx.snapshot)
        tools = operation_tooling_ids(op, order)
        unusable = {
            t
            for t in tools
            if (tool := ctx.snapshot.tooling.get(t)) is None
            or not tool.is_usable
            or (tool.available_from is not None and ensure_utc(tool.available_from) > ctx.now)
        }
        for tool_id in sorted(unusable or tools) or [UNKNOWN_RESOURCE]:
            blocked[tool_id].append(order)
    return _shortage_alerts(ctx, "tooling", blocked)


# ------------------------------------------------------- capacity / bottleneck


def capacity_overload_alerts(ctx: AlertContext) -> list[Alert]:
    """Capacity rows at or above ``capacity_overload_pct`` (HIGH when demand exceeds availability)."""
    if ctx.capacity is None:
        return []
    out: list[Alert] = []
    label = ctx.capacity.period
    for row in ctx.capacity.rows:
        if row.required_hours <= 0 or row.utilization_pct < ctx.config.capacity_overload_pct:
            continue
        start = local_date(row.period_start, ctx.tz).isoformat()
        severity = AlertSeverity.HIGH if row.gap_hours < 0 else AlertSeverity.WARNING
        reason = (
            f"{row.required_hours:.0f} h required vs {row.available_hours:.0f} h available "
            f"({row.utilization_pct:.0f}% >= {ctx.config.capacity_overload_pct:g}%) "
            f"in the {label} starting {start}"
        )
        action = (
            f"Add {-row.gap_hours:.0f} h of capacity on {row.key} (overtime, extra shift or outsourcing) "
            f"or move work to a later {label}"
            if row.gap_hours < 0
            else f"Protect {row.key}: avoid adding work in this {label} and prepare overtime as a contingency"
        )
        out.append(
            ctx.make(
                AlertType.CAPACITY_OVERLOAD,
                severity,
                f"{row.key} capacity overloaded ({row.utilization_pct:.0f}%) in the {label} of {start}",
                reason,
                action,
                entity=f"{row.key}@{start}",
                machine_id=row.key if row.dimension == "machine" else None,
                details={
                    "dimension": row.dimension,
                    "required_hours": row.required_hours,
                    "available_hours": row.available_hours,
                    "utilization_pct": row.utilization_pct,
                },
            )
        )
    return out


def bottleneck_alerts(ctx: AlertContext) -> list[Alert]:
    """One alert per detected bottleneck; severity maps from the bottleneck's risk level."""
    out: list[Alert] = []
    for b in ctx.bottlenecks:
        reason = (
            f"{b.resource_name}: utilisation {b.utilization_pct:.0f}%, {b.orders_waiting} order(s) waiting, "
            f"capacity shortfall {b.capacity_shortfall_hours:.0f} h, revenue at risk {b.revenue_at_risk:,.0f}"
        )
        out.append(
            ctx.make(
                AlertType.BOTTLENECK,
                RISK_TO_ALERT[b.severity],
                f"Bottleneck: {b.resource_name} ({b.resource_type.replace('_', ' ')})",
                reason,
                b.recommendation,
                entity=f"{b.resource_type}:{b.resource_id}",
                machine_id=b.resource_id if b.resource_type == "machine" else None,
                details={
                    "resource_type": b.resource_type,
                    "utilization_pct": b.utilization_pct,
                    "orders_waiting": b.orders_waiting,
                    "capacity_shortfall_hours": b.capacity_shortfall_hours,
                },
            )
        )
    return out


# ------------------------------------------------- disruption / data quality


def schedule_disruption_alerts(ctx: AlertContext) -> list[Alert]:
    """Replan changing entries: HIGH with frozen-window violations, WARNING otherwise, INFO if no replan."""
    decision = ctx.disruption
    if decision is None or decision.changed_entries <= 0:
        return []
    if not decision.should_replan:
        severity = AlertSeverity.INFO
    elif decision.frozen_violations > 0:
        severity = AlertSeverity.HIGH
    else:
        severity = AlertSeverity.WARNING
    triggers = ", ".join(decision.triggers) if decision.triggers else "unspecified"
    reason = (
        f"{decision.changed_entries} schedule entries change ({decision.improvement_pct:+.1f}% quality); "
        f"triggers: {triggers}; {decision.frozen_violations} frozen-window violation(s); {decision.reason}"
    )
    action = (
        "Review and approve or reject the proposed replan before it is published"
        if decision.requires_approval
        else "Publish the updated schedule and inform affected supervisors"
    )
    if not decision.should_replan:
        action = "No replan proposed; monitor the triggers and re-run if the disruption persists"
    return [
        ctx.make(
            AlertType.SCHEDULE_DISRUPTION,
            severity,
            f"Schedule disruption: {decision.changed_entries} entries affected",
            reason,
            action,
            entity="replan",
            details={
                "changed_entries": decision.changed_entries,
                "frozen_violations": decision.frozen_violations,
                "should_replan": decision.should_replan,
                "triggers": list(decision.triggers),
            },
        )
    ]


def data_quality_alerts(ctx: AlertContext) -> list[Alert]:
    """One HIGH alert per data-quality code with blocking issues, counting affected entities."""
    by_code: dict[str, list[str]] = defaultdict(list)
    recommendation: dict[str, str] = {}
    for issue in ctx.issues:
        if issue.severity is not DataQualitySeverity.BLOCKING:
            continue
        code = issue.code.value
        order_ref = issue.details.get("order_id")
        entity = order_ref if isinstance(order_ref, str) else issue.entity_id
        if entity not in by_code[code]:
            by_code[code].append(entity)
        if issue.recommendation and code not in recommendation:
            recommendation[code] = issue.recommendation
    out: list[Alert] = []
    for code in sorted(by_code):
        entities = by_code[code]
        label = code.replace("_", " ")
        out.append(
            ctx.make(
                AlertType.DATA_QUALITY,
                AlertSeverity.HIGH,
                f"{len(entities)} order(s) cannot be scheduled: {label}",
                f"{len(entities)} blocking {label} issue(s): {_fmt_ids(sorted(entities))}",
                recommendation.get(code, f"Correct the {label} data in the ERP and re-sync"),
                entity=code,
                details={"code": code, "entities": sorted(entities)},
            )
        )
    return out


__all__ = [
    "RISK_TO_ALERT",
    "UNKNOWN_RESOURCE",
    "bottleneck_alerts",
    "capacity_overload_alerts",
    "data_quality_alerts",
    "machine_downtime_alerts",
    "material_shortage_alerts",
    "schedule_disruption_alerts",
    "tool_shortage_alerts",
]
