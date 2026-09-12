"""``/alerts``: inbox, acknowledgement and severity summary (spec Phase 20)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import AlertServiceDep, require_min_role, require_read_access
from app.api.schemas.alerts import AcknowledgeRequest, AlertResponse, AlertSummaryResponse
from app.api.schemas.common import PageResponse
from app.core.security import CurrentUser
from app.domain.enums import AlertSeverity, AlertType, Role
from app.services.alert_service import AlertFilters
from app.services.base import Pagination

router = APIRouter(prefix="/alerts", tags=["alerts"])

Reader = Annotated[CurrentUser, Depends(require_read_access(Role.SUPERVISOR))]
Supervisor = Annotated[CurrentUser, Depends(require_min_role(Role.SUPERVISOR))]


@router.get("", response_model=PageResponse[AlertResponse], summary="Active alerts")
def list_alerts(
    _user: Reader,
    service: AlertServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=500)] = 50,
    severity: AlertSeverity | None = None,
    alert_type: AlertType | None = None,
    order_id: str | None = None,
    machine_id: str | None = None,
    acknowledged: bool | None = None,
) -> PageResponse[AlertResponse]:
    filters = AlertFilters(
        severity=severity,
        alert_type=alert_type,
        order_id=order_id,
        machine_id=machine_id,
        acknowledged=acknowledged,
    )
    result = service.list(filters, Pagination(page, page_size))
    return PageResponse.build(
        [AlertResponse.from_record(a) for a in result.items],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
    )


@router.get("/summary", response_model=AlertSummaryResponse, summary="Active alert counts by severity")
def alert_summary(_user: Reader, service: AlertServiceDep) -> AlertSummaryResponse:
    return AlertSummaryResponse.from_domain(service.summary())


@router.get("/{alert_id}", response_model=AlertResponse, summary="One alert")
def get_alert(alert_id: str, _user: Reader, service: AlertServiceDep) -> AlertResponse:
    return AlertResponse.from_record(service.get(alert_id))


@router.post("/{alert_id}/acknowledge", response_model=AlertResponse, summary="Acknowledge an alert")
def acknowledge_alert(
    alert_id: str, user: Supervisor, service: AlertServiceDep, body: AcknowledgeRequest | None = None
) -> AlertResponse:
    return AlertResponse.from_record(service.acknowledge(alert_id, user, body.note if body else None))


__all__ = ["router"]
