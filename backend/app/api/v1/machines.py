"""``/machines``: machine list, detail and per-machine schedule."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import MachineQueryServiceDep, require_read_access
from app.api.schemas.machines import MachineDetailResponse, MachineListItemResponse, MachineScheduleResponse
from app.core.security import CurrentUser
from app.domain.enums import MachineStatus, ProcessType, Role

router = APIRouter(prefix="/machines", tags=["machines"])

Reader = Annotated[CurrentUser, Depends(require_read_access(Role.OPERATOR))]


@router.get("", response_model=list[MachineListItemResponse], summary="Machines with current load")
def list_machines(
    _user: Reader,
    service: MachineQueryServiceDep,
    machine_group: str | None = None,
    process_type: ProcessType | None = None,
    status: MachineStatus | None = None,
) -> list[MachineListItemResponse]:
    items = service.list_machines(machine_group=machine_group, process_type=process_type, status=status)
    return [MachineListItemResponse.from_item(i) for i in items]


@router.get("/{machine_id}", response_model=MachineDetailResponse, summary="Machine detail")
def get_machine(machine_id: str, _user: Reader, service: MachineQueryServiceDep) -> MachineDetailResponse:
    return MachineDetailResponse.from_detail(service.get_machine_detail(machine_id))


@router.get("/{machine_id}/schedule", response_model=MachineScheduleResponse, summary="Machine schedule")
def get_machine_schedule(
    machine_id: str,
    _user: Reader,
    service: MachineQueryServiceDep,
    start: Annotated[datetime | None, Query(description="Default: now")] = None,
    end: Annotated[datetime | None, Query(description="Default: start + 24 h")] = None,
    version: Annotated[int | None, Query(description="Schedule version (default: current)")] = None,
) -> MachineScheduleResponse:
    schedule = service.get_machine_schedule(machine_id, start=start, end=end, version_number=version)
    return MachineScheduleResponse.from_domain(schedule)


__all__ = ["router"]
