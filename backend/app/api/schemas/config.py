"""Configuration schemas (spec Phase 14): versions, updates, diffs and the profile preview."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.api.schemas.common import ReasonBody
from app.db.records import ConfigVersionInfo
from app.domain.config import (
    AlertConfig,
    DataQualityConfig,
    PriorityProfile,
    ReplanningConfig,
    SchedulingConfig,
    SystemConfig,
)
from app.engines.priority.preview import ProfileComparison
from app.services.config_service import ConfigUpdate


class ConfigVersionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config_id: str
    version: int
    is_active: bool
    created_by: str | None = None
    reason: str | None = None
    created_at: datetime | None = None
    profile_id: str
    scheduling_config_id: str

    @classmethod
    def from_record(cls, info: ConfigVersionInfo) -> ConfigVersionResponse:
        return cls(
            config_id=info.config_id,
            version=info.version,
            is_active=info.is_active,
            created_by=info.created_by,
            reason=info.reason,
            created_at=info.created_at,
            profile_id=info.profile_id,
            scheduling_config_id=info.scheduling_config_id,
        )


class PriorityConfigurationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: ConfigVersionResponse | None = None
    profile: PriorityProfile
    weights_pct: dict[str, float] = Field(description="Normalised weights (enabled factors sum to 100)")

    @classmethod
    def build(cls, config: SystemConfig, info: ConfigVersionInfo | None) -> PriorityConfigurationResponse:
        profile = config.priority_profile
        return cls(
            version=ConfigVersionResponse.from_record(info) if info else None,
            profile=profile,
            weights_pct={k: round(v * 100.0, 4) for k, v in profile.weight_map().items()},
        )


class SchedulingConfigurationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: ConfigVersionResponse | None = None
    scheduling: SchedulingConfig
    replanning: ReplanningConfig
    alerts: AlertConfig
    data_quality: DataQualityConfig

    @classmethod
    def build(cls, config: SystemConfig, info: ConfigVersionInfo | None) -> SchedulingConfigurationResponse:
        return cls(
            version=ConfigVersionResponse.from_record(info) if info else None,
            scheduling=config.scheduling,
            replanning=config.replanning,
            alerts=config.alerts,
            data_quality=config.data_quality,
        )


class SystemConfigVersionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: ConfigVersionResponse
    config: SystemConfig


class PriorityProfileUpdateRequest(ReasonBody):
    profile: PriorityProfile


class SchedulingConfigUpdateRequest(ReasonBody):
    scheduling: SchedulingConfig | None = None
    replanning: ReplanningConfig | None = None
    alerts: AlertConfig | None = None
    data_quality: DataQualityConfig | None = None


class ActivateVersionRequest(ReasonBody):
    pass


class ConfigUpdateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: ConfigVersionResponse
    previous_version: int | None = None
    changed_previous: dict[str, Any] = Field(description="Only the keys that changed, previous values")
    changed_new: dict[str, Any] = Field(description="Only the keys that changed, new values")
    config: SystemConfig

    @classmethod
    def from_update(cls, update: ConfigUpdate) -> ConfigUpdateResponse:
        return cls(
            version=ConfigVersionResponse.from_record(update.info),
            previous_version=update.previous_version,
            changed_previous=update.changed_previous,
            changed_new=update.changed_new,
            config=update.config,
        )


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: PriorityProfile
    top_n: int = Field(default=50, ge=1, le=500)


class WeightChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    previous_pct: float
    new_pct: float


class OrderMove(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: str
    rank_delta: int
    score_delta: float


class PreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(
        description='"Changing Due Date weight from 30% to 40% would move 27 orders into the top 50"'
    )
    active_profile_id: str
    candidate_profile_id: str
    top_n: int
    orders_evaluated: int
    entered_top_n: list[str]
    left_top_n: list[str]
    orders_changed_rank: int
    mean_abs_score_delta: float
    max_abs_score_delta: float
    weight_changes: list[WeightChange]
    top_n_before: list[str]
    top_n_after: list[str]
    biggest_moves: list[OrderMove] = Field(description="Largest absolute rank changes (up to 25)")

    @classmethod
    def from_comparison(cls, c: ProfileComparison) -> PreviewResponse:
        moves = sorted(
            (
                OrderMove(order_id=oid, rank_delta=delta, score_delta=c.score_deltas.get(oid, 0.0))
                for oid, delta in c.rank_deltas.items()
                if delta != 0
            ),
            key=lambda m: (-abs(m.rank_delta), m.order_id),
        )[:25]
        return cls(
            summary=c.summary,
            active_profile_id=c.profile_a_id,
            candidate_profile_id=c.profile_b_id,
            top_n=c.top_n,
            orders_evaluated=c.orders_evaluated,
            entered_top_n=list(c.entered_top_n),
            left_top_n=list(c.left_top_n),
            orders_changed_rank=c.orders_changed_rank,
            mean_abs_score_delta=c.mean_abs_score_delta,
            max_abs_score_delta=c.max_abs_score_delta,
            weight_changes=[
                WeightChange(key=k, previous_pct=a, new_pct=b) for k, (a, b) in c.weight_changes.items()
            ],
            top_n_before=list(c.top_n_a),
            top_n_after=list(c.top_n_b),
            biggest_moves=moves,
        )


__all__ = [
    "ActivateVersionRequest",
    "ConfigUpdateResponse",
    "ConfigVersionResponse",
    "OrderMove",
    "PreviewRequest",
    "PreviewResponse",
    "PriorityConfigurationResponse",
    "PriorityProfileUpdateRequest",
    "SchedulingConfigUpdateRequest",
    "SchedulingConfigurationResponse",
    "SystemConfigVersionResponse",
    "WeightChange",
]
