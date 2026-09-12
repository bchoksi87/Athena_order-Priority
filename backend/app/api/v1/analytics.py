"""``/analytics``: executive KPIs, capacity, bottlenecks, on-time delivery, schedule quality.

Read access for every role including the read-only executive (contract §9).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import AnalyticsServiceDep, require_read_access
from app.api.schemas.analytics import (
    BottleneckReportResponse,
    CapacityResponse,
    KpiReportResponse,
    OtdResponse,
)
from app.api.schemas.schedule import ScheduleQualityReportResponse
from app.core.security import CurrentUser
from app.domain.enums import Role
from app.engines.analytics.capacity import Period
from app.engines.analytics.common import Dimension
from app.services.analytics_service import DEFAULT_OTD_WINDOW_DAYS, MAX_HORIZON_DAYS, MAX_OTD_WINDOW_DAYS

router = APIRouter(prefix="/analytics", tags=["analytics"])

Reader = Annotated[CurrentUser, Depends(require_read_access(Role.EXECUTIVE))]


@router.get("/kpis", response_model=KpiReportResponse, summary="Executive KPIs (spec Phase 8)")
def kpis(_user: Reader, service: AnalyticsServiceDep) -> KpiReportResponse:
    """Stored with the active plan when fresh (same day, no newer sync), otherwise recomputed live."""
    return KpiReportResponse.from_view(service.kpis())


@router.get(
    "/capacity", response_model=CapacityResponse, summary="Required vs available hours (spec Phase 13)"
)
def capacity(
    _user: Reader,
    service: AnalyticsServiceDep,
    dimension: Dimension = "machine_group",
    period: Period = "week",
    horizon_days: Annotated[int | None, Query(ge=1, le=MAX_HORIZON_DAYS)] = None,
) -> CapacityResponse:
    return CapacityResponse.from_report(service.capacity(dimension, period, horizon_days))


@router.get("/bottlenecks", response_model=BottleneckReportResponse, summary="Bottlenecks (spec Phase 12)")
def bottlenecks(_user: Reader, service: AnalyticsServiceDep) -> BottleneckReportResponse:
    return BottleneckReportResponse.from_view(service.bottlenecks())


@router.get("/on-time-delivery", response_model=OtdResponse, summary="Historical and projected OTD")
def on_time_delivery(
    _user: Reader,
    service: AnalyticsServiceDep,
    window_days: Annotated[int, Query(ge=1, le=MAX_OTD_WINDOW_DAYS)] = DEFAULT_OTD_WINDOW_DAYS,
) -> OtdResponse:
    return OtdResponse.from_report(service.on_time_delivery(window_days))


@router.get(
    "/schedule-quality",
    response_model=ScheduleQualityReportResponse,
    summary="Quality of the active plan vs the newest draft (spec Phase 36)",
)
def schedule_quality(_user: Reader, service: AnalyticsServiceDep) -> ScheduleQualityReportResponse:
    return ScheduleQualityReportResponse.from_view(service.schedule_quality())


__all__ = ["router"]
