"""Data quality dashboard schemas (spec Phase 21)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.db.records import DataQualityIssueRecord
from app.engines.data_quality.engine import DataQualityDashboard
from app.services.data_quality_service import DataQualityOverview


class DataQualityIssueResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issue_id: str
    run_id: str
    detected_at: datetime
    code: str
    severity: str
    entity_type: str
    entity_id: str
    message: str
    field_name: str | None = None
    recommendation: str | None = None
    details: dict[str, Any] = {}

    @classmethod
    def from_record(cls, r: DataQualityIssueRecord) -> DataQualityIssueResponse:
        return cls(
            issue_id=r.issue_id,
            run_id=r.run_id,
            detected_at=r.detected_at,
            code=r.code,
            severity=r.severity,
            entity_type=r.entity_type,
            entity_id=r.entity_id,
            message=r.message,
            field_name=r.field_name,
            recommendation=r.recommendation,
            details=dict(r.details),
        )


class DashboardReasonResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    label: str
    orders: int


class DataQualityDashboardResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    as_of: datetime
    open_orders: int
    unschedulable_orders: int
    headline: str = Field(
        description='"127 orders cannot be scheduled because: 43 missing machine information, ..."'
    )
    reasons: list[DashboardReasonResponse] = Field(
        description="Primary reason per order; sums to unschedulable_orders"
    )
    orders_by_code: dict[str, int]
    warnings_by_code: dict[str, int]

    @classmethod
    def from_domain(cls, d: DataQualityDashboard) -> DataQualityDashboardResponse:
        return cls(
            as_of=d.as_of,
            open_orders=d.open_orders,
            unschedulable_orders=d.unschedulable_orders,
            headline=d.headline,
            reasons=[
                DashboardReasonResponse(code=r.code.value, label=r.label, orders=r.orders) for r in d.reasons
            ],
            orders_by_code=dict(d.orders_by_code),
            warnings_by_code=dict(d.warnings_by_code),
        )


class DataQualitySummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str | None = None
    detected_at: datetime | None = None
    total_issues: int
    by_severity: dict[str, int]
    by_code: dict[str, int]
    by_entity_type: dict[str, int]
    blocked_entities: int
    dashboard: DataQualityDashboardResponse
    engine_summary: dict[str, Any] = Field(
        default_factory=dict, description="Present after a run in this request"
    )

    @classmethod
    def from_domain(cls, o: DataQualityOverview) -> DataQualitySummaryResponse:
        return cls(
            run_id=o.run_id,
            detected_at=o.detected_at,
            total_issues=o.total,
            by_severity=dict(o.by_severity),
            by_code=dict(o.by_code),
            by_entity_type=dict(o.by_entity_type),
            blocked_entities=o.blocked_entities,
            dashboard=DataQualityDashboardResponse.from_domain(o.dashboard),
            engine_summary=dict(o.engine_summary),
        )


__all__ = [
    "DashboardReasonResponse",
    "DataQualityDashboardResponse",
    "DataQualityIssueResponse",
    "DataQualitySummaryResponse",
]
