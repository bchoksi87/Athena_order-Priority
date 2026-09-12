"""Analytics schemas: KPIs, capacity table, bottlenecks and on-time delivery (spec Phases 8, 12, 13)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.db.records import ScheduleVersionInfo
from app.domain.enums import RiskLevel
from app.domain.results import Bottleneck, ExecutiveKpis
from app.engines.analytics import CapacityReport, OtdBucket, OtdReport
from app.services.analytics_service import BottleneckView, KpiView, capacity_report_to_dict


class KpisResponse(BaseModel):
    """Every executive KPI tile of the control tower (spec Phase 8)."""

    model_config = ConfigDict(extra="forbid")
    total_open_orders: int
    total_pending_quantity: float
    orders_due_today: int
    orders_due_tomorrow: int
    orders_due_this_week: int
    overdue_orders: int
    at_risk_orders: int
    on_time_delivery_pct: float | None = None
    expected_on_time_delivery_pct: float | None = None
    machine_utilization_pct: float | None = None
    capacity_utilization_pct: float | None = None
    revenue_at_risk: float
    margin_at_risk: float
    blocked_by_material: int
    blocked_by_tooling: int
    blocked_by_machine: int
    waiting_for_approval: int
    blocked_total: int
    scheduled_orders: int
    unscheduled_orders: int
    as_of: datetime

    @classmethod
    def from_domain(cls, k: ExecutiveKpis) -> KpisResponse:
        return cls(
            total_open_orders=k.total_open_orders,
            total_pending_quantity=k.total_pending_quantity,
            orders_due_today=k.orders_due_today,
            orders_due_tomorrow=k.orders_due_tomorrow,
            orders_due_this_week=k.orders_due_this_week,
            overdue_orders=k.overdue_orders,
            at_risk_orders=k.at_risk_orders,
            on_time_delivery_pct=k.on_time_delivery_pct,
            expected_on_time_delivery_pct=k.expected_on_time_delivery_pct,
            machine_utilization_pct=k.machine_utilization_pct,
            capacity_utilization_pct=k.capacity_utilization_pct,
            revenue_at_risk=k.revenue_at_risk,
            margin_at_risk=k.margin_at_risk,
            blocked_by_material=k.blocked_by_material,
            blocked_by_tooling=k.blocked_by_tooling,
            blocked_by_machine=k.blocked_by_machine,
            waiting_for_approval=k.waiting_for_approval,
            blocked_total=k.blocked_total,
            scheduled_orders=k.scheduled_orders,
            unscheduled_orders=k.unscheduled_orders,
            as_of=k.as_of,
        )


class KpiReportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kpis: KpisResponse
    source: str = Field(description="'stored' (analytics of the active plan) or 'live' (recomputed)")
    as_of: datetime
    version_number: int | None = Field(default=None, description="Active plan the numbers refer to")
    version_status: str | None = None

    @classmethod
    def from_view(cls, view: KpiView) -> KpiReportResponse:
        return cls(
            kpis=KpisResponse.from_domain(view.kpis),
            source=view.source,
            as_of=view.as_of,
            version_number=view.version.version_number if view.version else None,
            version_status=view.version.status.value if view.version else None,
        )


# ------------------------------------------------------------------ capacity


class CapacityRowResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    period_start: datetime
    period_end: datetime
    required_hours: float
    available_hours: float
    gap_hours: float
    utilization_pct: float


class CapacityTotalsResponse(BaseModel):
    """One line of the spec's "Process | Required Hrs | Available Hrs | Gap" table."""

    model_config = ConfigDict(extra="forbid")
    key: str
    required_hours: float
    available_hours: float
    gap_hours: float
    utilization_pct: float
    scheduled_hours: float
    estimated_hours: float
    shortfall_hours: float


class CapacityResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    dimension: str
    period: str
    horizon_start: datetime
    horizon_end: datetime
    rows: list[CapacityRowResponse]
    totals: list[CapacityTotalsResponse]
    total_required_hours: float
    total_available_hours: float
    gap_hours: float
    utilization_pct: float | None = None
    unallocated_hours: float
    unallocated_operations: int
    estimated_hours: float
    scheduled_hours: float
    notes: list[str] = []

    @classmethod
    def from_report(cls, report: CapacityReport) -> CapacityResponse:
        return cls.model_validate(capacity_report_to_dict(report))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CapacityResponse:
        return cls.model_validate(data)


# ---------------------------------------------------------------- bottlenecks


class BottleneckResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resource_type: str
    resource_id: str
    resource_name: str
    utilization_pct: float
    orders_waiting: int
    capacity_shortfall_hours: float
    revenue_at_risk: float
    margin_at_risk: float
    severity: RiskLevel
    recommendation: str

    @classmethod
    def from_domain(cls, b: Bottleneck) -> BottleneckResponse:
        return cls(
            resource_type=b.resource_type,
            resource_id=b.resource_id,
            resource_name=b.resource_name,
            utilization_pct=b.utilization_pct,
            orders_waiting=b.orders_waiting,
            capacity_shortfall_hours=b.capacity_shortfall_hours,
            revenue_at_risk=b.revenue_at_risk,
            margin_at_risk=b.margin_at_risk,
            severity=b.severity,
            recommendation=b.recommendation,
        )


class BottleneckReportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current: BottleneckResponse | None = Field(default=None, description="The most severe bottleneck")
    items: list[BottleneckResponse]
    source: str
    as_of: datetime
    version_number: int | None = None
    version_status: str | None = None

    @classmethod
    def from_view(cls, view: BottleneckView) -> BottleneckReportResponse:
        items = [BottleneckResponse.from_domain(b) for b in view.items]
        return cls(
            current=items[0] if items else None,
            items=items,
            source=view.source,
            as_of=view.as_of,
            version_number=view.version.version_number if view.version else None,
            version_status=view.version.status.value if view.version else None,
        )


# ------------------------------------------------------------------------ OTD


class OtdBucketResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    total: int
    on_time: int
    late: int
    pct: float | None = None

    @classmethod
    def from_domain(cls, b: OtdBucket) -> OtdBucketResponse:
        return cls(key=b.key, total=b.total, on_time=b.on_time, late=b.late, pct=b.pct)


class OtdTrendPointResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    day: date
    historical: OtdBucketResponse | None = None
    projected: OtdBucketResponse | None = None


class OtdResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    as_of: datetime
    window_days: int
    historical_pct: float | None = None
    projected_pct: float | None = None
    historical: OtdBucketResponse
    projected: OtdBucketResponse
    historical_by_tier: dict[str, OtdBucketResponse]
    projected_by_tier: dict[str, OtdBucketResponse]
    historical_by_process: dict[str, OtdBucketResponse]
    projected_by_process: dict[str, OtdBucketResponse]
    trend: list[OtdTrendPointResponse]
    notes: dict[str, int]
    summary: str

    @classmethod
    def from_report(cls, r: OtdReport) -> OtdResponse:
        bucket = OtdBucketResponse.from_domain
        return cls(
            as_of=r.as_of,
            window_days=r.window_days,
            historical_pct=r.historical_pct,
            projected_pct=r.projected_pct,
            historical=bucket(r.historical),
            projected=bucket(r.projected),
            historical_by_tier={k: bucket(v) for k, v in r.historical_by_tier.items()},
            projected_by_tier={k: bucket(v) for k, v in r.projected_by_tier.items()},
            historical_by_process={k: bucket(v) for k, v in r.historical_by_process.items()},
            projected_by_process={k: bucket(v) for k, v in r.projected_by_process.items()},
            trend=[
                OtdTrendPointResponse(
                    day=p.day,
                    historical=bucket(p.historical) if p.historical else None,
                    projected=bucket(p.projected) if p.projected else None,
                )
                for p in r.trend
            ],
            notes=dict(r.notes),
            summary=r.summary(),
        )


def version_ref(info: ScheduleVersionInfo | None) -> tuple[int | None, str | None]:
    return (info.version_number, info.status.value) if info is not None else (None, None)


__all__ = [
    "BottleneckReportResponse",
    "BottleneckResponse",
    "CapacityResponse",
    "CapacityRowResponse",
    "CapacityTotalsResponse",
    "KpiReportResponse",
    "KpisResponse",
    "OtdBucketResponse",
    "OtdResponse",
    "OtdTrendPointResponse",
    "version_ref",
]
