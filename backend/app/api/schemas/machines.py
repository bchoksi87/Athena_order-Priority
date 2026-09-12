"""Machine list / detail / schedule schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.api.schemas.orders import ScheduleEntryResponse
from app.api.schemas.overlays import LockResponse, TimeWindowResponse
from app.domain.enums import MachineStatus, ProcessType
from app.domain.models import Machine
from app.services.machine_query_service import MachineDetail, MachineListItem, MachineLoad, MachineSchedule


class MachineLoadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_number: int | None = Field(default=None, description="Schedule version the load comes from")
    status: str | None = None
    utilization_pct: float | None = None
    scheduled_hours: float
    setup_hours: float
    scheduled_entries: int
    next_free: datetime | None = None
    horizon_start: datetime | None = None
    horizon_end: datetime | None = None

    @classmethod
    def from_domain(cls, load: MachineLoad) -> MachineLoadResponse:
        return cls(
            version_number=load.version_number,
            status=load.status,
            utilization_pct=load.utilization_pct,
            scheduled_hours=load.scheduled_hours,
            setup_hours=load.setup_hours,
            scheduled_entries=load.scheduled_entries,
            next_free=load.next_free,
            horizon_start=load.horizon_start,
            horizon_end=load.horizon_end,
        )


class MachineSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    machine_id: str
    machine_name: str
    machine_type: str
    process_type: ProcessType
    machine_group: str
    location: str | None = None
    status: MachineStatus
    calendar_id: str | None = None
    efficiency: float
    utilization: float | None = Field(
        default=None, description="Trailing utilisation reported by the ERP (0..1)"
    )
    capacity_hours_per_day: float | None = None
    compatible_materials: list[str] = []
    compatible_processes: list[ProcessType] = []
    tooling_configuration: list[str] = []
    current_material_id: str | None = None
    current_setup_family: str | None = None
    available_from: datetime | None = None
    preferred_rank: int = 0

    @classmethod
    def from_domain(cls, m: Machine) -> MachineSummary:
        return cls(
            machine_id=m.machine_id,
            machine_name=m.machine_name,
            machine_type=m.machine_type,
            process_type=m.process_type,
            machine_group=m.machine_group,
            location=m.location,
            status=m.status,
            calendar_id=m.calendar_id,
            efficiency=m.efficiency,
            utilization=m.utilization,
            capacity_hours_per_day=m.capacity_hours_per_day,
            compatible_materials=sorted(m.compatible_materials),
            compatible_processes=sorted(m.compatible_processes, key=lambda p: p.value),
            tooling_configuration=sorted(m.tooling_configuration),
            current_material_id=m.current_material_id,
            current_setup_family=m.current_setup_family,
            available_from=m.available_from,
            preferred_rank=m.preferred_rank,
        )


class MachineListItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    machine: MachineSummary
    load: MachineLoadResponse
    active_locks: int

    @classmethod
    def from_item(cls, item: MachineListItem) -> MachineListItemResponse:
        return cls(
            machine=MachineSummary.from_domain(item.machine),
            load=MachineLoadResponse.from_domain(item.load),
            active_locks=item.active_locks,
        )


class DowntimeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    start: datetime
    end: datetime
    reason: str = ""


class MachineDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    machine: MachineSummary
    load: MachineLoadResponse
    calendar: dict[str, Any] = Field(
        description="MachineCalendar.describe(): shifts, holidays, downtime, summary"
    )
    calendar_source: str = Field(description="machine | default | fallback_24x7")
    downtime: list[DowntimeResponse] = []
    locks: list[LockResponse] = []
    upcoming: list[ScheduleEntryResponse] = Field(
        default_factory=list, description="Entries in the next 24 h"
    )

    @classmethod
    def from_detail(cls, d: MachineDetail) -> MachineDetailResponse:
        return cls(
            machine=MachineSummary.from_domain(d.machine),
            load=MachineLoadResponse.from_domain(d.load),
            calendar=d.calendar,
            calendar_source=d.calendar_source,
            downtime=[
                DowntimeResponse(kind=k, start=w.start, end=w.end, reason=w.reason) for k, w in d.downtime
            ],
            locks=[LockResponse.from_domain(lk) for lk in d.locks],
            upcoming=[ScheduleEntryResponse.from_domain(e) for e in d.upcoming],
        )


class MachineScheduleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    machine_id: str
    machine_name: str
    version_number: int | None = None
    status: str | None = None
    start: datetime
    end: datetime
    entries: list[ScheduleEntryResponse]
    downtime: list[TimeWindowResponse] = []
    locks: list[LockResponse] = []

    @classmethod
    def from_domain(cls, s: MachineSchedule) -> MachineScheduleResponse:
        return cls(
            machine_id=s.machine.machine_id,
            machine_name=s.machine.machine_name,
            version_number=s.version.version_number if s.version else None,
            status=s.version.status.value if s.version else None,
            start=s.start,
            end=s.end,
            entries=[ScheduleEntryResponse.from_domain(e) for e in s.entries],
            downtime=[TimeWindowResponse.from_domain(w) for w in s.downtime],
            locks=[LockResponse.from_domain(lk) for lk in s.locks],
        )


__all__ = [
    "DowntimeResponse",
    "MachineDetailResponse",
    "MachineListItemResponse",
    "MachineLoadResponse",
    "MachineScheduleResponse",
    "MachineSummary",
]
