"""``/schedule``: the active plan, versions, generation, approval workflow, views, comparison, replanning.

Static paths are registered before ``GET /schedule/{date}`` so that
``/schedule/versions``, ``/schedule/gantt`` ... (and the lock endpoints
mounted by :mod:`app.api.v1.locks`, included earlier in the router) win.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status

from app.api.deps import (
    ReplanningServiceDep,
    ScheduleServiceDep,
    ScheduleViewServiceDep,
    SimulationServiceDep,
    require_min_role,
    require_read_access,
)
from app.api.schemas.common import PageResponse
from app.api.schemas.orders import ScheduleEntryResponse
from app.api.schemas.schedule import (
    GenerateRequest,
    PublishResponse,
    RunDetailsResponse,
    ScheduleComparisonResponse,
    ScheduleGenerateResponse,
    SchedulePlanResponse,
    ScheduleVersionDetailResponse,
    ScheduleVersionResponse,
    VersionActionRequest,
)
from app.api.schemas.schedule_views import DayScheduleResponse, GanttResponse, ReplanRequest, ReplanResponse
from app.api.schemas.simulation import SimulateRequest, SimulationResponse
from app.core.errors import ValidationError
from app.core.security import CurrentUser
from app.domain.enums import ProcessType, Role, ScheduleStatus
from app.services.base import Pagination

router = APIRouter(prefix="/schedule", tags=["schedule"])

Reader = Annotated[CurrentUser, Depends(require_read_access(Role.OPERATOR))]
Planner = Annotated[CurrentUser, Depends(require_min_role(Role.PLANNER))]
Manager = Annotated[CurrentUser, Depends(require_min_role(Role.PRODUCTION_MANAGER))]

PageQ = Annotated[int, Query(ge=1)]
PageSizeQ = Annotated[int, Query(ge=1, le=500)]
MachineIdsQ = Annotated[list[str] | None, Query(alias="machine_id", description="Repeatable")]


def _entries_page(
    service: ScheduleServiceDep,
    *,
    version: int | None,
    machine_ids: list[str] | None,
    start: datetime | None,
    end: datetime | None,
    order_id: str | None,
    customer_id: str | None,
    page: int,
    page_size: int,
) -> PageResponse[ScheduleEntryResponse]:
    result = service.entries(
        Pagination(page, page_size),
        version_number=version,
        machine_ids=machine_ids,
        start=start,
        end=end,
        order_id=order_id,
        customer_id=customer_id,
    )
    return PageResponse.build(
        [ScheduleEntryResponse.from_domain(e) for e in result.page.items],
        total=result.page.total,
        page=result.page.page,
        page_size=result.page.page_size,
    )


# --------------------------------------------------------------- active plan


@router.get("", response_model=SchedulePlanResponse, summary="Active plan with a page of its entries")
def get_schedule(
    _user: Reader,
    service: ScheduleServiceDep,
    page: PageQ = 1,
    page_size: PageSizeQ = 100,
    machine_ids: MachineIdsQ = None,
    start: datetime | None = None,
    end: datetime | None = None,
    order_id: str | None = None,
    customer_id: str | None = None,
) -> SchedulePlanResponse:
    """Latest PUBLISHED, else APPROVED, else DRAFT version (``status`` says which); ``none`` when empty."""
    plan = service.current()
    if plan.version is None:
        return SchedulePlanResponse.build(
            plan, PageResponse.build([], total=0, page=page, page_size=page_size)
        )
    entries = _entries_page(
        service,
        version=plan.version.version_number,
        machine_ids=machine_ids,
        start=start,
        end=end,
        order_id=order_id,
        customer_id=customer_id,
        page=page,
        page_size=page_size,
    )
    return SchedulePlanResponse.build(plan, entries)


# ------------------------------------------------------------------ versions


@router.get("/versions", response_model=PageResponse[ScheduleVersionResponse], summary="Schedule versions")
def list_versions(
    _user: Reader,
    service: ScheduleServiceDep,
    page: PageQ = 1,
    page_size: PageSizeQ = 50,
    status_filter: Annotated[ScheduleStatus | None, Query(alias="status")] = None,
) -> PageResponse[ScheduleVersionResponse]:
    result = service.list_versions(Pagination(page, page_size), status=status_filter)
    return PageResponse.build(
        [ScheduleVersionResponse.from_record(v) for v in result.items],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
    )


@router.get("/versions/{version}", response_model=ScheduleVersionDetailResponse, summary="One version")
def get_version(version: int, _user: Reader, service: ScheduleServiceDep) -> ScheduleVersionDetailResponse:
    """Metrics, quality, unscheduled items, stored analytics and the writeback receipt (if published)."""
    return ScheduleVersionDetailResponse.from_record(service.get_version(version))


@router.get(
    "/versions/{version}/entries",
    response_model=PageResponse[ScheduleEntryResponse],
    summary="Entries of one version",
)
def list_version_entries(
    version: int,
    _user: Reader,
    service: ScheduleServiceDep,
    page: PageQ = 1,
    page_size: PageSizeQ = 100,
    machine_ids: MachineIdsQ = None,
    start: datetime | None = None,
    end: datetime | None = None,
    order_id: str | None = None,
    customer_id: str | None = None,
) -> PageResponse[ScheduleEntryResponse]:
    return _entries_page(
        service,
        version=version,
        machine_ids=machine_ids,
        start=start,
        end=end,
        order_id=order_id,
        customer_id=customer_id,
        page=page,
        page_size=page_size,
    )


# ---------------------------------------------------------------------- views


@router.get("/gantt", response_model=GanttResponse, summary="Gantt board: rows per machine")
def get_gantt(
    _user: Reader,
    service: ScheduleViewServiceDep,
    version: int | None = None,
    start: Annotated[datetime | None, Query(description="Default: now (or the horizon start)")] = None,
    end: Annotated[datetime | None, Query(description="Default: start + 7 days")] = None,
    machine_group: str | None = None,
    process_type: ProcessType | None = None,
    machine_ids: MachineIdsQ = None,
) -> GanttResponse:
    view = service.gantt(
        version_number=version,
        start=start,
        end=end,
        machine_group=machine_group,
        process_type=process_type,
        machine_ids=machine_ids,
    )
    return GanttResponse.from_domain(view)


@router.get("/compare", response_model=ScheduleComparisonResponse, summary="Compare two versions")
def compare_versions(
    a: Annotated[int, Query(ge=1, description="Before")],
    b: Annotated[int, Query(ge=1, description="After")],
    _user: Reader,
    service: ScheduleServiceDep,
) -> ScheduleComparisonResponse:
    """ "On-time delivery: 87% → 94%" pairs plus moved / added / removed entries (spec Phase 36)."""
    return ScheduleComparisonResponse.from_domain(service.compare(a, b))


@router.get("/runs/{run_id}", response_model=RunDetailsResponse, summary="Optimization run details")
def get_run(run_id: str, _user: Reader, service: ScheduleServiceDep) -> RunDetailsResponse:
    return RunDetailsResponse.from_domain(service.run_details(run_id))


# ------------------------------------------------------------------- actions


@router.post(
    "/generate",
    response_model=ScheduleGenerateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generate a new DRAFT schedule version",
)
def generate(
    user: Planner, service: ScheduleServiceDep, body: GenerateRequest | None = None
) -> ScheduleGenerateResponse:
    """Runs the planning pipeline on the live snapshot; the current plan's entries feed the frozen window."""
    return ScheduleGenerateResponse.from_summary(service.generate(user, note=body.note if body else None))


@router.post(
    "/simulate", response_model=SimulationResponse, summary="What-if simulation (nothing is persisted)"
)
def simulate(body: SimulateRequest, user: Planner, service: SimulationServiceDep) -> SimulationResponse:
    outcome = service.simulate(list(body.scenarios), user, body.note, top_n=body.top_n)
    return SimulationResponse.from_domain(outcome)


@router.post(
    "/approve", response_model=ScheduleVersionDetailResponse, summary="Approve a draft (DRAFT → APPROVED)"
)
def approve(
    body: VersionActionRequest, user: Manager, service: ScheduleServiceDep
) -> ScheduleVersionDetailResponse:
    return ScheduleVersionDetailResponse.from_record(service.approve(body.version, user, body.reason))


@router.post(
    "/publish", response_model=PublishResponse, summary="Publish an approved version (APPROVED → PUBLISHED)"
)
def publish(body: VersionActionRequest, user: Manager, service: ScheduleServiceDep) -> PublishResponse:
    """Goes through the writeback gateway (mode from settings); READ_ONLY records a skipped receipt."""
    return PublishResponse.from_domain(service.publish(body.version, user, body.reason))


@router.post(
    "/reject", response_model=ScheduleVersionDetailResponse, summary="Reject a draft or approved version"
)
def reject(
    body: VersionActionRequest, user: Manager, service: ScheduleServiceDep
) -> ScheduleVersionDetailResponse:
    return ScheduleVersionDetailResponse.from_record(service.reject(body.version, user, body.reason))


@router.post("/replan", response_model=ReplanResponse, summary="Evaluate continuous replanning now")
def replan(user: Planner, service: ReplanningServiceDep, body: ReplanRequest | None = None) -> ReplanResponse:
    """Detect changes since the active plan, generate a candidate and apply the stability rules."""
    request = body or ReplanRequest()
    return ReplanResponse.from_domain(service.evaluate(request.trigger, user, request.reason))


# ------------------------------------------------------------------ day view


@router.get("/{date}", response_model=DayScheduleResponse, summary="Day view (YYYY-MM-DD) grouped by machine")
def get_day(
    day_text: Annotated[str, Path(alias="date", description="Plant-local day, YYYY-MM-DD")],
    _user: Reader,
    service: ScheduleViewServiceDep,
    version: int | None = None,
) -> DayScheduleResponse:
    try:
        day = date.fromisoformat(day_text)
    except ValueError as exc:
        raise ValidationError(
            f"invalid date {day_text!r}; expected YYYY-MM-DD", details={"date": day_text}
        ) from exc
    return DayScheduleResponse.from_domain(service.day_view(day, version_number=version))


__all__ = ["router"]
