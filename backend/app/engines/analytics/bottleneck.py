"""Bottleneck detection (spec Phase 12).

Capacity resources (machine group, machine, process) are bottlenecks when
their horizon utilisation reaches ``alert_config.bottleneck_utilization_pct``
or when demand exceeds available hours in at least one week of the horizon
(``capacity_shortfall_hours`` = Σ per-week ``max(0, required - available)``,
because surplus in a later week cannot absorb this week's overload). The
numbers come from :mod:`analytics.capacity` so the bottleneck panel and the
capacity table always agree.

Severity ladder (all thresholds from ``AlertConfig``):

* CRITICAL — shortfall > 0 **and** utilisation ≥ ``capacity_overload_pct``;
* HIGH — shortfall > 0, or utilisation ≥ ``capacity_overload_pct``;
* MEDIUM — utilisation ≥ ``bottleneck_utilization_pct``;
* otherwise the resource is not a bottleneck.

Material / tooling bottlenecks: a material (tool) that blocks at least
``min_orders_blocked`` open orders (readiness WAITING_MATERIAL /
WAITING_TOOLING attributed through the order's next operation). CRITICAL when
one of those orders is overdue or due within
``alert_config.material_shortage_days_ahead`` days, HIGH otherwise; their
``utilization_pct`` is the share of the resource's users that are blocked
and ``capacity_shortfall_hours`` is 0 (the recommendation text carries the
hours of work held up).

``orders_waiting`` = open orders whose next operation targets the resource
and that have not started; revenue / margin at risk = late or unscheduled
orders (per :mod:`analytics.risk`) among those orders. Results are sorted by
severity, then shortfall, then utilisation.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import structlog

from app.core.clock import ensure_utc
from app.domain.config import AlertConfig, DataQualityConfig, SchedulingConfig
from app.domain.enums import ReadinessState, RiskLevel
from app.domain.models import Material, Order, Tooling
from app.domain.results import Bottleneck, PriorityResult, ScheduleResult
from app.domain.snapshot import PlanningSnapshot
from app.engines.analytics.capacity import CapacityReport, compute_capacity
from app.engines.analytics.common import (
    RISK_RANK,
    Dimension,
    ResourceResolver,
    estimate_order_remaining_minutes,
    label_for_process,
    local_date,
    local_midnight,
    next_pending_operation,
    operation_material_id,
    operation_tooling_ids,
    order_started,
    plant_timezone,
    readiness_for,
)
from app.engines.analytics.risk import RiskReport, orders_at_risk
from app.engines.calendar.calendar import MachineCalendar

log = structlog.get_logger(__name__)

CAPACITY_DIMENSIONS: tuple[Dimension, ...] = ("machine_group", "machine", "process")
_TYPE_ORDER: dict[str, int] = {"machine_group": 0, "machine": 1, "process": 2, "material": 3, "tooling": 4}


@dataclass(slots=True)
class ResourceDemand:
    waiting: int = 0
    waiting_ids: list[str] = field(default_factory=list)
    revenue_at_risk: float = 0.0
    margin_at_risk: float = 0.0


def capacity_severity(
    utilization_pct: float, shortfall_hours: float, alert_config: AlertConfig
) -> RiskLevel | None:
    """Severity ladder from the module docstring; ``None`` when not a bottleneck."""
    overloaded = utilization_pct >= alert_config.capacity_overload_pct
    if shortfall_hours > 0 and overloaded:
        return RiskLevel.CRITICAL
    if shortfall_hours > 0 or overloaded:
        return RiskLevel.HIGH
    if utilization_pct >= alert_config.bottleneck_utilization_pct:
        return RiskLevel.MEDIUM
    return None


def resource_demand(
    snapshot: PlanningSnapshot, risk: RiskReport, dimensions: tuple[Dimension, ...] = CAPACITY_DIMENSIONS
) -> dict[Dimension, dict[str, ResourceDemand]]:
    """Waiting orders and money at risk per resource key, attributed through each order's next operation."""
    out: dict[Dimension, dict[str, ResourceDemand]] = {dim: defaultdict(ResourceDemand) for dim in dimensions}
    money_ids = set(risk.money_at_risk_ids())
    by_order = risk.by_order
    resolver = ResourceResolver(snapshot)
    for order in snapshot.open_orders():
        op = next_pending_operation(order, snapshot)
        if op is None:
            continue
        started = order_started(order, snapshot.operations_for_order(order.order_id))
        item = by_order.get(order.order_id)
        for dim in dimensions:
            key = resolver.resolve(op, order, dim)
            if key is None:
                continue
            demand = out[dim][key]
            if not started:
                demand.waiting += 1
                demand.waiting_ids.append(order.order_id)
            if item is not None and order.order_id in money_ids:
                demand.revenue_at_risk += item.revenue
                demand.margin_at_risk += item.margin
    return out


def _resource_name(snapshot: PlanningSnapshot, dimension: Dimension, key: str) -> str:
    if dimension == "machine":
        machine = snapshot.machines.get(key)
        return machine.machine_name if machine is not None else key
    if dimension == "process":
        return label_for_process(key)
    return key


def _saturday_is_free(
    snapshot: PlanningSnapshot,
    calendars: Mapping[str, MachineCalendar],
    dimension: Dimension,
    key: str,
    now: datetime,
) -> bool:
    """True when at least one machine of the resource has no working time next Saturday."""
    from app.engines.analytics.common import machine_key

    tz = plant_timezone(snapshot)
    today = local_date(now, tz)
    saturday = today + timedelta(days=(5 - today.weekday()) % 7 or 7)
    lo, hi = local_midnight(saturday, tz), local_midnight(saturday + timedelta(days=1), tz)
    for machine_id in sorted(snapshot.machines):
        machine = snapshot.machines[machine_id]
        if machine_key(machine, dimension) != key:
            continue
        calendar = calendars.get(machine_id)
        if calendar is not None and calendar.working_minutes_between(lo, hi) <= 0:
            return True
    return False


def _capacity_recommendation(
    name: str,
    dimension: Dimension,
    utilization_pct: float,
    shortfall_hours: float,
    waiting: int,
    first_shortfall_period: datetime | None,
    saturday_free: bool,
    horizon_start: datetime,
) -> str:
    if shortfall_hours > 0:
        if first_shortfall_period is not None and (first_shortfall_period - horizon_start) < timedelta(
            days=7
        ):
            when = "this week"
        elif first_shortfall_period is not None:
            when = f"in the week of {first_shortfall_period.date().isoformat()}"
        else:
            when = "over the horizon"
        how = "Saturday shift" if saturday_free else "extra shift or overtime"
        return (
            f"Add {math.ceil(shortfall_hours)} machine hours on {name} {when} (e.g. {how}) "
            f"to clear {waiting} waiting order(s)"
        )
    alternative = {
        "machine": "machines in the same group",
        "machine_group": "machine groups",
        "process": "processes",
    }[dimension]
    return (
        f"{name} is at {utilization_pct:.0f}% utilisation with {waiting} order(s) waiting: "
        f"plan overtime or move work to alternative {alternative} to protect on-time delivery"
    )


def _capacity_bottlenecks(
    snapshot: PlanningSnapshot,
    calendars: Mapping[str, MachineCalendar],
    reports: Mapping[Dimension, CapacityReport],
    demand: Mapping[Dimension, Mapping[str, ResourceDemand]],
    alert_config: AlertConfig,
    now: datetime,
) -> list[Bottleneck]:
    out: list[Bottleneck] = []
    for dimension in CAPACITY_DIMENSIONS:
        report = reports[dimension]
        for totals in report.totals:
            if totals.required_hours <= 0:
                continue
            severity = capacity_severity(totals.utilization_pct, totals.shortfall_hours, alert_config)
            if severity is None:
                continue
            first_short = next((r.period_start for r in report.rows_for(totals.key) if r.gap_hours < 0), None)
            d = demand[dimension].get(totals.key, ResourceDemand())
            name = _resource_name(snapshot, dimension, totals.key)
            out.append(
                Bottleneck(
                    resource_type=dimension,
                    resource_id=totals.key,
                    resource_name=name,
                    utilization_pct=totals.utilization_pct,
                    orders_waiting=d.waiting,
                    capacity_shortfall_hours=totals.shortfall_hours,
                    revenue_at_risk=d.revenue_at_risk,
                    margin_at_risk=d.margin_at_risk,
                    severity=severity,
                    recommendation=_capacity_recommendation(
                        name,
                        dimension,
                        totals.utilization_pct,
                        totals.shortfall_hours,
                        d.waiting,
                        first_short,
                        _saturday_is_free(snapshot, calendars, dimension, totals.key, now),
                        report.horizon_start,
                    ),
                )
            )
    return out


def _urgent(orders: list[Order], now: datetime, days_ahead: int) -> bool:
    limit = now + timedelta(days=days_ahead)
    return any(o.due_date is not None and ensure_utc(o.due_date) <= limit for o in orders)


def _material_recommendation(
    material: Material | None, material_id: str, waiting: int, held_hours: float
) -> str:
    name = material.material_name if material is not None else material_id
    text = f"Expedite delivery of {name}: {waiting} order(s) ({held_hours:.0f} h of work) blocked"
    if material is not None:
        text += f"; {material.free_quantity:g} {material.unit} free"
        if material.incoming_quantity > 0:
            when = (
                f" expected {material.expected_receipt_date.date().isoformat()}"
                if material.expected_receipt_date is not None
                else ""
            )
            text += f", {material.incoming_quantity:g} {material.unit} incoming{when}"
    return text + "; consider a substitute material or partial release"


def _tooling_recommendation(tool: Tooling | None, tooling_id: str, waiting: int, held_hours: float) -> str:
    name = tool.tooling_name if tool is not None else tooling_id
    status = f" ({tool.maintenance_status})" if tool is not None and tool.maintenance_status != "ok" else ""
    back = (
        f", expected back {tool.available_from.date().isoformat()}"
        if tool is not None and tool.available_from is not None
        else ""
    )
    return (
        f"Restore tooling {name}{status}{back} to release {waiting} blocked order(s) "
        f"({held_hours:.0f} h of work)"
    )


def _resource_bottlenecks(
    snapshot: PlanningSnapshot,
    priorities: Mapping[str, PriorityResult],
    risk: RiskReport,
    alert_config: AlertConfig,
    now: datetime,
    min_orders_blocked: int,
    max_cycle_minutes_per_unit: float | None = None,
) -> list[Bottleneck]:
    blocked_by: dict[tuple[str, str], list[Order]] = defaultdict(list)
    users: dict[tuple[str, str], int] = defaultdict(int)
    for order in snapshot.open_orders():
        op = next_pending_operation(order, snapshot)
        material_id = operation_material_id(op, order)
        tools = operation_tooling_ids(op, order)
        if material_id is not None:
            users[("material", material_id)] += 1
        for tool_id in tools:
            users[("tooling", tool_id)] += 1
        state = readiness_for(order, priorities)
        if state is ReadinessState.WAITING_MATERIAL and material_id is not None:
            blocked_by[("material", material_id)].append(order)
        elif state is ReadinessState.WAITING_TOOLING:
            unusable = {
                t
                for t in tools
                if (tool := snapshot.tooling.get(t)) is None
                or not tool.is_usable
                or (tool.available_from is not None and ensure_utc(tool.available_from) > now)
            }
            for tool_id in sorted(unusable or tools):
                blocked_by[("tooling", tool_id)].append(order)

    money_ids = set(risk.money_at_risk_ids())
    by_order = risk.by_order
    out: list[Bottleneck] = []
    for (kind, resource_id), orders in sorted(blocked_by.items()):
        if len(orders) < max(1, min_orders_blocked):
            continue
        held = (
            sum(
                estimate_order_remaining_minutes(
                    o, snapshot, max_cycle_minutes_per_unit=max_cycle_minutes_per_unit
                )
                or 0.0
                for o in orders
            )
            / 60.0
        )
        revenue = sum(by_order[o.order_id].revenue for o in orders if o.order_id in money_ids)
        margin = sum(by_order[o.order_id].margin for o in orders if o.order_id in money_ids)
        severity = (
            RiskLevel.CRITICAL
            if _urgent(orders, now, alert_config.material_shortage_days_ahead)
            else RiskLevel.HIGH
        )
        n_users = users.get((kind, resource_id), 0)
        share = 100.0 * len(orders) / n_users if n_users else 100.0
        if kind == "material":
            material = snapshot.materials.get(resource_id)
            name = material.material_name if material is not None else resource_id
            recommendation = _material_recommendation(material, resource_id, len(orders), held)
        else:
            tool = snapshot.tooling.get(resource_id)
            name = tool.tooling_name if tool is not None else resource_id
            recommendation = _tooling_recommendation(tool, resource_id, len(orders), held)
        out.append(
            Bottleneck(
                resource_type=kind,
                resource_id=resource_id,
                resource_name=name,
                utilization_pct=share,
                orders_waiting=len(orders),
                capacity_shortfall_hours=0.0,
                revenue_at_risk=revenue,
                margin_at_risk=margin,
                severity=severity,
                recommendation=recommendation,
            )
        )
    return out


def bottleneck_sort_key(b: Bottleneck) -> tuple[int, float, float, int, str]:
    return (
        -RISK_RANK[b.severity],
        -b.capacity_shortfall_hours,
        -b.utilization_pct,
        _TYPE_ORDER.get(b.resource_type, 9),
        b.resource_id,
    )


def find_bottlenecks(
    snapshot: PlanningSnapshot,
    priorities: Mapping[str, PriorityResult],
    schedule: ScheduleResult | None,
    calendars: Mapping[str, MachineCalendar],
    now: datetime,
    config: SchedulingConfig,
    alert_config: AlertConfig,
    *,
    horizon_days: float | None = None,
    min_orders_blocked: int = 1,
    data_quality: DataQualityConfig | None = None,
    exclude_order_ids: Collection[str] | None = None,
) -> list[Bottleneck]:
    """Capacity, material and tooling bottlenecks sorted by severity then shortfall (see module docstring).

    ``data_quality`` / ``exclude_order_ids`` are forwarded to
    :func:`~app.engines.analytics.capacity.compute_capacity` so implausible
    cycle times and data-quality-withheld orders never create phantom demand.
    """
    now = ensure_utc(now)
    horizon = horizon_days if horizon_days is not None else config.horizon_days
    dq = data_quality or DataQualityConfig()
    risk = orders_at_risk(priorities, schedule, snapshot, config, now)
    reports: dict[Dimension, CapacityReport] = {
        dim: compute_capacity(
            snapshot,
            schedule,
            calendars,
            now,
            horizon,
            dim,
            "week",
            data_quality=dq,
            exclude_order_ids=exclude_order_ids,
        )
        for dim in CAPACITY_DIMENSIONS
    }
    demand = resource_demand(snapshot, risk)
    found = _capacity_bottlenecks(snapshot, calendars, reports, demand, alert_config, now)
    found += _resource_bottlenecks(
        snapshot, priorities, risk, alert_config, now, min_orders_blocked, dq.max_cycle_minutes_per_unit
    )
    found.sort(key=bottleneck_sort_key)
    log.debug(
        "analytics.bottlenecks",
        count=len(found),
        critical=sum(1 for b in found if b.severity is RiskLevel.CRITICAL),
    )
    return found


__all__ = [
    "CAPACITY_DIMENSIONS",
    "ResourceDemand",
    "bottleneck_sort_key",
    "capacity_severity",
    "find_bottlenecks",
    "resource_demand",
]
