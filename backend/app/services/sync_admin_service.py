"""SyncAdminService: run and inspect ERP synchronisation from the API and the worker.

The connector is chosen by ``settings.erp_connector`` through
:class:`ConnectorRegistry` (``mock`` serves the synthetic plant anchored to the
current day). ``run`` executes :class:`SyncService` (read-only towards the ERP)
and records an audit row; ``runs`` / ``get_run`` page the ``sync_runs``
history; ``capabilities`` is the Required / Available / Missing report of
:func:`assess_capabilities`; ``status`` combines the last run, the incremental
watermark and the connector's health probe.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.config import Settings, SyntheticScale
from app.core.security import CurrentUser
from app.db.records import SyncRunRecord
from app.db.repositories.sync_runs import SyncRunRepository
from app.domain.enums import SyncMode
from app.integration.capabilities import CapabilityReport, assess_capabilities
from app.integration.connector import ConnectorHealth, ConnectorRegistry, ERPConnector
from app.integration.sync_service import SYNC_STATUS_COMPLETED, SyncOptions, SyncRunSummary, SyncService
from app.services.audit_service import AuditService
from app.services.base import PagedResult, Pagination, Service, actor_id

log = structlog.get_logger(__name__)

ENTITY_SYNC_RUN = "sync_run"


@dataclass(slots=True)
class SyncStatus:
    connector: str
    health: ConnectorHealth | None
    last_run: SyncRunRecord | None
    last_completed: SyncRunRecord | None
    watermark: datetime | None
    runs_total: int
    checked_at: datetime


def build_connector(
    settings: Settings, clock: Clock, registry: ConnectorRegistry | None = None
) -> ERPConnector:
    """The configured ERP connector (mock options come from the synthetic settings)."""
    options: dict[str, Any] = {}
    if settings.erp_connector == "mock":
        scale: SyntheticScale = settings.synthetic_scale
        options = {"seed": settings.synthetic_seed, "scale": scale}
    return (registry or ConnectorRegistry.default()).create(settings.erp_connector, clock, **options)


class SyncAdminService(Service):
    def __init__(
        self,
        session: Session,
        clock: Clock,
        settings: Settings,
        connector: ERPConnector | None = None,
        *,
        audit: AuditService | None = None,
    ) -> None:
        super().__init__(session, clock)
        self._settings = settings
        self._connector = connector
        self._audit = audit or AuditService(session, clock)
        self._runs = SyncRunRepository(session)

    @property
    def connector(self) -> ERPConnector:
        if self._connector is None:
            self._connector = build_connector(self._settings, self._clock)
        return self._connector

    @property
    def connector_name(self) -> str:
        return str(getattr(self.connector, "name", self._settings.erp_connector))

    # ------------------------------------------------------------------ run
    def run(
        self,
        mode: SyncMode | str = SyncMode.FULL,
        user: CurrentUser | str = "system",
        *,
        prune_missing_orders: bool = False,
    ) -> SyncRunSummary:
        """Execute one sync run (commits its own transaction, see :class:`SyncService`)."""
        options = SyncOptions(prune_missing_orders=prune_missing_orders, triggered_by=actor_id(user))
        service = SyncService(self._session, self.connector, self._clock, options=options)
        summary = service.run(mode)
        self._audit.record(
            user,
            ENTITY_SYNC_RUN,
            summary.run_id,
            "sync.run",
            None,
            {
                "status": summary.status,
                "mode": summary.mode.value,
                "records_fetched": dict(summary.records_fetched),
                "records_upserted": dict(summary.records_upserted),
                "issues": summary.issues_count,
            },
            f"{summary.mode.value} sync via {summary.connector}",
            {"run_id": summary.run_id, "connector": summary.connector, "prune": prune_missing_orders},
        )
        self._session.commit()
        log.info("sync.admin.run", run_id=summary.run_id, status=summary.status, user_id=actor_id(user))
        return summary

    # ---------------------------------------------------------------- reads
    def runs(self, pagination: Pagination) -> PagedResult[SyncRunRecord]:
        page = self._runs.list(offset=pagination.offset, limit=pagination.limit)
        return PagedResult(
            items=list(page.items), total=page.total, page=pagination.page, page_size=pagination.page_size
        )

    def get_run(self, run_id: str) -> SyncRunRecord:
        return self._runs.get(run_id)

    def capabilities(self) -> CapabilityReport:
        return assess_capabilities(self.connector.capabilities())

    def status(self) -> SyncStatus:
        health: ConnectorHealth | None
        try:
            health = self.connector.health()
        except Exception as exc:  # a broken connector must not break the status view
            log.warning("sync.health_failed", error=str(exc))
            health = ConnectorHealth(self.connector_name, False, self.now(), message=str(exc))
        last_completed = self._runs.latest(status=SYNC_STATUS_COMPLETED)
        return SyncStatus(
            connector=self.connector_name,
            health=health,
            last_run=self._runs.latest(),
            last_completed=last_completed,
            watermark=last_completed.started_at if last_completed else None,
            runs_total=self._runs.list(limit=1).total,
            checked_at=self.now(),
        )


__all__ = ["ENTITY_SYNC_RUN", "SyncAdminService", "SyncStatus", "build_connector"]
