"""ORM: ``optimization_runs``, ``schedule_versions``, ``schedule_entries``, ``schedule_locks``."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import (
    Base,
    CodeString,
    IdString,
    JSONDict,
    NameString,
    TimestampMixin,
    UTCDateTime,
)


class OptimizationRunRow(Base, TimestampMixin):
    """One row per engine run (priority evaluation, schedule generation, simulation)."""

    __tablename__ = "optimization_runs"
    __table_args__ = (Index("ix_optimization_runs_kind_started", "kind", "started_at"),)

    run_id: Mapped[str] = mapped_column(IdString, primary_key=True)
    kind: Mapped[str] = mapped_column(CodeString, nullable=False, index=True)
    status: Mapped[str] = mapped_column(CodeString, nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    orders_considered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    orders_scheduled: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    orders_blocked: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    objective_score: Mapped[float | None] = mapped_column(Float)
    quality_score: Mapped[float | None] = mapped_column(Float)
    algorithm: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    algorithm_version: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    profile_id: Mapped[str | None] = mapped_column(String(128))
    profile_version: Mapped[int | None] = mapped_column(Integer)
    config_version: Mapped[int | None] = mapped_column(Integer)
    input_snapshot_id: Mapped[str | None] = mapped_column(
        IdString, ForeignKey("input_snapshots.snapshot_id", ondelete="SET NULL")
    )
    triggered_by: Mapped[str | None] = mapped_column(IdString)
    trigger_reason: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONDict, nullable=False, default=dict)
    warnings: Mapped[list[str]] = mapped_column(JSONDict, nullable=False, default=list)


class ScheduleVersionRow(Base, TimestampMixin):
    __tablename__ = "schedule_versions"

    schedule_version_id: Mapped[str] = mapped_column(IdString, primary_key=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(CodeString, nullable=False, index=True)
    label: Mapped[str | None] = mapped_column(NameString)
    run_id: Mapped[str | None] = mapped_column(
        IdString, ForeignKey("optimization_runs.run_id", ondelete="SET NULL"), index=True
    )
    input_snapshot_id: Mapped[str | None] = mapped_column(
        IdString, ForeignKey("input_snapshots.snapshot_id", ondelete="SET NULL")
    )
    algorithm: Mapped[str] = mapped_column(String(128), nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(64), nullable=False)
    profile_id: Mapped[str] = mapped_column(String(128), nullable=False)
    profile_version: Mapped[int] = mapped_column(Integer, nullable=False)
    config_version: Mapped[int] = mapped_column(Integer, nullable=False)
    generated_by: Mapped[str | None] = mapped_column(IdString)
    generated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    horizon_start: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    horizon_end: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    approved_by: Mapped[str | None] = mapped_column(IdString)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    published_by: Mapped[str | None] = mapped_column(IdString)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    superseded_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    entry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONDict, nullable=False, default=dict)
    quality: Mapped[dict[str, Any] | None] = mapped_column(JSONDict)
    unscheduled: Mapped[list[dict[str, Any]]] = mapped_column(JSONDict, nullable=False, default=list)
    warnings: Mapped[list[str]] = mapped_column(JSONDict, nullable=False, default=list)
    notes: Mapped[str | None] = mapped_column(Text)

    entries: Mapped[list[ScheduleEntryRow]] = relationship(
        back_populates="version",
        cascade="all, delete-orphan",
        order_by="(ScheduleEntryRow.machine_id, ScheduleEntryRow.sequence_on_machine)",
        lazy="select",
    )


class ScheduleEntryRow(Base, TimestampMixin):
    __tablename__ = "schedule_entries"
    __table_args__ = (
        UniqueConstraint("schedule_version_id", "entry_id", name="uq_schedule_entries_version_entry"),
        Index("ix_schedule_entries_version_machine_start", "schedule_version_id", "machine_id", "start"),
        Index("ix_schedule_entries_version_order", "schedule_version_id", "order_id"),
    )

    row_id: Mapped[str] = mapped_column(IdString, primary_key=True)
    schedule_version_id: Mapped[str] = mapped_column(
        IdString,
        ForeignKey("schedule_versions.schedule_version_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    entry_id: Mapped[str] = mapped_column(IdString, nullable=False)
    machine_id: Mapped[str] = mapped_column(IdString, nullable=False, index=True)
    order_id: Mapped[str] = mapped_column(IdString, nullable=False, index=True)
    operation_id: Mapped[str] = mapped_column(IdString, nullable=False)
    sequence_on_machine: Mapped[int] = mapped_column(Integer, nullable=False)
    setup_start: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    start: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    end: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    setup_minutes: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    run_minutes: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    quantity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    priority_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    placement_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_last_operation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    expected_completion: Mapped[datetime | None] = mapped_column(UTCDateTime)
    due_date: Mapped[datetime | None] = mapped_column(UTCDateTime)
    expected_lateness_hours: Mapped[float | None] = mapped_column(Float)
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    batch_key: Mapped[str | None] = mapped_column(String(128))
    setup_family: Mapped[str | None] = mapped_column(String(128))
    material_id: Mapped[str | None] = mapped_column(IdString)
    customer_id: Mapped[str | None] = mapped_column(IdString)

    version: Mapped[ScheduleVersionRow] = relationship(back_populates="entries")


class ScheduleLockRow(Base, TimestampMixin):
    __tablename__ = "schedule_locks"

    lock_id: Mapped[str] = mapped_column(IdString, primary_key=True)
    lock_type: Mapped[str] = mapped_column(CodeString, nullable=False, index=True)
    order_id: Mapped[str | None] = mapped_column(IdString, index=True)
    machine_id: Mapped[str | None] = mapped_column(IdString, index=True)
    window_start: Mapped[datetime | None] = mapped_column(UTCDateTime)
    window_end: Mapped[datetime | None] = mapped_column(UTCDateTime)
    window_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sequence_order_ids: Mapped[list[str]] = mapped_column(JSONDict, nullable=False, default=list)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[str] = mapped_column(IdString, nullable=False)
    lock_created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    released_by: Mapped[str | None] = mapped_column(IdString)
    released_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


__all__ = ["OptimizationRunRow", "ScheduleEntryRow", "ScheduleLockRow", "ScheduleVersionRow"]
