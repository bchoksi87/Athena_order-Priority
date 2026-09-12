"""ScheduleViewService: machine-centric read models of a schedule version (spec Phase 8, MACHINE VIEW).

* ``day_view(date)`` — the plant-local day (timezone of the default calendar)
  grouped by machine: every entry overlapping the day, the machine's downtime
  windows and active locks inside it;
* ``gantt(...)`` — rows per machine (optionally one machine group, one process
  or an explicit machine list) with setup/run blocks, the order's customer,
  part and status, ``locked`` and ``late`` flags, plus the axis bounds.

Both views read the stored entries of one version (default: the current plan)
and join order / customer master data in bulk; nothing here runs an engine.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog
from sqlalchemy.orm import Session

from app.core.clock import Clock, ensure_utc
from app.core.errors import ValidationError
from app.db.records import ScheduleVersionInfo
from app.db.repositories.customers import CustomerRepository
from app.db.repositories.orders import OrderRepository
from app.db.repositories.overlays import LockRepository
from app.db.repositories.resources import CalendarRepository, MachineRepository
from app.domain.enums import ProcessType
from app.domain.models import Customer, Machine, Order, ScheduleLock, TimeWindow
from app.domain.results import ScheduleEntry
from app.services.base import Service
from app.services.schedule_service import ScheduleService

log = structlog.get_logger(__name__)

DEFAULT_GANTT_DAYS = 7
MAX_GANTT_DAYS = 62


@dataclass(slots=True)
class GanttBlock:
    entry: ScheduleEntry
    order: Order | None
    customer_name: str | None
    late: bool

    @property
    def status(self) -> str:
        return self.order.order_status.value if self.order is not None else "unknown"


@dataclass(slots=True)
class MachineRow:
    machine: Machine
    blocks: list[GanttBlock] = field(default_factory=list)
    downtime: list[TimeWindow] = field(default_factory=list)
    locks: list[ScheduleLock] = field(default_factory=list)

    @property
    def busy_minutes(self) -> float:
        return sum(b.entry.setup_minutes + b.entry.run_minutes for b in self.blocks)


@dataclass(slots=True)
class DayView:
    day: date
    timezone: str
    start: datetime
    end: datetime
    version: ScheduleVersionInfo
    rows: list[MachineRow]

    @property
    def entries(self) -> int:
        return sum(len(r.blocks) for r in self.rows)


@dataclass(slots=True)
class GanttView:
    version: ScheduleVersionInfo
    axis_start: datetime
    axis_end: datetime
    rows: list[MachineRow]
    machine_group: str | None = None
    process_type: ProcessType | None = None

    @property
    def entries(self) -> int:
        return sum(len(r.blocks) for r in self.rows)


class ScheduleViewService(Service):
    def __init__(self, session: Session, clock: Clock, schedules: ScheduleService) -> None:
        super().__init__(session, clock)
        self._schedules = schedules
        self._machines = MachineRepository(session)
        self._orders = OrderRepository(session)
        self._customers = CustomerRepository(session)
        self._calendars = CalendarRepository(session)
        self._locks = LockRepository(session)

    # ------------------------------------------------------------- day view
    def day_view(self, day: date, version_number: int | None = None) -> DayView:
        version = self._schedules.resolve_version(version_number)
        tz_name, tz = self._plant_timezone()
        start = datetime.combine(day, datetime.min.time(), tzinfo=tz).astimezone(
            ensure_utc(self.now()).tzinfo
        )
        end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=tz).astimezone(
            start.tzinfo
        )
        entries = self._schedules.entries_of(version, start=start, end=end)
        rows = self._rows(self._machines.list_all(), entries, start, end)
        log.debug(
            "schedule.day_view", day=day.isoformat(), version=version.version_number, entries=len(entries)
        )
        return DayView(day=day, timezone=tz_name, start=start, end=end, version=version, rows=rows)

    # ---------------------------------------------------------------- gantt
    def gantt(
        self,
        *,
        version_number: int | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        machine_group: str | None = None,
        process_type: ProcessType | None = None,
        machine_ids: list[str] | None = None,
    ) -> GanttView:
        version = self._schedules.resolve_version(version_number)
        axis_start = (
            ensure_utc(start) if start is not None else max(ensure_utc(self.now()), version.horizon_start)
        )
        axis_end = ensure_utc(end) if end is not None else axis_start + timedelta(days=DEFAULT_GANTT_DAYS)
        if axis_end <= axis_start:
            raise ValidationError(
                "end must be after start",
                details={"start": axis_start.isoformat(), "end": axis_end.isoformat()},
            )
        if axis_end - axis_start > timedelta(days=MAX_GANTT_DAYS):
            raise ValidationError(f"the Gantt window may span at most {MAX_GANTT_DAYS} days")
        machines = self._machines.list_all(machine_group=machine_group, process_type=process_type)
        if machine_ids:
            wanted = set(machine_ids)
            machines = [m for m in machines if m.machine_id in wanted]
        entries = self._schedules.entries_of(
            version, machine_ids=[m.machine_id for m in machines] or None, start=axis_start, end=axis_end
        )
        rows = self._rows(machines, entries, axis_start, axis_end)
        return GanttView(
            version=version,
            axis_start=axis_start,
            axis_end=axis_end,
            rows=rows,
            machine_group=machine_group,
            process_type=process_type,
        )

    # ------------------------------------------------------------ internals
    def _rows(
        self, machines: list[Machine], entries: list[ScheduleEntry], start: datetime, end: datetime
    ) -> list[MachineRow]:
        window = TimeWindow(start, end)
        orders = self._orders.get_many({e.order_id for e in entries})
        customers = self._customers.get_many({o.customer_id for o in orders.values()})
        locks = self._locks.list_active(self.now())
        by_machine: dict[str, list[ScheduleEntry]] = {}
        for entry in entries:
            by_machine.setdefault(entry.machine_id, []).append(entry)
        rows: list[MachineRow] = []
        for machine in machines:
            blocks = [
                GanttBlock(
                    entry=e,
                    order=orders.get(e.order_id),
                    customer_name=_customer_name(customers, orders.get(e.order_id), e),
                    late=_is_late(e),
                )
                for e in sorted(
                    by_machine.get(machine.machine_id, []),
                    key=lambda e: (e.setup_start, e.sequence_on_machine),
                )
            ]
            rows.append(
                MachineRow(
                    machine=machine,
                    blocks=blocks,
                    downtime=sorted(
                        (w for w in machine.all_downtime if w.overlaps(window)), key=lambda w: w.start
                    ),
                    locks=[
                        lk
                        for lk in locks
                        if lk.machine_id == machine.machine_id
                        and (lk.window is None or lk.window.overlaps(window))
                    ],
                )
            )
        return rows

    def _plant_timezone(self) -> tuple[str, ZoneInfo]:
        default_id = self._calendars.get_default_id()
        name = "UTC"
        if default_id is not None:
            try:
                name = self._calendars.get(default_id).timezone or "UTC"
            except Exception:  # missing default calendar row: fall back to UTC
                name = "UTC"
        try:
            return name, ZoneInfo(name)
        except ZoneInfoNotFoundError:
            log.warning("schedule.unknown_plant_timezone", timezone=name)
            return "UTC", ZoneInfo("UTC")


def _customer_name(
    customers: Mapping[str, Customer], order: Order | None, entry: ScheduleEntry
) -> str | None:
    customer_id = order.customer_id if order is not None else entry.customer_id
    if customer_id is None:
        return None
    customer = customers.get(customer_id)
    return customer.customer_name if customer is not None else None


def _is_late(entry: ScheduleEntry) -> bool:
    if entry.expected_lateness_hours is not None:
        return entry.expected_lateness_hours > 0
    if entry.due_date is not None:
        return ensure_utc(entry.end) > ensure_utc(entry.due_date)
    return False


__all__ = [
    "DEFAULT_GANTT_DAYS",
    "MAX_GANTT_DAYS",
    "DayView",
    "GanttBlock",
    "GanttView",
    "MachineRow",
    "ScheduleViewService",
]
