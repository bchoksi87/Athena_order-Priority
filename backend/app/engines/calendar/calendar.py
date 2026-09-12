"""Machine working-time calendar (DESIGN_CONTRACT §6.3).

A :class:`MachineCalendar` answers "when can this machine work?" for the
scheduler, analytics and readiness engines. It combines

* a :class:`~app.domain.models.CalendarSpec` (recurring shifts by weekday,
  holidays, extra working days, absolute overtime windows) whose shift times
  are *local* to ``spec.timezone``,
* machine downtime windows (maintenance / planned / unplanned, UTC), and
* an optional "unavailable before ``available_from``" window.

All public API accepts and returns timezone-aware UTC datetimes; naive inputs
are assumed UTC (see :func:`app.core.clock.ensure_utc`).

Algorithm
---------
Working time is materialised lazily, one *UTC day* at a time, and cached:

1. :class:`ShiftPattern` converts the shifts of a local calendar date into
   UTC segments (a shift whose ``end <= start`` crosses midnight and ends on
   the next local date). DST transitions are handled by ``zoneinfo`` — a
   22:00-06:00 shift lasts 7 real hours on the spring-forward night and 9 on
   the fall-back night, exactly as on the shop floor.
2. For a UTC day, the segments of the local dates that can overlap it
   (``[U-2, U+1]`` — enough for every UTC offset in ``[-12, +14]`` and shifts
   up to 24 h) are clipped to the day, absolute overtime windows are added,
   overlapping segments are merged, and downtime plus the pre-``available_from``
   window are subtracted. The result is a sorted list of disjoint segments.
3. ``add_work_minutes`` / ``working_minutes_between`` / ``next_working_time``
   walk day by day over those cached segments. A typical scheduling horizon
   touches ~14 days per machine, so thousands of calls cost only dictionary
   look-ups after the first pass.

A calendar that never works (no shifts, no overtime, or everything eaten by
downtime) is detected by a bounded search: if ``MAX_SEARCH_DAYS`` consecutive
days contain no working time, a :class:`~app.core.errors.ConfigurationError`
is raised instead of looping forever.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.clock import ensure_utc
from app.core.errors import ConfigurationError, ValidationError
from app.domain.models import CalendarSpec, Shift, TimeWindow

#: Internal working/blocking segment: ``(start_utc, end_utc, reason)``.
Segment = tuple[datetime, datetime, str]

#: Safety bound for "never working" detection (consecutive empty days). This
#: is not a business parameter: it only bounds the search for the next
#: working window so misconfigured calendars fail fast instead of spinning.
MAX_SEARCH_DAYS = 60

_ONE_DAY = timedelta(days=1)
_MINUTE_EPS = 1e-9


def _join_reasons(a: str, b: str) -> str:
    if not a:
        return b
    if not b or a == b or b in a.split("+"):
        return a
    return f"{a}+{b}"


def merge_segments(segments: Iterable[Segment]) -> list[Segment]:
    """Sort segments and merge overlapping or touching ones (reasons joined)."""
    ordered = sorted((s for s in segments if s[1] > s[0]), key=lambda s: (s[0], s[1]))
    merged: list[Segment] = []
    for start, end, reason in ordered:
        if merged and start <= merged[-1][1]:
            last_start, last_end, last_reason = merged[-1]
            merged[-1] = (last_start, max(last_end, end), _join_reasons(last_reason, reason))
        else:
            merged.append((start, end, reason))
    return merged


def subtract_segments(segments: list[Segment], blockers: list[Segment]) -> list[Segment]:
    """Remove ``blockers`` from ``segments``; both inputs sorted and disjoint."""
    if not blockers or not segments:
        return list(segments)
    out: list[Segment] = []
    for start, end, reason in segments:
        cursor = start
        for b_start, b_end, _ in blockers:
            if b_end <= cursor:
                continue
            if b_start >= end:
                break
            if b_start > cursor:
                out.append((cursor, b_start, reason))
            cursor = max(cursor, b_end)
            if cursor >= end:
                break
        if cursor < end:
            out.append((cursor, end, reason))
    return out


def _day_bounds(ordinal: int) -> tuple[datetime, datetime]:
    d = date.fromordinal(ordinal)
    start = datetime(d.year, d.month, d.day, tzinfo=UTC)
    return start, start + _ONE_DAY


def _minutes(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 60.0


class ShiftPattern:
    """UTC working segments implied by a :class:`CalendarSpec`, cached per day.

    One pattern can be shared by every machine using the same spec: it holds
    no machine-specific state (downtime is applied by :class:`MachineCalendar`).
    """

    def __init__(self, spec: CalendarSpec) -> None:
        self.spec = spec
        tz_name = spec.timezone or "UTC"
        try:
            self.tz = ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError) as exc:  # pragma: no cover - env dependent
            raise ConfigurationError(
                f"Calendar {spec.calendar_id!r} has unknown timezone {tz_name!r}",
                details={"calendar_id": spec.calendar_id, "timezone": tz_name},
            ) from exc
        self._holidays = frozenset(spec.holidays)
        self._extra_days = frozenset(spec.extra_working_days)
        self._overtime = merge_segments(
            (ensure_utc(w.start), ensure_utc(w.end), w.reason or "overtime") for w in spec.overtime_windows
        )
        self._overtime_ends = [e for _, e, _ in self._overtime]
        self._local_cache: dict[int, list[Segment]] = {}
        self._utc_cache: dict[int, list[Segment]] = {}

    @property
    def has_working_time(self) -> bool:
        return bool(self.spec.shifts) or bool(self._overtime)

    def _active_shifts(self, d: date) -> list[Shift]:
        if d in self._extra_days:
            return list(self.spec.shifts)  # explicit extra day: every shift runs
        if d in self._holidays:
            return []
        weekday = d.weekday()
        return [s for s in self.spec.shifts if weekday in s.weekdays]

    def _local_date_segments(self, ordinal: int) -> list[Segment]:
        cached = self._local_cache.get(ordinal)
        if cached is not None:
            return cached
        d = date.fromordinal(ordinal)
        segments: list[Segment] = []
        for shift in self._active_shifts(d):
            start_local = datetime.combine(d, shift.start, tzinfo=self.tz)
            end_day = d + _ONE_DAY if shift.crosses_midnight else d
            end_local = datetime.combine(end_day, shift.end, tzinfo=self.tz)
            start, end = start_local.astimezone(UTC), end_local.astimezone(UTC)
            if end > start:
                segments.append((start, end, shift.name or "shift"))
        self._local_cache[ordinal] = segments
        return segments

    def _overtime_in(self, day_start: datetime, day_end: datetime) -> list[Segment]:
        out: list[Segment] = []
        idx = bisect_right(self._overtime_ends, day_start)
        while idx < len(self._overtime) and self._overtime[idx][0] < day_end:
            s, e, r = self._overtime[idx]
            out.append((max(s, day_start), min(e, day_end), r))
            idx += 1
        return out

    def utc_day_segments(self, ordinal: int) -> list[Segment]:
        """Merged working segments inside the UTC day with the given ordinal."""
        cached = self._utc_cache.get(ordinal)
        if cached is not None:
            return cached
        day_start, day_end = _day_bounds(ordinal)
        raw: list[Segment] = []
        for local_ordinal in range(ordinal - 2, ordinal + 2):
            for s, e, r in self._local_date_segments(local_ordinal):
                cs, ce = max(s, day_start), min(e, day_end)
                if ce > cs:
                    raw.append((cs, ce, r))
        raw.extend(self._overtime_in(day_start, day_end))
        merged = merge_segments(raw)
        self._utc_cache[ordinal] = merged
        return merged


class MachineCalendar:
    """Working time of one machine: shift pattern minus downtime (§6.3)."""

    def __init__(
        self,
        spec: CalendarSpec,
        downtime: Iterable[TimeWindow] = (),
        available_from: datetime | None = None,
        machine_id: str | None = None,
        pattern: ShiftPattern | None = None,
        max_search_days: int = MAX_SEARCH_DAYS,
    ) -> None:
        self.spec = spec
        self.machine_id = machine_id
        self.available_from = ensure_utc(available_from) if available_from is not None else None
        self.max_search_days = max_search_days
        self._pattern = pattern if pattern is not None and pattern.spec is spec else ShiftPattern(spec)
        self._downtime = merge_segments(
            (ensure_utc(w.start), ensure_utc(w.end), w.reason or "downtime") for w in downtime
        )
        self._downtime_ends = [e for _, e, _ in self._downtime]
        self._day_cache: dict[int, list[Segment]] = {}

    # ------------------------------------------------------------ internals
    @property
    def timezone(self) -> str:
        return self.spec.timezone or "UTC"

    def _blockers_in(self, day_start: datetime, day_end: datetime) -> list[Segment]:
        out: list[Segment] = []
        if self.available_from is not None and self.available_from > day_start:
            out.append((day_start, min(self.available_from, day_end), "before_available_from"))
        idx = bisect_right(self._downtime_ends, day_start)
        while idx < len(self._downtime) and self._downtime[idx][0] < day_end:
            s, e, r = self._downtime[idx]
            out.append((max(s, day_start), min(e, day_end), r))
            idx += 1
        return merge_segments(out)

    def _day(self, ordinal: int) -> list[Segment]:
        cached = self._day_cache.get(ordinal)
        if cached is not None:
            return cached
        working = self._pattern.utc_day_segments(ordinal)
        if working:
            day_start, day_end = _day_bounds(ordinal)
            blockers = self._blockers_in(day_start, day_end)
            if blockers:
                working = subtract_segments(working, blockers)
        self._day_cache[ordinal] = working
        return working

    def _never_working_error(self, t: datetime) -> ConfigurationError:
        return ConfigurationError(
            f"Calendar {self.spec.calendar_id!r} has no working time within {self.max_search_days} days "
            f"of {t.isoformat()}" + (f" for machine {self.machine_id!r}" if self.machine_id else ""),
            details={
                "calendar_id": self.spec.calendar_id,
                "machine_id": self.machine_id,
                "from": t.isoformat(),
                "search_days": self.max_search_days,
                "shifts": len(self.spec.shifts),
            },
        )

    # --------------------------------------------------------------- public
    def is_working(self, t: datetime) -> bool:
        t = ensure_utc(t)
        return any(s <= t < e for s, e, _ in self._day(t.date().toordinal()))

    def next_working_time(self, t: datetime) -> datetime:
        """Earliest working instant ``>= t`` (``t`` itself when already working)."""
        t = ensure_utc(t)
        ordinal = t.date().toordinal()
        empty_days = 0
        while True:
            segments = self._day(ordinal)
            for s, e, _ in segments:
                if e > t:
                    return max(s, t)
            empty_days = empty_days + 1 if not segments else 0
            if empty_days > self.max_search_days:
                raise self._never_working_error(t)
            ordinal += 1

    def add_work_minutes(self, start: datetime, minutes: float) -> datetime:
        """Instant at which ``minutes`` of working time after ``start`` are done.

        Non-working time is skipped. Zero minutes returns
        :meth:`next_working_time`; work that ends exactly at a shift boundary
        ends *at* that boundary (not at the start of the next shift).
        """
        if minutes < 0:
            raise ValidationError("add_work_minutes requires minutes >= 0", details={"minutes": minutes})
        start = ensure_utc(start)
        if minutes == 0:
            return self.next_working_time(start)
        remaining = float(minutes)
        cursor = start
        ordinal = start.date().toordinal()
        empty_days = 0
        while True:
            segments = self._day(ordinal)
            consumed = False
            for s, e, _ in segments:
                if e <= cursor:
                    continue
                seg_start = max(s, cursor)
                available = _minutes(seg_start, e)
                if remaining <= available + _MINUTE_EPS:
                    return seg_start + timedelta(minutes=remaining)
                remaining -= available
                cursor = e
                consumed = True
            empty_days = 0 if consumed else empty_days + 1
            if empty_days > self.max_search_days:
                raise self._never_working_error(cursor)
            ordinal += 1

    def working_minutes_between(self, a: datetime, b: datetime) -> float:
        """Working minutes in ``[a, b)``; ``0`` when ``b <= a``."""
        a, b = ensure_utc(a), ensure_utc(b)
        if b <= a:
            return 0.0
        total = 0.0
        for ordinal in range(a.date().toordinal(), b.date().toordinal() + 1):
            for s, e, _ in self._day(ordinal):
                lo, hi = max(s, a), min(e, b)
                if hi > lo:
                    total += _minutes(lo, hi)
        return total

    def working_windows(self, a: datetime, b: datetime) -> list[TimeWindow]:
        """Working windows clipped to ``[a, b)``, merged across day boundaries."""
        a, b = ensure_utc(a), ensure_utc(b)
        if b <= a:
            return []
        segments: list[Segment] = []
        for ordinal in range(a.date().toordinal(), b.date().toordinal() + 1):
            for s, e, r in self._day(ordinal):
                lo, hi = max(s, a), min(e, b)
                if hi > lo:
                    if segments and segments[-1][1] == lo and segments[-1][2] == r:
                        segments[-1] = (segments[-1][0], hi, r)
                    else:
                        segments.append((lo, hi, r))
        return [TimeWindow(s, e, r) for s, e, r in segments]

    def available_hours(self, a: datetime, b: datetime) -> float:
        return self.working_minutes_between(a, b) / 60.0

    def downtime_windows(self, a: datetime, b: datetime) -> list[TimeWindow]:
        """Downtime (and pre-``available_from``) windows overlapping ``[a, b)``."""
        a, b = ensure_utc(a), ensure_utc(b)
        if b <= a:
            return []
        out: list[TimeWindow] = []
        if self.available_from is not None and self.available_from > a:
            out.append(TimeWindow(a, min(self.available_from, b), "before_available_from"))
        idx = bisect_right(self._downtime_ends, a)
        while idx < len(self._downtime) and self._downtime[idx][0] < b:
            s, e, r = self._downtime[idx]
            out.append(TimeWindow(max(s, a), min(e, b), r))
            idx += 1
        return out

    def describe(self) -> dict[str, Any]:
        """Machine-readable description for explanations and API payloads."""
        shifts = [
            {
                "name": s.name,
                "start": s.start.strftime("%H:%M"),
                "end": s.end.strftime("%H:%M"),
                "weekdays": list(s.weekdays),
                "crosses_midnight": s.crosses_midnight,
            }
            for s in self.spec.shifts
        ]
        shift_text = ", ".join(f"{s['name']} {s['start']}-{s['end']}" for s in shifts) or "no shifts"
        summary = (
            f"Calendar {self.spec.name!r} ({self.timezone}): {shift_text}; "
            f"{len(self.spec.holidays)} holiday(s), {len(self.spec.extra_working_days)} extra day(s), "
            f"{len(self._pattern._overtime)} overtime window(s), {len(self._downtime)} downtime window(s)"
        )
        if self.available_from is not None:
            summary += f"; unavailable before {self.available_from.isoformat()}"
        return {
            "machine_id": self.machine_id,
            "calendar_id": self.spec.calendar_id,
            "name": self.spec.name,
            "timezone": self.timezone,
            "shifts": shifts,
            "holidays": len(self.spec.holidays),
            "extra_working_days": len(self.spec.extra_working_days),
            "overtime_windows": len(self._pattern._overtime),
            "downtime_windows": len(self._downtime),
            "available_from": self.available_from.isoformat() if self.available_from else None,
            "summary": summary,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"MachineCalendar(machine_id={self.machine_id!r}, calendar_id={self.spec.calendar_id!r})"


__all__ = [
    "MAX_SEARCH_DAYS",
    "MachineCalendar",
    "Segment",
    "ShiftPattern",
    "merge_segments",
    "subtract_segments",
]
