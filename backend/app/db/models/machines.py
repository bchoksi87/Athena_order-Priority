"""ORM: ``machines``, ``machine_downtime`` and ``calendar_specs``."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text
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

#: ``machine_downtime.kind`` values, mirroring the three lists on ``Machine``.
DOWNTIME_KIND_MAINTENANCE = "maintenance"
DOWNTIME_KIND_PLANNED = "planned"
DOWNTIME_KIND_UNPLANNED = "unplanned"
DOWNTIME_KINDS: tuple[str, ...] = (DOWNTIME_KIND_MAINTENANCE, DOWNTIME_KIND_PLANNED, DOWNTIME_KIND_UNPLANNED)


class MachineRow(Base, TimestampMixin):
    __tablename__ = "machines"
    __table_args__ = (Index("ix_machines_group_rank", "machine_group", "preferred_rank"),)

    machine_id: Mapped[str] = mapped_column(IdString, primary_key=True)
    machine_name: Mapped[str] = mapped_column(NameString, nullable=False)
    machine_type: Mapped[str] = mapped_column(String(128), nullable=False)
    process_type: Mapped[str] = mapped_column(CodeString, nullable=False, index=True)
    machine_group: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    location: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(CodeString, nullable=False, default="available", index=True)
    calendar_id: Mapped[str | None] = mapped_column(IdString)
    efficiency: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    utilization: Mapped[float | None] = mapped_column(Float)
    capacity_hours_per_day: Mapped[float | None] = mapped_column(Float)
    compatible_materials: Mapped[list[str]] = mapped_column(JSONDict, nullable=False, default=list)
    compatible_processes: Mapped[list[str]] = mapped_column(JSONDict, nullable=False, default=list)
    max_part_size_mm: Mapped[list[float] | None] = mapped_column(JSONDict)
    tooling_configuration: Mapped[list[str]] = mapped_column(JSONDict, nullable=False, default=list)
    setup_requirements: Mapped[dict[str, Any]] = mapped_column(JSONDict, nullable=False, default=dict)
    current_material_id: Mapped[str | None] = mapped_column(IdString)
    current_setup_family: Mapped[str | None] = mapped_column(String(128))
    available_from: Mapped[datetime | None] = mapped_column(UTCDateTime)
    preferred_rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONDict, nullable=False, default=dict)
    synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    downtime: Mapped[list[MachineDowntimeRow]] = relationship(
        back_populates="machine",
        cascade="all, delete-orphan",
        order_by="MachineDowntimeRow.start",
        lazy="select",
    )


class MachineDowntimeRow(Base, TimestampMixin):
    __tablename__ = "machine_downtime"
    __table_args__ = (Index("ix_machine_downtime_machine_start", "machine_id", "start"),)

    downtime_id: Mapped[str] = mapped_column(IdString, primary_key=True)
    machine_id: Mapped[str] = mapped_column(
        IdString, ForeignKey("machines.machine_id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(CodeString, nullable=False)
    start: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    end: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")

    machine: Mapped[MachineRow] = relationship(back_populates="downtime")


class CalendarSpecRow(Base, TimestampMixin):
    """Working-time definition. Shifts/holidays/windows are JSON lists (see mappers)."""

    __tablename__ = "calendar_specs"

    calendar_id: Mapped[str] = mapped_column(IdString, primary_key=True)
    name: Mapped[str] = mapped_column(NameString, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    shifts: Mapped[list[dict[str, Any]]] = mapped_column(JSONDict, nullable=False, default=list)
    holidays: Mapped[list[str]] = mapped_column(JSONDict, nullable=False, default=list)
    overtime_windows: Mapped[list[dict[str, Any]]] = mapped_column(JSONDict, nullable=False, default=list)
    extra_working_days: Mapped[list[str]] = mapped_column(JSONDict, nullable=False, default=list)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)


__all__ = [
    "DOWNTIME_KINDS",
    "DOWNTIME_KIND_MAINTENANCE",
    "DOWNTIME_KIND_PLANNED",
    "DOWNTIME_KIND_UNPLANNED",
    "CalendarSpecRow",
    "MachineDowntimeRow",
    "MachineRow",
]
