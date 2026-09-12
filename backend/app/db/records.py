"""Lightweight records returned by repositories for tables that have no domain
dataclass (runs, versions, users, audit rows ...). They are plain data holders;
services translate them to API schemas."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.domain.enums import AlertSeverity, AlertType, Role, ScheduleStatus, SyncMode


@dataclass(slots=True)
class Page:
    """Offset pagination envelope. ``items`` is typed by the repository method."""

    items: list[Any]
    total: int
    offset: int
    limit: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.items) < self.total


@dataclass(slots=True)
class UserRecord:
    user_id: str
    username: str
    role: Role
    display_name: str
    email: str | None
    active: bool
    last_login_at: datetime | None
    created_at: datetime | None = None


@dataclass(slots=True)
class ConfigVersionInfo:
    config_id: str
    version: int
    is_active: bool
    created_by: str | None
    reason: str | None
    created_at: datetime | None
    profile_id: str
    scheduling_config_id: str


@dataclass(slots=True)
class OptimizationRunRecord:
    run_id: str
    kind: str
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    orders_considered: int = 0
    orders_scheduled: int = 0
    orders_blocked: int = 0
    objective_score: float | None = None
    quality_score: float | None = None
    algorithm: str = ""
    algorithm_version: str = ""
    profile_id: str | None = None
    profile_version: int | None = None
    config_version: int | None = None
    input_snapshot_id: str | None = None
    triggered_by: str | None = None
    trigger_reason: str | None = None
    error_message: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float | None:
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()


@dataclass(slots=True)
class ScheduleVersionInfo:
    schedule_version_id: str
    version_number: int
    status: ScheduleStatus
    algorithm: str
    algorithm_version: str
    profile_id: str
    profile_version: int
    config_version: int
    generated_at: datetime
    horizon_start: datetime
    horizon_end: datetime
    label: str | None = None
    run_id: str | None = None
    input_snapshot_id: str | None = None
    generated_by: str | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None
    published_by: str | None = None
    published_at: datetime | None = None
    superseded_at: datetime | None = None
    entry_count: int = 0
    metrics: dict[str, Any] = field(default_factory=dict)
    quality: dict[str, Any] | None = None
    unscheduled: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: str | None = None


@dataclass(slots=True)
class AlertRecord:
    alert_id: str
    dedupe_key: str
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
    details: dict[str, Any] = field(default_factory=dict)
    acknowledged_by: str | None = None
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None

    @property
    def acknowledged(self) -> bool:
        return self.acknowledged_at is not None


@dataclass(slots=True)
class AuditEntry:
    audit_id: str
    user_id: str
    timestamp: datetime
    entity_type: str
    entity_id: str
    action: str
    previous_value: dict[str, Any] | list[Any] | None = None
    new_value: dict[str, Any] | list[Any] | None = None
    reason: str | None = None
    request_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DataQualityIssueRecord:
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
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DataQualitySummary:
    run_id: str | None
    detected_at: datetime | None
    total: int
    by_severity: dict[str, int]
    by_code: dict[str, int]
    by_entity_type: dict[str, int]
    blocked_entities: int


@dataclass(slots=True)
class SyncRunRecord:
    run_id: str
    mode: SyncMode
    status: str
    started_at: datetime
    connector: str = ""
    finished_at: datetime | None = None
    since: datetime | None = None
    records_fetched: dict[str, int] = field(default_factory=dict)
    records_upserted: dict[str, int] = field(default_factory=dict)
    issues_count: int = 0
    triggered_by: str | None = None
    error_message: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SnapshotInfo:
    snapshot_id: str
    as_of: datetime
    source: str
    codec: str
    size_bytes: int
    sha256: str
    summary: dict[str, int]
    created_at: datetime | None = None
    created_by: str | None = None


__all__ = [
    "AlertRecord",
    "AuditEntry",
    "ConfigVersionInfo",
    "DataQualityIssueRecord",
    "DataQualitySummary",
    "OptimizationRunRecord",
    "Page",
    "ScheduleVersionInfo",
    "SnapshotInfo",
    "SyncRunRecord",
    "UserRecord",
]
