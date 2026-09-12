"""ORM: planner overlays ``priority_overrides`` and ``expedites``."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Float, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, CodeString, IdString, TimestampMixin, UTCDateTime


class PriorityOverrideRow(Base, TimestampMixin):
    __tablename__ = "priority_overrides"
    __table_args__ = (Index("ix_priority_overrides_order_active", "order_id", "active"),)

    override_id: Mapped[str] = mapped_column(IdString, primary_key=True)
    order_id: Mapped[str] = mapped_column(IdString, nullable=False, index=True)
    override_type: Mapped[str] = mapped_column(CodeString, nullable=False, index=True)
    value: Mapped[float | None] = mapped_column(Float)
    target_machine_id: Mapped[str | None] = mapped_column(IdString)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[str] = mapped_column(IdString, nullable=False)
    override_created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    released_by: Mapped[str | None] = mapped_column(IdString)
    released_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class ExpediteRow(Base, TimestampMixin):
    __tablename__ = "expedites"
    __table_args__ = (Index("ix_expedites_order_active", "order_id", "active"),)

    expedite_id: Mapped[str] = mapped_column(IdString, primary_key=True)
    order_id: Mapped[str] = mapped_column(IdString, nullable=False, index=True)
    boost_points: Mapped[float] = mapped_column(Float, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[str] = mapped_column(IdString, nullable=False)
    expedite_created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    released_by: Mapped[str | None] = mapped_column(IdString)
    released_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


__all__ = ["ExpediteRow", "PriorityOverrideRow"]
