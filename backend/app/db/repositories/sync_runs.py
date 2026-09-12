"""SyncRunRepository: history of ERP synchronisation runs."""

from __future__ import annotations

from sqlalchemy import select

from app.core.errors import NotFoundError
from app.db.models import SyncRunRow
from app.db.records import Page, SyncRunRecord
from app.db.repositories.base import DEFAULT_PAGE_SIZE, Repository
from app.domain.enums import SyncMode


class SyncRunRepository(Repository):
    def start(self, record: SyncRunRecord) -> SyncRunRecord:
        row = SyncRunRow(run_id=record.run_id)
        _apply(row, record)
        self._session.add(row)
        self._flush()
        return _record(row)

    def save(self, record: SyncRunRecord) -> SyncRunRecord:
        row = self._session.get(SyncRunRow, record.run_id)
        if row is None:
            raise NotFoundError(f"sync run '{record.run_id}' not found", details={"run_id": record.run_id})
        _apply(row, record)
        self._flush()
        return _record(row)

    def get(self, run_id: str) -> SyncRunRecord:
        row = self._session.get(SyncRunRow, run_id)
        if row is None:
            raise NotFoundError(f"sync run '{run_id}' not found", details={"run_id": run_id})
        return _record(row)

    def list(self, *, offset: int = 0, limit: int = DEFAULT_PAGE_SIZE) -> Page:
        stmt = select(SyncRunRow).order_by(SyncRunRow.started_at.desc(), SyncRunRow.run_id.desc())
        page = self._paginate(stmt, offset, limit)
        page.items = [_record(r) for r in page.items]
        return page

    def latest(self, status: str | None = None) -> SyncRunRecord | None:
        stmt = select(SyncRunRow).order_by(SyncRunRow.started_at.desc(), SyncRunRow.run_id.desc()).limit(1)
        if status:
            stmt = stmt.where(SyncRunRow.status == status)
        row = self._session.execute(stmt).scalar_one_or_none()
        return _record(row) if row else None

    def latest_successful_started_at(self) -> SyncRunRecord | None:
        """The newest completed run — its ``started_at`` is the ``since`` for incremental syncs."""
        return self.latest(status="completed")


def _apply(row: SyncRunRow, record: SyncRunRecord) -> None:
    row.mode = record.mode.value
    row.status = record.status
    row.connector = record.connector
    row.started_at = record.started_at
    row.finished_at = record.finished_at
    row.since = record.since
    row.records_fetched = dict(record.records_fetched)
    row.records_upserted = dict(record.records_upserted)
    row.issues_count = record.issues_count
    row.triggered_by = record.triggered_by
    row.error_message = record.error_message
    row.details = dict(record.details)


def _record(row: SyncRunRow) -> SyncRunRecord:
    return SyncRunRecord(
        run_id=row.run_id,
        mode=SyncMode(row.mode),
        status=row.status,
        started_at=row.started_at,
        connector=row.connector,
        finished_at=row.finished_at,
        since=row.since,
        records_fetched=dict(row.records_fetched or {}),
        records_upserted=dict(row.records_upserted or {}),
        issues_count=row.issues_count,
        triggered_by=row.triggered_by,
        error_message=row.error_message,
        details=dict(row.details or {}),
    )


__all__ = ["SyncRunRepository"]
