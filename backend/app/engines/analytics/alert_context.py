"""Shared inputs for the alert rules and the single place alerts are constructed.

``AlertContext`` bundles everything a rule may look at (snapshot, priorities,
schedule, bottlenecks, data-quality issues, risk report, optional capacity
report and replan decision) plus the plant-local day used in dedupe keys.
``AlertContext.make`` is the only constructor for :class:`Alert`, so every
alert carries a stable ``dedupe_key`` of the form ``<type>:<entity>:<day>``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from typing import Any

from app.domain.config import AlertConfig, PriorityProfile
from app.domain.enums import AlertSeverity, AlertType
from app.domain.results import (
    Alert,
    Bottleneck,
    DataQualityIssue,
    PriorityResult,
    ReplanDecision,
    ScheduleEntry,
    ScheduleResult,
)
from app.domain.snapshot import PlanningSnapshot
from app.engines.analytics.capacity import CapacityReport
from app.engines.analytics.common import ResourceResolver, entries_by_order, next_pending_operation
from app.engines.analytics.risk import OrderRisk, RiskReport


def dedupe_key(alert_type: AlertType, entity: str, day: str) -> str:
    return f"{alert_type.value}:{entity}:{day}"


@dataclass(slots=True)
class AlertContext:
    snapshot: PlanningSnapshot
    priorities: Mapping[str, PriorityResult]
    schedule: ScheduleResult | None
    bottlenecks: list[Bottleneck]
    issues: list[DataQualityIssue]
    now: datetime
    config: AlertConfig
    risk: RiskReport
    tz: tzinfo
    day: str  # plant-local ISO date of ``now``
    profile: PriorityProfile
    capacity: CapacityReport | None = None
    disruption: ReplanDecision | None = None
    downtime_lookahead_hours: float = 24.0
    entries: dict[str, list[ScheduleEntry]] = field(default_factory=dict)  # order_id -> entries
    risk_by_order: dict[str, OrderRisk] = field(default_factory=dict)
    entries_on_machine: dict[str, int] = field(default_factory=dict)  # machine_id -> scheduled entries
    waiting_on_machine: dict[str, int] = field(
        default_factory=dict
    )  # machine_id -> orders whose next op targets it

    def __post_init__(self) -> None:
        self.entries = entries_by_order(self.schedule)
        self.risk_by_order = self.risk.by_order
        counts: dict[str, int] = {}
        for entries in self.entries.values():
            for entry in entries:
                counts[entry.machine_id] = counts.get(entry.machine_id, 0) + 1
        self.entries_on_machine = counts
        resolver = ResourceResolver(self.snapshot)
        waiting: dict[str, int] = {}
        for order in self.snapshot.open_orders():
            op = next_pending_operation(order, self.snapshot)
            if op is None:
                continue
            machine_id = resolver.resolve(op, order, "machine")
            if machine_id is not None:
                waiting[machine_id] = waiting.get(machine_id, 0) + 1
        self.waiting_on_machine = waiting

    def make(
        self,
        alert_type: AlertType,
        severity: AlertSeverity,
        title: str,
        reason: str,
        action: str,
        *,
        entity: str,
        order_id: str | None = None,
        machine_id: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> Alert:
        return Alert(
            alert_type=alert_type,
            severity=severity,
            title=title,
            reason=reason,
            recommended_action=action,
            raised_at=self.now,
            order_id=order_id,
            machine_id=machine_id,
            entity_ref=entity,
            dedupe_key=dedupe_key(alert_type, entity, self.day),
            details=dict(details or {}),
        )

    def customer_name(self, customer_id: str) -> str:
        customer = self.snapshot.customers.get(customer_id)
        return customer.customer_name if customer is not None else customer_id

    def machine_name(self, machine_id: str) -> str:
        machine = self.snapshot.machines.get(machine_id)
        return machine.machine_name if machine is not None else machine_id


def coerce_issues(dq_report: Iterable[DataQualityIssue] | None) -> list[DataQualityIssue]:
    """Accept a ``DataQualityReport`` (iterable), a plain list or ``None``."""
    return list(dq_report) if dq_report is not None else []


__all__ = ["AlertContext", "coerce_issues", "dedupe_key"]
