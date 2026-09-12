"""AlertRepository: deduplicated alert inbox."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import select

from app.core.errors import NotFoundError
from app.core.ids import new_id
from app.db.models import AlertRow
from app.db.records import AlertRecord, Page
from app.db.repositories.base import DEFAULT_PAGE_SIZE, Repository
from app.domain.enums import AlertSeverity, AlertType
from app.domain.results import Alert


class AlertRepository(Repository):
    def upsert(self, alert: Alert, now: datetime | None = None) -> AlertRecord:
        """Insert a new alert or refresh the existing one with the same ``dedupe_key``.

        A re-raised alert keeps its id/acknowledgement but bumps ``occurrences``,
        ``last_seen_at`` and takes the newest severity/text; a resolved alert
        that re-occurs is re-activated (and un-acknowledged).
        """
        key = alert.dedupe_key or _default_dedupe_key(alert)
        seen_at = now or alert.raised_at
        row = self._session.execute(select(AlertRow).where(AlertRow.dedupe_key == key)).scalar_one_or_none()
        if row is None:
            row = AlertRow(alert_id=new_id("al"), dedupe_key=key, raised_at=alert.raised_at, occurrences=1)
            self._session.add(row)
        else:
            row.occurrences += 1
            if not row.active:
                row.active = True
                row.resolved_at = None
                row.acknowledged_by = None
                row.acknowledged_at = None
        row.alert_type = alert.alert_type.value
        row.severity = alert.severity.value
        row.title = alert.title
        row.reason = alert.reason
        row.recommended_action = alert.recommended_action
        row.last_seen_at = seen_at
        row.order_id = alert.order_id
        row.machine_id = alert.machine_id
        row.entity_ref = alert.entity_ref
        row.details = dict(alert.details)
        self._flush()
        return _record(row)

    def upsert_many(self, alerts: Iterable[Alert], now: datetime | None = None) -> list[AlertRecord]:
        return [self.upsert(a, now) for a in alerts]

    def resolve_missing(self, active_keys: Iterable[str], at: datetime) -> int:
        """Resolve active alerts whose dedupe key is not in ``active_keys`` (alert sweep)."""
        keep = set(active_keys)
        count = 0
        for row in self._session.execute(select(AlertRow).where(AlertRow.active.is_(True))).scalars():
            if row.dedupe_key not in keep:
                row.active = False
                row.resolved_at = at
                count += 1
        self._flush()
        return count

    def get(self, alert_id: str) -> AlertRecord:
        row = self._session.get(AlertRow, alert_id)
        if row is None:
            raise NotFoundError(f"alert '{alert_id}' not found", details={"alert_id": alert_id})
        return _record(row)

    def list_active(
        self,
        *,
        severity: AlertSeverity | None = None,
        alert_type: AlertType | None = None,
        order_id: str | None = None,
        machine_id: str | None = None,
        unacknowledged_only: bool = False,
        offset: int = 0,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> Page:
        stmt = select(AlertRow).where(AlertRow.active.is_(True))
        if severity is not None:
            stmt = stmt.where(AlertRow.severity == severity.value)
        if alert_type is not None:
            stmt = stmt.where(AlertRow.alert_type == alert_type.value)
        if order_id:
            stmt = stmt.where(AlertRow.order_id == order_id)
        if machine_id:
            stmt = stmt.where(AlertRow.machine_id == machine_id)
        if unacknowledged_only:
            stmt = stmt.where(AlertRow.acknowledged_at.is_(None))
        stmt = stmt.order_by(AlertRow.raised_at.desc(), AlertRow.alert_id)
        page = self._paginate(stmt, offset, limit)
        page.items = [_record(r) for r in page.items]
        return page

    def acknowledge(self, alert_id: str, *, user_id: str, at: datetime) -> AlertRecord:
        row = self._session.get(AlertRow, alert_id)
        if row is None:
            raise NotFoundError(f"alert '{alert_id}' not found", details={"alert_id": alert_id})
        row.acknowledged_by = user_id
        row.acknowledged_at = at
        self._flush()
        return _record(row)

    def resolve(self, alert_id: str, at: datetime) -> AlertRecord:
        row = self._session.get(AlertRow, alert_id)
        if row is None:
            raise NotFoundError(f"alert '{alert_id}' not found", details={"alert_id": alert_id})
        row.active = False
        row.resolved_at = at
        self._flush()
        return _record(row)

    def active_counts_by_severity(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for (severity,) in self._session.execute(
            select(AlertRow.severity).where(AlertRow.active.is_(True))
        ).all():
            counts[severity] = counts.get(severity, 0) + 1
        return counts


def _default_dedupe_key(alert: Alert) -> str:
    return "|".join(
        [alert.alert_type.value, alert.order_id or "", alert.machine_id or "", alert.entity_ref or ""]
    )


def _record(row: AlertRow) -> AlertRecord:
    return AlertRecord(
        alert_id=row.alert_id,
        dedupe_key=row.dedupe_key,
        alert_type=AlertType(row.alert_type),
        severity=AlertSeverity(row.severity),
        title=row.title,
        reason=row.reason,
        recommended_action=row.recommended_action,
        raised_at=row.raised_at,
        last_seen_at=row.last_seen_at,
        occurrences=row.occurrences,
        active=row.active,
        order_id=row.order_id,
        machine_id=row.machine_id,
        entity_ref=row.entity_ref,
        details=dict(row.details or {}),
        acknowledged_by=row.acknowledged_by,
        acknowledged_at=row.acknowledged_at,
        resolved_at=row.resolved_at,
    )


__all__ = ["AlertRepository"]
