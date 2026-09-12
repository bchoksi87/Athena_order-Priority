"""Alert inbox schemas (spec Phase 20)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.db.records import AlertRecord
from app.domain.enums import AlertSeverity, AlertType
from app.services.alert_service import AlertSummary


class AlertResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    alert_id: str
    alert_type: AlertType
    severity: AlertSeverity
    title: str
    reason: str
    recommended_action: str
    raised_at: datetime
    last_seen_at: datetime
    occurrences: int
    active: bool
    order_id: str | None = None
    machine_id: str | None = None
    entity_ref: str | None = None
    details: dict[str, Any] = {}
    acknowledged: bool
    acknowledged_by: str | None = None
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None

    @classmethod
    def from_record(cls, a: AlertRecord) -> AlertResponse:
        return cls(
            alert_id=a.alert_id,
            alert_type=a.alert_type,
            severity=a.severity,
            title=a.title,
            reason=a.reason,
            recommended_action=a.recommended_action,
            raised_at=a.raised_at,
            last_seen_at=a.last_seen_at,
            occurrences=a.occurrences,
            active=a.active,
            order_id=a.order_id,
            machine_id=a.machine_id,
            entity_ref=a.entity_ref,
            details=dict(a.details),
            acknowledged=a.acknowledged,
            acknowledged_by=a.acknowledged_by,
            acknowledged_at=a.acknowledged_at,
            resolved_at=a.resolved_at,
        )


class AcknowledgeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = Field(default=None, max_length=2000)


class AlertSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    total_active: int
    unacknowledged: int
    by_severity: dict[str, int]

    @classmethod
    def from_domain(cls, s: AlertSummary) -> AlertSummaryResponse:
        return cls(
            total_active=s.total_active, unacknowledged=s.unacknowledged, by_severity=dict(s.by_severity)
        )


__all__ = ["AcknowledgeRequest", "AlertResponse", "AlertSummaryResponse"]
