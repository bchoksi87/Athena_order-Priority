"""Planner overlay schemas: overrides, expedites and locks."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.api.schemas.common import ReasonBody
from app.domain.enums import LockType, OverrideType
from app.domain.models import Expedite, PriorityOverride, ScheduleLock, TimeWindow

# ------------------------------------------------------------------ responses


class TimeWindowResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: datetime
    end: datetime
    reason: str = ""

    @classmethod
    def from_domain(cls, window: TimeWindow) -> TimeWindowResponse:
        return cls(start=window.start, end=window.end, reason=window.reason)


class OverrideResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    override_id: str
    order_id: str
    override_type: OverrideType
    value: float | None = None
    target_machine_id: str | None = None
    reason: str
    created_by: str
    created_at: datetime
    expires_at: datetime | None = None
    active: bool

    @classmethod
    def from_domain(cls, override: PriorityOverride) -> OverrideResponse:
        return cls(
            override_id=override.override_id,
            order_id=override.order_id,
            override_type=override.override_type,
            value=override.value,
            target_machine_id=override.target_machine_id,
            reason=override.reason,
            created_by=override.created_by,
            created_at=override.created_at,
            expires_at=override.expires_at,
            active=override.active,
        )


class LockResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lock_id: str
    lock_type: LockType
    order_id: str | None = None
    machine_id: str | None = None
    window: TimeWindowResponse | None = None
    sequence_order_ids: list[str] = []
    reason: str
    created_by: str
    created_at: datetime
    active: bool

    @classmethod
    def from_domain(cls, lock: ScheduleLock) -> LockResponse:
        return cls(
            lock_id=lock.lock_id,
            lock_type=lock.lock_type,
            order_id=lock.order_id,
            machine_id=lock.machine_id,
            window=TimeWindowResponse.from_domain(lock.window) if lock.window else None,
            sequence_order_ids=list(lock.sequence_order_ids),
            reason=lock.reason,
            created_by=lock.created_by,
            created_at=lock.created_at,
            active=lock.active,
        )


class ExpediteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expedite_id: str
    order_id: str
    boost_points: float
    starts_at: datetime
    expires_at: datetime
    reason: str
    created_by: str
    created_at: datetime
    active: bool

    @classmethod
    def from_domain(cls, expedite: Expedite) -> ExpediteResponse:
        return cls(
            expedite_id=expedite.expedite_id,
            order_id=expedite.order_id,
            boost_points=expedite.boost_points,
            starts_at=expedite.starts_at,
            expires_at=expedite.expires_at,
            reason=expedite.reason,
            created_by=expedite.created_by,
            created_at=expedite.created_at,
            active=expedite.active,
        )


class MoveOrderResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    override: OverrideResponse
    lock: LockResponse


# ------------------------------------------------------------------- requests


class ExpediteRequest(ReasonBody):
    boost_points: float | None = Field(default=None, gt=0, description="Default: profile expedite boost")
    duration_hours: float | None = Field(default=None, gt=0, description="Default: profile expedite duration")
    starts_at: datetime | None = None
    expires_at: datetime | None = Field(default=None, description="Alternative to duration_hours")


class HoldRequest(ReasonBody):
    expires_at: datetime | None = None


class ReleaseRequest(ReasonBody):
    pass


OverridePriorityKind = Literal["increase", "decrease", "set"]


class OverridePriorityRequest(ReasonBody):
    type: OverridePriorityKind = Field(description="increase/decrease by `value` points or set to `value`")
    value: float = Field(ge=0, le=100)
    expires_at: datetime | None = None


class ForceNextRequest(ReasonBody):
    expires_at: datetime | None = None


class MoveOrderRequest(ReasonBody):
    target_machine_id: str = Field(min_length=1)
    start_at: datetime | None = Field(default=None, description="Optional slot start on the target machine")
    expires_at: datetime | None = None


class LockMachineAssignmentRequest(ReasonBody):
    machine_id: str = Field(min_length=1)
    expires_at: datetime | None = None


class CancelRequest(ReasonBody):
    pass


class LockRequest(ReasonBody):
    lock_type: LockType
    order_id: str | None = None
    machine_id: str | None = None
    window_start: datetime | None = Field(default=None, description="Default: now (MACHINE/TIME_SLOT)")
    window_end: datetime | None = Field(
        default=None, description="Default: window_start + scheduling.lock_window_minutes"
    )
    sequence_order_ids: list[str] = Field(default_factory=list)


class UnlockRequest(ReasonBody):
    lock_id: str = Field(min_length=1)


__all__ = [
    "CancelRequest",
    "ExpediteRequest",
    "ExpediteResponse",
    "ForceNextRequest",
    "HoldRequest",
    "LockMachineAssignmentRequest",
    "LockRequest",
    "LockResponse",
    "MoveOrderRequest",
    "MoveOrderResponse",
    "OverridePriorityKind",
    "OverridePriorityRequest",
    "OverrideResponse",
    "ReleaseRequest",
    "TimeWindowResponse",
    "UnlockRequest",
]
