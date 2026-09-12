"""Tests for analytics.capacity: required vs available hours by dimension and period."""

from __future__ import annotations

from datetime import UTC
from zoneinfo import ZoneInfo

import pytest

from app.core.errors import ValidationError
from app.domain.enums import MachineStatus, ProcessType
from app.engines.analytics.capacity import capacity_rows, compute_capacity, period_bounds
from app.engines.calendar import build_calendars
from tests.engines.factories import (
    NOW,
    at,
    make_calendar_spec,
    make_entry,
    make_machine,
    make_operation,
    make_order,
    make_order_with_ops,
    make_schedule,
    make_snapshot,
)

CAL = make_calendar_spec("CAL")  # 08:00-16:00 UTC Mon-Fri; NOW is Monday 08:00 UTC


def _plant(orders=(), operations=(), machines=None):  # type: ignore[no-untyped-def]
    return make_snapshot(
        orders=orders,
        operations=operations,
        machines=machines if machines is not None else [make_machine("CNC-01", calendar_id="CAL")],
        calendars=[CAL],
        default_calendar_id="CAL",
    )


def test_period_bounds_days_and_weeks_in_plant_timezone() -> None:
    ist = ZoneInfo("Asia/Kolkata")
    days = period_bounds(NOW, 3, "day", ist)
    assert days[0] == (NOW, at(hours=10, minutes=30))  # first period clipped to now, ends 00:00 IST
    assert len(days) == 4 and days[-1][1] == at(days=3)  # last period clipped to the horizon
    for (_, end), (start, _) in zip(days, days[1:], strict=False):
        assert end == start
    weeks = period_bounds(NOW, 14, "week", ist)
    assert len(weeks) == 3 and weeks[0][0] == NOW and weeks[1][0] == at(days=6, hours=10, minutes=30)
    assert weeks[-1][1] == at(days=14)
    assert period_bounds(NOW, 0, "day", UTC) == []


def test_available_hours_follow_calendar_per_day() -> None:
    snap = _plant()
    report = compute_capacity(snap, None, build_calendars(snap), NOW, 7, "machine", "day")
    rows = report.rows_for("CNC-01")
    assert [r.available_hours for r in rows] == [8.0, 8.0, 8.0, 8.0, 8.0, 0.0, 0.0, 0.0]
    assert report.total_available_hours == 40.0 and report.total_required_hours == 0.0
    assert report.gap_hours == 40.0 and report.utilization_pct == 0.0
    assert len(report) == 8 and list(report) == report.rows
    assert rows[0].period_start == NOW and rows[-1].period_end == at(days=7)


def test_scheduled_entries_are_split_across_periods_by_working_time() -> None:
    order, ops = make_order_with_ops("O1", (ProcessType.CNC_MACHINING, ProcessType.CNC_MACHINING))
    snap = _plant([order], ops)
    schedule = make_schedule(
        [
            make_entry("O1", start=at(hours=1), run_minutes=240, operation_id="O1-op1"),  # Mon 09-13
            # Mon 15:00 -> Tue 10:00 wall clock: working minutes Mon 15-16 (1 h) + Tue 08-10 (2 h)
            make_entry(
                "O1", start=at(hours=7), run_minutes=19 * 60, operation_id="O1-op2", sequence_on_machine=2
            ),
        ]
    )
    report = compute_capacity(snap, schedule, build_calendars(snap), NOW, 7, "machine_group", "day")
    required = [r.required_hours for r in report.rows_for("CNC")]
    assert required[:3] == [5.0, 2.0, 0.0]
    assert report.scheduled_hours == 7.0 and report.estimated_hours == 0.0
    totals = report.totals_for("CNC")
    assert totals is not None and totals.required_hours == 7.0 and totals.gap_hours == 33.0
    assert totals.utilization_pct == pytest.approx(17.5)


def test_entry_without_calendar_is_spread_proportionally() -> None:
    order, ops = make_order_with_ops("O1")
    snap = _plant(
        [order],
        ops,
        machines=[make_machine("CNC-01", calendar_id="CAL"), make_machine("EXT", calendar_id="CAL")],
    )
    schedule = make_schedule(
        [make_entry("O1", "EXT", start=at(hours=12), run_minutes=8 * 60, setup_minutes=0)]
    )  # Mon 20 -> Tue 04
    calendars = build_calendars(snap)
    del calendars["EXT"]
    report = compute_capacity(snap, schedule, calendars, NOW, 3, "machine", "day")
    rows = report.rows_for("EXT")
    assert [r.required_hours for r in rows] == [4.0, 4.0, 0.0, 0.0]
    assert rows[0].available_hours == 0.0 and "without calendar" in report.notes[0]


def test_unscheduled_demand_goes_to_first_period_and_unresolvable_is_unallocated() -> None:
    o1, ops1 = make_order_with_ops("O1")  # 30 + 6 x 10 = 90 min
    o2, ops2 = make_order_with_ops(
        "O2", op_overrides=[{"cycle_minutes_per_unit": None, "setup_minutes": None}]
    )
    snap = _plant(
        [o1, o2],
        ops1 + ops2,
        machines=[make_machine("CNC-01", calendar_id="CAL"), make_machine("CNC-02", calendar_id="CAL")],
    )
    calendars = build_calendars(snap)
    by_group = compute_capacity(snap, None, calendars, NOW, 7, "machine_group", "week")
    assert by_group.rows_for("CNC")[0].required_hours == 1.5 and by_group.estimated_hours == 1.5
    assert by_group.unallocated_hours == 0.0
    assert any("without cycle time" in n for n in by_group.notes)
    by_machine = compute_capacity(snap, None, calendars, NOW, 7, "machine", "week")
    assert by_machine.unallocated_hours == 1.5 and by_machine.unallocated_operations == 1
    assert by_machine.total_required_hours == 0.0
    ops1[0].machine_id = "CNC-02"
    assigned = compute_capacity(snap, None, calendars, NOW, 7, "machine", "week")
    assert assigned.rows_for("CNC-02")[0].required_hours == 1.5 and assigned.unallocated_hours == 0.0
    # a scheduled operation is not estimated again
    schedule = make_schedule([make_entry("O1", "CNC-02", start=NOW, run_minutes=60)])
    with_schedule = compute_capacity(snap, schedule, calendars, NOW, 7, "machine", "week")
    assert with_schedule.rows_for("CNC-02")[0].required_hours == 1.0 and with_schedule.estimated_hours == 0.0


def test_department_and_process_dimensions() -> None:
    order = make_order("O1")
    op = make_operation("O1", operation_type=ProcessType.DEBURRING, machine_group="FIN")
    snap = _plant(
        [order],
        [op],
        machines=[
            make_machine("CNC-01", calendar_id="CAL", location="Hall A"),
            make_machine("CNC-02", calendar_id="CAL", attributes={"department": "Hall B"}),
            make_machine("DEB-01", ProcessType.DEBURRING, "FIN", calendar_id="CAL"),
        ],
    )
    calendars = build_calendars(snap)
    dept = compute_capacity(snap, None, calendars, NOW, 7, "department", "week")
    assert [t.key for t in dept.totals] == ["Hall A", "Hall B", "unassigned"]
    assert dept.totals_for("unassigned").required_hours == 1.5  # type: ignore[union-attr]
    process = compute_capacity(snap, None, calendars, NOW, 7, "process", "week")
    assert [t.key for t in process.totals] == ["cnc_machining", "deburring"]
    assert process.totals_for("deburring").required_hours == 1.5  # type: ignore[union-attr]
    assert process.totals_for("cnc_machining").available_hours == 80.0  # type: ignore[union-attr]
    table = process.table()
    assert table[1]["process"] == "deburring" and table[1]["gap_hours"] == 38.5
    assert "cnc_machining | 0 | 80 | +80" in process.table_text()


def test_gap_sign_and_shortfall_arithmetic() -> None:
    order, ops = make_order_with_ops(
        "O1", op_overrides=[{"cycle_minutes_per_unit": 300.0}]
    )  # 30 + 3000 min = 50.5 h
    snap = _plant([order], ops)
    report = compute_capacity(snap, None, build_calendars(snap), NOW, 7, "machine_group", "week")
    totals = report.totals_for("CNC")
    assert totals is not None
    assert totals.required_hours == 50.5 and totals.available_hours == 40.0
    assert totals.gap_hours == -10.5 and totals.shortfall_hours == 10.5
    assert totals.utilization_pct == pytest.approx(126.25)
    assert "CNC | 50 | 40 | -10" in report.table_text() or "CNC | 51 | 40 | -10" in report.table_text()
    assert capacity_rows(snap, None, build_calendars(snap), NOW, 7, "machine_group", "week") == report.rows


def test_inoperable_machine_without_return_time_has_no_capacity() -> None:
    snap = _plant(
        machines=[
            make_machine("DOWN", calendar_id="CAL", status=MachineStatus.DOWN),
            make_machine(
                "BACK", calendar_id="CAL", status=MachineStatus.MAINTENANCE, available_from=at(days=1)
            ),
        ]
    )
    report = compute_capacity(snap, None, build_calendars(snap), NOW, 7, "machine", "week")
    assert report.totals_for("DOWN").available_hours == 0.0  # type: ignore[union-attr]
    assert report.totals_for("BACK").available_hours == 32.0  # type: ignore[union-attr]
    assert any("inoperable" in n for n in report.notes)


def test_invalid_dimension_or_period_rejected() -> None:
    snap = _plant()
    with pytest.raises(ValidationError):
        compute_capacity(snap, None, {}, NOW, 7, "plant", "week")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        compute_capacity(snap, None, {}, NOW, 7, "machine", "month")  # type: ignore[arg-type]
