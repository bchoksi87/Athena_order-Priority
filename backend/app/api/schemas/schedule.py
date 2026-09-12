"""Schedule schemas: versions, generation, approval workflow, runs and comparison (spec Phases 36, 37)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.api.schemas.analytics import BottleneckResponse, CapacityResponse, KpisResponse
from app.api.schemas.common import PageResponse, ReasonBody
from app.api.schemas.orders import ScheduleEntryResponse
from app.db.records import OptimizationRunRecord, ScheduleVersionInfo, SnapshotInfo
from app.domain.enums import ScheduleStatus
from app.domain.results import ScheduleMetrics, ScheduleQuality
from app.integration.writeback import WritebackReceipt
from app.services.analytics_service import ScheduleComparison, ScheduleQualityView
from app.services.schedule_service import CurrentPlan, GenerationSummary, PublishOutcome, RunDetails
from app.services.writeback_service import receipt_of

# ------------------------------------------------------------------ versions


class ScheduleMetricsResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    scheduled_orders: int = 0
    unscheduled_orders: int = 0
    scheduled_operations: int = 0
    on_time_orders: int = 0
    late_orders: int = 0
    orders_at_risk: int = 0
    on_time_pct: float = 0.0
    avg_lateness_hours: float = 0.0
    max_lateness_hours: float = 0.0
    total_tardiness_hours: float = 0.0
    total_run_hours: float = 0.0
    total_setup_hours: float = 0.0
    setup_count: int = 0
    makespan_hours: float = 0.0
    overall_utilization_pct: float = 0.0
    machine_utilization_pct: dict[str, float] = {}
    revenue_scheduled: float = 0.0
    revenue_at_risk: float = 0.0
    margin_at_risk: float = 0.0
    wip_orders_avg: float = 0.0

    @classmethod
    def from_domain(cls, m: ScheduleMetrics) -> ScheduleMetricsResponse:
        from app.db.snapshot_codec import to_jsonable

        return cls.model_validate(to_jsonable(m))


class ScheduleQualityResponse(BaseModel):
    """Spec Phase 36: "Schedule Quality: 89/100" with its components."""

    model_config = ConfigDict(extra="ignore")
    score: float
    components: dict[str, float]
    weights: dict[str, float]
    summary: str

    @classmethod
    def from_domain(cls, q: ScheduleQuality) -> ScheduleQualityResponse:
        return cls(score=q.score, components=dict(q.components), weights=dict(q.weights), summary=q.summary)


class UnscheduledItemResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    order_id: str
    operation_id: str | None = None
    reason_code: str
    reason: str
    readiness: str | None = None


class WritebackReceiptResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    receipt_id: str
    mode: str
    status: str
    attempted_at: datetime
    entries_published: int = 0
    approved_by: str | None = None
    message: str = ""
    run_id: str | None = None
    details: dict[str, Any] = {}

    @classmethod
    def from_domain(cls, r: WritebackReceipt) -> WritebackReceiptResponse:
        return cls.model_validate(r.to_dict())


class ScheduleVersionResponse(BaseModel):
    """Spec Phase 37: "Schedule v124 — Generated, Algorithm, Configuration, Status"."""

    model_config = ConfigDict(extra="forbid")
    schedule_version_id: str
    version_number: int
    status: ScheduleStatus
    label: str | None = None
    algorithm: str
    algorithm_version: str
    profile_id: str
    profile_version: int
    config_version: int
    generated_by: str | None = None
    generated_at: datetime
    horizon_start: datetime
    horizon_end: datetime
    run_id: str | None = None
    input_snapshot_id: str | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None
    published_by: str | None = None
    published_at: datetime | None = None
    superseded_at: datetime | None = None
    entry_count: int
    quality_score: float | None = None
    quality_summary: str | None = None
    metrics: ScheduleMetricsResponse
    trigger: str | None = Field(default=None, description="'manual', 'replan:<trigger>' ...")
    previous_version: int | None = None
    notes: str | None = None

    @classmethod
    def from_record(cls, v: ScheduleVersionInfo) -> ScheduleVersionResponse:
        quality = v.quality or {}
        trigger = v.details.get("trigger")
        previous = v.details.get("previous_version")
        return cls(
            schedule_version_id=v.schedule_version_id,
            version_number=v.version_number,
            status=v.status,
            label=v.label,
            algorithm=v.algorithm,
            algorithm_version=v.algorithm_version,
            profile_id=v.profile_id,
            profile_version=v.profile_version,
            config_version=v.config_version,
            generated_by=v.generated_by,
            generated_at=v.generated_at,
            horizon_start=v.horizon_start,
            horizon_end=v.horizon_end,
            run_id=v.run_id,
            input_snapshot_id=v.input_snapshot_id,
            approved_by=v.approved_by,
            approved_at=v.approved_at,
            published_by=v.published_by,
            published_at=v.published_at,
            superseded_at=v.superseded_at,
            entry_count=v.entry_count,
            quality_score=quality.get("score"),
            quality_summary=quality.get("summary"),
            metrics=ScheduleMetricsResponse.model_validate(v.metrics or {}),
            trigger=str(trigger) if trigger is not None else None,
            previous_version=int(previous) if isinstance(previous, int) else None,
            notes=v.notes,
        )


class AlertCountsResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    total: int = 0
    by_severity: dict[str, int] = {}


class VersionAnalyticsResponse(BaseModel):
    """The analytics computed together with the version (stored ``analytics`` JSON)."""

    model_config = ConfigDict(extra="ignore")
    as_of: datetime
    run_id: str | None = None
    kpis: KpisResponse
    capacity: CapacityResponse
    bottlenecks: list[BottleneckResponse]
    data_quality: dict[str, Any] = {}
    alerts: AlertCountsResponse = AlertCountsResponse()
    timings: dict[str, float] = {}


class ScheduleVersionDetailResponse(ScheduleVersionResponse):
    quality: ScheduleQualityResponse | None = None
    unscheduled: list[UnscheduledItemResponse] = []
    warnings: list[str] = []
    analytics: VersionAnalyticsResponse | None = None
    writeback_receipt: WritebackReceiptResponse | None = None
    details: dict[str, Any] = {}

    @classmethod
    def from_record(cls, v: ScheduleVersionInfo) -> ScheduleVersionDetailResponse:
        base = ScheduleVersionResponse.from_record(v).model_dump()
        receipt = receipt_of(v)
        return cls(
            **base,
            quality=ScheduleQualityResponse.model_validate(v.quality) if v.quality else None,
            unscheduled=[UnscheduledItemResponse.model_validate(u) for u in v.unscheduled],
            warnings=list(v.warnings),
            analytics=VersionAnalyticsResponse.model_validate(v.analytics) if v.analytics else None,
            writeback_receipt=WritebackReceiptResponse.model_validate(receipt) if receipt else None,
            details={k: val for k, val in v.details.items() if k != "writeback_receipt"},
        )


class SchedulePlanResponse(BaseModel):
    """``GET /schedule``: the active plan (which status it has) with a page of its entries."""

    model_config = ConfigDict(extra="forbid")
    status: str = Field(description="published / approved / draft / none")
    version: ScheduleVersionResponse | None = None
    entries: PageResponse[ScheduleEntryResponse]

    @classmethod
    def build(cls, plan: CurrentPlan, entries: PageResponse[ScheduleEntryResponse]) -> SchedulePlanResponse:
        return cls(
            status=plan.status,
            version=ScheduleVersionResponse.from_record(plan.version) if plan.version else None,
            entries=entries,
        )


# ---------------------------------------------------------------- generation


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = Field(default=None, max_length=2000, description="Stored with the version")


class SnapshotInfoResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_id: str
    as_of: datetime
    source: str
    size_bytes: int
    sha256: str
    summary: dict[str, int]

    @classmethod
    def from_record(cls, s: SnapshotInfo) -> SnapshotInfoResponse:
        return cls(
            snapshot_id=s.snapshot_id,
            as_of=s.as_of,
            source=s.source,
            size_bytes=s.size_bytes,
            sha256=s.sha256,
            summary=dict(s.summary),
        )


class OptimizationRunResponse(BaseModel):
    """Observability record of one engine run (spec OBSERVABILITY)."""

    model_config = ConfigDict(extra="forbid")
    run_id: str
    kind: str
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    duration_seconds: float | None = None
    orders_considered: int
    orders_scheduled: int
    orders_blocked: int
    objective_score: float | None = None
    quality_score: float | None = None
    algorithm: str
    algorithm_version: str
    profile_id: str | None = None
    profile_version: int | None = None
    config_version: int | None = None
    input_snapshot_id: str | None = None
    triggered_by: str | None = None
    trigger_reason: str | None = None
    error_message: str | None = None
    metrics: dict[str, Any] = {}
    warnings: list[str] = []

    @classmethod
    def from_record(cls, r: OptimizationRunRecord) -> OptimizationRunResponse:
        return cls(
            run_id=r.run_id,
            kind=r.kind,
            status=r.status,
            started_at=r.started_at,
            finished_at=r.finished_at,
            duration_seconds=r.duration_seconds,
            orders_considered=r.orders_considered,
            orders_scheduled=r.orders_scheduled,
            orders_blocked=r.orders_blocked,
            objective_score=r.objective_score,
            quality_score=r.quality_score,
            algorithm=r.algorithm,
            algorithm_version=r.algorithm_version,
            profile_id=r.profile_id,
            profile_version=r.profile_version,
            config_version=r.config_version,
            input_snapshot_id=r.input_snapshot_id,
            triggered_by=r.triggered_by,
            trigger_reason=r.trigger_reason,
            error_message=r.error_message,
            metrics=dict(r.metrics),
            warnings=list(r.warnings),
        )


class ScheduleGenerateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: ScheduleVersionDetailResponse
    run: OptimizationRunResponse
    snapshot: SnapshotInfoResponse
    previous_version: int | None = None
    priority_results: int
    entries: int
    unscheduled: int
    data_quality_issues: int
    alerts: int
    timings: dict[str, float]

    @classmethod
    def from_summary(cls, g: GenerationSummary) -> ScheduleGenerateResponse:
        return cls(
            version=ScheduleVersionDetailResponse.from_record(g.version),
            run=OptimizationRunResponse.from_record(g.run),
            snapshot=SnapshotInfoResponse.from_record(g.snapshot),
            previous_version=g.previous_version,
            priority_results=g.priority_results,
            entries=g.entries,
            unscheduled=g.unscheduled,
            data_quality_issues=g.data_quality_issues,
            alerts=g.alerts,
            timings=dict(g.timings),
        )


class RunDetailsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: OptimizationRunResponse
    version: ScheduleVersionResponse | None = None
    snapshot: SnapshotInfoResponse | None = None
    priority_results: int
    data_quality_issues: int

    @classmethod
    def from_domain(cls, d: RunDetails) -> RunDetailsResponse:
        return cls(
            run=OptimizationRunResponse.from_record(d.run),
            version=ScheduleVersionResponse.from_record(d.version) if d.version else None,
            snapshot=SnapshotInfoResponse.from_record(d.snapshot) if d.snapshot else None,
            priority_results=d.priority_results,
            data_quality_issues=d.data_quality_issues,
        )


# ------------------------------------------------------------------ workflow


class VersionActionRequest(ReasonBody):
    version: int = Field(ge=1, description="Schedule version number")


class PublishResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: ScheduleVersionDetailResponse
    receipt: WritebackReceiptResponse
    superseded: int

    @classmethod
    def from_domain(cls, p: PublishOutcome) -> PublishResponse:
        return cls(
            version=ScheduleVersionDetailResponse.from_record(p.version),
            receipt=WritebackReceiptResponse.from_domain(p.receipt),
            superseded=p.superseded,
        )


# ---------------------------------------------------------------- comparison


class MetricPairResponse(BaseModel):
    """ "On-time delivery: 87% → 94%"."""

    model_config = ConfigDict(extra="forbid")
    label: str
    before: float | None = None
    after: float | None = None
    delta: float | None = None


class EntryChangeResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    operation_id: str
    order_id: str
    kind: str
    previous_machine_id: str | None = None
    proposed_machine_id: str | None = None
    previous_start: datetime | None = None
    proposed_start: datetime | None = None
    shift_minutes: float | None = None
    reason: str


class ScheduleChangesResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    moved_entries: int
    added_entries: int
    removed_entries: int
    unchanged_entries: int
    changed_orders: list[str]
    frozen_violations: int
    entries: list[EntryChangeResponse] = []


class ScheduleComparisonResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    a: ScheduleVersionResponse
    b: ScheduleVersionResponse
    metrics: dict[str, MetricPairResponse]
    quality: MetricPairResponse
    changes: ScheduleChangesResponse
    moved_orders: int
    summary: str

    @classmethod
    def from_domain(cls, c: ScheduleComparison) -> ScheduleComparisonResponse:
        return cls(
            a=ScheduleVersionResponse.from_record(c.a),
            b=ScheduleVersionResponse.from_record(c.b),
            metrics={k: MetricPairResponse.model_validate(v) for k, v in c.metrics.items()},
            quality=MetricPairResponse.model_validate(c.quality),
            changes=ScheduleChangesResponse.model_validate(c.changes),
            moved_orders=c.moved_orders,
            summary=c.summary,
        )


class ScheduleQualityReportResponse(BaseModel):
    """``GET /analytics/schedule-quality``: active plan quality, compared with the newest draft."""

    model_config = ConfigDict(extra="forbid")
    version: ScheduleVersionResponse | None = None
    quality: ScheduleQualityResponse | None = None
    metrics: ScheduleMetricsResponse | None = None
    newest_draft: ScheduleVersionResponse | None = None
    comparison: ScheduleComparisonResponse | None = None

    @classmethod
    def from_view(cls, v: ScheduleQualityView) -> ScheduleQualityReportResponse:
        return cls(
            version=ScheduleVersionResponse.from_record(v.version) if v.version else None,
            quality=ScheduleQualityResponse.from_domain(v.quality) if v.quality else None,
            metrics=ScheduleMetricsResponse.from_domain(v.metrics) if v.metrics else None,
            newest_draft=ScheduleVersionResponse.from_record(v.draft) if v.draft else None,
            comparison=ScheduleComparisonResponse.from_domain(v.comparison) if v.comparison else None,
        )


__all__ = [
    "AlertCountsResponse",
    "EntryChangeResponse",
    "GenerateRequest",
    "MetricPairResponse",
    "OptimizationRunResponse",
    "PublishResponse",
    "RunDetailsResponse",
    "ScheduleChangesResponse",
    "ScheduleComparisonResponse",
    "ScheduleGenerateResponse",
    "ScheduleMetricsResponse",
    "SchedulePlanResponse",
    "ScheduleQualityReportResponse",
    "ScheduleQualityResponse",
    "ScheduleVersionDetailResponse",
    "ScheduleVersionResponse",
    "SnapshotInfoResponse",
    "UnscheduledItemResponse",
    "VersionActionRequest",
    "VersionAnalyticsResponse",
    "WritebackReceiptResponse",
]
