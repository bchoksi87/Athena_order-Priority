"""Machine working-time calendars (DESIGN_CONTRACT §6.3)."""

from app.engines.calendar.builder import (
    DEFAULT_24X7_CALENDAR_ID,
    build_calendar,
    build_calendars,
    default_24x7_spec,
    resolve_calendar_spec,
)
from app.engines.calendar.calendar import MAX_SEARCH_DAYS, MachineCalendar, ShiftPattern

__all__ = [
    "DEFAULT_24X7_CALENDAR_ID",
    "MAX_SEARCH_DAYS",
    "MachineCalendar",
    "ShiftPattern",
    "build_calendar",
    "build_calendars",
    "default_24x7_spec",
    "resolve_calendar_spec",
]
