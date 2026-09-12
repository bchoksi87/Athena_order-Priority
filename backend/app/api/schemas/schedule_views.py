"""Schedule view schemas: day view, Gantt board and continuous replanning (spec Phases 8, 11)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.api.schemas.orders import ScheduleEntryResponse
from app.api.schemas.overlays import LockResponse, TimeWindowResponse
from app.api.schemas.schedule import ScheduleComparisonResponse, ScheduleVersionResponse
from app.domain.enums import OrderStatus, ProcessType, ReplanTriggerType
from app.domain.results import ReplanDecision
from app.engines.replanning.triggers import ReplanEvent
from app.services.replanning_service import ReplanOutcome
from app.services.schedule_view_service import DayView, GanttBlock, GanttView, MachineRow

# ------------------------------------------------------------ machine views


class GanttBlockResponse(BaseModel):
    """One job on the machine board: setup block ``[setup_start, start)`` and run block ``[start, end)``."""

    model_config = ConfigDict(extra="forbid")
    entry: ScheduleEntryResponse
    order_id: str
    customer_id: str | None = None
    customer_name: str | None = None
    part_id: str | None = None
    part_name: str | None = None
    order_status: OrderStatus | None = None
    setup_start: datetime
    start: datetime
    end: datetime
    locked: bool
    late: bool

    @classmethod
    def from_domain(cls, b: GanttBlock) -> GanttBlockResponse:
        e = b.entry
        return cls(
            entry=ScheduleEntryResponse.from_domain(e),
            order_id=e.order_id,
            customer_id=b.order.customer_id if b.order else e.customer_id,
            customer_name=b.customer_name,
            part_id=b.order.part_id if b.order else None,
            part_name=b.order.part_name if b.order else None,
            order_status=b.order.order_status if b.order else None,
            setup_start=e.setup_start,
            start=e.start,
            end=e.end,
            locked=e.locked,
            late=b.late,
        )


class MachineRowResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    machine_id: str
    machine_name: str
    machine_group: str
    process_type: ProcessType
    status: str
    busy_hours: float
    blocks: list[GanttBlockResponse]
    downtime: list[TimeWindowResponse]
    locks: list[LockResponse]

    @classmethod
    def from_domain(cls, r: MachineRow) -> MachineRowResponse:
        return cls(
            machine_id=r.machine.machine_id,
            machine_name=r.machine.machine_name,
            machine_group=r.machine.machine_group,
            process_type=r.machine.process_type,
            status=r.machine.status.value,
            busy_hours=r.busy_minutes / 60.0,
            blocks=[GanttBlockResponse.from_domain(b) for b in r.blocks],
            downtime=[TimeWindowResponse.from_domain(w) for w in r.downtime],
            locks=[LockResponse.from_domain(lk) for lk in r.locks],
        )


class DayScheduleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: date
    timezone: str
    start: datetime
    end: datetime
    version: ScheduleVersionResponse
    entries: int
    machines: list[MachineRowResponse]

    @classmethod
    def from_domain(cls, v: DayView) -> DayScheduleResponse:
        return cls(
            date=v.day,
            timezone=v.timezone,
            start=v.start,
            end=v.end,
            version=ScheduleVersionResponse.from_record(v.version),
            entries=v.entries,
            machines=[MachineRowResponse.from_domain(r) for r in v.rows],
        )


class GanttResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: ScheduleVersionResponse
    axis_start: datetime
    axis_end: datetime
    machine_group: str | None = None
    process_type: ProcessType | None = None
    entries: int
    rows: list[MachineRowResponse]

    @classmethod
    def from_domain(cls, v: GanttView) -> GanttResponse:
        return cls(
            version=ScheduleVersionResponse.from_record(v.version),
            axis_start=v.axis_start,
            axis_end=v.axis_end,
            machine_group=v.machine_group,
            process_type=v.process_type,
            entries=v.entries,
            rows=[MachineRowResponse.from_domain(r) for r in v.rows],
        )


# --------------------------------------------------------------- replanning


class ReplanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trigger: ReplanTriggerType = ReplanTriggerType.MANUAL
    reason: str | None = Field(default=None, max_length=2000)


class ReplanEventResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: ReplanTriggerType
    entity_type: str
    entity_id: str
    occurred_at: datetime
    message: str
    order_id: str | None = None
    machine_id: str | None = None
    details: dict[str, Any] = {}

    @classmethod
    def from_domain(cls, e: ReplanEvent) -> ReplanEventResponse:
        from app.db.snapshot_codec import to_jsonable

        return cls(
            type=e.type,
            entity_type=e.entity_type,
            entity_id=e.entity_id,
            occurred_at=e.occurred_at,
            message=e.message,
            order_id=e.order_id,
            machine_id=e.machine_id,
            details=to_jsonable(e.details),
        )


class ReplanDecisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    should_replan: bool
    reason: str
    improvement_pct: float
    changed_entries: int
    frozen_violations: int
    requires_approval: bool
    triggers: list[str]

    @classmethod
    def from_domain(cls, d: ReplanDecision) -> ReplanDecisionResponse:
        return cls(
            should_replan=d.should_replan,
            reason=d.reason,
            improvement_pct=d.improvement_pct,
            changed_entries=d.changed_entries,
            frozen_violations=d.frozen_violations,
            requires_approval=d.requires_approval,
            triggers=list(d.triggers),
        )


class ReplanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trigger: ReplanTriggerType
    evaluated_at: datetime
    triggered: bool
    action: str = Field(description="not_triggered / rejected / awaiting_approval / approved / published")
    reason: str
    event_types: list[str]
    events: list[ReplanEventResponse]
    decision: ReplanDecisionResponse | None = None
    active_version: ScheduleVersionResponse | None = None
    candidate_version: ScheduleVersionResponse | None = None
    comparison: ScheduleComparisonResponse | None = None
    alert_id: str | None = None

    @classmethod
    def from_domain(cls, o: ReplanOutcome) -> ReplanResponse:
        return cls(
            trigger=o.trigger,
            evaluated_at=o.evaluated_at,
            triggered=o.triggered,
            action=o.action,
            reason=o.reason,
            event_types=o.event_types,
            events=[ReplanEventResponse.from_domain(e) for e in o.events],
            decision=ReplanDecisionResponse.from_domain(o.decision) if o.decision else None,
            active_version=ScheduleVersionResponse.from_record(o.active_version)
            if o.active_version
            else None,
            candidate_version=(
                ScheduleVersionResponse.from_record(o.candidate_version) if o.candidate_version else None
            ),
            comparison=ScheduleComparisonResponse.from_domain(o.comparison) if o.comparison else None,
            alert_id=o.alert.alert_id if o.alert else None,
        )


__all__ = [
    "DayScheduleResponse",
    "GanttBlockResponse",
    "GanttResponse",
    "MachineRowResponse",
    "ReplanDecisionResponse",
    "ReplanEventResponse",
    "ReplanRequest",
    "ReplanResponse",
]
