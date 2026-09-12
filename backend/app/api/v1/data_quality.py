"""``/data-quality``: dashboard, issues and manual runs (spec Phase 21)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import DataQualityServiceDep, require_min_role, require_read_access
from app.api.schemas.common import PageResponse
from app.api.schemas.data_quality import DataQualityIssueResponse, DataQualitySummaryResponse
from app.core.security import CurrentUser
from app.domain.enums import DataQualityCode, DataQualitySeverity, Role
from app.services.base import Pagination
from app.services.data_quality_service import DataQualityIssueFilters

router = APIRouter(prefix="/data-quality", tags=["data-quality"])

Reader = Annotated[CurrentUser, Depends(require_read_access(Role.PLANNER))]
Planner = Annotated[CurrentUser, Depends(require_min_role(Role.PLANNER))]


@router.get("", response_model=DataQualitySummaryResponse, summary="Data quality dashboard")
def get_summary(_user: Reader, service: DataQualityServiceDep) -> DataQualitySummaryResponse:
    return DataQualitySummaryResponse.from_domain(service.summary())


@router.get(
    "/issues", response_model=PageResponse[DataQualityIssueResponse], summary="Issues of the latest run"
)
def list_issues(
    _user: Reader,
    service: DataQualityServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=500)] = 50,
    severity: DataQualitySeverity | None = None,
    code: DataQualityCode | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
) -> PageResponse[DataQualityIssueResponse]:
    filters = DataQualityIssueFilters(
        severity=severity, code=code, entity_type=entity_type, entity_id=entity_id
    )
    result = service.list_issues(filters, Pagination(page, page_size))
    return PageResponse.build(
        [DataQualityIssueResponse.from_record(i) for i in result.items],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
    )


@router.post("/run", response_model=DataQualitySummaryResponse, summary="Run the data quality engine now")
def run(user: Planner, service: DataQualityServiceDep) -> DataQualitySummaryResponse:
    return DataQualitySummaryResponse.from_domain(service.run(user))


__all__ = ["router"]
