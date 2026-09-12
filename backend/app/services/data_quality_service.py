"""DataQualityService: run the Data Quality Engine and serve its dashboard (spec Phase 21).

``run`` loads the snapshot, evaluates every rule with the active
``DataQualityConfig``, replaces the stored issues of a new run and returns the
"N orders cannot be scheduled because: ..." breakdown. ``summary`` rebuilds the
same dashboard from the stored issues of the latest run (through the engine's
own :class:`DataQualityReport`, so the headline is produced by one code path).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.ids import new_id
from app.core.security import CurrentUser
from app.db.records import DataQualityIssueRecord, DataQualitySummary
from app.db.repositories.base import MAX_PAGE_SIZE as REPO_MAX_PAGE_SIZE
from app.db.repositories.data_quality import DataQualityRepository
from app.db.repositories.orders import OrderRepository
from app.domain.enums import DataQualityCode, DataQualitySeverity
from app.domain.results import DataQualityIssue
from app.engines.data_quality.engine import DataQualityDashboard, DataQualityEngine, DataQualityReport
from app.services.audit_service import ENTITY_DATA_QUALITY, AuditService
from app.services.base import PagedResult, Pagination, Service
from app.services.snapshot_service import SnapshotService

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class DataQualityIssueFilters:
    severity: DataQualitySeverity | None = None
    code: DataQualityCode | None = None
    entity_type: str | None = None
    entity_id: str | None = None


@dataclass(slots=True)
class DataQualityOverview:
    run_id: str | None
    detected_at: datetime | None
    total: int
    by_severity: dict[str, int]
    by_code: dict[str, int]
    by_entity_type: dict[str, int]
    blocked_entities: int
    dashboard: DataQualityDashboard
    engine_summary: dict[str, Any] = field(default_factory=dict)


class DataQualityService(Service):
    def __init__(
        self,
        session: Session,
        clock: Clock,
        snapshots: SnapshotService | None = None,
        audit: AuditService | None = None,
        engine: DataQualityEngine | None = None,
    ) -> None:
        super().__init__(session, clock)
        self._snapshots = snapshots or SnapshotService(session, clock)
        self._audit = audit
        self._engine = engine or DataQualityEngine()
        self._issues = DataQualityRepository(session)
        self._orders = OrderRepository(session)

    def run(self, user: CurrentUser | str | None = None) -> DataQualityOverview:
        now = self.now()
        config = self._snapshots.active_config().data_quality
        snapshot = self._snapshots.load_snapshot(now)
        report = self._engine.run(snapshot, config)
        run_id = new_id("dqr")
        stored = self._issues.replace_run(run_id, report.issues, now)
        dashboard = report.dashboard()
        if self._audit is not None and user is not None:
            self._audit.record(
                user,
                ENTITY_DATA_QUALITY,
                run_id,
                "data_quality.run",
                None,
                {
                    "issues": stored,
                    "unschedulable_orders": dashboard.unschedulable_orders,
                    "headline": dashboard.headline,
                },
                "manual data quality run",
                {"run_id": run_id, "open_orders": dashboard.open_orders},
            )
        log.info(
            "data_quality.stored", run_id=run_id, issues=stored, unschedulable=dashboard.unschedulable_orders
        )
        summary = self._issues.summary(run_id)
        return _overview(summary, dashboard, report.summary())

    def summary(self) -> DataQualityOverview:
        summary = self._issues.summary()
        if summary.run_id is None:
            open_ids = frozenset(o.order_id for o in self._orders.get_open()[0])
            report = DataQualityReport(self.now(), [], open_ids, len(open_ids), 0, [])
            return _overview(summary, report.dashboard(), {})
        issues = [_domain(r) for r in self._all_issues(summary.run_id)]
        open_ids = frozenset(o.order_id for o in self._orders.get_open()[0])
        report = DataQualityReport(
            as_of=summary.detected_at or self.now(),
            issues=issues,
            open_order_ids=open_ids,
            orders_checked=len(open_ids),
            operations_checked=0,
            rules_run=[],
        )
        return _overview(summary, report.dashboard(), {})

    def list_issues(
        self, filters: DataQualityIssueFilters | None, pagination: Pagination
    ) -> PagedResult[DataQualityIssueRecord]:
        filters = filters or DataQualityIssueFilters()
        page = self._issues.list_issues(
            severity=filters.severity,
            code=filters.code,
            entity_type=filters.entity_type,
            entity_id=filters.entity_id,
            offset=pagination.offset,
            limit=pagination.limit,
        )
        return PagedResult(
            items=list(page.items), total=page.total, page=pagination.page, page_size=pagination.page_size
        )

    def _all_issues(self, run_id: str) -> list[DataQualityIssueRecord]:
        out: list[DataQualityIssueRecord] = []
        offset = 0
        while True:
            page = self._issues.list_issues(run_id, offset=offset, limit=REPO_MAX_PAGE_SIZE)
            out.extend(page.items)
            offset += len(page.items)
            if not page.has_more or not page.items:
                return out


def _domain(record: DataQualityIssueRecord) -> DataQualityIssue:
    return DataQualityIssue(
        code=DataQualityCode(record.code),
        severity=DataQualitySeverity(record.severity),
        entity_type=record.entity_type,
        entity_id=record.entity_id,
        message=record.message,
        field_name=record.field_name,
        recommendation=record.recommendation,
        details=dict(record.details),
    )


def _overview(
    summary: DataQualitySummary, dashboard: DataQualityDashboard, engine_summary: dict[str, Any]
) -> DataQualityOverview:
    return DataQualityOverview(
        run_id=summary.run_id,
        detected_at=summary.detected_at,
        total=summary.total,
        by_severity={sev.value: summary.by_severity.get(sev.value, 0) for sev in DataQualitySeverity},
        by_code=dict(summary.by_code),
        by_entity_type=dict(summary.by_entity_type),
        blocked_entities=summary.blocked_entities,
        dashboard=dashboard,
        engine_summary=engine_summary,
    )


__all__ = ["DataQualityIssueFilters", "DataQualityOverview", "DataQualityService"]
