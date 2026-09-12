"""Alert evaluation (spec Phase 20): every ``AlertType`` from one set of inputs.

``evaluate_alerts`` builds one :class:`AlertContext` (shared risk report,
plant-local day, schedule entry index) and runs the twelve rule functions
in a fixed order, then de-duplicates on ``dedupe_key`` (first wins) and sorts
by severity, alert type and entity. Each alert carries severity, time
(``raised_at = now``), order / machine reference, reason and recommended
action as the spec requires; ``dedupe_key = <type>:<entity>:<plant day>`` is
stable across repeated evaluations on the same day so the service layer can
upsert instead of re-raising.

Inputs beyond the contract signature are optional keyword arguments:
``capacity`` (a ``CapacityReport`` for capacity-overload alerts),
``disruption`` (a ``ReplanDecision`` for schedule-disruption alerts),
``profile`` (SLA scoring thresholds; the shipped default profile when
omitted), ``scheduling_config`` (at-risk slack; default config when omitted)
and ``downtime_lookahead_hours`` (how far ahead a planned downtime window
raises a warning).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import datetime

import structlog

from app.core.clock import ensure_utc
from app.domain.config import AlertConfig, PriorityProfile, SchedulingConfig
from app.domain.enums import AlertType
from app.domain.results import (
    Alert,
    Bottleneck,
    DataQualityIssue,
    PriorityResult,
    ReplanDecision,
    ScheduleResult,
)
from app.domain.snapshot import PlanningSnapshot
from app.engines.analytics.alert_context import AlertContext, coerce_issues
from app.engines.analytics.alert_rules_orders import (
    behind_schedule_alerts,
    likely_late_alerts,
    overdue_alerts,
    sla_breach_alerts,
    starvation_alerts,
)
from app.engines.analytics.alert_rules_resources import (
    bottleneck_alerts,
    capacity_overload_alerts,
    data_quality_alerts,
    machine_downtime_alerts,
    material_shortage_alerts,
    schedule_disruption_alerts,
    tool_shortage_alerts,
)
from app.engines.analytics.capacity import CapacityReport
from app.engines.analytics.common import ALERT_SEVERITY_RANK, local_date, plant_timezone
from app.engines.analytics.risk import orders_at_risk

log = structlog.get_logger(__name__)

AlertRule = Callable[[AlertContext], list[Alert]]

#: Rule per alert type, in evaluation order (every ``AlertType`` is covered).
ALERT_RULES: dict[AlertType, AlertRule] = {
    AlertType.ORDER_LIKELY_LATE: likely_late_alerts,
    AlertType.ORDER_OVERDUE: overdue_alerts,
    AlertType.MACHINE_DOWNTIME: machine_downtime_alerts,
    AlertType.MATERIAL_SHORTAGE: material_shortage_alerts,
    AlertType.TOOL_SHORTAGE: tool_shortage_alerts,
    AlertType.CAPACITY_OVERLOAD: capacity_overload_alerts,
    AlertType.BOTTLENECK: bottleneck_alerts,
    AlertType.SLA_BREACH_RISK: sla_breach_alerts,
    AlertType.PRODUCTION_BEHIND_SCHEDULE: behind_schedule_alerts,
    AlertType.SCHEDULE_DISRUPTION: schedule_disruption_alerts,
    AlertType.STARVATION: starvation_alerts,
    AlertType.DATA_QUALITY: data_quality_alerts,
}

_TYPE_ORDER: dict[AlertType, int] = {t: i for i, t in enumerate(AlertType)}


def alert_sort_key(alert: Alert) -> tuple[int, int, str, str]:
    return (
        -ALERT_SEVERITY_RANK[alert.severity],
        _TYPE_ORDER.get(alert.alert_type, len(_TYPE_ORDER)),
        alert.entity_ref or "",
        alert.order_id or "",
    )


def dedupe_alerts(alerts: Iterable[Alert]) -> list[Alert]:
    """Keep the first alert per ``dedupe_key`` (rules run in a fixed order, so this is deterministic)."""
    seen: set[str] = set()
    out: list[Alert] = []
    for alert in alerts:
        if alert.dedupe_key in seen:
            continue
        seen.add(alert.dedupe_key)
        out.append(alert)
    return out


def build_alert_context(
    snapshot: PlanningSnapshot,
    priorities: Mapping[str, PriorityResult],
    schedule: ScheduleResult | None,
    bottlenecks: Iterable[Bottleneck] | None,
    dq_report: Iterable[DataQualityIssue] | None,
    now: datetime,
    alert_config: AlertConfig,
    *,
    scheduling_config: SchedulingConfig | None = None,
    capacity: CapacityReport | None = None,
    disruption: ReplanDecision | None = None,
    profile: PriorityProfile | None = None,
    downtime_lookahead_hours: float = 24.0,
) -> AlertContext:
    now = ensure_utc(now)
    tz = plant_timezone(snapshot)
    scheduling_config = scheduling_config if scheduling_config is not None else SchedulingConfig()
    return AlertContext(
        snapshot=snapshot,
        priorities=priorities,
        schedule=schedule,
        bottlenecks=list(bottlenecks) if bottlenecks is not None else [],
        issues=coerce_issues(dq_report),
        now=now,
        config=alert_config,
        risk=orders_at_risk(priorities, schedule, snapshot, scheduling_config, now),
        tz=tz,
        day=local_date(now, tz).isoformat(),
        profile=profile if profile is not None else PriorityProfile(),
        capacity=capacity,
        disruption=disruption,
        downtime_lookahead_hours=downtime_lookahead_hours,
    )


def evaluate_alerts(
    snapshot: PlanningSnapshot,
    priorities: Mapping[str, PriorityResult],
    schedule: ScheduleResult | None,
    bottlenecks: Iterable[Bottleneck] | None,
    dq_report: Iterable[DataQualityIssue] | None,
    now: datetime,
    alert_config: AlertConfig,
    *,
    scheduling_config: SchedulingConfig | None = None,
    capacity: CapacityReport | None = None,
    disruption: ReplanDecision | None = None,
    profile: PriorityProfile | None = None,
    downtime_lookahead_hours: float = 24.0,
    rules: Mapping[AlertType, AlertRule] | None = None,
) -> list[Alert]:
    """All alerts for the inputs, de-duplicated and sorted by severity, type and entity."""
    ctx = build_alert_context(
        snapshot,
        priorities,
        schedule,
        bottlenecks,
        dq_report,
        now,
        alert_config,
        scheduling_config=scheduling_config,
        capacity=capacity,
        disruption=disruption,
        profile=profile,
        downtime_lookahead_hours=downtime_lookahead_hours,
    )
    active = rules if rules is not None else ALERT_RULES
    raised: list[Alert] = []
    for alert_type in ALERT_RULES:  # fixed order regardless of mapping order
        rule = active.get(alert_type)
        if rule is None:
            continue
        produced = rule(ctx)
        for alert in produced:
            if alert.alert_type is not alert_type:
                log.warning(
                    "analytics.alert_type_mismatch", expected=alert_type.value, got=alert.alert_type.value
                )
        raised.extend(produced)
    alerts = dedupe_alerts(raised)
    alerts.sort(key=alert_sort_key)
    log.debug("analytics.alerts", raised=len(raised), unique=len(alerts))
    return alerts


__all__ = [
    "ALERT_RULES",
    "AlertRule",
    "alert_sort_key",
    "build_alert_context",
    "dedupe_alerts",
    "evaluate_alerts",
]
