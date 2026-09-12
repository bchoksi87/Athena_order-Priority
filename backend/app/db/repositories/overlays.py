"""Planner overlays: LockRepository, OverrideRepository, ExpediteRepository."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_, select

from app.core.errors import NotFoundError
from app.db.mappers import (
    expedite_from_row,
    expedite_to_row,
    lock_from_row,
    lock_to_row,
    override_from_row,
    override_to_row,
)
from app.db.models import ExpediteRow, PriorityOverrideRow, ScheduleLockRow
from app.db.repositories.base import Repository
from app.domain.enums import OverrideType
from app.domain.models import Expedite, PriorityOverride, ScheduleLock


class LockRepository(Repository):
    def add(self, lock: ScheduleLock) -> ScheduleLock:
        row = lock_to_row(lock)
        self._session.add(row)
        self._flush()
        return lock_from_row(row)

    def get(self, lock_id: str) -> ScheduleLock:
        row = self._session.get(ScheduleLockRow, lock_id)
        if row is None:
            raise NotFoundError(f"lock '{lock_id}' not found", details={"lock_id": lock_id})
        return lock_from_row(row)

    def list_active(self, now: datetime | None = None) -> list[ScheduleLock]:
        """Active locks; when ``now`` is given, locks whose window already ended are omitted."""
        stmt = select(ScheduleLockRow).where(ScheduleLockRow.active.is_(True))
        if now is not None:
            stmt = stmt.where(or_(ScheduleLockRow.window_end.is_(None), ScheduleLockRow.window_end > now))
        stmt = stmt.order_by(ScheduleLockRow.lock_created_at, ScheduleLockRow.lock_id)
        return [lock_from_row(r) for r in self._session.execute(stmt).scalars()]

    def list_for_order(self, order_id: str, active_only: bool = True) -> list[ScheduleLock]:
        stmt = select(ScheduleLockRow).where(ScheduleLockRow.order_id == order_id)
        if active_only:
            stmt = stmt.where(ScheduleLockRow.active.is_(True))
        return [
            lock_from_row(r) for r in self._session.execute(stmt.order_by(ScheduleLockRow.lock_id)).scalars()
        ]

    def release(self, lock_id: str, *, released_by: str, at: datetime) -> ScheduleLock:
        row = self._session.get(ScheduleLockRow, lock_id)
        if row is None:
            raise NotFoundError(f"lock '{lock_id}' not found", details={"lock_id": lock_id})
        row.active = False
        row.released_by = released_by
        row.released_at = at
        self._flush()
        return lock_from_row(row)


class OverrideRepository(Repository):
    def add(self, override: PriorityOverride) -> PriorityOverride:
        row = override_to_row(override)
        self._session.add(row)
        self._flush()
        return override_from_row(row)

    def get(self, override_id: str) -> PriorityOverride:
        row = self._session.get(PriorityOverrideRow, override_id)
        if row is None:
            raise NotFoundError(f"override '{override_id}' not found", details={"override_id": override_id})
        return override_from_row(row)

    def list_active(self, now: datetime | None = None) -> list[PriorityOverride]:
        stmt = select(PriorityOverrideRow).where(PriorityOverrideRow.active.is_(True))
        if now is not None:
            stmt = stmt.where(
                or_(PriorityOverrideRow.expires_at.is_(None), PriorityOverrideRow.expires_at > now)
            )
        stmt = stmt.order_by(PriorityOverrideRow.override_created_at, PriorityOverrideRow.override_id)
        return [override_from_row(r) for r in self._session.execute(stmt).scalars()]

    def list_for_order(
        self, order_id: str, *, active_only: bool = True, override_type: OverrideType | None = None
    ) -> list[PriorityOverride]:
        stmt = select(PriorityOverrideRow).where(PriorityOverrideRow.order_id == order_id)
        if active_only:
            stmt = stmt.where(PriorityOverrideRow.active.is_(True))
        if override_type is not None:
            stmt = stmt.where(PriorityOverrideRow.override_type == override_type.value)
        stmt = stmt.order_by(PriorityOverrideRow.override_created_at, PriorityOverrideRow.override_id)
        return [override_from_row(r) for r in self._session.execute(stmt).scalars()]

    def deactivate(self, override_id: str, *, released_by: str, at: datetime) -> PriorityOverride:
        row = self._session.get(PriorityOverrideRow, override_id)
        if row is None:
            raise NotFoundError(f"override '{override_id}' not found", details={"override_id": override_id})
        row.active = False
        row.released_by = released_by
        row.released_at = at
        self._flush()
        return override_from_row(row)

    def deactivate_for_order(
        self, order_id: str, *, released_by: str, at: datetime, override_type: OverrideType | None = None
    ) -> int:
        count = 0
        stmt = select(PriorityOverrideRow).where(
            PriorityOverrideRow.order_id == order_id, PriorityOverrideRow.active.is_(True)
        )
        if override_type is not None:
            stmt = stmt.where(PriorityOverrideRow.override_type == override_type.value)
        for row in self._session.execute(stmt).scalars():
            row.active = False
            row.released_by = released_by
            row.released_at = at
            count += 1
        self._flush()
        return count


class ExpediteRepository(Repository):
    def add(self, expedite: Expedite) -> Expedite:
        row = expedite_to_row(expedite)
        self._session.add(row)
        self._flush()
        return expedite_from_row(row)

    def get(self, expedite_id: str) -> Expedite:
        row = self._session.get(ExpediteRow, expedite_id)
        if row is None:
            raise NotFoundError(f"expedite '{expedite_id}' not found", details={"expedite_id": expedite_id})
        return expedite_from_row(row)

    def list_active(self, now: datetime | None = None) -> list[Expedite]:
        stmt = select(ExpediteRow).where(ExpediteRow.active.is_(True))
        if now is not None:
            stmt = stmt.where(ExpediteRow.expires_at > now)
        stmt = stmt.order_by(ExpediteRow.expedite_created_at, ExpediteRow.expedite_id)
        return [expedite_from_row(r) for r in self._session.execute(stmt).scalars()]

    def list_for_order(self, order_id: str, active_only: bool = True) -> list[Expedite]:
        stmt = select(ExpediteRow).where(ExpediteRow.order_id == order_id)
        if active_only:
            stmt = stmt.where(ExpediteRow.active.is_(True))
        stmt = stmt.order_by(ExpediteRow.expedite_created_at, ExpediteRow.expedite_id)
        return [expedite_from_row(r) for r in self._session.execute(stmt).scalars()]

    def deactivate(self, expedite_id: str, *, released_by: str, at: datetime) -> Expedite:
        row = self._session.get(ExpediteRow, expedite_id)
        if row is None:
            raise NotFoundError(f"expedite '{expedite_id}' not found", details={"expedite_id": expedite_id})
        row.active = False
        row.released_by = released_by
        row.released_at = at
        self._flush()
        return expedite_from_row(row)


__all__ = ["ExpediteRepository", "LockRepository", "OverrideRepository"]
