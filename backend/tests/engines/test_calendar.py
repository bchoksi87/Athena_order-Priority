"""MachineCalendar arithmetic: shifts, midnight crossing, holidays, downtime, timezones, DST."""

from __future__ import annotations

import time as _time
from datetime import UTC, date, datetime, time, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.core.errors import ConfigurationError, ValidationError
from app.domain.models import Shift, TimeWindow
from app.engines.calendar import (
    MachineCalendar,
    ShiftPattern,
    build_calendar,
    build_calendars,
    default_24x7_spec,
)
from app.engines.calendar.calendar import merge_segments, subtract_segments
from tests.engines.factories import (
    ALL_WEEK,
    NOW,
    WEEKDAYS,
    make_24x7_spec,
    make_calendar_spec,
    make_machine,
    make_snapshot,
    window,
)

MON = datetime(2026, 9, 7, tzinfo=UTC)  # Monday


def utc(day: int, hour: int, minute: int = 0, month: int = 9) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=UTC)


# ---------------------------------------------------------------- basics


class TestIsWorkingAndNext:
    def test_inside_shift_is_working(self, day_calendar: MachineCalendar) -> None:
        assert day_calendar.is_working(utc(7, 8))  # inclusive start
        assert day_calendar.is_working(utc(7, 15, 59))
        assert not day_calendar.is_working(utc(7, 16))  # exclusive end
        assert not day_calendar.is_working(utc(7, 7, 59))

    def test_weekend_not_working(self, day_calendar: MachineCalendar) -> None:
        assert not day_calendar.is_working(utc(5, 10))  # Saturday
        assert not day_calendar.is_working(utc(6, 10))  # Sunday

    def test_next_working_time_identity_when_working(self, day_calendar: MachineCalendar) -> None:
        assert day_calendar.next_working_time(utc(7, 10)) == utc(7, 10)

    def test_next_working_time_skips_night_and_weekend(self, day_calendar: MachineCalendar) -> None:
        assert day_calendar.next_working_time(utc(7, 16)) == utc(8, 8)
        assert day_calendar.next_working_time(utc(4, 17)) == utc(7, 8)  # Fri evening -> Mon

    def test_naive_datetimes_are_treated_as_utc(self, day_calendar: MachineCalendar) -> None:
        assert day_calendar.is_working(datetime(2026, 9, 7, 9, 0))
        assert day_calendar.next_working_time(datetime(2026, 9, 7, 17, 0)) == utc(8, 8)

    def test_non_utc_input_is_converted(self, day_calendar: MachineCalendar) -> None:
        from zoneinfo import ZoneInfo

        kolkata = datetime(2026, 9, 7, 14, 30, tzinfo=ZoneInfo("Asia/Kolkata"))  # 09:00 UTC
        assert day_calendar.is_working(kolkata)
        assert day_calendar.next_working_time(kolkata) == utc(7, 9)


class TestAddWorkMinutes:
    def test_within_shift(self, day_calendar: MachineCalendar) -> None:
        assert day_calendar.add_work_minutes(utc(7, 9), 90) == utc(7, 10, 30)

    def test_across_shift_boundary(self, day_calendar: MachineCalendar) -> None:
        # 15:00 + 120 min: 60 today, 60 tomorrow -> 09:00 next day
        assert day_calendar.add_work_minutes(utc(7, 15), 120) == utc(8, 9)

    def test_exact_fill_ends_at_shift_end(self, day_calendar: MachineCalendar) -> None:
        assert day_calendar.add_work_minutes(utc(7, 15), 60) == utc(7, 16)

    def test_start_outside_shift_moves_to_next_shift(self, day_calendar: MachineCalendar) -> None:
        assert day_calendar.add_work_minutes(utc(7, 6), 30) == utc(7, 8, 30)

    def test_zero_minutes_returns_next_working_time(self, day_calendar: MachineCalendar) -> None:
        assert day_calendar.add_work_minutes(utc(7, 17), 0) == utc(8, 8)
        assert day_calendar.add_work_minutes(utc(7, 9), 0) == utc(7, 9)

    def test_across_weekend(self, day_calendar: MachineCalendar) -> None:
        # Friday 15:00 + 8h shift + 1h = 60 (Fri) + 480 (Mon) + 60 (Tue)
        assert day_calendar.add_work_minutes(utc(4, 15), 600) == utc(8, 9)

    def test_fractional_minutes(self, day_calendar: MachineCalendar) -> None:
        assert day_calendar.add_work_minutes(utc(7, 8), 0.5) == utc(7, 8) + timedelta(seconds=30)

    def test_negative_minutes_rejected(self, day_calendar: MachineCalendar) -> None:
        with pytest.raises(ValidationError):
            day_calendar.add_work_minutes(utc(7, 8), -1)

    def test_multi_week_span(self, day_calendar: MachineCalendar) -> None:
        # 10 working days of 480 min from Monday 08:00 -> ends Friday of the second week at 16:00
        assert day_calendar.add_work_minutes(utc(7, 8), 4800) == utc(18, 16)


class TestWorkingMinutesAndWindows:
    def test_working_minutes_between(self, day_calendar: MachineCalendar) -> None:
        assert day_calendar.working_minutes_between(utc(7, 0), utc(8, 0)) == 480
        assert day_calendar.working_minutes_between(utc(7, 10), utc(7, 11)) == 60
        assert day_calendar.working_minutes_between(utc(5, 0), utc(7, 0)) == 0  # weekend
        assert day_calendar.working_minutes_between(utc(7, 0), utc(14, 0)) == 5 * 480

    def test_reverse_range_is_zero(self, day_calendar: MachineCalendar) -> None:
        assert day_calendar.working_minutes_between(utc(8, 0), utc(7, 0)) == 0
        assert day_calendar.working_windows(utc(8, 0), utc(7, 0)) == []

    def test_working_windows_clipped(self, day_calendar: MachineCalendar) -> None:
        windows = day_calendar.working_windows(utc(7, 12), utc(8, 10))
        assert [(w.start, w.end) for w in windows] == [(utc(7, 12), utc(7, 16)), (utc(8, 8), utc(8, 10))]
        assert windows[0].reason == "day"

    def test_available_hours(self, day_calendar: MachineCalendar) -> None:
        assert day_calendar.available_hours(utc(7, 0), utc(12, 0)) == 40.0

    def test_adjacent_shifts_merge(self) -> None:
        spec = make_calendar_spec(
            shifts=[Shift("a", time(6), time(14), WEEKDAYS), Shift("b", time(14), time(22), WEEKDAYS)]
        )
        cal = MachineCalendar(spec)
        windows = cal.working_windows(utc(7, 0), utc(8, 0))
        assert len(windows) == 1
        assert (windows[0].start, windows[0].end) == (utc(7, 6), utc(7, 22))
        assert windows[0].reason == "a+b"
        assert cal.add_work_minutes(utc(7, 13), 120) == utc(7, 15)


# ------------------------------------------------------- midnight / 24x7


class TestMidnightCrossing:
    @pytest.fixture
    def night(self) -> MachineCalendar:
        return MachineCalendar(make_calendar_spec(shifts=[Shift("night", time(22), time(6), WEEKDAYS)]))

    def test_shift_extends_into_next_day(self, night: MachineCalendar) -> None:
        assert night.is_working(utc(7, 23))
        assert night.is_working(utc(8, 3))
        assert not night.is_working(utc(8, 6))
        assert not night.is_working(utc(8, 12))

    def test_friday_night_runs_into_saturday(self, night: MachineCalendar) -> None:
        assert night.is_working(utc(5, 2))  # Saturday 02:00 belongs to Friday's shift
        assert not night.is_working(utc(6, 2))  # Sunday 02:00: no Saturday shift

    def test_add_minutes_across_midnight(self, night: MachineCalendar) -> None:
        assert night.add_work_minutes(utc(7, 23), 300) == utc(8, 4)
        assert night.working_minutes_between(utc(7, 22), utc(8, 6)) == 480

    def test_24x7_calendar(self, always_calendar: MachineCalendar) -> None:
        assert always_calendar.is_working(utc(6, 3))
        assert always_calendar.add_work_minutes(utc(5, 23), 120) == utc(6, 1)
        assert always_calendar.working_minutes_between(utc(1, 0), utc(8, 0)) == 7 * 1440
        assert len(always_calendar.working_windows(utc(1, 0), utc(8, 0))) == 1


# --------------------------------------------- holidays / extra days / overtime


class TestHolidaysAndOvertime:
    def test_holiday_removes_working_time(self) -> None:
        cal = MachineCalendar(make_calendar_spec(holidays=[date(2026, 9, 8)]))
        assert not cal.is_working(utc(8, 10))
        assert cal.add_work_minutes(utc(7, 15), 120) == utc(9, 9)

    def test_holiday_with_midnight_shift_only_removes_that_days_shift(self) -> None:
        cal = MachineCalendar(
            make_calendar_spec(
                shifts=[Shift("night", time(22), time(6), WEEKDAYS)], holidays=[date(2026, 9, 8)]
            )
        )
        assert cal.is_working(utc(8, 2))  # Monday's shift still runs into Tuesday morning
        assert not cal.is_working(utc(8, 23))  # Tuesday's own shift is cancelled

    def test_extra_working_day_enables_weekend(self) -> None:
        cal = MachineCalendar(make_calendar_spec(extra_working_days=[date(2026, 9, 5)]))
        assert cal.is_working(utc(5, 10))  # Saturday
        assert not cal.is_working(utc(6, 10))

    def test_extra_working_day_beats_holiday(self) -> None:
        cal = MachineCalendar(
            make_calendar_spec(holidays=[date(2026, 9, 8)], extra_working_days=[date(2026, 9, 8)])
        )
        assert cal.is_working(utc(8, 10))

    def test_overtime_window_adds_working_time(self) -> None:
        cal = MachineCalendar(make_calendar_spec(overtime_windows=[window(utc(7, 16), 2, "ot")]))
        assert cal.is_working(utc(7, 17))
        assert cal.working_minutes_between(utc(7, 0), utc(8, 0)) == 600
        windows = cal.working_windows(utc(7, 0), utc(8, 0))
        assert len(windows) == 1 and windows[0].reason == "day+ot"

    def test_overtime_on_weekend(self) -> None:
        cal = MachineCalendar(make_calendar_spec(overtime_windows=[window(utc(5, 8), 4, "saturday")]))
        assert cal.working_windows(utc(5, 0), utc(6, 0)) == [TimeWindow(utc(5, 8), utc(5, 12), "saturday")]


# ------------------------------------------------------------- downtime


class TestDowntime:
    def test_downtime_removes_working_time(self) -> None:
        cal = MachineCalendar(make_calendar_spec(), downtime=[window(utc(7, 10), 2, "maintenance")])
        assert not cal.is_working(utc(7, 11))
        assert cal.working_minutes_between(utc(7, 0), utc(8, 0)) == 360
        assert cal.add_work_minutes(utc(7, 9), 120) == utc(7, 13)
        assert cal.downtime_windows(utc(7, 0), utc(8, 0)) == [
            TimeWindow(utc(7, 10), utc(7, 12), "maintenance")
        ]

    def test_window_fully_inside_downtime(self) -> None:
        cal = MachineCalendar(make_calendar_spec(), downtime=[window(utc(7, 0), 24, "down all day")])
        assert cal.working_windows(utc(7, 0), utc(8, 0)) == []
        assert cal.next_working_time(utc(7, 9)) == utc(8, 8)
        assert cal.working_minutes_between(utc(7, 9), utc(7, 10)) == 0

    def test_multi_day_downtime_and_overlaps_merge(self) -> None:
        cal = MachineCalendar(
            make_calendar_spec(),
            downtime=[window(utc(7, 12), 30, "a"), window(utc(8, 10), 3, "b"), window(utc(8, 17), 1, "c")],
        )
        # Mon 12:00 -> Tue 18:00 blocked (a merges with b); Tue 17-18 is inside a anyway
        assert cal.working_minutes_between(utc(7, 0), utc(10, 0)) == 240 + 0 + 480
        assert cal.downtime_windows(utc(7, 0), utc(10, 0)) == [TimeWindow(utc(7, 12), utc(8, 18), "a+b+c")]

    def test_available_from_blocks_earlier_time(self) -> None:
        cal = MachineCalendar(make_calendar_spec(), available_from=utc(8, 12))
        assert not cal.is_working(utc(7, 10))
        assert cal.next_working_time(utc(7, 10)) == utc(8, 12)
        assert cal.downtime_windows(utc(7, 0), utc(9, 0))[0] == TimeWindow(
            utc(7, 0), utc(8, 12), "before_available_from"
        )

    def test_downtime_clipped_in_downtime_windows(self) -> None:
        cal = MachineCalendar(make_calendar_spec(), downtime=[window(utc(7, 6), 6, "m")])
        assert cal.downtime_windows(utc(7, 8), utc(7, 10)) == [TimeWindow(utc(7, 8), utc(7, 10), "m")]
        assert cal.downtime_windows(utc(8, 0), utc(9, 0)) == []


# -------------------------------------------------------- timezone / DST


class TestTimezones:
    def test_local_shift_converted_to_utc(self) -> None:
        cal = MachineCalendar(
            make_calendar_spec(timezone="Asia/Kolkata")
        )  # 08:00-16:00 IST = 02:30-10:30 UTC
        assert cal.working_windows(utc(7, 0), utc(8, 0)) == [TimeWindow(utc(7, 2, 30), utc(7, 10, 30), "day")]

    def test_negative_offset_midnight_shift(self) -> None:
        # America/Los_Angeles (-7 in September): 22:00-06:00 local = 05:00-13:00 UTC next day
        cal = MachineCalendar(
            make_calendar_spec(
                timezone="America/Los_Angeles", shifts=[Shift("n", time(22), time(6), WEEKDAYS)]
            )
        )
        assert cal.working_windows(utc(8, 0), utc(9, 0)) == [TimeWindow(utc(8, 5), utc(8, 13), "n")]

    def test_far_east_offset(self) -> None:
        # Pacific/Kiritimati is UTC+14: Monday 08:00 local = Sunday 18:00 UTC
        cal = MachineCalendar(make_calendar_spec(timezone="Pacific/Kiritimati"))
        assert cal.is_working(utc(6, 19))
        assert cal.working_minutes_between(utc(6, 18), utc(7, 2)) == 480

    def test_dst_spring_forward_shortens_night_shift(self) -> None:
        cal = MachineCalendar(
            make_calendar_spec(timezone="Europe/Berlin", shifts=[Shift("n", time(22), time(6), ALL_WEEK)])
        )
        # 2026-03-29 02:00 CET -> 03:00 CEST: the 22:00-06:00 shift lasts 7 hours
        assert cal.working_minutes_between(utc(28, 20, month=3), utc(29, 12, month=3)) == 420
        assert cal.working_minutes_between(utc(29, 20, month=3), utc(30, 12, month=3)) == 480

    def test_dst_fall_back_lengthens_night_shift(self) -> None:
        cal = MachineCalendar(
            make_calendar_spec(timezone="Europe/Berlin", shifts=[Shift("n", time(22), time(6), ALL_WEEK)])
        )
        assert cal.working_minutes_between(utc(24, 20, month=10), utc(25, 12, month=10)) == 540

    def test_shift_inside_dst_gap_is_skipped_not_negative(self) -> None:
        cal = MachineCalendar(
            make_calendar_spec(timezone="Europe/Berlin", shifts=[Shift("gap", time(2), time(3), ALL_WEEK)])
        )
        # On 2026-03-29 02:00-03:00 does not exist locally: no working time that night, no crash
        assert cal.working_minutes_between(utc(28, 22, month=3), utc(29, 6, month=3)) == 0
        assert cal.working_minutes_between(utc(29, 22, month=3), utc(30, 6, month=3)) == 60

    def test_unknown_timezone_is_configuration_error(self) -> None:
        with pytest.raises(ConfigurationError):
            ShiftPattern(make_calendar_spec(timezone="Mars/Olympus"))


# ---------------------------------------------------------- degenerate


class TestNeverWorking:
    def test_no_shifts_raises_after_bounded_search(self) -> None:
        cal = MachineCalendar(make_calendar_spec(shifts=[]))
        with pytest.raises(ConfigurationError) as exc:
            cal.add_work_minutes(NOW, 10)
        assert "no working time" in str(exc.value)
        assert exc.value.details["calendar_id"] == "CAL-DAY"
        with pytest.raises(ConfigurationError):
            cal.next_working_time(NOW)
        assert cal.working_minutes_between(NOW, NOW + timedelta(days=30)) == 0

    def test_shift_with_no_weekdays(self) -> None:
        cal = MachineCalendar(make_calendar_spec(shifts=[Shift("never", time(8), time(16), ())]))
        with pytest.raises(ConfigurationError):
            cal.add_work_minutes(NOW, 1)

    def test_everything_in_downtime(self) -> None:
        cal = MachineCalendar(make_calendar_spec(), downtime=[window(utc(1, 0), 24 * 120, "long outage")])
        with pytest.raises(ConfigurationError):
            cal.add_work_minutes(NOW, 1)

    def test_long_gap_but_eventually_working(self) -> None:
        cal = MachineCalendar(make_calendar_spec(), downtime=[window(utc(1, 0), 24 * 40, "outage")])
        assert cal.next_working_time(NOW) == datetime(
            2026, 10, 12, 8, tzinfo=UTC
        )  # first Monday after outage


class TestDescribeAndHelpers:
    def test_describe(self) -> None:
        cal = MachineCalendar(
            make_calendar_spec(timezone="Asia/Kolkata", holidays=[date(2026, 10, 2)]), machine_id="CNC-01"
        )
        info = cal.describe()
        assert info["machine_id"] == "CNC-01"
        assert info["timezone"] == "Asia/Kolkata"
        assert info["shifts"] == [
            {
                "name": "day",
                "start": "08:00",
                "end": "16:00",
                "weekdays": [0, 1, 2, 3, 4],
                "crosses_midnight": False,
            }
        ]
        assert info["holidays"] == 1
        assert "day 08:00-16:00" in info["summary"]

    def test_merge_and_subtract_segments(self) -> None:
        a, b, c, d = utc(7, 8), utc(7, 10), utc(7, 12), utc(7, 14)
        assert merge_segments([(c, d, "x"), (a, b, "y"), (b, c, "z")]) == [(a, d, "y+z+x")]
        assert subtract_segments([(a, d, "w")], [(b, c, "m")]) == [(a, b, "w"), (c, d, "w")]
        assert subtract_segments([(a, b, "w")], [(a, d, "m")]) == []
        assert subtract_segments([(a, b, "w")], []) == [(a, b, "w")]


# ------------------------------------------------------------- builder


class TestBuilder:
    def test_build_calendar_uses_machine_downtime_and_extra(self) -> None:
        machine = make_machine(
            maintenance_windows=[window(utc(7, 8), 2, "pm")],
            planned_downtime=[window(utc(8, 8), 1, "planned")],
            unplanned_downtime=[window(utc(9, 8), 1, "breakdown")],
            available_from=utc(7, 9),
        )
        cal = build_calendar(
            machine, make_calendar_spec(), extra_downtime=[window(utc(10, 8), 8, "scenario")]
        )
        assert cal.machine_id == "CNC-01"
        assert cal.working_minutes_between(utc(7, 0), utc(11, 0)) == (480 - 120) + 420 + 420 + 0
        reasons = [w.reason for w in cal.downtime_windows(utc(7, 0), utc(11, 0))]
        assert reasons == ["before_available_from", "pm", "planned", "breakdown", "scenario"]

    def test_build_calendars_resolution_and_fallback(self) -> None:
        spec_a = make_calendar_spec("A", timezone="Asia/Kolkata")
        spec_default = make_calendar_spec("DEF")
        machines = [
            make_machine("M1", calendar_id="A"),
            make_machine("M2"),  # -> default
            make_machine("M3", calendar_id="missing"),  # -> default with warning
        ]
        snap = make_snapshot(machines=machines, calendars=[spec_a, spec_default], default_calendar_id="DEF")
        cals = build_calendars(snap)
        assert set(cals) == {"M1", "M2", "M3"}
        assert cals["M1"].spec.calendar_id == "A"
        assert cals["M2"].spec.calendar_id == "DEF" and cals["M3"].spec.calendar_id == "DEF"
        assert cals["M2"]._pattern is cals["M3"]._pattern  # shared pattern per spec

    def test_build_calendars_24x7_when_nothing_configured(self) -> None:
        snap = make_snapshot(machines=[make_machine("M1")])
        cals = build_calendars(snap)
        assert cals["M1"].spec.calendar_id == default_24x7_spec().calendar_id
        assert cals["M1"].working_minutes_between(utc(5, 0), utc(6, 0)) == 1440

    def test_build_calendars_extra_downtime_per_machine(self) -> None:
        snap = make_snapshot(
            machines=[make_machine("M1"), make_machine("M2")],
            calendars=[make_24x7_spec("X")],
            default_calendar_id="X",
        )
        cals = build_calendars(snap, extra_downtime={"M1": [window(utc(7, 0), 24, "what-if")]})
        assert cals["M1"].working_minutes_between(utc(7, 0), utc(8, 0)) == 0
        assert cals["M2"].working_minutes_between(utc(7, 0), utc(8, 0)) == 1440


# ----------------------------------------------------------- performance


def test_add_work_minutes_performance() -> None:
    spec = make_calendar_spec(
        timezone="Asia/Kolkata",
        shifts=[Shift("a", time(6), time(14), WEEKDAYS), Shift("b", time(14), time(22), (0, 1, 2, 3, 4, 5))],
    )
    pattern = ShiftPattern(spec)
    calendars = [
        MachineCalendar(
            spec, downtime=[window(utc(7 + i % 10, 8), 2, "pm")], machine_id=f"M{i}", pattern=pattern
        )
        for i in range(200)
    ]
    start = _time.perf_counter()
    for i in range(10_000):
        calendars[i % 200].add_work_minutes(NOW + timedelta(hours=i % 300), (i % 2000) + 15)
    elapsed = _time.perf_counter() - start
    assert elapsed < 1.0, f"10k add_work_minutes took {elapsed:.2f}s"


# -------------------------------------------------------------- property


_calendars = [
    make_calendar_spec("p-day"),
    make_calendar_spec("p-night", timezone="Europe/Berlin", shifts=[Shift("n", time(22), time(6), ALL_WEEK)]),
    make_calendar_spec(
        "p-two",
        timezone="Asia/Kolkata",
        shifts=[Shift("a", time(6), time(14), WEEKDAYS), Shift("b", time(15), time(23), WEEKDAYS)],
        holidays=[date(2026, 9, 10)],
    ),
    make_24x7_spec("p-24x7"),
]
_downtimes = [[], [window(utc(8, 9), 3, "pm"), window(utc(11, 0), 30, "long")]]


@st.composite
def calendar_and_points(draw: st.DrawFn) -> tuple[MachineCalendar, datetime, float, float]:
    spec = draw(st.sampled_from(_calendars))
    downtime = draw(st.sampled_from(_downtimes))
    cal = MachineCalendar(spec, downtime=downtime)
    start = NOW + timedelta(minutes=draw(st.integers(min_value=-3 * 1440, max_value=20 * 1440)))
    m1 = draw(st.floats(min_value=0, max_value=5000, allow_nan=False, allow_infinity=False))
    m2 = draw(st.floats(min_value=0, max_value=5000, allow_nan=False, allow_infinity=False))
    return cal, start, m1, m2


@settings(max_examples=150, deadline=None)
@given(calendar_and_points())
def test_add_work_minutes_is_monotonic_and_invertible(
    data: tuple[MachineCalendar, datetime, float, float],
) -> None:
    cal, start, m1, m2 = data
    lo, hi = sorted((m1, m2))
    end_lo, end_hi = cal.add_work_minutes(start, lo), cal.add_work_minutes(start, hi)
    assert end_lo <= end_hi
    assert end_lo >= start
    assert cal.working_minutes_between(start, end_hi) == pytest.approx(hi, abs=1e-6)
    if hi > 0:
        assert cal.is_working(end_hi - timedelta(microseconds=1)) or cal.is_working(end_hi)
    assert cal.add_work_minutes(start, 0) == cal.next_working_time(start)


@settings(max_examples=60, deadline=None)
@given(calendar_and_points())
def test_working_minutes_is_additive(data: tuple[MachineCalendar, datetime, float, float]) -> None:
    cal, start, m1, _ = data
    mid = start + timedelta(minutes=m1)
    end = mid + timedelta(minutes=m1)
    assert cal.working_minutes_between(start, end) == pytest.approx(
        cal.working_minutes_between(start, mid) + cal.working_minutes_between(mid, end), abs=1e-6
    )
    assert cal.working_minutes_between(start, end) == pytest.approx(
        sum(w.minutes for w in cal.working_windows(start, end)), abs=1e-6
    )
