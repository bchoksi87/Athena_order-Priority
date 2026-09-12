"""MachineTimeline: slots, gaps, predecessor state, the successor rule and find_slot."""

from __future__ import annotations

from datetime import datetime

from app.domain.config import SchedulingConfig
from app.domain.enums import ProcessType
from app.engines.calendar import MachineCalendar
from app.engines.constraints import MachineState
from app.engines.scheduling.machine_assignment import find_slot
from app.engines.scheduling.timeline import BusySlot, MachineTimeline, successor_setup_holds
from tests.engines.factories import (
    NOW,
    at,
    make_calendar_spec,
    make_entry,
    make_machine,
    make_operation,
    make_order,
    make_snapshot,
    window,
)

CAL = MachineCalendar(make_calendar_spec(), machine_id="CNC-01")  # 08-16 Mon-Fri, NOW = Monday 08:00


def _timeline(floor: datetime = NOW, **initial: object) -> MachineTimeline:
    return MachineTimeline("CNC-01", CAL, floor, **initial)  # type: ignore[arg-type]


def _slot(
    entry_id: str,
    start: datetime,
    minutes: float,
    *,
    family: str | None = None,
    material: str | None = None,
    basis: str | None = "changeover",
    setup_minutes: float = 30.0,
) -> BusySlot:
    return BusySlot(
        setup_start=start,
        end=CAL.add_work_minutes(start, minutes),
        entry_id=entry_id,
        setup_minutes=setup_minutes,
        setup_basis=basis,
        op=None,
        order=None,
        setup_family=family,
        material_id=material,
        mounted_tooling=frozenset(),
        customer_id=None,
        part_family=None,
    )


class TestGaps:
    def test_tail_append_and_gap_before_a_late_job(self) -> None:
        tl = _timeline()
        assert tl.tail_end == NOW and tl.gaps == [] and len(tl) == 0
        late = _slot("late", at(hours=5), 60)
        tl.insert(late)
        assert tl.tail_end == at(hours=6)
        (gap,) = tl.gaps
        assert (gap.start, gap.end, gap.work_minutes) == (NOW, at(hours=5), 300.0)
        assert gap.predecessor is None and gap.successor is late
        assert list(tl.gaps_from(at(hours=5))) == [] and list(tl.gaps_from(at(hours=4))) == [gap]

    def test_insert_splits_the_gap_and_keeps_neighbours(self) -> None:
        tl = _timeline()
        late = _slot("late", at(hours=5), 60)
        tl.insert(late)
        middle = _slot("mid", at(hours=1), 60)
        tl.insert(middle)
        assert [s.entry_id for s in tl.slots] == ["mid", "late"]
        assert [(g.start, g.end, g.work_minutes) for g in tl.gaps] == [
            (NOW, at(hours=1), 60.0),
            (at(hours=2), at(hours=5), 180.0),
        ]
        assert tl.gaps[0].successor is middle and tl.gaps[1].predecessor is middle
        assert tl.gaps[1].successor is late
        first = _slot("first", NOW, 60)
        tl.insert(first)  # consumes the first gap exactly
        assert [(g.start, g.end) for g in tl.gaps] == [(at(hours=2), at(hours=5))]
        assert tl.predecessor(at(hours=3)) is middle and tl.predecessor(at(minutes=-1)) is None

    def test_gaps_without_working_time_are_not_stored(self) -> None:
        tl = _timeline()
        tl.insert(_slot("fri", at(days=4), 480))  # Friday 08:00-16:00
        tl.insert(_slot("mon", at(days=7), 60))  # Monday next week: only the weekend in between
        assert [g.end for g in tl.gaps] == [at(days=4)]  # Mon..Thu gap only
        assert tl.gaps[0].work_minutes == 4 * 480.0

    def test_state_before_uses_the_slot_before_the_gap(self) -> None:
        tl = _timeline(setup_family="F0", material_id="M0", mounted_tooling={"T0"})
        tl.insert(_slot("a", at(hours=1), 60, family="F1", material="M1"))
        tl.insert(_slot("b", at(hours=4), 60, family="F2", material="M2"))
        base = MachineState("CNC-01", at(hours=5), "F2", "M2", {"T0"}, scheduled_minutes=120.0)
        before_a, before_b = (tl.state_before(g, base) for g in tl.gaps)
        assert (before_a.current_setup_family, before_a.current_material_id) == ("F0", "M0")
        assert before_a.mounted_tooling == {"T0"} and before_a.next_free == NOW
        assert (before_b.current_setup_family, before_b.current_material_id) == ("F1", "M1")
        assert before_b.scheduled_minutes == 120.0 and before_b.next_free == at(hours=2)

    def test_add_entry_derives_the_after_state_from_operation_and_order(self) -> None:
        tl = _timeline()
        order = make_order("O1", required_material_id="AL", part_family="PF")
        op = make_operation("O1", setup_family="F1", tooling_ids={"T1"})
        slot = tl.add_entry(make_entry("O1", start=at(hours=1)), op, order, setup_basis="same_family")
        assert (slot.setup_family, slot.material_id, slot.part_family) == ("F1", "AL", "PF")
        assert slot.mounted_tooling == {"T1"} and slot.setup_basis == "same_family"
        assert slot.customer_id == "C1" and slot.op is op


class TestSuccessorRule:
    def _gap(self, successor: BusySlot, predecessor: BusySlot | None = None):  # type: ignore[no-untyped-def]
        tl = _timeline()
        if predecessor is not None:
            tl.insert(predecessor)
        tl.insert(successor)
        return tl.gaps[-1]

    def test_full_changeover_recorded_accepts_anything(self, scheduling_config: SchedulingConfig) -> None:
        for basis in ("changeover", "unknown"):
            gap = self._gap(_slot("s", at(hours=4), 60, family="F1", material="M1", basis=basis))
            assert successor_setup_holds(gap, "F9", "M9", scheduling_config)

    def test_same_family_successor_needs_the_same_family(self, scheduling_config: SchedulingConfig) -> None:
        gap = self._gap(_slot("s", at(hours=4), 60, family="F1", material="M1", basis="same_family"))
        assert successor_setup_holds(gap, "F1", "M9", scheduling_config)
        assert not successor_setup_holds(gap, "F2", "M1", scheduling_config)
        assert not successor_setup_holds(gap, None, "M1", scheduling_config)

    def test_same_material_successor_needs_material_or_cheaper_family(
        self, scheduling_config: SchedulingConfig
    ) -> None:
        gap = self._gap(_slot("s", at(hours=4), 60, family="F1", material="M1", basis="same_material"))
        assert successor_setup_holds(gap, "F2", "M1", scheduling_config)
        assert successor_setup_holds(gap, "F1", "M2", scheduling_config)  # family factor 0 <= 0.5
        assert not successor_setup_holds(gap, "F2", "M2", scheduling_config)
        scheduling_config.setup.same_family_setup_factor = 0.8
        assert not successor_setup_holds(gap, "F1", "M2", scheduling_config)

    def test_unknown_basis_requires_unchanged_relationship(self, scheduling_config: SchedulingConfig) -> None:
        pred = _slot("p", at(hours=1), 60, family="F0", material="M0")
        gap = self._gap(_slot("s", at(hours=4), 60, family="F1", material="M1", basis=None), pred)
        assert gap.predecessor is pred
        assert successor_setup_holds(gap, "F0", "M0", scheduling_config)
        assert not successor_setup_holds(gap, "F1", "M0", scheduling_config)
        first = self._gap(_slot("s", at(hours=4), 60, basis=None))
        assert successor_setup_holds(first, None, None, scheduling_config)
        assert not successor_setup_holds(first, "F0", None, scheduling_config)


class TestFindSlot:
    def _setup(self):  # type: ignore[no-untyped-def]
        machine = make_machine("CNC-01", calendar_id="CAL")
        order = make_order("O1")
        op = make_operation("O1", setup_minutes=30.0, cycle_minutes_per_unit=6.0, setup_family="F1")  # 90 min
        snap = make_snapshot(
            orders=[order], operations=[op], machines=[machine], calendars=[make_calendar_spec("CAL")]
        )
        return machine, order, op, snap

    def test_earliest_fitting_gap_else_tail(self, scheduling_config: SchedulingConfig) -> None:
        machine, order, op, snap = self._setup()
        tl = _timeline()
        tl.insert(_slot("a", at(hours=1), 60))  # 09:00-10:00: the 08-09 gap is too small for 90 min
        tl.insert(_slot("b", at(hours=4), 60))  # 12:00-13:00: 10:00-12:00 fits
        state = MachineState("CNC-01", at(hours=5), "F0", "M0")
        slot = find_slot(
            op, order, machine, state, CAL, scheduling_config, NOW, 60.0, timeline=tl, snapshot=snap
        )
        assert slot.in_gap and slot.setup_start == at(hours=2) and slot.end == at(hours=3.5)
        assert slot.gap_end == at(hours=4) and slot.estimate.minutes == 30.0
        assert slot.state.current_setup_family is None  # follows slot "a" (no family), not the tail
        tail = find_slot(
            op,
            order,
            machine,
            state,
            CAL,
            scheduling_config,
            NOW,
            60.0,
            timeline=tl,
            snapshot=snap,
            tail_only=True,
        )
        assert not tail.in_gap and tail.setup_start == at(hours=5) and tail.state is state
        no_timeline = find_slot(op, order, machine, state, CAL, scheduling_config, NOW, 60.0, snapshot=snap)
        assert (no_timeline.setup_start, no_timeline.end) == (tail.setup_start, tail.end)

    def test_release_inside_a_gap_and_reserved_windows(self, scheduling_config: SchedulingConfig) -> None:
        machine, order, op, snap = self._setup()
        tl = _timeline()
        tl.insert(_slot("late", at(hours=6), 60))  # 14:00-15:00; gap 08:00-14:00
        state = MachineState("CNC-01", at(hours=7))
        slot = find_slot(
            op, order, machine, state, CAL, scheduling_config, at(hours=3), 60.0, timeline=tl, snapshot=snap
        )
        assert slot.in_gap and slot.setup_start == at(hours=3) and slot.end == at(hours=4.5)
        reserved = [window(at(hours=3), 2, "lock")]  # 11:00-13:00 reserved: 13:00-14:00 is too short
        slot = find_slot(
            op,
            order,
            machine,
            state,
            CAL,
            scheduling_config,
            at(hours=3),
            60.0,
            reserved,
            timeline=tl,
            snapshot=snap,
        )
        assert not slot.in_gap and slot.setup_start == at(hours=7)
        assert slot.end == at(days=1, hours=0.5)  # 15:00-16:00 + Tuesday 08:00-08:30

    def test_setup_follows_the_predecessor_of_the_gap(self, scheduling_config: SchedulingConfig) -> None:
        machine, order, op, snap = self._setup()
        tl = _timeline()
        tl.insert(_slot("a", at(hours=1), 60, family="F1"))  # same family as op: no setup after it
        tl.insert(_slot("b", at(hours=3), 60, family="F2"))
        state = MachineState("CNC-01", at(hours=4), "F2", None)
        slot = find_slot(
            op, order, machine, state, CAL, scheduling_config, NOW, 60.0, timeline=tl, snapshot=snap
        )
        assert slot.in_gap and slot.setup_start == at(hours=2) and slot.estimate.minutes == 0.0
        assert slot.estimate.basis == "same_family" and slot.end == at(hours=3)

    def test_process_type_is_irrelevant_to_the_timeline(self) -> None:
        assert ProcessType.CNC_MACHINING.value == "cnc_machining"
