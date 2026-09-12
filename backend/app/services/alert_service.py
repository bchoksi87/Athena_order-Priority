"""AlertService: read and acknowledge the alert inbox (spec Phase 20).

Alerts are raised/resolved by the analytics pipeline; this service only lists,
summarises and acknowledges them (acknowledgements are audited).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.errors import ConflictError
from app.core.security import CurrentUser
from app.db.records import AlertRecord
from app.db.repositories.alerts import AlertRepository
from app.db.repositories.base import MAX_PAGE_SIZE as REPO_MAX_PAGE_SIZE
from app.domain.enums import AlertSeverity, AlertType
from app.services.audit_service import ENTITY_ALERT, AuditService
from app.services.base import PagedResult, Pagination, Service, actor_id

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class AlertFilters:
    severity: AlertSeverity | None = None
    alert_type: AlertType | None = None
    order_id: str | None = None
    machine_id: str | None = None
    acknowledged: bool | None = None


@dataclass(slots=True)
class AlertSummary:
    total_active: int
    unacknowledged: int
    by_severity: dict[str, int] = field(default_factory=dict)


class AlertService(Service):
    def __init__(self, session: Session, clock: Clock, audit: AuditService) -> None:
        super().__init__(session, clock)
        self._audit = audit
        self._alerts = AlertRepository(session)

    def get(self, alert_id: str) -> AlertRecord:
        return self._alerts.get(alert_id)

    def list(self, filters: AlertFilters | None, pagination: Pagination) -> PagedResult[AlertRecord]:
        filters = filters or AlertFilters()
        if filters.acknowledged is True:
            # The repository only knows "unacknowledged only"; filter the active set in memory.
            items: list[AlertRecord] = []
            offset = 0
            while True:
                page = self._alerts.list_active(
                    severity=filters.severity,
                    alert_type=filters.alert_type,
                    order_id=filters.order_id,
                    machine_id=filters.machine_id,
                    offset=offset,
                    limit=REPO_MAX_PAGE_SIZE,
                )
                items.extend(a for a in page.items if a.acknowledged)
                offset += len(page.items)
                if not page.has_more or not page.items:
                    break
            return PagedResult.slice(items, pagination)
        page = self._alerts.list_active(
            severity=filters.severity,
            alert_type=filters.alert_type,
            order_id=filters.order_id,
            machine_id=filters.machine_id,
            unacknowledged_only=filters.acknowledged is False,
            offset=pagination.offset,
            limit=pagination.limit,
        )
        return PagedResult(
            items=list(page.items), total=page.total, page=pagination.page, page_size=pagination.page_size
        )

    def acknowledge(self, alert_id: str, user: CurrentUser | str, note: str | None = None) -> AlertRecord:
        current = self._alerts.get(alert_id)
        if not current.active:
            raise ConflictError(f"alert '{alert_id}' is resolved and cannot be acknowledged")
        if current.acknowledged:
            raise ConflictError(
                f"alert '{alert_id}' was already acknowledged by {current.acknowledged_by}",
                details={
                    "acknowledged_by": current.acknowledged_by,
                    "acknowledged_at": current.acknowledged_at.isoformat()
                    if current.acknowledged_at
                    else None,
                },
            )
        updated = self._alerts.acknowledge(alert_id, user_id=actor_id(user), at=self.now())
        self._audit.record(
            user,
            ENTITY_ALERT,
            alert_id,
            "alert.acknowledge",
            {"acknowledged": False, "acknowledged_by": None},
            {
                "acknowledged": True,
                "acknowledged_by": updated.acknowledged_by,
                "acknowledged_at": updated.acknowledged_at,
            },
            note or "acknowledged",
            {
                "alert_type": current.alert_type.value,
                "severity": current.severity.value,
                "order_id": current.order_id,
                "machine_id": current.machine_id,
            },
        )
        log.info("alert.acknowledged", alert_id=alert_id, user_id=actor_id(user))
        return updated

    def summary(self) -> AlertSummary:
        counts = self._alerts.active_counts_by_severity()
        by_severity = {sev.value: counts.get(sev.value, 0) for sev in AlertSeverity}
        unacknowledged = self._alerts.list_active(unacknowledged_only=True, limit=1).total
        return AlertSummary(
            total_active=sum(by_severity.values()), unacknowledged=unacknowledged, by_severity=by_severity
        )


__all__ = ["AlertFilters", "AlertService", "AlertSummary"]
