"""Order list / detail / explanation / machine-option schemas (spec Phase 8)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.api.schemas.audit import AuditEntryResponse
from app.api.schemas.overlays import ExpediteResponse, LockResponse, OverrideResponse
from app.domain.enums import (
    MaterialStatus,
    OperationStatus,
    OrderStatus,
    ProcessType,
    QualityStatus,
    ReadinessState,
    RiskLevel,
)
from app.domain.models import Customer, Material, Operation, Order, Tooling
from app.domain.results import (
    DataQualityIssue,
    FactorScore,
    PriorityAdjustment,
    PriorityResult,
    ScheduleEntry,
)
from app.services.order_query_service import (
    ExplanationData,
    MachineOption,
    MachineOptions,
    OrderDetail,
    OrderListItem,
    OrderScheduleInfo,
)


class CustomerSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_id: str
    customer_name: str
    customer_tier: str
    strategic_customer_flag: bool
    sla_hours: float | None = None

    @classmethod
    def from_domain(cls, customer: Customer) -> CustomerSummary:
        return cls(
            customer_id=customer.customer_id,
            customer_name=customer.customer_name,
            customer_tier=customer.customer_tier.value,
            strategic_customer_flag=customer.strategic_customer_flag,
            sla_hours=customer.sla_hours,
        )


class FactorScoreResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    name: str
    kind: str
    raw_score: float
    weight: float
    points: float
    reason: str
    details: dict[str, Any] = {}

    @classmethod
    def from_domain(cls, f: FactorScore) -> FactorScoreResponse:
        return cls(
            key=f.key,
            name=f.name,
            kind=f.kind,
            raw_score=f.raw_score,
            weight=f.weight,
            points=f.points,
            reason=f.reason,
            details=dict(f.details),
        )


class AdjustmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    points: float
    reason: str
    source_id: str | None = None

    @classmethod
    def from_domain(cls, a: PriorityAdjustment) -> AdjustmentResponse:
        return cls(kind=a.kind, points=a.points, reason=a.reason, source_id=a.source_id)


class PriorityInfo(BaseModel):
    """Latest stored priority result of an order."""

    model_config = ConfigDict(extra="forbid")
    score: float
    base_score: float
    rank: int | None = None
    risk_level: RiskLevel
    readiness: ReadinessState
    blocked: bool
    blocking_reasons: list[str] = []
    forced_next: bool = False
    hours_until_due: float | None = None
    projected_completion: datetime | None = None
    projected_lateness_hours: float | None = None
    explanation: str
    profile_id: str
    profile_version: int
    computed_at: datetime

    @classmethod
    def from_domain(cls, r: PriorityResult) -> PriorityInfo:
        return cls(
            score=r.score,
            base_score=r.base_score,
            rank=r.rank,
            risk_level=r.risk_level,
            readiness=r.readiness,
            blocked=r.blocked,
            blocking_reasons=list(r.blocking_reasons),
            forced_next=r.forced_next,
            hours_until_due=r.hours_until_due,
            projected_completion=r.projected_completion,
            projected_lateness_hours=r.projected_lateness_hours,
            explanation=r.explanation,
            profile_id=r.profile_id,
            profile_version=r.profile_version,
            computed_at=r.computed_at,
        )


class ScheduleEntryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entry_id: str
    machine_id: str
    order_id: str
    operation_id: str
    sequence_on_machine: int
    setup_start: datetime
    start: datetime
    end: datetime
    setup_minutes: float
    run_minutes: float
    quantity: float
    priority_score: float
    placement_reason: str
    is_last_operation: bool
    expected_completion: datetime | None = None
    due_date: datetime | None = None
    expected_lateness_hours: float | None = None
    locked: bool = False
    batch_key: str | None = None
    setup_family: str | None = None
    material_id: str | None = None
    customer_id: str | None = None

    @classmethod
    def from_domain(cls, e: ScheduleEntry) -> ScheduleEntryResponse:
        return cls(
            entry_id=e.entry_id,
            machine_id=e.machine_id,
            order_id=e.order_id,
            operation_id=e.operation_id,
            sequence_on_machine=e.sequence_on_machine,
            setup_start=e.setup_start,
            start=e.start,
            end=e.end,
            setup_minutes=e.setup_minutes,
            run_minutes=e.run_minutes,
            quantity=e.quantity,
            priority_score=e.priority_score,
            placement_reason=e.placement_reason,
            is_last_operation=e.is_last_operation,
            expected_completion=e.expected_completion,
            due_date=e.due_date,
            expected_lateness_hours=e.expected_lateness_hours,
            locked=e.locked,
            batch_key=e.batch_key,
            setup_family=e.setup_family,
            material_id=e.material_id,
            customer_id=e.customer_id,
        )


class OrderScheduleResponse(BaseModel):
    """Placement of the order in the current schedule version."""

    model_config = ConfigDict(extra="forbid")
    version_number: int
    status: str = Field(description="draft/approved/published: which schedule the placement comes from")
    machine_id: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    expected_completion: datetime | None = None
    expected_lateness_hours: float | None = None
    entries: list[ScheduleEntryResponse] = []

    @classmethod
    def from_domain(cls, info: OrderScheduleInfo, *, with_entries: bool = False) -> OrderScheduleResponse:
        return cls(
            version_number=info.version_number,
            status=info.status,
            machine_id=info.machine_id,
            start=info.start,
            end=info.end,
            expected_completion=info.expected_completion,
            expected_lateness_hours=info.expected_lateness_hours,
            entries=[ScheduleEntryResponse.from_domain(e) for e in info.entries] if with_entries else [],
        )


class OrderSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: str
    external_order_ref: str | None = None
    customer_id: str
    customer_name: str | None = None
    part_id: str
    part_name: str | None = None
    part_family: str | None = None
    quantity: float
    pending_quantity: float
    completed_quantity: float
    due_date: datetime | None = None
    order_date: datetime | None = None
    order_status: OrderStatus
    production_status: str | None = None
    material_status: MaterialStatus
    quality_status: QualityStatus
    process_type: ProcessType
    machine_group: str | None = None
    required_machine_id: str | None = None
    required_material_id: str | None = None
    order_value: float | None = None
    estimated_margin: float | None = None
    erp_priority: int | None = None
    on_hold: bool
    hold_reason: str | None = None

    @classmethod
    def from_domain(
        cls, order: Order, customer: Customer | None, on_hold: bool, hold_reason: str | None
    ) -> OrderSummary:
        return cls(
            order_id=order.order_id,
            external_order_ref=order.external_order_ref,
            customer_id=order.customer_id,
            customer_name=customer.customer_name if customer else None,
            part_id=order.part_id,
            part_name=order.part_name,
            part_family=order.part_family,
            quantity=order.quantity,
            pending_quantity=order.pending_quantity,
            completed_quantity=order.completed_quantity,
            due_date=order.due_date,
            order_date=order.order_date,
            order_status=order.order_status,
            production_status=order.production_status,
            material_status=order.material_status,
            quality_status=order.quality_status,
            process_type=order.process_type,
            machine_group=order.machine_group,
            required_machine_id=order.required_machine_id,
            required_material_id=order.required_material_id,
            order_value=order.order_value,
            estimated_margin=order.estimated_margin,
            erp_priority=order.erp_priority,
            on_hold=on_hold,
            hold_reason=hold_reason,
        )


class OrderListItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order: OrderSummary
    priority: PriorityInfo | None = None
    schedule: OrderScheduleResponse | None = None

    @classmethod
    def from_item(cls, item: OrderListItem) -> OrderListItemResponse:
        return cls(
            order=OrderSummary.from_domain(item.order, item.customer, item.on_hold, item.hold_reason),
            priority=PriorityInfo.from_domain(item.priority) if item.priority else None,
            schedule=OrderScheduleResponse.from_domain(item.schedule) if item.schedule else None,
        )


class OperationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str
    sequence: int
    operation_type: ProcessType
    machine_group: str | None = None
    machine_id: str | None = None
    operation_status: OperationStatus
    quantity: float
    completed_quantity: float
    setup_minutes: float | None = None
    cycle_minutes_per_unit: float | None = None
    material_id: str | None = None
    tooling_ids: list[str] = []
    prerequisite_operation_id: str | None = None
    estimated_start: datetime | None = None
    estimated_end: datetime | None = None
    actual_start: datetime | None = None
    actual_end: datetime | None = None

    @classmethod
    def from_domain(cls, op: Operation) -> OperationResponse:
        return cls(
            operation_id=op.operation_id,
            sequence=op.sequence,
            operation_type=op.operation_type,
            machine_group=op.machine_group,
            machine_id=op.machine_id,
            operation_status=op.operation_status,
            quantity=op.quantity,
            completed_quantity=op.completed_quantity,
            setup_minutes=op.setup_minutes,
            cycle_minutes_per_unit=op.cycle_minutes_per_unit,
            material_id=op.material_id,
            tooling_ids=sorted(op.tooling_ids),
            prerequisite_operation_id=op.prerequisite_operation_id,
            estimated_start=op.estimated_start,
            estimated_end=op.estimated_end,
            actual_start=op.actual_start,
            actual_end=op.actual_end,
        )


class MaterialResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    material_id: str
    material_name: str
    material_type: str = ""
    available_quantity: float
    reserved_quantity: float
    free_quantity: float
    incoming_quantity: float
    expected_receipt_date: datetime | None = None
    unit: str

    @classmethod
    def from_domain(cls, m: Material) -> MaterialResponse:
        return cls(
            material_id=m.material_id,
            material_name=m.material_name,
            material_type=m.material_type,
            available_quantity=m.available_quantity,
            reserved_quantity=m.reserved_quantity,
            free_quantity=m.free_quantity,
            incoming_quantity=m.incoming_quantity,
            expected_receipt_date=m.expected_receipt_date,
            unit=m.unit,
        )


class ToolingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tooling_id: str
    tooling_name: str
    available: bool
    usable: bool
    available_from: datetime | None = None
    maintenance_status: str

    @classmethod
    def from_domain(cls, t: Tooling) -> ToolingResponse:
        return cls(
            tooling_id=t.tooling_id,
            tooling_name=t.tooling_name,
            available=t.available,
            usable=t.is_usable,
            available_from=t.available_from,
            maintenance_status=t.maintenance_status,
        )


class ExplanationLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    key: str
    label: str
    points: float
    reason: str
    raw_score: float | None = None
    weight: float | None = None
    source_id: str | None = None


class DataQualityIssueBrief(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    severity: str
    message: str
    field_name: str | None = None
    recommendation: str | None = None

    @classmethod
    def from_domain(cls, issue: DataQualityIssue) -> DataQualityIssueBrief:
        return cls(
            code=issue.code.value,
            severity=issue.severity.value,
            message=issue.message,
            field_name=issue.field_name,
            recommendation=issue.recommendation,
        )


class OrderDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order: OrderSummary
    customer: CustomerSummary | None = None
    priority: PriorityInfo | None = None
    breakdown: list[ExplanationLine] = Field(
        default_factory=list, description="Why is this order prioritised?"
    )
    factors: list[FactorScoreResponse] = []
    adjustments: list[AdjustmentResponse] = []
    schedule: OrderScheduleResponse | None = None
    operations: list[OperationResponse] = []
    materials: list[MaterialResponse] = []
    tooling: list[ToolingResponse] = []
    production_minutes: float | None = None
    dependencies: list[str] = []
    dependents: list[str] = []
    overrides: list[OverrideResponse] = []
    expedites: list[ExpediteResponse] = []
    locks: list[LockResponse] = []
    audit: list[AuditEntryResponse] = []
    data_quality_issues: list[DataQualityIssueBrief] = []
    special_instructions: str | None = None
    drawing_approved: bool = True

    @classmethod
    def from_detail(cls, d: OrderDetail) -> OrderDetailResponse:
        return cls(
            order=OrderSummary.from_domain(d.order, d.customer, d.on_hold, d.hold_reason),
            customer=CustomerSummary.from_domain(d.customer) if d.customer else None,
            priority=PriorityInfo.from_domain(d.priority) if d.priority else None,
            breakdown=[ExplanationLine(**line) for line in d.breakdown],
            factors=[FactorScoreResponse.from_domain(f) for f in d.priority.factors] if d.priority else [],
            adjustments=[AdjustmentResponse.from_domain(a) for a in d.priority.adjustments]
            if d.priority
            else [],
            schedule=OrderScheduleResponse.from_domain(d.schedule, with_entries=True) if d.schedule else None,
            operations=[OperationResponse.from_domain(op) for op in d.operations],
            materials=[MaterialResponse.from_domain(m) for m in d.materials],
            tooling=[ToolingResponse.from_domain(t) for t in d.tooling],
            production_minutes=d.production_minutes,
            dependencies=d.dependencies,
            dependents=d.dependents,
            overrides=[OverrideResponse.from_domain(o) for o in d.overrides],
            expedites=[ExpediteResponse.from_domain(e) for e in d.expedites],
            locks=[LockResponse.from_domain(lk) for lk in d.locks],
            audit=[AuditEntryResponse.from_record(a) for a in d.audit],
            data_quality_issues=[DataQualityIssueBrief.from_domain(i) for i in d.data_quality_issues],
            special_instructions=d.order.special_instructions,
            drawing_approved=d.order.drawing_approved,
        )


class ExplanationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: str
    score: float
    rank: int | None = None
    risk_level: RiskLevel
    readiness: ReadinessState
    blocked: bool
    blocking_reasons: list[str] = []
    forced_next: bool
    explanation: str = Field(description="Rendered text, e.g. '+25 — Due Date Urgency: Due in 18 hours'")
    lines: list[ExplanationLine]
    profile_id: str
    profile_version: int
    computed_at: datetime

    @classmethod
    def from_data(cls, data: ExplanationData) -> ExplanationResponse:
        r = data.result
        return cls(
            order_id=r.order_id,
            score=r.score,
            rank=r.rank,
            risk_level=r.risk_level,
            readiness=r.readiness,
            blocked=r.blocked,
            blocking_reasons=list(r.blocking_reasons),
            forced_next=r.forced_next,
            explanation=r.explanation,
            lines=[ExplanationLine(**line) for line in data.lines],
            profile_id=r.profile_id,
            profile_version=r.profile_version,
            computed_at=r.computed_at,
        )


class MachineOptionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    machine_id: str
    machine_name: str
    rank: int
    recommended: bool
    setup_minutes: float | None = None
    run_minutes: float | None = None
    soft_cost: float
    reasons: list[str]

    @classmethod
    def from_domain(cls, o: MachineOption) -> MachineOptionResponse:
        return cls(
            machine_id=o.machine_id,
            machine_name=o.machine_name,
            rank=o.rank,
            recommended=o.recommended,
            setup_minutes=o.setup_minutes,
            run_minutes=o.run_minutes,
            soft_cost=o.soft_cost,
            reasons=list(o.reasons),
        )


class MachineOptionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: str
    operation_id: str
    synthetic_operation: bool = Field(
        description="True when the ERP supplied no routing and an operation was derived"
    )
    source: str = Field(description="'live' = evaluated now by the constraint engine")
    evaluated_at: datetime
    recommended_machine_id: str | None = None
    scheduled_machine_id: str | None = None
    pinned_machine_id: str | None = None
    pinned_by: str | None = None
    eligible: list[MachineOptionResponse]
    rejected: dict[str, list[str]]

    @classmethod
    def from_domain(cls, m: MachineOptions) -> MachineOptionsResponse:
        return cls(
            order_id=m.order_id,
            operation_id=m.operation_id,
            synthetic_operation=m.synthetic_operation,
            source=m.source,
            evaluated_at=m.evaluated_at,
            recommended_machine_id=m.recommended_machine_id,
            scheduled_machine_id=m.scheduled_machine_id,
            pinned_machine_id=m.pinned_machine_id,
            pinned_by=m.pinned_by,
            eligible=[MachineOptionResponse.from_domain(o) for o in m.eligible],
            rejected={k: list(v) for k, v in m.rejected.items()},
        )


__all__ = [
    "AdjustmentResponse",
    "CustomerSummary",
    "DataQualityIssueBrief",
    "ExplanationLine",
    "ExplanationResponse",
    "FactorScoreResponse",
    "MachineOptionResponse",
    "MachineOptionsResponse",
    "MaterialResponse",
    "OperationResponse",
    "OrderDetailResponse",
    "OrderListItemResponse",
    "OrderScheduleResponse",
    "OrderSummary",
    "PriorityInfo",
    "ScheduleEntryResponse",
    "ToolingResponse",
]
