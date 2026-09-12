"""LockService: schedule locking (spec Phase 10).

Lock types and their meaning to the scheduler (``app.engines.scheduling.locks``):

* ``ORDER``     — the order is processed first; with ``machine_id`` it is pinned to it.
* ``MACHINE``   — the machine's window is reserved (default: now + ``lock_window_minutes``,
                  the "lock the next 4 hours" case); nothing new is placed inside it.
* ``SEQUENCE``  — the listed orders keep their relative order (optionally on a machine).
* ``TIME_SLOT`` — a window on a machine is reserved; with ``order_id`` the order is placed there.

Overlapping reservations on one machine and duplicate order/sequence locks are
rejected with ``409`` so planners release the earlier lock explicitly.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import structlog
from sqlalchemy.orm import Session

from app.core.clock import Clock, ensure_utc
from app.core.errors import ConflictError, ValidationError
from app.core.ids import new_id
from app.core.security import CurrentUser
from app.db.repositories.orders import OrderRepository
from app.db.repositories.overlays import LockRepository
from app.db.repositories.resources import MachineRepository
from app.domain.enums import LockType
from app.domain.models import ScheduleLock, TimeWindow
from app.services.audit_service import ENTITY_LOCK, AuditService
from app.services.base import Service, actor_id, require_reason
from app.services.snapshot_service import SnapshotService

log = structlog.get_logger(__name__)


class LockService(Service):
    def __init__(
        self,
        session: Session,
        clock: Clock,
        audit: AuditService,
        snapshots: SnapshotService | None = None,
    ) -> None:
        super().__init__(session, clock)
        self._audit = audit
        self._snapshots = snapshots or SnapshotService(session, clock)
        self._locks = LockRepository(session)
        self._orders = OrderRepository(session)
        self._machines = MachineRepository(session)

    # ---------------------------------------------------------------- reads
    def get(self, lock_id: str) -> ScheduleLock:
        return self._locks.get(lock_id)

    def list_active(
        self,
        *,
        machine_id: str | None = None,
        order_id: str | None = None,
        lock_type: LockType | None = None,
    ) -> list[ScheduleLock]:
        locks = self._locks.list_active(self.now())
        if machine_id:
            locks = [lk for lk in locks if lk.machine_id == machine_id]
        if order_id:
            locks = [lk for lk in locks if lk.order_id == order_id or order_id in lk.sequence_order_ids]
        if lock_type is not None:
            locks = [lk for lk in locks if lk.lock_type is lock_type]
        return locks

    # --------------------------------------------------------------- create
    def create(
        self,
        lock_type: LockType,
        user: CurrentUser | str,
        reason: str,
        *,
        order_id: str | None = None,
        machine_id: str | None = None,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        sequence_order_ids: list[str] | None = None,
    ) -> ScheduleLock:
        reason = require_reason(reason)
        now = self.now()
        sequence = [oid for oid in (sequence_order_ids or []) if oid]
        if machine_id is not None:
            self._machines.get(machine_id)
        if order_id is not None:
            self._open_order(order_id)

        window: TimeWindow | None
        if lock_type is LockType.ORDER:
            if order_id is None:
                raise ValidationError("order_id is required for an ORDER lock")
            window = self._window(window_start, window_end, now, required=False)
        elif lock_type is LockType.MACHINE:
            if machine_id is None:
                raise ValidationError("machine_id is required for a MACHINE lock")
            window = self._window(window_start, window_end, now, required=True)
        elif lock_type is LockType.TIME_SLOT:
            if machine_id is None:
                raise ValidationError("machine_id is required for a TIME_SLOT lock")
            window = self._window(window_start, window_end, now, required=True)
        else:  # SEQUENCE
            if len(sequence) < 2 or len(set(sequence)) != len(sequence):
                raise ValidationError(
                    "sequence_order_ids must list at least two distinct orders",
                    details={"sequence_order_ids": sequence},
                )
            for oid in sequence:
                self._open_order(oid)
            window = self._window(window_start, window_end, now, required=False)

        active = self._locks.list_active(now)
        self._check_conflicts(lock_type, order_id, machine_id, window, sequence, active)

        lock = self._locks.add(
            ScheduleLock(
                lock_id=new_id("lock"),
                lock_type=lock_type,
                created_by=actor_id(user),
                created_at=now,
                reason=reason,
                order_id=order_id,
                machine_id=machine_id,
                window=window,
                sequence_order_ids=sequence,
            )
        )
        self._audit.record(
            user,
            ENTITY_LOCK,
            lock.lock_id,
            "lock.create",
            None,
            lock,
            reason,
            {
                "lock_type": lock_type.value,
                "order_id": order_id,
                "machine_id": machine_id,
                "sequence_order_ids": sequence,
            },
        )
        log.info("lock.created", lock_id=lock.lock_id, lock_type=lock_type.value, machine_id=machine_id)
        return lock

    def unlock(self, lock_id: str, user: CurrentUser | str, reason: str) -> ScheduleLock:
        reason = require_reason(reason)
        current = self._locks.get(lock_id)
        if not current.active:
            raise ConflictError(f"lock '{lock_id}' is already released")
        released = self._locks.release(lock_id, released_by=actor_id(user), at=self.now())
        self._audit.record(
            user,
            ENTITY_LOCK,
            lock_id,
            "lock.release",
            current,
            released,
            reason,
            {
                "lock_type": current.lock_type.value,
                "order_id": current.order_id,
                "machine_id": current.machine_id,
            },
        )
        return released

    # ------------------------------------------------------------ internals
    def _open_order(self, order_id: str) -> None:
        order = self._orders.get(order_id)
        if not order.is_open:
            raise ConflictError(
                f"order '{order_id}' is closed ({order.order_status.value}) and cannot be locked",
                details={"order_status": order.order_status.value},
            )

    def _window(
        self, start: datetime | None, end: datetime | None, now: datetime, *, required: bool
    ) -> TimeWindow | None:
        if start is None and end is None:
            if not required:
                return None
            minutes = self._snapshots.active_config().scheduling.lock_window_minutes
            return TimeWindow(now, now + timedelta(minutes=minutes), "locked")
        if start is None:
            start = now
        start = ensure_utc(start)
        if end is None:
            minutes = self._snapshots.active_config().scheduling.lock_window_minutes
            end = start + timedelta(minutes=minutes)
        end = ensure_utc(end)
        if end <= start:
            raise ValidationError(
                "window_end must be after window_start",
                details={"window_start": start.isoformat(), "window_end": end.isoformat()},
            )
        if end <= now:
            raise ValidationError(
                "the lock window has already ended", details={"window_end": end.isoformat()}
            )
        return TimeWindow(start, end, "locked")

    @staticmethod
    def _check_conflicts(
        lock_type: LockType,
        order_id: str | None,
        machine_id: str | None,
        window: TimeWindow | None,
        sequence: list[str],
        active: list[ScheduleLock],
    ) -> None:
        if lock_type in (LockType.MACHINE, LockType.TIME_SLOT) and window is not None:
            for other in active:
                if other.machine_id != machine_id or other.window is None:
                    continue
                if other.lock_type in (LockType.MACHINE, LockType.TIME_SLOT) and other.window.overlaps(
                    window
                ):
                    raise ConflictError(
                        f"machine '{machine_id}' already has lock '{other.lock_id}' overlapping this window",
                        details={"conflicting_lock_id": other.lock_id, "lock_type": other.lock_type.value},
                    )
        if lock_type is LockType.ORDER:
            for other in active:
                if other.lock_type is LockType.ORDER and other.order_id == order_id:
                    raise ConflictError(
                        f"order '{order_id}' is already locked by '{other.lock_id}'",
                        details={"conflicting_lock_id": other.lock_id},
                    )
        if lock_type is LockType.SEQUENCE:
            wanted = set(sequence)
            for other in active:
                if other.lock_type is not LockType.SEQUENCE:
                    continue
                overlap = sorted(wanted & set(other.sequence_order_ids))
                if overlap:
                    raise ConflictError(
                        f"orders {overlap} are already part of sequence lock '{other.lock_id}'",
                        details={"conflicting_lock_id": other.lock_id, "order_ids": overlap},
                    )


__all__ = ["LockService"]
