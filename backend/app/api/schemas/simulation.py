"""What-if simulation schemas (spec Phase 7)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.api.schemas.schedule import MetricPairResponse, ScheduleMetricsResponse, ScheduleQualityResponse
from app.domain.results import OrderDelta, ScheduleDiff, ScheduleResult
from app.engines.simulation.scenarios import Scenario
from app.services.simulation_service import (
    DEFAULT_TOP_N,
    MAX_SCENARIOS,
    MAX_TOP_N,
    AffectedOrder,
    ScenarioType,
    ScenarioTypes,
    SimulationOutcome,
)

_RECORD_KEYS = ("kind", "label", "description", "notes", "affected_order_ids", "affected_machine_ids")


class SimulateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenarios: list[Scenario] = Field(min_length=1, max_length=MAX_SCENARIOS)
    note: str | None = Field(default=None, max_length=2000)
    top_n: int = Field(default=DEFAULT_TOP_N, ge=1, le=MAX_TOP_N, description="Affected orders to return")


class ScenarioRecordResponse(BaseModel):
    """A scenario as applied: its parameters plus what it touched."""

    model_config = ConfigDict(extra="forbid")
    kind: str
    label: str | None = None
    description: str
    notes: list[str] = []
    affected_order_ids: list[str] = []
    affected_machine_ids: list[str] = []
    parameters: dict[str, Any] = {}

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> ScenarioRecordResponse:
        return cls(
            kind=str(record.get("kind", "")),
            label=record.get("label"),
            description=str(record.get("description", "")),
            notes=list(record.get("notes", [])),
            affected_order_ids=list(record.get("affected_order_ids", [])),
            affected_machine_ids=list(record.get("affected_machine_ids", [])),
            parameters={k: v for k, v in record.items() if k not in _RECORD_KEYS},
        )


class OrderDeltaResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: str
    baseline_completion: datetime | None = None
    scenario_completion: datetime | None = None
    delta_hours: float | None = None
    baseline_late: bool
    scenario_late: bool
    baseline_machine_id: str | None = None
    scenario_machine_id: str | None = None
    baseline_score: float | None = None
    scenario_score: float | None = None

    @classmethod
    def from_domain(cls, d: OrderDelta) -> OrderDeltaResponse:
        return cls(
            order_id=d.order_id,
            baseline_completion=d.baseline_completion,
            scenario_completion=d.scenario_completion,
            delta_hours=d.delta_hours,
            baseline_late=d.baseline_late,
            scenario_late=d.scenario_late,
            baseline_machine_id=d.baseline_machine_id,
            scenario_machine_id=d.scenario_machine_id,
            baseline_score=d.baseline_score,
            scenario_score=d.scenario_score,
        )


class AffectedOrderResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: str
    customer_id: str | None = None
    customer_name: str | None = None
    part_id: str | None = None
    part_name: str | None = None
    due_date: datetime | None = None
    order_value: float | None = None
    delta: OrderDeltaResponse

    @classmethod
    def from_domain(cls, a: AffectedOrder) -> AffectedOrderResponse:
        return cls(
            order_id=a.delta.order_id,
            customer_id=a.customer_id,
            customer_name=a.customer_name,
            part_id=a.part_id,
            part_name=a.part_name,
            due_date=a.due_date,
            order_value=a.order_value,
            delta=OrderDeltaResponse.from_domain(a.delta),
        )


class ScheduleDiffResponse(BaseModel):
    """Delivery impact, utilisation, late orders, overtime, bottlenecks, money at risk (spec Phase 7)."""

    model_config = ConfigDict(extra="forbid")
    orders_affected: int
    orders_moved_machine: int
    orders_resequenced: int
    orders_newly_late: int
    orders_newly_on_time: int
    late_orders_before: int
    late_orders_after: int
    on_time_pct_before: float
    on_time_pct_after: float
    avg_lateness_before: float
    avg_lateness_after: float
    utilization_before: float
    utilization_after: float
    setup_hours_before: float
    setup_hours_after: float
    revenue_at_risk_before: float
    revenue_at_risk_after: float
    margin_at_risk_before: float
    margin_at_risk_after: float
    additional_overtime_hours: float
    bottlenecks_before: list[str]
    bottlenecks_after: list[str]
    summary: str

    @classmethod
    def from_domain(cls, d: ScheduleDiff) -> ScheduleDiffResponse:
        return cls(
            orders_affected=d.orders_affected,
            orders_moved_machine=d.orders_moved_machine,
            orders_resequenced=d.orders_resequenced,
            orders_newly_late=d.orders_newly_late,
            orders_newly_on_time=d.orders_newly_on_time,
            late_orders_before=d.late_orders_before,
            late_orders_after=d.late_orders_after,
            on_time_pct_before=d.on_time_pct_before,
            on_time_pct_after=d.on_time_pct_after,
            avg_lateness_before=d.avg_lateness_before,
            avg_lateness_after=d.avg_lateness_after,
            utilization_before=d.utilization_before,
            utilization_after=d.utilization_after,
            setup_hours_before=d.setup_hours_before,
            setup_hours_after=d.setup_hours_after,
            revenue_at_risk_before=d.revenue_at_risk_before,
            revenue_at_risk_after=d.revenue_at_risk_after,
            margin_at_risk_before=d.margin_at_risk_before,
            margin_at_risk_after=d.margin_at_risk_after,
            additional_overtime_hours=d.additional_overtime_hours,
            bottlenecks_before=list(d.bottlenecks_before),
            bottlenecks_after=list(d.bottlenecks_after),
            summary=d.summary,
        )


class PlanSummaryResponse(BaseModel):
    """Baseline or scenario plan in brief (never persisted)."""

    model_config = ConfigDict(extra="forbid")
    algorithm: str
    algorithm_version: str
    horizon_start: datetime
    horizon_end: datetime
    entries: int
    unscheduled: int
    metrics: ScheduleMetricsResponse
    quality: ScheduleQualityResponse | None = None
    warnings: list[str] = []

    @classmethod
    def from_domain(cls, r: ScheduleResult) -> PlanSummaryResponse:
        return cls(
            algorithm=r.algorithm,
            algorithm_version=r.algorithm_version,
            horizon_start=r.horizon_start,
            horizon_end=r.horizon_end,
            entries=len(r.entries),
            unscheduled=len(r.unscheduled),
            metrics=ScheduleMetricsResponse.from_domain(r.metrics),
            quality=ScheduleQualityResponse.from_domain(r.quality) if r.quality else None,
            warnings=list(r.warnings),
        )


class SimulationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    simulation_id: str
    generated_at: datetime
    note: str | None = None
    baseline_version: int | None = Field(default=None, description="Plan whose entries fed the frozen window")
    scenarios: list[ScenarioRecordResponse]
    baseline: PlanSummaryResponse
    scenario: PlanSummaryResponse
    diff: ScheduleDiffResponse
    comparison: dict[str, MetricPairResponse] = Field(description="Before → after metric pairs")
    comparison_summary: str
    affected_orders: list[AffectedOrderResponse]
    top_n: int
    summary: str = Field(description="Management summary sentence rendered by the diff engine")

    @classmethod
    def from_domain(cls, o: SimulationOutcome) -> SimulationResponse:
        comparison = {
            k: MetricPairResponse.model_validate(v) for k, v in o.comparison.items() if isinstance(v, dict)
        }
        return cls(
            simulation_id=o.result.simulation_id,
            generated_at=o.result.generated_at,
            note=o.note,
            baseline_version=o.baseline_version.version_number if o.baseline_version else None,
            scenarios=[ScenarioRecordResponse.from_record(s) for s in o.result.scenarios],
            baseline=PlanSummaryResponse.from_domain(o.result.baseline),
            scenario=PlanSummaryResponse.from_domain(o.result.scenario),
            diff=ScheduleDiffResponse.from_domain(o.result.diff),
            comparison=comparison,
            comparison_summary=str(o.comparison.get("summary", "")),
            affected_orders=[AffectedOrderResponse.from_domain(a) for a in o.affected],
            top_n=o.top_n,
            summary=o.summary,
        )


class ScenarioTypeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    kind: str
    title: str
    description: str
    schema_: dict[str, Any] = Field(alias="schema", serialization_alias="schema")

    @classmethod
    def from_domain(cls, t: ScenarioType) -> ScenarioTypeResponse:
        return cls(kind=t.kind, title=t.title, description=t.description, schema=t.schema)


class ScenarioTypesResponse(BaseModel):
    """JSON schema of the ``Scenario`` union for the UI form builder."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    kinds: list[ScenarioTypeResponse]
    schema_: dict[str, Any] = Field(alias="schema", serialization_alias="schema")

    @classmethod
    def from_domain(cls, t: ScenarioTypes) -> ScenarioTypesResponse:
        return cls(kinds=[ScenarioTypeResponse.from_domain(k) for k in t.kinds], schema=t.schema)


__all__ = [
    "AffectedOrderResponse",
    "OrderDeltaResponse",
    "PlanSummaryResponse",
    "ScenarioRecordResponse",
    "ScenarioTypeResponse",
    "ScenarioTypesResponse",
    "ScheduleDiffResponse",
    "SimulateRequest",
    "SimulationResponse",
]
