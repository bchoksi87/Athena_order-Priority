"""ORM: ``priority_results`` (one row per order per priority run)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, CodeString, IdString, JSONDict, TimestampMixin, UTCDateTime


class PriorityResultRow(Base, TimestampMixin):
    __tablename__ = "priority_results"
    __table_args__ = (
        UniqueConstraint("run_id", "order_id", name="uq_priority_results_run_order"),
        Index("ix_priority_results_run_rank", "run_id", "rank"),
        Index("ix_priority_results_order_computed", "order_id", "computed_at"),
    )

    result_id: Mapped[str] = mapped_column(IdString, primary_key=True)
    run_id: Mapped[str] = mapped_column(IdString, nullable=False, index=True)
    order_id: Mapped[str] = mapped_column(IdString, nullable=False, index=True)
    score: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    base_score: Mapped[float] = mapped_column(Float, nullable=False)
    factors: Mapped[list[dict[str, Any]]] = mapped_column(JSONDict, nullable=False, default=list)
    adjustments: Mapped[list[dict[str, Any]]] = mapped_column(JSONDict, nullable=False, default=list)
    readiness: Mapped[str] = mapped_column(CodeString, nullable=False, index=True)
    blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    blocking_reasons: Mapped[list[str]] = mapped_column(JSONDict, nullable=False, default=list)
    risk_level: Mapped[str] = mapped_column(CodeString, nullable=False, index=True)
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    profile_id: Mapped[str] = mapped_column(String(128), nullable=False)
    profile_version: Mapped[int] = mapped_column(Integer, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    hours_until_due: Mapped[float | None] = mapped_column(Float)
    projected_completion: Mapped[datetime | None] = mapped_column(UTCDateTime)
    projected_lateness_hours: Mapped[float | None] = mapped_column(Float)
    forced_next: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rank: Mapped[int | None] = mapped_column(Integer)


__all__ = ["PriorityResultRow"]
