"""Machine state initialisation and frozen-window reproduction for the rule-based scheduler.

``init_machine_states`` builds one :class:`MachineState` per machine that can
take work in this run: ``next_free = max(now, available_from)``, current setup
family / material / mounted tooling from the ERP snapshot. Machines that are
not operable start at the end of their known downtime; with no known return
they are excluded (reported as warnings, and their operations end up
unscheduled with a clear reason instead of being silently dropped). Every
state is paired with a :class:`~app.engines.scheduling.timeline.MachineTimeline`
(``MachinePool.timelines``) that records the placed entries as busy slots and
the idle gaps between them for back-filling; ``MachineState`` itself keeps the
*tail* view: ``next_free`` is the end of the machine's last job and the
family / material / tooling / customer / part-family fields describe the
state after that job. ``apply_entry_to_state(..., tail=False)`` records a
back-filled entry (load and mounted tooling only).

``reproduce_frozen_entries`` implements the "lock the next N hours" rule of
spec Phase 10: entries of the previous schedule that start within
``config.lock_window_minutes`` of ``now`` (plus entries of order-locked orders
and of frozen machines) are copied verbatim, marked ``locked`` and become the
starting point of every machine queue. Entries that no longer make sense
(closed order, finished operation, unknown machine) are dropped with a warning.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

import structlog

from app.core.clock import ensure_utc
from app.domain.config import SchedulingConfig
from app.domain.models import Machine, Operation, Order
from app.domain.results import ScheduleEntry
from app.domain.snapshot import PlanningSnapshot
from app.engines.calendar.calendar import MachineCalendar
from app.engines.constraints.base import MachineState
from app.engines.constraints.hard import maintenance_end_after, required_tooling_ids
from app.engines.scheduling.locks import LockIndex
from app.engines.scheduling.timeline import MachineTimeline

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class MachinePool:
    """Machine states for one run plus why some machines were left out."""

    states: dict[str, MachineState] = field(default_factory=dict)
    timelines: dict[str, MachineTimeline] = field(default_factory=dict)
    excluded: dict[str, str] = field(default_factory=dict)  # machine_id -> reason
    warnings: list[str] = field(default_factory=list)


def init_machine_states(
    snapshot: PlanningSnapshot,
    calendars: Mapping[str, MachineCalendar],
    now: datetime,
    locks: LockIndex | None = None,
) -> MachinePool:
    """One :class:`MachineState` per usable machine (sorted by machine id for determinism)."""
    now = ensure_utc(now)
    pool = MachinePool()
    for machine_id in sorted(snapshot.machines):
        machine = snapshot.machines[machine_id]
        if machine_id not in calendars:
            pool.excluded[machine_id] = "no calendar supplied for this machine"
            continue
        next_free = now
        if machine.available_from is not None:
            next_free = max(next_free, ensure_utc(machine.available_from))
        if not machine.status.is_operable:
            returns_at = maintenance_end_after(machine, now)
            if returns_at is None:
                pool.excluded[machine_id] = f"{machine.status.value} with no known return"
                continue
            next_free = max(next_free, returns_at)
        if locks is not None and machine_id in locks.frozen_machines:
            pool.excluded[machine_id] = "machine locked by planner; existing entries kept, no new work"
        pool.states[machine_id] = MachineState(
            machine_id=machine_id,
            next_free=next_free,
            current_setup_family=machine.current_setup_family,
            current_material_id=machine.current_material_id,
            mounted_tooling=set(machine.tooling_configuration),
        )
        pool.timelines[machine_id] = MachineTimeline(
            machine_id,
            calendars[machine_id],
            next_free,
            setup_family=machine.current_setup_family,
            material_id=machine.current_material_id,
            mounted_tooling=machine.tooling_configuration,
        )
    for machine_id, reason in sorted(pool.excluded.items()):
        pool.warnings.append(f"Machine {machine_id} excluded: {reason}")
    if pool.excluded:
        log.info("scheduler.machines_excluded", excluded=dict(sorted(pool.excluded.items())))
    return pool


def apply_entry_to_state(
    state: MachineState,
    entry: ScheduleEntry,
    op: Operation | None,
    order: Order | None,
    *,
    tail: bool = True,
) -> None:
    """Advance ``state`` as if ``entry`` had just been placed on the machine.

    With ``tail=False`` the entry was back-filled into a gap before the
    machine's last job: only the load and the mounted tooling change, the
    tail setup state (family, material, customer, part family) stays.
    """
    state.next_free = max(state.next_free, entry.end)
    state.scheduled_minutes += entry.setup_minutes + entry.run_minutes
    if op is not None:
        state.mounted_tooling |= required_tooling_ids(op, order)
    if not tail:
        return
    family = op.setup_family if op is not None else entry.setup_family
    material = (op.material_id if op is not None else None) or entry.material_id
    if material is None and order is not None:
        material = order.required_material_id
    state.current_setup_family = family
    state.current_material_id = material
    state.last_customer_id = entry.customer_id or (order.customer_id if order is not None else None)
    state.last_part_family = order.part_family if order is not None else None


@dataclass(slots=True)
class FrozenResult:
    entries: list[ScheduleEntry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def placed_operation_ids(self) -> set[str]:
        return {e.operation_id for e in self.entries}


def _operation_is_pending(snapshot: PlanningSnapshot, entry: ScheduleEntry) -> bool:
    op = snapshot.operations.get(entry.operation_id)
    if op is not None:
        return not op.is_done
    # synthetic order-level operation: pending while the order is open
    order = snapshot.orders.get(entry.order_id)
    return order is not None and order.is_open


def reproduce_frozen_entries(
    previous_entries: Sequence[ScheduleEntry] | None,
    snapshot: PlanningSnapshot,
    states: Mapping[str, MachineState],
    config: SchedulingConfig,
    now: datetime,
    locks: LockIndex | None = None,
    timelines: Mapping[str, MachineTimeline] | None = None,
) -> FrozenResult:
    """Copy previous entries that fall inside the lock window (or belong to locks) as locked entries.

    Frozen entries also become busy slots of ``timelines`` (when given) so
    later placements can back-fill around them without ever moving them.
    """
    result = FrozenResult()
    if not previous_entries:
        return result
    now = ensure_utc(now)
    window_end = now + timedelta(minutes=max(0.0, config.lock_window_minutes))
    seen: set[str] = set()
    for entry in sorted(previous_entries, key=lambda e: (e.machine_id, e.setup_start, e.operation_id)):
        if entry.operation_id in seen:
            continue
        in_window = ensure_utc(entry.setup_start) < window_end
        order_locked = locks is not None and locks.is_locked(entry.order_id)
        machine_frozen = locks is not None and entry.machine_id in locks.frozen_machines
        if not (in_window or order_locked or machine_frozen):
            continue
        if ensure_utc(entry.end) <= now:
            continue  # already finished; nothing to freeze
        order = snapshot.orders.get(entry.order_id)
        if order is None or not order.is_open:
            result.warnings.append(
                f"Frozen entry for {entry.order_id}/{entry.operation_id} dropped: order closed or unknown"
            )
            continue
        if not _operation_is_pending(snapshot, entry):
            continue
        state = states.get(entry.machine_id)
        if state is None:
            result.warnings.append(
                f"Frozen entry for {entry.order_id}/{entry.operation_id} dropped: "
                f"machine {entry.machine_id} is not available in this run"
            )
            continue
        pinned = locks.pinned.get(entry.order_id) if locks is not None else None
        if pinned is not None and pinned[0] != entry.machine_id:
            result.warnings.append(
                f"Frozen entry for {entry.order_id}/{entry.operation_id} dropped: "
                f"order now locked to {pinned[0]}, entry was on {entry.machine_id}"
            )
            continue
        frozen = replace(entry, locked=True)
        result.entries.append(frozen)
        seen.add(entry.operation_id)
        op = snapshot.operations.get(entry.operation_id)
        timeline = timelines.get(entry.machine_id) if timelines is not None else None
        is_tail = timeline is None or ensure_utc(frozen.setup_start) >= timeline.tail_end
        apply_entry_to_state(state, frozen, op, order, tail=is_tail)
        if timeline is not None:
            timeline.add_entry(frozen, op, order)
    if result.entries:
        log.info(
            "scheduler.frozen_entries", count=len(result.entries), window_minutes=config.lock_window_minutes
        )
    return result


def machine_by_id(snapshot: PlanningSnapshot, machine_id: str) -> Machine | None:
    return snapshot.machines.get(machine_id)


__all__ = [
    "FrozenResult",
    "MachinePool",
    "apply_entry_to_state",
    "init_machine_states",
    "machine_by_id",
    "reproduce_frozen_entries",
]
