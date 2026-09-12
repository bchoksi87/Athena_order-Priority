"""ERP synchronisation schemas: runs, capabilities report and status."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.db.records import SyncRunRecord
from app.domain.enums import SyncMode
from app.integration.capabilities import CapabilityReport
from app.integration.connector import ConnectorHealth
from app.integration.sync_service import SyncRunSummary
from app.services.sync_admin_service import SyncStatus


class SyncRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: SyncMode = SyncMode.FULL
    prune_missing_orders: bool = Field(
        default=False, description="Full mode only: delete stored orders the ERP no longer serves"
    )


class SyncIssueResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    stage: str
    entity: str
    external_id: str
    field: str | None = None
    code: str
    message: str


class EntityDeltaResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    entity: str
    connector_count: int
    stored_count: int
    delta: int
    delta_pct: float
    status: str


class ReconciliationResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: str
    summary: str = ""
    deltas: list[EntityDeltaResponse] = []


class SyncRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    mode: SyncMode
    status: str
    connector: str
    started_at: datetime
    finished_at: datetime | None = None
    since: datetime | None = None
    duration_seconds: float | None = None
    records_fetched: dict[str, int] = {}
    records_upserted: dict[str, int] = {}
    issues_count: int = 0
    issue_counts: dict[str, int] = {}
    issues: list[SyncIssueResponse] = []
    issues_truncated: bool = False
    reconciliation: ReconciliationResponse | None = None
    stored_totals: dict[str, int] = {}
    pruned_orders: int = 0
    orders_closed_missing: int = 0
    watermark_source: str = "none"
    triggered_by: str | None = None
    error_message: str | None = None

    @classmethod
    def from_summary(
        cls, s: SyncRunSummary, *, max_issues: int = 50, triggered_by: str | None = None
    ) -> SyncRunResponse:
        data = s.to_dict(max_issues=max_issues)
        return cls._from_payload(data, triggered_by=triggered_by, fallback=s)

    @classmethod
    def from_record(cls, r: SyncRunRecord, *, max_issues: int = 50) -> SyncRunResponse:
        """The persisted row; ``details`` carries the summary written at the end of the run."""
        data: dict[str, Any] = dict(r.details) if r.details else {}
        data.setdefault("run_id", r.run_id)
        data.setdefault("mode", r.mode.value)
        data.setdefault("connector", r.connector)
        data.setdefault("started_at", r.started_at.isoformat())
        data["status"] = r.status
        data["finished_at"] = r.finished_at.isoformat() if r.finished_at else data.get("finished_at")
        data.setdefault("since", r.since.isoformat() if r.since else None)
        data.setdefault("records_fetched", dict(r.records_fetched))
        data.setdefault("records_upserted", dict(r.records_upserted))
        data.setdefault("issues_count", r.issues_count)
        data["error_message"] = r.error_message
        issues = list(data.get("issues", []))
        data["issues_truncated"] = bool(data.get("issues_truncated", False)) or len(issues) > max_issues
        data["issues"] = issues[:max_issues]
        return cls._from_payload(data, triggered_by=r.triggered_by, fallback=None)

    @classmethod
    def _from_payload(
        cls, data: dict[str, Any], *, triggered_by: str | None, fallback: SyncRunSummary | None
    ) -> SyncRunResponse:
        recon = data.get("reconciliation")
        return cls(
            run_id=str(data["run_id"]),
            mode=SyncMode(str(data["mode"])),
            status=str(data["status"]),
            connector=str(data.get("connector", "")),
            started_at=data["started_at"],
            finished_at=data.get("finished_at"),
            since=data.get("since"),
            duration_seconds=data.get("duration_seconds"),
            records_fetched=dict(data.get("records_fetched", {})),
            records_upserted=dict(data.get("records_upserted", {})),
            issues_count=int(data.get("issues_count", 0)),
            issue_counts=dict(data.get("issue_counts", {})),
            issues=[SyncIssueResponse.model_validate(i) for i in data.get("issues", [])],
            issues_truncated=bool(data.get("issues_truncated", False)),
            reconciliation=ReconciliationResponse.model_validate(recon) if isinstance(recon, dict) else None,
            stored_totals=dict(data.get("stored_totals", {})),
            pruned_orders=int(data.get("pruned_orders", 0)),
            orders_closed_missing=int(data.get("orders_closed_missing", 0)),
            watermark_source=str(data.get("watermark_source", "none")),
            triggered_by=triggered_by,
            error_message=data.get("error_message") or (fallback.error_message if fallback else None),
        )


class FieldAssessmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity: str
    field: str
    importance: str
    status: str = Field(description="Available / Missing")
    used_by: list[str]
    impact_if_missing: str
    recommendation: str


class CapabilityReportResponse(BaseModel):
    """Required / Available / Missing report of the configured connector (docs/ERP_INTEGRATION.md)."""

    model_config = ConfigDict(extra="forbid")
    connector_name: str
    supports_incremental: bool
    supports_webhooks: bool
    coverage_pct: float
    can_schedule: bool
    missing_required: list[str]
    assessments: list[FieldAssessmentResponse]

    @classmethod
    def from_report(cls, r: CapabilityReport) -> CapabilityReportResponse:
        return cls.model_validate(r.to_dict())


class ConnectorHealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connector_name: str
    healthy: bool
    checked_at: datetime
    latency_ms: float | None = None
    message: str = ""
    details: dict[str, Any] = {}

    @classmethod
    def from_domain(cls, h: ConnectorHealth) -> ConnectorHealthResponse:
        return cls(
            connector_name=h.connector_name,
            healthy=h.healthy,
            checked_at=h.checked_at,
            latency_ms=h.latency_ms,
            message=h.message,
            details=dict(h.details),
        )


class SyncStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connector: str
    health: ConnectorHealthResponse | None = None
    last_run: SyncRunResponse | None = None
    last_completed: SyncRunResponse | None = None
    watermark: datetime | None = Field(default=None, description="'since' the next incremental run will use")
    runs_total: int
    checked_at: datetime

    @classmethod
    def from_domain(cls, s: SyncStatus) -> SyncStatusResponse:
        return cls(
            connector=s.connector,
            health=ConnectorHealthResponse.from_domain(s.health) if s.health else None,
            last_run=SyncRunResponse.from_record(s.last_run, max_issues=0) if s.last_run else None,
            last_completed=(
                SyncRunResponse.from_record(s.last_completed, max_issues=0) if s.last_completed else None
            ),
            watermark=s.watermark,
            runs_total=s.runs_total,
            checked_at=s.checked_at,
        )


__all__ = [
    "CapabilityReportResponse",
    "ConnectorHealthResponse",
    "EntityDeltaResponse",
    "FieldAssessmentResponse",
    "ReconciliationResponse",
    "SyncIssueResponse",
    "SyncRunRequest",
    "SyncRunResponse",
    "SyncStatusResponse",
]
