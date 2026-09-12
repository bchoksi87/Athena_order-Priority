"""ScheduleRepository and OptimizationRunRepository (contract §10)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select

from app.core.errors import ConflictError, NotFoundError
from app.core.ids import new_id
from app.db.mappers import schedule_entry_from_row, schedule_entry_to_row
from app.db.models import OptimizationRunRow, ScheduleEntryRow, ScheduleVersionRow
from app.db.records import OptimizationRunRecord, Page, ScheduleVersionInfo
from app.db.repositories.base import DEFAULT_PAGE_SIZE, Repository
from app.db.snapshot_codec import to_jsonable
from app.domain.enums import ScheduleStatus
from app.domain.results import ScheduleEntry, ScheduleResult

#: Allowed status transitions (contract §10: DRAFT -> APPROVED -> PUBLISHED -> SUPERSEDED).
SCHEDULE_TRANSITIONS: dict[ScheduleStatus, frozenset[ScheduleStatus]] = {
    ScheduleStatus.DRAFT: frozenset(
        {ScheduleStatus.APPROVED, ScheduleStatus.REJECTED, ScheduleStatus.SUPERSEDED}
    ),
    ScheduleStatus.APPROVED: frozenset(
        {ScheduleStatus.PUBLISHED, ScheduleStatus.REJECTED, ScheduleStatus.SUPERSEDED}
    ),
    ScheduleStatus.PUBLISHED: frozenset({ScheduleStatus.SUPERSEDED}),
    ScheduleStatus.SUPERSEDED: frozenset(),
    ScheduleStatus.REJECTED: frozenset(),
}


class ScheduleRepository(Repository):
    # ----------------------------------------------------------- versions
    def next_version_number(self) -> int:
        current = self._session.execute(select(func.max(ScheduleVersionRow.version_number))).scalar_one()
        return int(current or 0) + 1

    def create_version(
        self,
        result: ScheduleResult,
        *,
        generated_by: str | None,
        run_id: str | None = None,
        input_snapshot_id: str | None = None,
        status: ScheduleStatus = ScheduleStatus.DRAFT,
        label: str | None = None,
        notes: str | None = None,
        analytics: dict[str, Any] | None = None,
        details: dict[str, Any] | None = None,
    ) -> ScheduleVersionInfo:
        """Store a :class:`ScheduleResult` as a new monotonic schedule version."""
        version_id = new_id("sv")
        row = ScheduleVersionRow(
            schedule_version_id=version_id,
            version_number=self.next_version_number(),
            status=status.value,
            label=label,
            run_id=run_id,
            input_snapshot_id=input_snapshot_id,
            algorithm=result.algorithm,
            algorithm_version=result.algorithm_version,
            profile_id=result.profile_id,
            profile_version=result.profile_version,
            config_version=result.config_version,
            generated_by=generated_by,
            generated_at=result.generated_at,
            horizon_start=result.horizon_start,
            horizon_end=result.horizon_end,
            entry_count=len(result.entries),
            metrics=to_jsonable(result.metrics),
            quality=to_jsonable(result.quality) if result.quality else None,
            unscheduled=[to_jsonable(u) for u in result.unscheduled],
            warnings=list(result.warnings),
            notes=notes,
            analytics=analytics,
            details=dict(details or {}),
        )
        self._session.add(row)
        self._session.add_all(schedule_entry_to_row(e, version_id) for e in result.entries)
        self._flush()
        return _version_info(row)

    def get_version(self, version_number: int) -> ScheduleVersionInfo:
        return _version_info(self._version_row(version_number))

    def get_version_by_id(self, schedule_version_id: str) -> ScheduleVersionInfo:
        row = self._session.get(ScheduleVersionRow, schedule_version_id)
        if row is None:
            raise NotFoundError(
                f"schedule version '{schedule_version_id}' not found",
                details={"schedule_version_id": schedule_version_id},
            )
        return _version_info(row)

    def get_by_run_id(self, run_id: str) -> ScheduleVersionInfo | None:
        stmt = select(ScheduleVersionRow).where(ScheduleVersionRow.run_id == run_id).limit(1)
        row = self._session.execute(stmt).scalar_one_or_none()
        return _version_info(row) if row else None

    def get_latest(self, status: ScheduleStatus | None = None) -> ScheduleVersionInfo | None:
        """Newest version, optionally restricted to one status."""
        stmt = select(ScheduleVersionRow).order_by(ScheduleVersionRow.version_number.desc()).limit(1)
        if status is not None:
            stmt = stmt.where(ScheduleVersionRow.status == status.value)
        row = self._session.execute(stmt).scalar_one_or_none()
        return _version_info(row) if row else None

    def get_current(self) -> ScheduleVersionInfo | None:
        """The schedule the shop floor should follow: latest PUBLISHED, else APPROVED, else DRAFT."""
        for status in (ScheduleStatus.PUBLISHED, ScheduleStatus.APPROVED, ScheduleStatus.DRAFT):
            info = self.get_latest(status)
            if info is not None:
                return info
        return None

    def list_versions(
        self, *, status: ScheduleStatus | None = None, offset: int = 0, limit: int = DEFAULT_PAGE_SIZE
    ) -> Page:
        stmt = select(ScheduleVersionRow).order_by(ScheduleVersionRow.version_number.desc())
        if status is not None:
            stmt = stmt.where(ScheduleVersionRow.status == status.value)
        page = self._paginate(stmt, offset, limit)
        page.items = [_version_info(r) for r in page.items]
        return page

    def count_versions(self, status: ScheduleStatus | None = None) -> int:
        stmt = select(ScheduleVersionRow)
        if status is not None:
            stmt = stmt.where(ScheduleVersionRow.status == status.value)
        return self._count(stmt)

    def update_details(self, version_number: int, patch: dict[str, Any]) -> ScheduleVersionInfo:
        """Merge ``patch`` into the version's ``details`` JSON (receipts, replan decisions ...)."""
        row = self._version_row(version_number)
        merged = dict(row.details or {})
        merged.update(patch)
        row.details = merged
        self._flush()
        return _version_info(row)

    def set_status(
        self,
        version_number: int,
        status: ScheduleStatus,
        *,
        user_id: str | None,
        at: datetime,
        enforce_transition: bool = True,
    ) -> ScheduleVersionInfo:
        row = self._version_row(version_number)
        current = ScheduleStatus(row.status)
        if current == status:
            return _version_info(row)
        if enforce_transition and status not in SCHEDULE_TRANSITIONS[current]:
            raise ConflictError(
                f"schedule v{version_number} cannot go from {current.value} to {status.value}",
                details={"from": current.value, "to": status.value},
            )
        row.status = status.value
        if status == ScheduleStatus.APPROVED:
            row.approved_by, row.approved_at = user_id, at
        elif status == ScheduleStatus.PUBLISHED:
            row.published_by, row.published_at = user_id, at
        elif status == ScheduleStatus.SUPERSEDED:
            row.superseded_at = at
        self._flush()
        return _version_info(row)

    def supersede_others(self, keep_version_number: int, at: datetime) -> int:
        """Mark every other PUBLISHED/APPROVED version as SUPERSEDED (on publish)."""
        stmt = select(ScheduleVersionRow).where(
            ScheduleVersionRow.version_number != keep_version_number,
            ScheduleVersionRow.status.in_([ScheduleStatus.PUBLISHED.value, ScheduleStatus.APPROVED.value]),
        )
        count = 0
        for row in self._session.execute(stmt).scalars():
            row.status = ScheduleStatus.SUPERSEDED.value
            row.superseded_at = at
            count += 1
        self._flush()
        return count

    # ------------------------------------------------------------ entries
    def get_entries(
        self,
        version_number: int | None = None,
        *,
        schedule_version_id: str | None = None,
        machine_id: str | None = None,
        order_id: str | None = None,
        start_from: datetime | None = None,
        start_to: datetime | None = None,
        overlapping: tuple[datetime, datetime] | None = None,
    ) -> list[ScheduleEntry]:
        """Entries of one version (by number or id; default the current version).

        ``overlapping=(a, b)`` returns entries whose ``[setup_start, end)`` overlaps
        ``[a, b)`` (used for day views); ``start_from``/``start_to`` filter on ``start``.
        """
        if schedule_version_id is None:
            if version_number is None:
                current = self.get_current()
                if current is None:
                    return []
                schedule_version_id = current.schedule_version_id
            else:
                schedule_version_id = self._version_row(version_number).schedule_version_id
        stmt = select(ScheduleEntryRow).where(ScheduleEntryRow.schedule_version_id == schedule_version_id)
        if machine_id:
            stmt = stmt.where(ScheduleEntryRow.machine_id == machine_id)
        if order_id:
            stmt = stmt.where(ScheduleEntryRow.order_id == order_id)
        if start_from is not None:
            stmt = stmt.where(ScheduleEntryRow.start >= start_from)
        if start_to is not None:
            stmt = stmt.where(ScheduleEntryRow.start < start_to)
        if overlapping is not None:
            window_start, window_end = overlapping
            stmt = stmt.where(ScheduleEntryRow.setup_start < window_end, ScheduleEntryRow.end > window_start)
        stmt = stmt.order_by(
            ScheduleEntryRow.machine_id, ScheduleEntryRow.sequence_on_machine, ScheduleEntryRow.start
        )
        return [schedule_entry_from_row(r) for r in self._session.execute(stmt).scalars()]

    def entry_count(self, version_number: int) -> int:
        row = self._version_row(version_number)
        stmt = select(func.count(ScheduleEntryRow.row_id)).where(
            ScheduleEntryRow.schedule_version_id == row.schedule_version_id
        )
        return int(self._session.execute(stmt).scalar_one())

    def _version_row(self, version_number: int) -> ScheduleVersionRow:
        stmt = select(ScheduleVersionRow).where(ScheduleVersionRow.version_number == version_number)
        row = self._session.execute(stmt).scalar_one_or_none()
        if row is None:
            raise NotFoundError(
                f"schedule version {version_number} not found", details={"version": version_number}
            )
        return row


class OptimizationRunRepository(Repository):
    def start(self, record: OptimizationRunRecord) -> OptimizationRunRecord:
        row = OptimizationRunRow(run_id=record.run_id)
        _apply_run(row, record)
        self._session.add(row)
        self._flush()
        return _run_record(row)

    def save(self, record: OptimizationRunRecord) -> OptimizationRunRecord:
        """Update an existing run (typically to mark it finished/failed)."""
        row = self._session.get(OptimizationRunRow, record.run_id)
        if row is None:
            raise NotFoundError(f"run '{record.run_id}' not found", details={"run_id": record.run_id})
        _apply_run(row, record)
        self._flush()
        return _run_record(row)

    def get(self, run_id: str) -> OptimizationRunRecord:
        row = self._session.get(OptimizationRunRow, run_id)
        if row is None:
            raise NotFoundError(f"run '{run_id}' not found", details={"run_id": run_id})
        return _run_record(row)

    def list(self, *, kind: str | None = None, offset: int = 0, limit: int = DEFAULT_PAGE_SIZE) -> Page:
        stmt = select(OptimizationRunRow).order_by(
            OptimizationRunRow.started_at.desc(), OptimizationRunRow.run_id.desc()
        )
        if kind:
            stmt = stmt.where(OptimizationRunRow.kind == kind)
        page = self._paginate(stmt, offset, limit)
        page.items = [_run_record(r) for r in page.items]
        return page

    def count(self, kind: str | None = None, status: str | None = None) -> int:
        stmt = select(OptimizationRunRow)
        if kind:
            stmt = stmt.where(OptimizationRunRow.kind == kind)
        if status:
            stmt = stmt.where(OptimizationRunRow.status == status)
        return self._count(stmt)

    def latest(self, kind: str | None = None, status: str | None = None) -> OptimizationRunRecord | None:
        stmt = select(OptimizationRunRow).order_by(OptimizationRunRow.started_at.desc()).limit(1)
        if kind:
            stmt = stmt.where(OptimizationRunRow.kind == kind)
        if status:
            stmt = stmt.where(OptimizationRunRow.status == status)
        row = self._session.execute(stmt).scalar_one_or_none()
        return _run_record(row) if row else None


# ------------------------------------------------------------------ helpers


def _version_info(row: ScheduleVersionRow) -> ScheduleVersionInfo:
    return ScheduleVersionInfo(
        schedule_version_id=row.schedule_version_id,
        version_number=row.version_number,
        status=ScheduleStatus(row.status),
        algorithm=row.algorithm,
        algorithm_version=row.algorithm_version,
        profile_id=row.profile_id,
        profile_version=row.profile_version,
        config_version=row.config_version,
        generated_at=row.generated_at,
        horizon_start=row.horizon_start,
        horizon_end=row.horizon_end,
        label=row.label,
        run_id=row.run_id,
        input_snapshot_id=row.input_snapshot_id,
        generated_by=row.generated_by,
        approved_by=row.approved_by,
        approved_at=row.approved_at,
        published_by=row.published_by,
        published_at=row.published_at,
        superseded_at=row.superseded_at,
        entry_count=row.entry_count,
        metrics=dict(row.metrics or {}),
        quality=dict(row.quality) if row.quality else None,
        unscheduled=list(row.unscheduled or []),
        warnings=list(row.warnings or []),
        notes=row.notes,
        analytics=dict(row.analytics) if row.analytics else None,
        details=dict(row.details or {}),
    )


def _apply_run(row: OptimizationRunRow, record: OptimizationRunRecord) -> None:
    row.kind = record.kind
    row.status = record.status
    row.started_at = record.started_at
    row.finished_at = record.finished_at
    row.orders_considered = record.orders_considered
    row.orders_scheduled = record.orders_scheduled
    row.orders_blocked = record.orders_blocked
    row.objective_score = record.objective_score
    row.quality_score = record.quality_score
    row.algorithm = record.algorithm
    row.algorithm_version = record.algorithm_version
    row.profile_id = record.profile_id
    row.profile_version = record.profile_version
    row.config_version = record.config_version
    row.input_snapshot_id = record.input_snapshot_id
    row.triggered_by = record.triggered_by
    row.trigger_reason = record.trigger_reason
    row.error_message = record.error_message
    row.metrics = dict(record.metrics)
    row.warnings = list(record.warnings)


def _run_record(row: OptimizationRunRow) -> OptimizationRunRecord:
    return OptimizationRunRecord(
        run_id=row.run_id,
        kind=row.kind,
        status=row.status,
        started_at=row.started_at,
        finished_at=row.finished_at,
        orders_considered=row.orders_considered,
        orders_scheduled=row.orders_scheduled,
        orders_blocked=row.orders_blocked,
        objective_score=row.objective_score,
        quality_score=row.quality_score,
        algorithm=row.algorithm,
        algorithm_version=row.algorithm_version,
        profile_id=row.profile_id,
        profile_version=row.profile_version,
        config_version=row.config_version,
        input_snapshot_id=row.input_snapshot_id,
        triggered_by=row.triggered_by,
        trigger_reason=row.trigger_reason,
        error_message=row.error_message,
        metrics=dict(row.metrics or {}),
        warnings=list(row.warnings or []),
    )


__all__ = ["SCHEDULE_TRANSITIONS", "OptimizationRunRepository", "ScheduleRepository"]
