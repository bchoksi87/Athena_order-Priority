"""Build :class:`MachineCalendar` objects from snapshot data (§6.3).

``build_calendar`` composes one machine's calendar from its spec plus its own
downtime (maintenance / planned / unplanned) and any *extra* downtime the
caller supplies (e.g. a what-if "machine down" scenario). ``build_calendars``
does it for a whole snapshot, sharing one :class:`ShiftPattern` per spec so
the day-by-day shift materialisation is computed once per calendar rather
than once per machine.

Fallback rules (logged, never silent):

* machine.calendar_id points to an unknown spec → use ``snapshot.default_calendar_id``;
* no default calendar either → a 24x7 calendar (``DEFAULT_24X7_CALENDAR_ID``)
  with a warning, so scheduling still works on incomplete ERP data.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import time

import structlog

from app.domain.models import CalendarSpec, Machine, Shift, TimeWindow
from app.domain.snapshot import PlanningSnapshot
from app.engines.calendar.calendar import MachineCalendar, ShiftPattern

log = structlog.get_logger(__name__)

DEFAULT_24X7_CALENDAR_ID = "__24x7__"


def default_24x7_spec(calendar_id: str = DEFAULT_24X7_CALENDAR_ID) -> CalendarSpec:
    """A calendar that works around the clock every day (fallback / tests)."""
    return CalendarSpec(
        calendar_id=calendar_id,
        name="24x7 (fallback)",
        timezone="UTC",
        shifts=[Shift(name="24x7", start=time(0, 0), end=time(0, 0), weekdays=(0, 1, 2, 3, 4, 5, 6))],
    )


def build_calendar(
    machine: Machine,
    spec: CalendarSpec,
    extra_downtime: Iterable[TimeWindow] = (),
    pattern: ShiftPattern | None = None,
) -> MachineCalendar:
    """Calendar for ``machine``: ``spec`` minus all machine downtime and ``extra_downtime``."""
    downtime = [*machine.all_downtime, *extra_downtime]
    return MachineCalendar(
        spec,
        downtime=downtime,
        available_from=machine.available_from,
        machine_id=machine.machine_id,
        pattern=pattern,
    )


def resolve_calendar_spec(machine: Machine, snapshot: PlanningSnapshot) -> tuple[CalendarSpec, str]:
    """Pick the spec for ``machine``; returns ``(spec, source)`` where source explains the choice."""
    if machine.calendar_id is not None:
        spec = snapshot.calendars.get(machine.calendar_id)
        if spec is not None:
            return spec, "machine"
        log.warning(
            "calendar.unknown_machine_calendar",
            machine_id=machine.machine_id,
            calendar_id=machine.calendar_id,
            fallback=snapshot.default_calendar_id,
        )
    if snapshot.default_calendar_id is not None:
        spec = snapshot.calendars.get(snapshot.default_calendar_id)
        if spec is not None:
            return spec, "default"
        log.warning("calendar.unknown_default_calendar", calendar_id=snapshot.default_calendar_id)
    return default_24x7_spec(), "fallback_24x7"


def build_calendars(
    snapshot: PlanningSnapshot,
    extra_downtime: Mapping[str, Iterable[TimeWindow]] | None = None,
) -> dict[str, MachineCalendar]:
    """One calendar per machine in ``snapshot`` (keyed by ``machine_id``).

    ``extra_downtime`` maps machine ids to additional windows (what-if scenarios).
    """
    patterns: dict[str, ShiftPattern] = {}
    calendars: dict[str, MachineCalendar] = {}
    fallback_machines: list[str] = []
    for machine_id in sorted(snapshot.machines):
        machine = snapshot.machines[machine_id]
        spec, source = resolve_calendar_spec(machine, snapshot)
        if source == "fallback_24x7":
            fallback_machines.append(machine_id)
        pattern = patterns.get(spec.calendar_id)
        if pattern is None or pattern.spec is not spec:
            pattern = ShiftPattern(spec)
            patterns[spec.calendar_id] = pattern
        extra = extra_downtime.get(machine_id, ()) if extra_downtime else ()
        calendars[machine_id] = build_calendar(machine, spec, extra_downtime=extra, pattern=pattern)
    if fallback_machines:
        log.warning(
            "calendar.fallback_24x7",
            machine_count=len(fallback_machines),
            machine_ids=fallback_machines[:20],
            reason="no machine calendar and no default calendar in snapshot",
        )
    log.debug("calendar.built", machines=len(calendars), specs=len(patterns))
    return calendars


__all__ = [
    "DEFAULT_24X7_CALENDAR_ID",
    "build_calendar",
    "build_calendars",
    "default_24x7_spec",
    "resolve_calendar_spec",
]
