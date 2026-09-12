"""AuditRepository: append-only audit trail (contract §10)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.core.ids import new_id
from app.db.models import AuditLogRow
from app.db.records import AuditEntry, Page
from app.db.repositories.base import DEFAULT_PAGE_SIZE, Repository
from app.db.snapshot_codec import to_jsonable


@dataclass(slots=True)
class AuditFilters:
    user_id: str | None = None
    entity_type: str | None = None
    entity_id: str | None = None
    action: str | None = None
    since: datetime | None = None
    until: datetime | None = None


class AuditRepository(Repository):
    def append(
        self,
        *,
        user_id: str,
        timestamp: datetime,
        entity_type: str,
        entity_id: str,
        action: str,
        previous_value: Any = None,
        new_value: Any = None,
        reason: str | None = None,
        request_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> AuditEntry:
        """Write one audit row. Values are JSON-encoded via the domain codec."""
        row = AuditLogRow(
            audit_id=new_id("aud"),
            user_id=user_id,
            timestamp=timestamp,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            previous_value=_json_or_none(previous_value),
            new_value=_json_or_none(new_value),
            reason=reason,
            request_id=request_id,
            details=dict(details or {}),
        )
        self._session.add(row)
        self._flush()
        return _entry(row)

    def query(
        self, filters: AuditFilters | None = None, *, offset: int = 0, limit: int = DEFAULT_PAGE_SIZE
    ) -> Page:
        filters = filters or AuditFilters()
        stmt = select(AuditLogRow)
        if filters.user_id:
            stmt = stmt.where(AuditLogRow.user_id == filters.user_id)
        if filters.entity_type:
            stmt = stmt.where(AuditLogRow.entity_type == filters.entity_type)
        if filters.entity_id:
            stmt = stmt.where(AuditLogRow.entity_id == filters.entity_id)
        if filters.action:
            stmt = stmt.where(AuditLogRow.action == filters.action)
        if filters.since is not None:
            stmt = stmt.where(AuditLogRow.timestamp >= filters.since)
        if filters.until is not None:
            stmt = stmt.where(AuditLogRow.timestamp < filters.until)
        stmt = stmt.order_by(AuditLogRow.timestamp.desc(), AuditLogRow.audit_id.desc())
        page = self._paginate(stmt, offset, limit)
        page.items = [_entry(r) for r in page.items]
        return page

    def for_entity(
        self, entity_type: str, entity_id: str, limit: int = DEFAULT_PAGE_SIZE
    ) -> list[AuditEntry]:
        page = self.query(AuditFilters(entity_type=entity_type, entity_id=entity_id), limit=limit)
        return list(page.items)


def _json_or_none(value: Any) -> dict[str, Any] | list[Any] | None:
    if value is None:
        return None
    encoded = to_jsonable(value)
    if isinstance(encoded, dict | list):
        return encoded
    return {"value": encoded}


def _entry(row: AuditLogRow) -> AuditEntry:
    return AuditEntry(
        audit_id=row.audit_id,
        user_id=row.user_id,
        timestamp=row.timestamp,
        entity_type=row.entity_type,
        entity_id=row.entity_id,
        action=row.action,
        previous_value=row.previous_value,
        new_value=row.new_value,
        reason=row.reason,
        request_id=row.request_id,
        details=dict(row.details or {}),
    )


__all__ = ["AuditFilters", "AuditRepository"]
