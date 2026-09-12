"""OverrideService: human overrides of the priority engine (spec Phase 9).

Every action validates the order (exists, open), stores a
:class:`PriorityOverride` through :class:`OverrideRepository` and writes an
audit row against the *order* (``entity_type="order"``) carrying the user,
timestamp, previous value, new value and reason. The ERP is never written.

Representation of each override type (all honoured by the engines):

* ``INCREASE_PRIORITY`` / ``DECREASE_PRIORITY`` — ``value`` = delta points.
* ``SET_PRIORITY`` — ``value`` = absolute score 0..100.
* ``FORCE_NEXT`` — score pinned to 100; one active per order (the previous is cancelled).
* ``HOLD_ORDER`` / ``RELEASE_HOLD`` — the latest decision wins; readiness becomes ``ON_HOLD``.
* ``LOCK_MACHINE_ASSIGNMENT`` — ``target_machine_id`` pins eligibility (hard constraint).
* ``MOVE_ORDER`` — ``target_machine_id`` records the decision and a companion
  :class:`ScheduleLock` (``ORDER`` pin, or ``TIME_SLOT`` when a start time is given)
  makes the scheduler place it there; cancelling the override releases the lock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import structlog
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.errors import ConflictError, ValidationError
from app.core.ids import new_id
from app.core.security import CurrentUser
from app.db.repositories.orders import OrderRepository
from app.db.repositories.overlays import LockRepository, OverrideRepository
from app.db.repositories.priority import PriorityResultRepository
from app.db.repositories.resources import MachineRepository
from app.db.repositories.schedule import ScheduleRepository
from app.domain.enums import LockType, OverrideType
from app.domain.models import Machine, Order, PriorityOverride, ScheduleLock, TimeWindow
from app.domain.snapshot import PlanningSnapshot
from app.engines.constraints.readiness import next_operation
from app.engines.constraints.registry import default_constraint_engine
from app.services.audit_service import ENTITY_ORDER, AuditService
from app.services.base import Service, actor_id, ensure_future, require_reason
from app.services.snapshot_service import SnapshotService, effective_hold

log = structlog.get_logger(__name__)

SCORE_MIN = 0.0
SCORE_MAX = 100.0
MAX_DELTA_POINTS = 100.0


@dataclass(slots=True)
class MoveResult:
    override: PriorityOverride
    lock: ScheduleLock


class OverrideService(Service):
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
        self._orders = OrderRepository(session)
        self._machines = MachineRepository(session)
        self._overrides = OverrideRepository(session)
        self._locks = LockRepository(session)
        self._results = PriorityResultRepository(session)
        self._schedule = ScheduleRepository(session)

    # ---------------------------------------------------------------- reads
    def get(self, override_id: str) -> PriorityOverride:
        return self._overrides.get(override_id)

    def list_active(self) -> list[PriorityOverride]:
        return self._overrides.list_active(self.now())

    def list_for_order(self, order_id: str, *, active_only: bool = True) -> list[PriorityOverride]:
        self._orders.get(order_id)
        return self._overrides.list_for_order(order_id, active_only=active_only)

    # ------------------------------------------------------------ priority
    def increase_priority(
        self,
        order_id: str,
        points: float,
        user: CurrentUser | str,
        reason: str,
        expires_at: datetime | None = None,
    ) -> PriorityOverride:
        return self._delta(OverrideType.INCREASE_PRIORITY, order_id, points, user, reason, expires_at)

    def decrease_priority(
        self,
        order_id: str,
        points: float,
        user: CurrentUser | str,
        reason: str,
        expires_at: datetime | None = None,
    ) -> PriorityOverride:
        return self._delta(OverrideType.DECREASE_PRIORITY, order_id, points, user, reason, expires_at)

    def set_priority(
        self,
        order_id: str,
        score: float,
        user: CurrentUser | str,
        reason: str,
        expires_at: datetime | None = None,
    ) -> PriorityOverride:
        if not SCORE_MIN <= score <= SCORE_MAX:
            raise ValidationError(f"score must be in {SCORE_MIN:g}..{SCORE_MAX:g}", details={"score": score})
        order, reason, now, expires = self._prepare(order_id, reason, expires_at)
        previous = self._priority_state(order)
        # A new absolute score supersedes any earlier absolute score.
        superseded = self._deactivate(order_id, OverrideType.SET_PRIORITY, user, now)
        override = self._add(
            order_id, OverrideType.SET_PRIORITY, user, reason, now, value=float(score), expires_at=expires
        )
        self._audit_order(
            user,
            order_id,
            "override.set_priority",
            previous,
            {**self._priority_state(order, projected=float(score)), "override": override},
            reason,
            {"override_id": override.override_id, "superseded_override_ids": superseded},
        )
        return override

    def force_next(
        self,
        order_id: str,
        user: CurrentUser | str,
        reason: str,
        expires_at: datetime | None = None,
    ) -> PriorityOverride:
        order, reason, now, expires = self._prepare(order_id, reason, expires_at)
        previous = self._priority_state(order)
        superseded = self._deactivate(order_id, OverrideType.FORCE_NEXT, user, now)
        override = self._add(order_id, OverrideType.FORCE_NEXT, user, reason, now, expires_at=expires)
        self._audit_order(
            user,
            order_id,
            "override.force_next",
            previous,
            {**self._priority_state(order, projected=SCORE_MAX), "forced_next": True, "override": override},
            reason,
            {"override_id": override.override_id, "superseded_override_ids": superseded},
        )
        return override

    # ---------------------------------------------------------------- hold
    def hold(
        self,
        order_id: str,
        user: CurrentUser | str,
        reason: str,
        expires_at: datetime | None = None,
    ) -> PriorityOverride:
        order, reason, now, expires = self._prepare(order_id, reason, expires_at)
        on_hold, hold_reason, current = effective_hold(order, self._overrides.list_active(now), now)
        if on_hold:
            raise ConflictError(
                f"order '{order_id}' is already on hold",
                details={"hold_reason": hold_reason, "override_id": current.override_id if current else None},
            )
        released = self._deactivate(order_id, OverrideType.RELEASE_HOLD, user, now)
        override = self._add(order_id, OverrideType.HOLD_ORDER, user, reason, now, expires_at=expires)
        self._audit_order(
            user,
            order_id,
            "override.hold",
            {"on_hold": False, "hold_reason": None},
            {"on_hold": True, "hold_reason": reason, "override": override},
            reason,
            {"override_id": override.override_id, "superseded_override_ids": released},
        )
        return override

    def release(self, order_id: str, user: CurrentUser | str, reason: str) -> PriorityOverride:
        order, reason, now, _ = self._prepare(order_id, reason, None)
        on_hold, hold_reason, current = effective_hold(order, self._overrides.list_active(now), now)
        if not on_hold:
            raise ConflictError(f"order '{order_id}' is not on hold")
        if current is None:
            raise ConflictError(
                f"order '{order_id}' is held in the ERP and must be released there",
                details={"hold_reason": hold_reason},
            )
        released = self._deactivate(order_id, OverrideType.HOLD_ORDER, user, now)
        override = self._add(order_id, OverrideType.RELEASE_HOLD, user, reason, now)
        self._audit_order(
            user,
            order_id,
            "override.release",
            {"on_hold": True, "hold_reason": hold_reason, "hold_override_id": current.override_id},
            {"on_hold": False, "hold_reason": None, "override": override},
            reason,
            {"override_id": override.override_id, "released_override_ids": released},
        )
        return override

    # ---------------------------------------------------------------- move
    def move_order(
        self,
        order_id: str,
        target_machine_id: str,
        user: CurrentUser | str,
        reason: str,
        start_at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> MoveResult:
        order, reason, now, expires = self._prepare(order_id, reason, expires_at)
        start = ensure_future(start_at, now, "start_at")
        machine = self._machines.get(target_machine_id)
        if start is not None and expires is not None and start >= expires:
            raise ValidationError("start_at must be before expires_at")
        snapshot = self._snapshots.load_snapshot(now)
        minutes = self._check_eligible(order, machine, snapshot, now)
        previous = self._placement_state(order_id, now)
        superseded = self._deactivate(order_id, OverrideType.MOVE_ORDER, user, now)
        superseded += self._deactivate(order_id, OverrideType.LOCK_MACHINE_ASSIGNMENT, user, now)
        released = self._release_move_locks(order_id, user, now)
        override = self._add(
            order_id,
            OverrideType.MOVE_ORDER,
            user,
            reason,
            now,
            target_machine_id=machine.machine_id,
            expires_at=expires,
        )
        lock = self._locks.add(self._move_lock(override, start, minutes, now, expires))
        self._audit_order(
            user,
            order_id,
            "override.move",
            previous,
            {
                "target_machine_id": machine.machine_id,
                "start_at": start,
                "estimated_minutes": minutes,
                "override": override,
                "lock": lock,
            },
            reason,
            {
                "override_id": override.override_id,
                "lock_id": lock.lock_id,
                "superseded_override_ids": superseded,
                "released_lock_ids": released,
            },
        )
        return MoveResult(override=override, lock=lock)

    def lock_machine_assignment(
        self,
        order_id: str,
        machine_id: str,
        user: CurrentUser | str,
        reason: str,
        expires_at: datetime | None = None,
    ) -> PriorityOverride:
        order, reason, now, expires = self._prepare(order_id, reason, expires_at)
        machine = self._machines.get(machine_id)
        snapshot = self._snapshots.load_snapshot(now)
        self._check_eligible(order, machine, snapshot, now)
        previous = self._placement_state(order_id, now)
        superseded = self._deactivate(order_id, OverrideType.LOCK_MACHINE_ASSIGNMENT, user, now)
        override = self._add(
            order_id,
            OverrideType.LOCK_MACHINE_ASSIGNMENT,
            user,
            reason,
            now,
            target_machine_id=machine.machine_id,
            expires_at=expires,
        )
        self._audit_order(
            user,
            order_id,
            "override.lock_machine_assignment",
            previous,
            {"pinned_machine_id": machine.machine_id, "override": override},
            reason,
            {"override_id": override.override_id, "superseded_override_ids": superseded},
        )
        return override

    # -------------------------------------------------------------- cancel
    def cancel(self, override_id: str, user: CurrentUser | str, reason: str) -> PriorityOverride:
        reason = require_reason(reason)
        now = self.now()
        current = self._overrides.get(override_id)
        if not current.active:
            raise ConflictError(f"override '{override_id}' is already cancelled")
        updated = self._overrides.deactivate(override_id, released_by=actor_id(user), at=now)
        released: list[str] = []
        if current.override_type is OverrideType.MOVE_ORDER:
            released = self._release_move_locks(current.order_id, user, now, created_by=current.created_by)
        self._audit_order(
            user,
            current.order_id,
            "override.cancel",
            current,
            updated,
            reason,
            {
                "override_id": override_id,
                "override_type": current.override_type.value,
                "released_lock_ids": released,
            },
        )
        return updated

    # ------------------------------------------------------------ internals
    def _delta(
        self,
        kind: OverrideType,
        order_id: str,
        points: float,
        user: CurrentUser | str,
        reason: str,
        expires_at: datetime | None,
    ) -> PriorityOverride:
        if points <= 0 or points > MAX_DELTA_POINTS:
            raise ValidationError(f"points must be in (0, {MAX_DELTA_POINTS:g}]", details={"points": points})
        order, reason, now, expires = self._prepare(order_id, reason, expires_at)
        previous = self._priority_state(order)
        override = self._add(order_id, kind, user, reason, now, value=float(points), expires_at=expires)
        signed = points if kind is OverrideType.INCREASE_PRIORITY else -points
        projected = None
        if previous["stored_score"] is not None:
            projected = max(SCORE_MIN, min(SCORE_MAX, float(previous["stored_score"]) + signed))
        action = "override.increase_priority" if signed > 0 else "override.decrease_priority"
        self._audit_order(
            user,
            order_id,
            action,
            previous,
            {
                **self._priority_state(order, projected=projected),
                "delta_points": signed,
                "override": override,
            },
            reason,
            {"override_id": override.override_id},
        )
        return override

    def _prepare(
        self, order_id: str, reason: str, expires_at: datetime | None
    ) -> tuple[Order, str, datetime, datetime | None]:
        reason = require_reason(reason)
        now = self.now()
        order = self._orders.get(order_id)
        if not order.is_open:
            raise ConflictError(
                f"order '{order_id}' is closed ({order.order_status.value}); overrides apply to open orders",
                details={
                    "order_status": order.order_status.value,
                    "pending_quantity": order.pending_quantity,
                },
            )
        return order, reason, now, ensure_future(expires_at, now, "expires_at")

    def _add(
        self,
        order_id: str,
        kind: OverrideType,
        user: CurrentUser | str,
        reason: str,
        now: datetime,
        *,
        value: float | None = None,
        target_machine_id: str | None = None,
        expires_at: datetime | None = None,
    ) -> PriorityOverride:
        override = PriorityOverride(
            override_id=new_id("ovr"),
            order_id=order_id,
            override_type=kind,
            created_by=actor_id(user),
            created_at=now,
            reason=reason,
            value=value,
            target_machine_id=target_machine_id,
            expires_at=expires_at,
        )
        stored = self._overrides.add(override)
        log.info("override.created", override_id=stored.override_id, order_id=order_id, type=kind.value)
        return stored

    def _deactivate(
        self, order_id: str, kind: OverrideType, user: CurrentUser | str, now: datetime
    ) -> list[str]:
        ids = [
            o.override_id
            for o in self._overrides.list_for_order(order_id, active_only=True, override_type=kind)
        ]
        if ids:
            self._overrides.deactivate_for_order(
                order_id, released_by=actor_id(user), at=now, override_type=kind
            )
        return ids

    def _priority_state(self, order: Order, projected: float | None = None) -> dict[str, Any]:
        result = self._results.latest_for_order(order.order_id)
        active = self._overrides.list_for_order(order.order_id, active_only=True)
        state: dict[str, Any] = {
            "stored_score": result.score if result else None,
            "stored_rank": result.rank if result else None,
            "active_override_ids": [o.override_id for o in active],
        }
        if projected is not None:
            state["projected_score"] = projected
        return state

    def _placement_state(self, order_id: str, now: datetime) -> dict[str, Any]:
        entries = self._schedule.get_entries(order_id=order_id)
        pins = [
            o
            for o in self._overrides.list_for_order(order_id, active_only=True)
            if o.target_machine_id is not None and o.is_active_at(now)
        ]
        return {
            "scheduled_machine_id": entries[0].machine_id if entries else None,
            "scheduled_start": entries[0].start if entries else None,
            "pinned_machine_id": pins[-1].target_machine_id if pins else None,
            "active_override_ids": [o.override_id for o in pins],
        }

    def _check_eligible(
        self, order: Order, machine: Machine, snapshot: PlanningSnapshot, now: datetime
    ) -> float:
        """Reject a pin the hard constraints would refuse; returns the estimated minutes on the machine."""
        if order.order_id not in snapshot.orders:
            raise ConflictError(f"order '{order.order_id}' is not part of the planning snapshot")
        # Evaluate without the order's own pins so a re-pin to another machine is possible.
        snapshot.overrides = [o for o in snapshot.overrides if o.order_id != order.order_id]
        snapshot.locks = [lk for lk in snapshot.locks if lk.order_id != order.order_id]
        config = self._snapshots.active_config().scheduling
        engine = default_constraint_engine(config)
        op, _synthetic = next_operation(snapshot.orders[order.order_id], snapshot)
        eligibility = engine.eligible_machines(op, snapshot, now, order=snapshot.orders[order.order_id])
        if machine.machine_id not in eligibility.eligible_machine_ids:
            violations = eligibility.rejected.get(machine.machine_id)
            messages = (
                [v.message for v in violations]
                if violations
                else [f"{machine.machine_id} is not a candidate for {op.operation_type.value}"]
            )
            raise ValidationError(
                f"machine '{machine.machine_id}' cannot run operation '{op.operation_id}'",
                details={"operation_id": op.operation_id, "violations": messages},
            )
        run = op.run_minutes_on(machine)
        setup = op.setup_minutes
        if setup is None:
            setup = order.estimated_setup_minutes
        if setup is None:
            setup = config.setup.default_setup_minutes
        return float(setup) + float(run if run is not None else config.lock_window_minutes)

    def _move_lock(
        self,
        override: PriorityOverride,
        start: datetime | None,
        minutes: float,
        now: datetime,
        expires: datetime | None,
    ) -> ScheduleLock:
        if start is not None:
            lock_type = LockType.TIME_SLOT
            end = start + timedelta(minutes=max(minutes, 1.0))
            window: TimeWindow | None = TimeWindow(start, end, f"moved by {override.created_by}")
        else:
            lock_type = LockType.ORDER
            window = TimeWindow(now, expires, "move") if expires is not None else None
        return ScheduleLock(
            lock_id=new_id("lock"),
            lock_type=lock_type,
            created_by=override.created_by,
            created_at=now,
            reason=override.reason,
            order_id=override.order_id,
            machine_id=override.target_machine_id,
            window=window,
        )

    def _release_move_locks(
        self, order_id: str, user: CurrentUser | str, now: datetime, created_by: str | None = None
    ) -> list[str]:
        """Release the companion locks of MOVE_ORDER overrides on ``order_id``."""
        moves = self._overrides.list_for_order(
            order_id, active_only=False, override_type=OverrideType.MOVE_ORDER
        )
        keys = {
            (m.created_by, m.created_at, m.target_machine_id)
            for m in moves
            if created_by is None or m.created_by == created_by
        }
        released: list[str] = []
        for lock in self._locks.list_for_order(order_id, active_only=True):
            if lock.lock_type not in (LockType.ORDER, LockType.TIME_SLOT):
                continue
            if (lock.created_by, lock.created_at, lock.machine_id) in keys:
                self._locks.release(lock.lock_id, released_by=actor_id(user), at=now)
                released.append(lock.lock_id)
        return released

    def _audit_order(
        self,
        user: CurrentUser | str,
        order_id: str,
        action: str,
        previous: Any,
        new: Any,
        reason: str,
        details: dict[str, Any],
    ) -> None:
        self._audit.record(user, ENTITY_ORDER, order_id, action, previous, new, reason, details)


__all__ = ["MAX_DELTA_POINTS", "SCORE_MAX", "SCORE_MIN", "MoveResult", "OverrideService"]
