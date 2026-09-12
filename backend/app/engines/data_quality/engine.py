"""Data Quality Engine and report (spec Phase 21).

``DataQualityEngine.run`` builds one :class:`DataQualityContext` (all indexes,
O(orders + operations + machines)), runs every rule against it, and sorts the
collected issues by a stable key so two runs over the same snapshot produce
byte-identical reports. The :class:`DataQualityReport` then answers the three
questions the UI needs without re-scanning the snapshot:

* ``summary()`` — counts by code and by severity;
* ``unschedulable_order_ids`` / ``by_order`` — which open orders carry a
  *blocking* issue and why;
* ``dashboard()`` — the spec's "127 orders cannot be scheduled because: 43
  missing machine information, 31 missing cycle time ..." breakdown. Each
  unschedulable order is attributed to exactly one *primary* reason (fixed
  precedence, most fundamental defect first) so the counts add up to the
  headline number, exactly like the spec example; ``orders_by_code`` gives
  the non-partitioned counts for drill-down.

The headline text is rendered from the same counters it reports, never
written independently (contract §4, explainability).
"""

from __future__ import annotations

import time
from collections import Counter, defaultdict
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog

from app.domain.config import DataQualityConfig
from app.domain.enums import DataQualityCode, DataQualitySeverity
from app.domain.results import DataQualityIssue
from app.domain.snapshot import PlanningSnapshot
from app.engines.data_quality.base import (
    DETAIL_ORDER_ID,
    DataQualityContext,
    DataQualityRule,
    SeverityPolicy,
)
from app.engines.data_quality.rules import default_rules

log = structlog.get_logger(__name__)

#: Human labels used in the dashboard headline.
CODE_LABELS: Mapping[DataQualityCode, str] = {
    DataQualityCode.MISSING_DUE_DATE: "missing due date",
    DataQualityCode.INVALID_DATE: "invalid date",
    DataQualityCode.MISSING_CYCLE_TIME: "missing cycle time",
    DataQualityCode.MISSING_SETUP_TIME: "missing setup time",
    DataQualityCode.MISSING_MACHINE_ASSIGNMENT: "missing machine information",
    DataQualityCode.MISSING_MATERIAL: "missing material",
    DataQualityCode.NEGATIVE_QUANTITY: "negative quantity",
    DataQualityCode.DUPLICATE_ORDER: "duplicate order",
    DataQualityCode.INCORRECT_STATUS: "incorrect status",
    DataQualityCode.IMPOSSIBLE_PRODUCTION_TIME: "impossible production time",
    DataQualityCode.MISSING_CUSTOMER: "missing customer information",
    DataQualityCode.CONFLICTING_MACHINE_CAPABILITY: "conflicting machine capability",
    DataQualityCode.INVALID_ROUTING: "invalid routing",
    DataQualityCode.MISSING_OPERATIONS: "missing operations",
    DataQualityCode.UNKNOWN_REFERENCE: "unknown reference",
}

#: Primary-reason precedence for the dashboard partition: the defect that must
#: be fixed *first* wins (no routing at all beats a missing cycle time on it).
DASHBOARD_PRECEDENCE: tuple[DataQualityCode, ...] = (
    DataQualityCode.MISSING_OPERATIONS,
    DataQualityCode.INVALID_ROUTING,
    DataQualityCode.MISSING_MACHINE_ASSIGNMENT,
    DataQualityCode.CONFLICTING_MACHINE_CAPABILITY,
    DataQualityCode.MISSING_CYCLE_TIME,
    DataQualityCode.IMPOSSIBLE_PRODUCTION_TIME,
    DataQualityCode.MISSING_SETUP_TIME,
    DataQualityCode.MISSING_MATERIAL,
    DataQualityCode.MISSING_DUE_DATE,
    DataQualityCode.INVALID_DATE,
    DataQualityCode.NEGATIVE_QUANTITY,
    DataQualityCode.INCORRECT_STATUS,
    DataQualityCode.DUPLICATE_ORDER,
    DataQualityCode.MISSING_CUSTOMER,
    DataQualityCode.UNKNOWN_REFERENCE,
)
_PRECEDENCE_RANK: Mapping[DataQualityCode, int] = {c: i for i, c in enumerate(DASHBOARD_PRECEDENCE)}


def issue_sort_key(issue: DataQualityIssue) -> tuple[str, str, str, str, str, str]:
    return (
        issue.entity_type,
        issue.entity_id,
        issue.code.value,
        issue.field_name or "",
        issue.severity.value,
        issue.message,
    )


def issue_order_id(issue: DataQualityIssue) -> str | None:
    """Order an issue belongs to (order issues, and operation issues via details)."""
    if issue.entity_type == "order":
        return issue.entity_id
    value = issue.details.get(DETAIL_ORDER_ID)
    return str(value) if value is not None else None


@dataclass(slots=True)
class DashboardReason:
    code: DataQualityCode
    label: str
    orders: int


@dataclass(slots=True)
class DataQualityDashboard:
    as_of: datetime
    open_orders: int
    unschedulable_orders: int
    reasons: list[DashboardReason]  # partitioned: sums to unschedulable_orders
    orders_by_code: dict[str, int]  # any blocking issue with that code (overlapping)
    warnings_by_code: dict[str, int]  # open orders with a warning of that code
    headline: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat(),
            "open_orders": self.open_orders,
            "unschedulable_orders": self.unschedulable_orders,
            "reasons": [{"code": r.code.value, "label": r.label, "orders": r.orders} for r in self.reasons],
            "orders_by_code": dict(self.orders_by_code),
            "warnings_by_code": dict(self.warnings_by_code),
            "headline": self.headline,
        }


@dataclass(slots=True)
class DataQualityReport:
    """Result of one engine run. Iterating the report yields its issues (contract §6.6)."""

    as_of: datetime
    issues: list[DataQualityIssue]
    open_order_ids: frozenset[str]
    orders_checked: int
    operations_checked: int
    rules_run: list[str]
    duration_ms: float = 0.0
    by_order: dict[str, list[DataQualityIssue]] = field(default_factory=dict, init=False, repr=False)
    _blocking_open: dict[str, list[DataQualityIssue]] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        by_order: dict[str, list[DataQualityIssue]] = defaultdict(list)
        blocking: dict[str, list[DataQualityIssue]] = defaultdict(list)
        for issue in self.issues:
            oid = issue_order_id(issue)
            if oid is None:
                continue
            by_order[oid].append(issue)
            if issue.severity == DataQualitySeverity.BLOCKING and oid in self.open_order_ids:
                blocking[oid].append(issue)
        self.by_order = dict(by_order)
        self._blocking_open = dict(blocking)

    def __iter__(self) -> Iterator[DataQualityIssue]:
        return iter(self.issues)

    def __len__(self) -> int:
        return len(self.issues)

    @property
    def unschedulable_order_ids(self) -> list[str]:
        """Open orders carrying at least one blocking issue (sorted)."""
        return sorted(self._blocking_open)

    def blocking_issues_for(self, order_id: str) -> list[DataQualityIssue]:
        return list(self._blocking_open.get(order_id, ()))

    def issues_for(self, order_id: str) -> list[DataQualityIssue]:
        return list(self.by_order.get(order_id, ()))

    def summary(self) -> dict[str, Any]:
        by_code: Counter[str] = Counter(i.code.value for i in self.issues)
        by_severity: Counter[str] = Counter(i.severity.value for i in self.issues)
        return {
            "as_of": self.as_of.isoformat(),
            "total_issues": len(self.issues),
            "by_code": {code.value: by_code.get(code.value, 0) for code in DataQualityCode},
            "by_severity": {sev.value: by_severity.get(sev.value, 0) for sev in DataQualitySeverity},
            "orders_checked": self.orders_checked,
            "open_orders": len(self.open_order_ids),
            "operations_checked": self.operations_checked,
            "orders_with_issues": len(self.by_order),
            "unschedulable_orders": len(self._blocking_open),
            "rules_run": list(self.rules_run),
            "duration_ms": self.duration_ms,
        }

    def dashboard(self) -> DataQualityDashboard:
        primary: Counter[DataQualityCode] = Counter()
        any_code: Counter[DataQualityCode] = Counter()
        for issues in self._blocking_open.values():
            codes = {i.code for i in issues}
            any_code.update(codes)
            primary[min(codes, key=lambda c: (_PRECEDENCE_RANK.get(c, len(_PRECEDENCE_RANK)), c.value))] += 1
        warnings: Counter[DataQualityCode] = Counter()
        for oid, issues in self.by_order.items():
            if oid in self.open_order_ids:
                warnings.update({i.code for i in issues if i.severity == DataQualitySeverity.WARNING})

        reasons = [
            DashboardReason(code=code, label=CODE_LABELS.get(code, code.value), orders=count)
            for code, count in sorted(primary.items(), key=lambda kv: (-kv[1], kv[0].value))
        ]
        total = len(self._blocking_open)
        if total == 0:
            headline = "All open orders pass the blocking data quality checks"
        else:
            parts = ", ".join(f"{r.orders} {r.label}" for r in reasons)
            headline = f"{total} order{'s' if total != 1 else ''} cannot be scheduled because: {parts}"
        return DataQualityDashboard(
            as_of=self.as_of,
            open_orders=len(self.open_order_ids),
            unschedulable_orders=total,
            reasons=reasons,
            orders_by_code={c.value: n for c, n in sorted(any_code.items(), key=lambda kv: kv[0].value)},
            warnings_by_code={c.value: n for c, n in sorted(warnings.items(), key=lambda kv: kv[0].value)},
            headline=headline,
        )


class DataQualityEngine:
    """Runs a fixed list of rules over a snapshot and assembles the report."""

    def __init__(
        self,
        rules: Sequence[DataQualityRule] | None = None,
        *,
        severity_overrides: Mapping[DataQualityCode, DataQualitySeverity] | None = None,
    ) -> None:
        self._rules: list[DataQualityRule] = list(rules) if rules is not None else default_rules()
        keys = [r.key for r in self._rules]
        if len(keys) != len(set(keys)):
            dupes = sorted({k for k in keys if keys.count(k) > 1})
            raise ValueError(f"duplicate data quality rule keys: {dupes}")
        self._severity_overrides = dict(severity_overrides or {})

    @property
    def rules(self) -> list[DataQualityRule]:
        return list(self._rules)

    def run(self, snapshot: PlanningSnapshot, config: DataQualityConfig) -> DataQualityReport:
        started = time.perf_counter()
        policy = SeverityPolicy.from_config(config, self._severity_overrides)
        ctx = DataQualityContext.build(snapshot, config, policy)
        issues: list[DataQualityIssue] = []
        for rule in self._rules:
            before = len(issues)
            issues.extend(rule.run(snapshot, config, ctx))
            log.debug("data_quality.rule", rule=rule.key, issues=len(issues) - before)
        issues.sort(key=issue_sort_key)
        report = DataQualityReport(
            as_of=ctx.as_of,
            issues=issues,
            open_order_ids=frozenset(o.order_id for o in ctx.open_orders),
            orders_checked=len(ctx.all_orders),
            operations_checked=len(snapshot.operations),
            rules_run=[r.key for r in self._rules],
            duration_ms=(time.perf_counter() - started) * 1000.0,
        )
        log.info(
            "data_quality.run",
            issues=len(issues),
            unschedulable=len(report.unschedulable_order_ids),
            open_orders=len(report.open_order_ids),
            duration_ms=round(report.duration_ms, 1),
        )
        return report


__all__ = [
    "CODE_LABELS",
    "DASHBOARD_PRECEDENCE",
    "DashboardReason",
    "DataQualityDashboard",
    "DataQualityEngine",
    "DataQualityReport",
    "issue_order_id",
    "issue_sort_key",
]
