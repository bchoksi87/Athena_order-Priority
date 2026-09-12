"""ORM: operational tables ``alerts``, ``audit_log``, ``data_quality_issues``, ``sync_runs``,
``input_snapshots``."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Index, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, CodeString, IdString, JSONDict, NameString, TimestampMixin, UTCDateTime


class AlertRow(Base, TimestampMixin):
    __tablename__ = "alerts"
    __table_args__ = (
        Index("ix_alerts_active_severity", "active", "severity"),
        Index("ix_alerts_active_raised", "active", "raised_at"),
    )

    alert_id: Mapped[str] = mapped_column(IdString, primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    alert_type: Mapped[str] = mapped_column(CodeString, nullable=False, index=True)
    severity: Mapped[str] = mapped_column(CodeString, nullable=False, index=True)
    title: Mapped[str] = mapped_column(NameString, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    recommended_action: Mapped[str] = mapped_column(Text, nullable=False, default="")
    raised_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    occurrences: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    order_id: Mapped[str | None] = mapped_column(IdString, index=True)
    machine_id: Mapped[str | None] = mapped_column(IdString, index=True)
    entity_ref: Mapped[str | None] = mapped_column(String(128))
    details: Mapped[dict[str, Any]] = mapped_column(JSONDict, nullable=False, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    acknowledged_by: Mapped[str | None] = mapped_column(IdString)
    acknowledged_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class AuditLogRow(Base, TimestampMixin):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_entity", "entity_type", "entity_id"),
        Index("ix_audit_log_user_time", "user_id", "timestamp"),
    )

    audit_id: Mapped[str] = mapped_column(IdString, primary_key=True)
    user_id: Mapped[str] = mapped_column(IdString, nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(IdString, nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    previous_value: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSONDict)
    new_value: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSONDict)
    reason: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(String(128))
    details: Mapped[dict[str, Any]] = mapped_column(JSONDict, nullable=False, default=dict)


class DataQualityIssueRow(Base, TimestampMixin):
    __tablename__ = "data_quality_issues"
    __table_args__ = (
        Index("ix_data_quality_issues_run_code", "run_id", "code"),
        Index("ix_data_quality_issues_run_entity", "run_id", "entity_type", "entity_id"),
    )

    issue_id: Mapped[str] = mapped_column(IdString, primary_key=True)
    run_id: Mapped[str] = mapped_column(IdString, nullable=False, index=True)
    detected_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    code: Mapped[str] = mapped_column(CodeString, nullable=False, index=True)
    severity: Mapped[str] = mapped_column(CodeString, nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str] = mapped_column(IdString, nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    field_name: Mapped[str | None] = mapped_column(String(128))
    recommendation: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONDict, nullable=False, default=dict)


class SyncRunRow(Base, TimestampMixin):
    __tablename__ = "sync_runs"

    run_id: Mapped[str] = mapped_column(IdString, primary_key=True)
    mode: Mapped[str] = mapped_column(CodeString, nullable=False, index=True)
    status: Mapped[str] = mapped_column(CodeString, nullable=False, index=True)
    connector: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    since: Mapped[datetime | None] = mapped_column(UTCDateTime)
    records_fetched: Mapped[dict[str, int]] = mapped_column(JSONDict, nullable=False, default=dict)
    records_upserted: Mapped[dict[str, int]] = mapped_column(JSONDict, nullable=False, default=dict)
    issues_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    triggered_by: Mapped[str | None] = mapped_column(IdString)
    error_message: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONDict, nullable=False, default=dict)


class InputSnapshotRow(Base, TimestampMixin):
    """Compressed :class:`PlanningSnapshot` used by a run (see ``app.db.snapshot_codec``)."""

    __tablename__ = "input_snapshots"

    snapshot_id: Mapped[str] = mapped_column(IdString, primary_key=True)
    as_of: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown")
    codec: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    summary: Mapped[dict[str, int]] = mapped_column(JSONDict, nullable=False, default=dict)
    created_by: Mapped[str | None] = mapped_column(IdString)


__all__ = ["AlertRow", "AuditLogRow", "DataQualityIssueRow", "InputSnapshotRow", "SyncRunRow"]
