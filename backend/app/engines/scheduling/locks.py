"""Planner locks as the scheduler sees them (spec Phase 9/10).

``snapshot.active_locks`` is normalised once per run into a :class:`LockIndex`:

* ``TIME_SLOT`` / ``MACHINE`` lock with a window → the window is *reserved* on
  that machine: no new work is placed inside it. When the lock also names an
  order, that order's next operation is placed at the window start (locked).
* ``ORDER`` (or ``MACHINE``) lock naming an order and a machine → the order is
  *pinned* to the machine (the hard constraint ``locked_machine_assignment``
  already restricts eligibility) and goes first in that machine's queue, in
  lock creation order.
* ``ORDER`` lock without a machine → the order's previous entries are kept
  verbatim (when the caller supplies them) and it is processed first.
* ``SEQUENCE`` lock → the listed orders keep their relative order.
* ``MACHINE`` lock naming neither order nor window → the machine is *frozen*:
  its previous entries are reproduced and nothing new is placed on it.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from app.core.clock import ensure_utc
from app.domain.enums import LockType
from app.domain.models import ScheduleLock, TimeWindow
from app.domain.snapshot import PlanningSnapshot


@dataclass(slots=True)
class LockIndex:
    reserved: dict[str, list[TimeWindow]] = field(default_factory=dict)  # machine -> sorted windows
    pinned: dict[str, tuple[str, datetime, str]] = field(default_factory=dict)  # order -> (machine, at, id)
    order_locked: dict[str, tuple[datetime, str]] = field(default_factory=dict)  # order -> (created, id)
    slot_locks: dict[str, ScheduleLock] = field(default_factory=dict)  # order -> time-slot lock
    sequence_locks: list[ScheduleLock] = field(default_factory=list)
    frozen_machines: set[str] = field(default_factory=set)

    def lock_sort_key(self, order_id: str) -> tuple[datetime, str] | None:
        """Creation order of the lock that puts ``order_id`` first (None when not locked)."""
        return self.order_locked.get(order_id)

    def is_locked(self, order_id: str) -> bool:
        return order_id in self.order_locked

    def sequence_position(self, order_id: str) -> tuple[str, int] | None:
        """``(lock_id, index)`` of the order in the first sequence lock listing it."""
        for lock in self.sequence_locks:
            if order_id in lock.sequence_order_ids:
                return lock.lock_id, lock.sequence_order_ids.index(order_id)
        return None


def build_lock_index(snapshot: PlanningSnapshot, now: datetime) -> LockIndex:
    """Normalise the active locks of ``snapshot`` (deterministic: sorted by creation, then id)."""
    now = ensure_utc(now)
    index = LockIndex()
    reserved: dict[str, list[TimeWindow]] = defaultdict(list)
    locks = sorted(snapshot.active_locks(now), key=lambda lk: (ensure_utc(lk.created_at), lk.lock_id))
    for lock in locks:
        created = ensure_utc(lock.created_at)
        window = lock.window
        if window is not None and ensure_utc(window.end) <= now:
            continue
        if lock.lock_type == LockType.SEQUENCE:
            if len(lock.sequence_order_ids) > 1:
                index.sequence_locks.append(lock)
                for order_id in lock.sequence_order_ids:
                    index.order_locked.setdefault(order_id, (created, lock.lock_id))
            continue
        if lock.lock_type in (LockType.TIME_SLOT, LockType.MACHINE) and window is not None:
            if lock.machine_id is not None:
                reserved[lock.machine_id].append(
                    TimeWindow(ensure_utc(window.start), ensure_utc(window.end), lock.reason or lock.lock_id)
                )
                if lock.order_id is not None:
                    index.slot_locks.setdefault(lock.order_id, lock)
                    index.order_locked.setdefault(lock.order_id, (created, lock.lock_id))
            continue
        if lock.lock_type == LockType.MACHINE and lock.order_id is None:
            if lock.machine_id is not None:
                index.frozen_machines.add(lock.machine_id)
            continue
        if lock.order_id is None:
            continue
        # ORDER lock, or MACHINE lock naming an order (pin without window)
        index.order_locked.setdefault(lock.order_id, (created, lock.lock_id))
        if lock.machine_id is not None:
            current = index.pinned.get(lock.order_id)
            if current is None or (created, lock.lock_id) > (current[1], current[2]):
                index.pinned[lock.order_id] = (lock.machine_id, created, lock.lock_id)
    for machine_id, windows in reserved.items():
        index.reserved[machine_id] = sorted(windows, key=lambda w: (w.start, w.end))
    return index


__all__ = ["LockIndex", "build_lock_index"]
