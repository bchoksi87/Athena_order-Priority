"""Machine ranking: earliest completion, costs, tie-breaks, spec Phase 35 reasons."""

from __future__ import annotations

from datetime import timedelta

from app.domain.config import SchedulingConfig
from app.domain.models import TimeWindow
from app.engines.calendar import MachineCalendar
from app.engines.constraints import ConstraintEngine, MachineState
from app.engines.scheduling.machine_assignment import (
    CYCLE_TIME_UNKNOWN,
    build_recommendation,
    place_on_calendar,
    rank_machines,
)
from tests.engines.factories import (
    NOW,
    at,
    make_calendar_spec,
    make_machine,
    make_operation,
    make_order,
    make_snapshot,
    make_tooling,
    window,
)


def _states(*ids: str, **next_free: object) -> dict[str, MachineState]:
    return {m: MachineState(machine_id=m, next_free=next_free.get(m, NOW)) for m in ids}  # type: ignore[arg-type]


def _calendars(*ids: str) -> dict[str, MachineCalendar]:
    return {m: MachineCalendar(make_calendar_spec(), machine_id=m) for m in ids}


class TestRanking:
    def test_earliest_completion_wins_with_phase35_reasons(
        self, constraint_engine: ConstraintEngine, scheduling_config: SchedulingConfig
    ) -> None:
        machines = [make_machine("CNC-01"), make_machine("CNC-04"), make_machine("CNC-07")]
        order = make_order("O1")
        op = make_operation("O1", setup_minutes=30.0, cycle_minutes_per_unit=6.0)
        snap = make_snapshot(orders=[order], operations=[op], machines=machines)
        states = _states("CNC-01", "CNC-04", "CNC-07")
        states["CNC-01"].next_free = at(hours=2)  # busy for 2 h
        states["CNC-07"].next_free = at(hours=1)
        states["CNC-01"].scheduled_minutes = 120.0
        states["CNC-07"].scheduled_minutes = 60.0
        ranked = rank_machines(
            op,
            order,
            machines,
            states,
            _calendars("CNC-01", "CNC-04", "CNC-07"),
            constraint_engine,
            scheduling_config,
            NOW,
            snapshot=snap,
        )
        assert [c.machine_id for c in ranked] == ["CNC-04", "CNC-07", "CNC-01"]
        assert [c.rank for c in ranked] == [1, 2, 3]
        best = ranked[0]
        assert best.recommended and not ranked[1].recommended
        assert best.expected_start == NOW and best.expected_end == at(hours=1.5)
        assert best.reasons[0] == "Expected completion 1.0 hours earlier than next best (CNC-07)"
        assert "Machine currently available" in best.reasons
        assert any(r.startswith("Lower downstream impact") for r in best.reasons)
        assert ranked[2].reasons[0] == "Expected completion 2.0 hours later than CNC-04"

    def test_tie_broken_by_preferred_rank_then_id(
        self, constraint_engine: ConstraintEngine, scheduling_config: SchedulingConfig
    ) -> None:
        machines = [
            make_machine("CNC-B", preferred_rank=1),
            make_machine("CNC-A", preferred_rank=1),
            make_machine("CNC-Z", preferred_rank=0),
        ]
        order = make_order("O1")
        op = make_operation("O1")
        snap = make_snapshot(orders=[order], operations=[op], machines=machines)
        ranked = rank_machines(
            op,
            order,
            machines,
            _states("CNC-A", "CNC-B", "CNC-Z"),
            _calendars("CNC-A", "CNC-B", "CNC-Z"),
            constraint_engine,
            scheduling_config,
            NOW,
            snapshot=snap,
        )
        assert [c.machine_id for c in ranked] == ["CNC-Z", "CNC-A", "CNC-B"]
        assert "tied with CNC-A" in ranked[0].reasons[0] and "machine rank 0 vs 1" in ranked[0].reasons[0]
        assert "lower machine preference rank" in ranked[2].reasons[0]

    def test_soft_cost_can_override_earlier_completion(
        self, constraint_engine: ConstraintEngine, scheduling_config: SchedulingConfig
    ) -> None:
        # CNC-02 finishes 10 min earlier but is not the ERP-preferred machine (30 min penalty)
        machines = [make_machine("CNC-01"), make_machine("CNC-02", efficiency=1.2)]
        order = make_order("O1")
        op = make_operation("O1", machine_id="CNC-01", setup_minutes=0.0, cycle_minutes_per_unit=6.0)
        snap = make_snapshot(orders=[order], operations=[op], machines=machines)
        ranked = rank_machines(
            op,
            order,
            machines,
            _states("CNC-01", "CNC-02"),
            _calendars("CNC-01", "CNC-02"),
            constraint_engine,
            scheduling_config,
            NOW,
            snapshot=snap,
        )
        assert ranked[0].machine_id == "CNC-01"
        assert ranked[1].soft_cost == scheduling_config.machine_preference.non_preferred_machine_cost_minutes
        assert ranked[1].expected_end is not None and ranked[0].expected_end is not None
        assert ranked[1].expected_end < ranked[0].expected_end

    def test_no_additional_tooling_setup_reason(
        self, constraint_engine: ConstraintEngine, scheduling_config: SchedulingConfig
    ) -> None:
        machines = [make_machine("CNC-01", tooling_configuration={"T1"}), make_machine("CNC-02")]
        order = make_order("O1")
        op = make_operation("O1", tooling_ids={"T1"}, setup_family="F")
        snap = make_snapshot(
            orders=[order],
            operations=[op],
            machines=machines,
            tooling=[make_tooling("T1", setup_minutes=20.0)],
        )
        states = _states("CNC-01", "CNC-02")
        states["CNC-01"].mounted_tooling = {"T1"}
        states["CNC-01"].current_setup_family = "F"
        ranked = rank_machines(
            op,
            order,
            machines,
            states,
            _calendars("CNC-01", "CNC-02"),
            constraint_engine,
            scheduling_config,
            NOW,
            snapshot=snap,
        )
        assert ranked[0].machine_id == "CNC-01" and ranked[0].setup_minutes == 0.0
        assert "No additional tooling setup" in ranked[0].reasons
        assert "No setup change needed (same setup family 'F')" in ranked[0].reasons
        assert ranked[1].setup_minutes == 50.0

    def test_release_and_available_from_delay_start(
        self, constraint_engine: ConstraintEngine, scheduling_config: SchedulingConfig
    ) -> None:
        machines = [make_machine("CNC-01", available_from=at(hours=3))]
        order = make_order("O1")
        op = make_operation("O1", setup_minutes=0.0, cycle_minutes_per_unit=6.0)
        snap = make_snapshot(orders=[order], operations=[op], machines=machines)
        ranked = rank_machines(
            op,
            order,
            machines,
            _states("CNC-01"),
            _calendars("CNC-01"),
            constraint_engine,
            scheduling_config,
            NOW,
            snapshot=snap,
            release=at(hours=1),
        )
        assert ranked[0].expected_start == at(hours=3)
        assert "Machine currently available" not in ranked[0].reasons
        assert ranked[0].reasons[0] == "Only eligible machine"

    def test_missing_cycle_time_rejects_machine(
        self, constraint_engine: ConstraintEngine, scheduling_config: SchedulingConfig
    ) -> None:
        machines = [make_machine("CNC-01"), make_machine("CNC-02")]
        order = make_order("O1")
        op = make_operation("O1", cycle_minutes_per_unit=None, machine_cycle_minutes={"CNC-02": 5.0})
        snap = make_snapshot(orders=[order], operations=[op], machines=machines)
        ranked = rank_machines(
            op,
            order,
            machines,
            _states("CNC-01", "CNC-02"),
            _calendars("CNC-01", "CNC-02"),
            constraint_engine,
            scheduling_config,
            NOW,
            snapshot=snap,
        )
        assert [c.machine_id for c in ranked] == ["CNC-02", "CNC-01"]
        assert ranked[1].expected_end is None and ranked[1].rank == 0
        assert ranked[1].reasons == [CYCLE_TIME_UNKNOWN]
        rec = build_recommendation(op, ranked, constraint_engine.eligible_machines(op, snap, NOW))
        assert rec.recommended_machine_id == "CNC-02"
        assert [c.machine_id for c in rec.eligible] == ["CNC-02"]
        assert rec.rejected == {"CNC-01": [CYCLE_TIME_UNKNOWN]}

    def test_no_state_or_calendar_rejects(
        self, constraint_engine: ConstraintEngine, scheduling_config: SchedulingConfig
    ) -> None:
        machines = [make_machine("CNC-01"), make_machine("CNC-02")]
        order = make_order("O1")
        op = make_operation("O1")
        snap = make_snapshot(orders=[order], operations=[op], machines=machines)
        ranked = rank_machines(
            op,
            order,
            machines,
            _states("CNC-01"),
            _calendars("CNC-02"),
            constraint_engine,
            scheduling_config,
            NOW,
            snapshot=snap,
        )
        assert all(c.expected_end is None for c in ranked)
        assert build_recommendation(op, ranked).recommended_machine_id is None


class TestPlaceOnCalendar:
    def test_skips_non_working_time(self, day_calendar: MachineCalendar) -> None:
        # 15:00 Monday, 30 min setup + 120 min run: crosses the 16:00 shift end
        placement = place_on_calendar(day_calendar, at(hours=7), 30.0, 120.0)
        assert placement.setup_start == at(hours=7)
        assert placement.start == at(hours=7.5)
        assert placement.end == at(days=1, hours=1.5)  # Tuesday 09:30

    def test_avoids_reserved_windows(self, day_calendar: MachineCalendar) -> None:
        reserved = [window(at(hours=1), 2, "lock")]  # 09:00-11:00
        placement = place_on_calendar(day_calendar, NOW, 30.0, 60.0, reserved)
        assert placement.setup_start == at(hours=3)  # cannot fit before 09:00, so after 11:00
        short = place_on_calendar(day_calendar, NOW, 0.0, 30.0, reserved)
        assert short.setup_start == NOW and short.end == at(minutes=30)

    def test_zero_duration_returns_next_working_time(self, day_calendar: MachineCalendar) -> None:
        placement = place_on_calendar(day_calendar, at(hours=10), 0.0, 0.0)
        assert placement.setup_start == placement.end == at(days=1)

    def test_reserved_window_type(self) -> None:
        assert TimeWindow(NOW, NOW + timedelta(hours=1)).minutes == 60.0


class TestPlaceSpan:
    def test_span_end_matches_exact_placement(self, day_calendar: MachineCalendar) -> None:
        from app.engines.scheduling.machine_assignment import place_span

        reserved = [window(at(days=1, hours=2), 1, "lock")]
        for start_h in (0, 3.25, 7.9, 16, 40):
            for setup in (0.0, 12.5, 45.0):
                for run in (0.0, 30.0, 400.0, 1234.0):
                    exact = place_on_calendar(day_calendar, at(hours=start_h), setup, run, reserved)
                    setup_start, end = place_span(day_calendar, at(hours=start_h), setup + run, reserved)
                    assert (setup_start, end) == (exact.setup_start, exact.end), (start_h, setup, run)
