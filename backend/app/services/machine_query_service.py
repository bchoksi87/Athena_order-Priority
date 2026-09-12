"""MachineQueryService: machine list, detail (calendar, downtime, capabilities) and schedule."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import structlog
from sqlalchemy.orm import Session

from app.core.clock import Clock, ensure_utc
from app.core.errors import ValidationError
from app.db.records import ScheduleVersionInfo
from app.db.repositories.overlays import LockRepository
from app.db.repositories.resources import CalendarRepository, MachineRepository
from app.db.repositories.schedule import ScheduleRepository
from app.domain.enums import MachineStatus, ProcessType
from app.domain.models import Machine, ScheduleLock, TimeWindow
from app.domain.results import ScheduleEntry
from app.domain.snapshot import PlanningSnapshot
from app.engines.calendar.builder import build_calendar, resolve_calendar_spec
from app.services.base import Service

log = structlog.get_logger(__name__)

DEFAULT_SCHEDULE_WINDOW_HOURS = 24.0
MAX_SCHEDULE_WINDOW_DAYS = 62


@dataclass(slots=True)
class MachineLoad:
    """Load of one machine in the current schedule version."""

    version_number: int | None
    status: str | None
    utilization_pct: float | None
    scheduled_hours: float
    setup_hours: float
    scheduled_entries: int
    next_free: datetime | None
    horizon_start: datetime | None
    horizon_end: datetime | None


@dataclass(slots=True)
class MachineListItem:
    machine: Machine
    load: MachineLoad
    active_locks: int


@dataclass(slots=True)
class MachineDetail:
    machine: Machine
    load: MachineLoad
    calendar: dict[str, Any]
    calendar_source: str
    downtime: list[tuple[str, TimeWindow]]
    locks: list[ScheduleLock]
    upcoming: list[ScheduleEntry] = field(default_factory=list)


@dataclass(slots=True)
class MachineSchedule:
    machine: Machine
    version: ScheduleVersionInfo | None
    start: datetime
    end: datetime
    entries: list[ScheduleEntry]
    downtime: list[TimeWindow]
    locks: list[ScheduleLock]


class MachineQueryService(Service):
    def __init__(self, session: Session, clock: Clock) -> None:
        super().__init__(session, clock)
        self._machines = MachineRepository(session)
        self._calendars = CalendarRepository(session)
        self._schedule = ScheduleRepository(session)
        self._locks = LockRepository(session)

    # ----------------------------------------------------------------- list
    def list_machines(
        self,
        *,
        machine_group: str | None = None,
        process_type: ProcessType | None = None,
        status: MachineStatus | None = None,
    ) -> list[MachineListItem]:
        now = self.now()
        machines = self._machines.list_all(
            machine_group=machine_group, process_type=process_type, status=status
        )
        version = self._schedule.get_current()
        entries = (
            self._schedule.get_entries(schedule_version_id=version.schedule_version_id) if version else []
        )
        by_machine: dict[str, list[ScheduleEntry]] = {}
        for entry in entries:
            by_machine.setdefault(entry.machine_id, []).append(entry)
        locks = self._locks.list_active(now)
        return [
            MachineListItem(
                machine=m,
                load=_load(version, by_machine.get(m.machine_id, []), m, now),
                active_locks=sum(1 for lk in locks if lk.machine_id == m.machine_id),
            )
            for m in machines
        ]

    # --------------------------------------------------------------- detail
    def get_machine_detail(self, machine_id: str) -> MachineDetail:
        now = self.now()
        machine = self._machines.get(machine_id)
        version = self._schedule.get_current()
        entries = self._schedule.get_entries(machine_id=machine_id) if version else []
        spec, source = resolve_calendar_spec(machine, self._calendar_snapshot(machine, now))
        calendar = build_calendar(machine, spec)
        downtime = [
            *(("maintenance", w) for w in machine.maintenance_windows),
            *(("planned", w) for w in machine.planned_downtime),
            *(("unplanned", w) for w in machine.unplanned_downtime),
        ]
        downtime.sort(key=lambda kw: (kw[1].start, kw[0]))
        horizon = now + timedelta(hours=DEFAULT_SCHEDULE_WINDOW_HOURS)
        upcoming = [e for e in entries if e.end > now and e.setup_start < horizon]
        upcoming.sort(key=lambda e: (e.setup_start, e.sequence_on_machine))
        return MachineDetail(
            machine=machine,
            load=_load(version, entries, machine, now),
            calendar=calendar.describe(),
            calendar_source=source,
            downtime=downtime,
            locks=[lk for lk in self._locks.list_active(now) if lk.machine_id == machine_id],
            upcoming=upcoming,
        )

    def get_machine_schedule(
        self,
        machine_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        version_number: int | None = None,
    ) -> MachineSchedule:
        now = self.now()
        machine = self._machines.get(machine_id)
        window_start = ensure_utc(start) if start is not None else now
        window_end = (
            ensure_utc(end)
            if end is not None
            else window_start + timedelta(hours=DEFAULT_SCHEDULE_WINDOW_HOURS)
        )
        if window_end <= window_start:
            raise ValidationError(
                "end must be after start",
                details={"start": window_start.isoformat(), "end": window_end.isoformat()},
            )
        if window_end - window_start > timedelta(days=MAX_SCHEDULE_WINDOW_DAYS):
            raise ValidationError(f"the schedule window may span at most {MAX_SCHEDULE_WINDOW_DAYS} days")
        version = (
            self._schedule.get_version(version_number)
            if version_number is not None
            else self._schedule.get_current()
        )
        entries: list[ScheduleEntry] = []
        if version is not None:
            entries = self._schedule.get_entries(
                schedule_version_id=version.schedule_version_id,
                machine_id=machine_id,
                overlapping=(window_start, window_end),
            )
            entries.sort(key=lambda e: (e.setup_start, e.sequence_on_machine))
        window = TimeWindow(window_start, window_end)
        downtime = sorted((w for w in machine.all_downtime if w.overlaps(window)), key=lambda w: w.start)
        locks = [
            lk
            for lk in self._locks.list_active(now)
            if lk.machine_id == machine_id and (lk.window is None or lk.window.overlaps(window))
        ]
        return MachineSchedule(
            machine=machine,
            version=version,
            start=window_start,
            end=window_end,
            entries=entries,
            downtime=downtime,
            locks=locks,
        )

    # ------------------------------------------------------------ internals
    def _calendar_snapshot(self, machine: Machine, now: datetime) -> PlanningSnapshot:
        """A minimal snapshot (machine + calendars) for calendar resolution."""
        return PlanningSnapshot(
            as_of=now,
            machines={machine.machine_id: machine},
            calendars={c.calendar_id: c for c in self._calendars.list_all()},
            default_calendar_id=self._calendars.get_default_id(),
            source="db",
        )


def _load(
    version: ScheduleVersionInfo | None, entries: list[ScheduleEntry], machine: Machine, now: datetime
) -> MachineLoad:
    utilisation: float | None = None
    if version is not None:
        per_machine = version.metrics.get("machine_utilization_pct") or {}
        value = per_machine.get(machine.machine_id) if isinstance(per_machine, dict) else None
        utilisation = float(value) if value is not None else None
    run_minutes = sum(e.run_minutes for e in entries)
    setup_minutes = sum(e.setup_minutes for e in entries)
    last_end = max((e.end for e in entries), default=None)
    next_free = last_end if last_end is not None and last_end > now else now
    if machine.available_from is not None and machine.available_from > next_free:
        next_free = machine.available_from
    return MachineLoad(
        version_number=version.version_number if version else None,
        status=version.status.value if version else None,
        utilization_pct=utilisation,
        scheduled_hours=(run_minutes + setup_minutes) / 60.0,
        setup_hours=setup_minutes / 60.0,
        scheduled_entries=len(entries),
        next_free=next_free,
        horizon_start=version.horizon_start if version else None,
        horizon_end=version.horizon_end if version else None,
    )


__all__ = [
    "DEFAULT_SCHEDULE_WINDOW_HOURS",
    "MachineDetail",
    "MachineListItem",
    "MachineLoad",
    "MachineQueryService",
    "MachineSchedule",
]
