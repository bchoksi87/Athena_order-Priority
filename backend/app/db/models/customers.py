"""ORM: ``customers`` and ``customer_rules``."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import (
    Base,
    CodeString,
    IdString,
    JSONDict,
    NameString,
    TimestampMixin,
    UTCDateTime,
)


class CustomerRow(Base, TimestampMixin):
    __tablename__ = "customers"

    customer_id: Mapped[str] = mapped_column(IdString, primary_key=True)
    customer_name: Mapped[str] = mapped_column(NameString, nullable=False)
    customer_category: Mapped[str] = mapped_column(CodeString, nullable=False, default="standard")
    customer_tier: Mapped[str] = mapped_column(CodeString, nullable=False, default="standard", index=True)
    customer_priority: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    strategic_customer_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    annual_revenue: Mapped[float | None] = mapped_column(Float)
    customer_revenue: Mapped[float | None] = mapped_column(Float)
    customer_profitability: Mapped[float | None] = mapped_column(Float)
    customer_service_level: Mapped[float | None] = mapped_column(Float)
    sla_hours: Mapped[float | None] = mapped_column(Float)
    escalation_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    historical_on_time_delivery: Mapped[float | None] = mapped_column(Float)
    payment_risk: Mapped[str] = mapped_column(CodeString, nullable=False, default="unknown")
    preferred_delivery_expectation: Mapped[str | None] = mapped_column(NameString)
    account_manager: Mapped[str | None] = mapped_column(NameString)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    external_ref: Mapped[str | None] = mapped_column(String(128))
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONDict, nullable=False, default=dict)
    synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class CustomerRuleRow(Base, TimestampMixin):
    """Planner-controlled per-customer rule (one row per customer)."""

    __tablename__ = "customer_rules"

    customer_id: Mapped[str] = mapped_column(
        IdString, ForeignKey("customers.customer_id", ondelete="CASCADE"), primary_key=True
    )
    sla_hours: Mapped[float | None] = mapped_column(Float)
    tier_override: Mapped[str | None] = mapped_column(CodeString)
    priority_boost_points: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    notes: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_by: Mapped[str | None] = mapped_column(IdString)
    last_changed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


__all__ = ["CustomerRow", "CustomerRuleRow"]
