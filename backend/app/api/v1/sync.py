"""``/sync``: ERP synchronisation runs, capabilities and status (admin only)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import SyncAdminServiceDep, require_min_role
from app.api.schemas.common import PageResponse
from app.api.schemas.sync import (
    CapabilityReportResponse,
    SyncRunRequest,
    SyncRunResponse,
    SyncStatusResponse,
)
from app.core.security import CurrentUser
from app.domain.enums import Role
from app.services.base import Pagination

router = APIRouter(prefix="/sync", tags=["sync"])

Admin = Annotated[CurrentUser, Depends(require_min_role(Role.ADMIN))]


@router.post("/run", response_model=SyncRunResponse, summary="Run an ERP synchronisation now")
def run_sync(
    user: Admin, service: SyncAdminServiceDep, body: SyncRunRequest | None = None
) -> SyncRunResponse:
    """fetch → normalise → validate → upsert → reconcile → ``sync_runs`` row (read-only towards the ERP)."""
    request = body or SyncRunRequest()
    summary = service.run(request.mode, user, prune_missing_orders=request.prune_missing_orders)
    return SyncRunResponse.from_summary(summary, triggered_by=user.user_id)


@router.get("/runs", response_model=PageResponse[SyncRunResponse], summary="Sync run history")
def list_runs(
    _user: Admin,
    service: SyncAdminServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=500)] = 50,
) -> PageResponse[SyncRunResponse]:
    result = service.runs(Pagination(page, page_size))
    return PageResponse.build(
        [SyncRunResponse.from_record(r, max_issues=0) for r in result.items],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
    )


@router.get(
    "/capabilities", response_model=CapabilityReportResponse, summary="Required / Available / Missing"
)
def capabilities(_user: Admin, service: SyncAdminServiceDep) -> CapabilityReportResponse:
    return CapabilityReportResponse.from_report(service.capabilities())


@router.get("/status", response_model=SyncStatusResponse, summary="Last run, watermark and connector health")
def sync_status(_user: Admin, service: SyncAdminServiceDep) -> SyncStatusResponse:
    return SyncStatusResponse.from_domain(service.status())


@router.get("/runs/{run_id}", response_model=SyncRunResponse, summary="One sync run")
def get_run(run_id: str, _user: Admin, service: SyncAdminServiceDep) -> SyncRunResponse:
    return SyncRunResponse.from_record(service.get_run(run_id))


__all__ = ["router"]
