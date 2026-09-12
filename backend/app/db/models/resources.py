"""ORM: ``materials`` and ``tooling``."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, IdString, JSONDict, NameString, TimestampMixin, UTCDateTime


class MaterialRow(Base, TimestampMixin):
    __tablename__ = "materials"

    material_id: Mapped[str] = mapped_column(IdString, primary_key=True)
    material_name: Mapped[str] = mapped_column(NameString, nullable=False)
    material_type: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    grade: Mapped[str | None] = mapped_column(String(128))
    supplier: Mapped[str | None] = mapped_column(NameString)
    unit: Mapped[str] = mapped_column(String(32), nullable=False, default="kg")
    available_quantity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    reserved_quantity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    incoming_quantity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    expected_receipt_date: Mapped[datetime | None] = mapped_column(UTCDateTime)
    minimum_stock: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    compatible_machine_ids: Mapped[list[str]] = mapped_column(JSONDict, nullable=False, default=list)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONDict, nullable=False, default=dict)
    synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class ToolingRow(Base, TimestampMixin):
    __tablename__ = "tooling"

    tooling_id: Mapped[str] = mapped_column(IdString, primary_key=True)
    tooling_name: Mapped[str] = mapped_column(NameString, nullable=False)
    available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    available_from: Mapped[datetime | None] = mapped_column(UTCDateTime)
    compatible_machine_ids: Mapped[list[str]] = mapped_column(JSONDict, nullable=False, default=list)
    setup_minutes: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    expected_life: Mapped[float | None] = mapped_column(Float)
    current_usage: Mapped[float | None] = mapped_column(Float)
    maintenance_status: Mapped[str] = mapped_column(String(64), nullable=False, default="ok")
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONDict, nullable=False, default=dict)
    synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


__all__ = ["MaterialRow", "ToolingRow"]
