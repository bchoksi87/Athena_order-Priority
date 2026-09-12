"""Per-machine busy-interval timeline for gap-filling ("back-fill") placement.

The rule-based scheduler places operations in global priority order. Without
back-filling every placement goes after the machine's last job, so one job
that is released late (its upstream operation sits far down a loaded queue)
drags every lower-priority job on the downstream machine behind it while that
machine idles for days. :class:`MachineTimeline` keeps the placed entries of
one machine as a time-ordered list of busy slots plus the *open gaps* between
them (wall-clock windows that contain calendar working time), so the placement
code (:func:`app.engines.scheduling.machine_assignment.find_slot`) can look
for the earliest gap a job fits into before falling back to the tail.

Rules
-----
* Slots never overlap and never move once inserted; locked / frozen entries
  are ordinary slots. The tail (everything after the last slot) is the open
  gap: ``tail_end`` equals the machine's ``MachineState.next_free``.
* The setup of a job placed in a gap is derived from the slot immediately
  *before* the gap in time (its setup family, material, mounted tooling,
  customer, part family) - :meth:`MachineTimeline.state_before` - never from
  the machine's tail state.
* **Successor rule** (:func:`successor_setup_holds`): a job is inserted in
  front of an existing slot only when that slot's recorded setup stays
  sufficient with the new job as its predecessor, decided in O(1) from the
  basis the setup was recorded with:

  - ``changeover`` / ``unknown`` (a full changeover was recorded) → always;
  - ``same_family`` → only when the new job has the successor's setup family;
  - ``same_material`` → only when the new job has the successor's material
    (or its family, when the family factor is not larger than the material
    factor);
  - no recorded basis (a frozen entry of an earlier run) → only when the new
    job leaves the setup family and material the successor followed
    unchanged.

  Otherwise the gap is skipped, so no existing entry ever needs re-timing.
  Tooling never invalidates a successor: mounted tooling only accumulates
  along the timeline, so a job inserted before a slot can only reduce the
  tooling that slot still has to mount.
* The cumulative tooling set stored with a slot is not rewritten when a job is
  inserted before it, which can only over-estimate the tooling setup of later
  gap probes (the safe direction).
* Gaps without working time (nights, weekends, downtime) are not stored; a
  gap records its calendar working minutes and its neighbouring slots, so a
  probe decides "does this job fit here?" from stored numbers and walks the
  calendar only for the slot it finally takes. Look-ups use ``bisect`` on the
  slot starts / gap ends and insertion is a list insert, so the per-machine
  cost is O(log n) per probe plus the scan over the open gaps after the
  release instant.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime

from app.domain.config import SchedulingConfig
from app.domain.models import Operation, Order
from app.domain.results import ScheduleEntry
from app.engines.calendar.calendar import MachineCalendar
from app.engines.constraints.base import MachineState
from app.engines.constraints.hard import required_tooling_ids

#: Setup bases that mean "a full changeover was recorded" (see :func:`successor_setup_holds`).
FULL_CHANGEOVER_BASES: frozenset[str] = frozenset({"changeover", "unknown"})


@dataclass(slots=True)
class BusySlot:
    """One placed entry and the machine state it leaves behind."""

    setup_start: datetime
    end: datetime
    entry_id: str
    setup_minutes: float
    setup_basis: str | None  # SetupEstimate.basis the entry was placed with (None: unknown)
    op: Operation | None
    order: Order | None
    setup_family: str | None
    material_id: str | None
    mounted_tooling: frozenset[str]
    customer_id: str | None
    part_family: str | None
    locked: bool = False


@dataclass(slots=True, frozen=True)
class Gap:
    """Idle wall-clock window between two slots (or the floor and the first slot)."""

    start: datetime
    end: datetime
    work_minutes: float  # calendar working minutes inside [start, end)
    predecessor: BusySlot | None  # slot ending at ``start`` (None: the machine's initial state)
    successor: BusySlot  # slot starting at ``end``


class MachineTimeline:
    """Time-ordered busy slots and open gaps of one machine (see module docstring)."""

    __slots__ = (
        "_gap_ends",
        "_initial",
        "_starts",
        "calendar",
        "floor",
        "gaps",
        "machine_id",
        "slots",
        "tail_end",
    )

    def __init__(
        self,
        machine_id: str,
        calendar: MachineCalendar,
        floor: datetime,
        *,
        setup_family: str | None = None,
        material_id: str | None = None,
        mounted_tooling: Iterable[str] = (),
    ) -> None:
        self.machine_id = machine_id
        self.calendar = calendar
        self.floor = floor  # earliest instant the machine can take work (now / available_from / return)
        self.tail_end = floor
        self.slots: list[BusySlot] = []
        self._starts: list[datetime] = []
        self.gaps: list[Gap] = []
        self._gap_ends: list[datetime] = []
        self._initial = (setup_family, material_id, frozenset(mounted_tooling))

    def __len__(self) -> int:
        return len(self.slots)

    # ------------------------------------------------------------- queries
    def predecessor(self, t: datetime) -> BusySlot | None:
        """Last slot starting at or before ``t`` (``None`` before the first slot)."""
        i = bisect_right(self._starts, t) - 1
        return self.slots[i] if i >= 0 else None

    def state_before(self, gap: Gap, base: MachineState) -> MachineState:
        """Transient :class:`MachineState` a job placed in ``gap`` would follow.

        Family / material / tooling / customer / part family come from the slot
        before the gap; ``scheduled_minutes`` (machine load) is copied from ``base``.
        """
        pred = gap.predecessor
        if pred is None:
            family, material, tooling = self._initial
            customer: str | None = None
            part_family: str | None = None
        else:
            family, material, tooling = pred.setup_family, pred.material_id, pred.mounted_tooling
            customer, part_family = pred.customer_id, pred.part_family
        return MachineState(
            machine_id=self.machine_id,
            next_free=gap.start,
            current_setup_family=family,
            current_material_id=material,
            mounted_tooling=set(tooling),
            scheduled_minutes=base.scheduled_minutes,
            last_customer_id=customer,
            last_part_family=part_family,
        )

    def gaps_from(self, t: datetime) -> Iterator[Gap]:
        """Open gaps that end after ``t``, in time order."""
        start = bisect_right(self._gap_ends, t)
        for i in range(start, len(self.gaps)):
            yield self.gaps[i]

    # ----------------------------------------------------------- mutation
    def add_entry(
        self,
        entry: ScheduleEntry,
        op: Operation | None,
        order: Order | None,
        *,
        setup_basis: str | None = None,
    ) -> BusySlot:
        """Insert ``entry`` as a slot; its after-state follows the same rules as ``apply_entry_to_state``."""
        pred = self.predecessor(entry.setup_start)
        mounted = pred.mounted_tooling if pred is not None else self._initial[2]
        family = op.setup_family if op is not None else entry.setup_family
        material = (op.material_id if op is not None else None) or entry.material_id
        if material is None and order is not None:
            material = order.required_material_id
        tooling = mounted | required_tooling_ids(op, order) if op is not None else mounted
        slot = BusySlot(
            setup_start=entry.setup_start,
            end=entry.end,
            entry_id=entry.entry_id,
            setup_minutes=entry.setup_minutes,
            setup_basis=setup_basis,
            op=op,
            order=order,
            setup_family=family,
            material_id=material,
            mounted_tooling=frozenset(tooling),
            customer_id=entry.customer_id or (order.customer_id if order is not None else None),
            part_family=order.part_family if order is not None else None,
            locked=entry.locked,
        )
        self.insert(slot)
        return slot

    def insert(self, slot: BusySlot) -> None:
        """Insert a non-overlapping slot and split / consume the gap it lands in."""
        i = bisect_right(self._starts, slot.setup_start)
        prev = self.slots[i - 1] if i > 0 else None
        nxt = self.slots[i] if i < len(self.slots) else None
        left_start = max(prev.end, self.floor) if prev is not None else self.floor
        # the gap between prev and nxt (if it was stored) is replaced by up to two smaller ones
        if nxt is not None:
            g = bisect_right(self._gap_ends, nxt.setup_start) - 1
            if g >= 0 and self.gaps[g].end == nxt.setup_start:
                del self.gaps[g]
                del self._gap_ends[g]
        self.slots.insert(i, slot)
        self._starts.insert(i, slot.setup_start)
        if slot.setup_start > left_start:
            self._add_gap(left_start, slot.setup_start, prev, slot)
        if nxt is not None and slot.end < nxt.setup_start:
            self._add_gap(slot.end, nxt.setup_start, slot, nxt)
        if slot.end > self.tail_end:
            self.tail_end = slot.end

    def _add_gap(self, start: datetime, end: datetime, prev: BusySlot | None, nxt: BusySlot) -> None:
        work = self.calendar.working_minutes_between(start, end)
        if work <= 0:
            return
        g = bisect_right(self._gap_ends, end)
        self.gaps.insert(g, Gap(start, end, work, prev, nxt))
        self._gap_ends.insert(g, end)


def successor_setup_holds(
    gap: Gap, setup_family: str | None, material_id: str | None, config: SchedulingConfig
) -> bool:
    """True when the slot after ``gap`` keeps a sufficient recorded setup with a job of
    ``setup_family`` / ``material_id`` placed in the gap (rule in the module docstring)."""
    successor = gap.successor
    basis = successor.setup_basis
    if basis is None:
        pred = gap.predecessor
        pred_family = pred.setup_family if pred is not None else None
        pred_material = pred.material_id if pred is not None else None
        return setup_family == pred_family and material_id == pred_material
    if basis in FULL_CHANGEOVER_BASES:
        return True
    if basis == "same_family":
        return setup_family is not None and setup_family == successor.setup_family
    # same_material
    if material_id is not None and material_id == successor.material_id:
        return True
    return (
        setup_family is not None
        and setup_family == successor.setup_family
        and config.setup.same_family_setup_factor <= config.setup.same_material_setup_factor
    )


__all__ = ["FULL_CHANGEOVER_BASES", "BusySlot", "Gap", "MachineTimeline", "successor_setup_holds"]
