"""ORM: ``orders`` (one row per order line) and ``operations``."""

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


class OrderRow(Base, TimestampMixin):
    __tablename__ = "orders"
    __table_args__ = (
        Index("ix_orders_customer_status", "customer_id", "order_status"),
        Index("ix_orders_status_due", "order_status", "due_date"),
    )

    order_id: Mapped[str] = mapped_column(IdString, primary_key=True)
    customer_id: Mapped[str] = mapped_column(IdString, nullable=False, index=True)
    part_id: Mapped[str] = mapped_column(IdString, nullable=False, index=True)
    order_line_id: Mapped[str | None] = mapped_column(IdString)
    external_order_ref: Mapped[str | None] = mapped_column(String(128), index=True)
    part_name: Mapped[str | None] = mapped_column(NameString)
    part_family: Mapped[str | None] = mapped_column(String(128), index=True)
    order_date: Mapped[datetime | None] = mapped_column(UTCDateTime)
    received_date: Mapped[datetime | None] = mapped_column(UTCDateTime)
    requested_delivery_date: Mapped[datetime | None] = mapped_column(UTCDateTime)
    promised_delivery_date: Mapped[datetime | None] = mapped_column(UTCDateTime)
    revised_delivery_date: Mapped[datetime | None] = mapped_column(UTCDateTime)
    #: Effective due date (revised > promised > requested), denormalised for indexing.
    due_date: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    quantity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    completed_quantity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    cancelled_quantity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    order_status: Mapped[str] = mapped_column(CodeString, nullable=False, default="new", index=True)
    erp_priority: Mapped[int | None] = mapped_column(Integer)
    production_status: Mapped[str | None] = mapped_column(String(128), index=True)
    material_status: Mapped[str] = mapped_column(CodeString, nullable=False, default="unknown")
    quality_status: Mapped[str] = mapped_column(CodeString, nullable=False, default="none")
    shipping_status: Mapped[str] = mapped_column(CodeString, nullable=False, default="not_shipped")
    order_value: Mapped[float | None] = mapped_column(Float)
    estimated_cost: Mapped[float | None] = mapped_column(Float)
    estimated_margin: Mapped[float | None] = mapped_column(Float)
    actual_margin: Mapped[float | None] = mapped_column(Float)
    process_type: Mapped[str] = mapped_column(CodeString, nullable=False, default="other", index=True)
    manufacturing_route: Mapped[list[str]] = mapped_column(JSONDict, nullable=False, default=list)
    machine_group: Mapped[str | None] = mapped_column(String(128), index=True)
    required_machine_id: Mapped[str | None] = mapped_column(IdString, index=True)
    required_material_id: Mapped[str | None] = mapped_column(IdString, index=True)
    tooling_requirement: Mapped[list[str]] = mapped_column(JSONDict, nullable=False, default=list)
    estimated_setup_minutes: Mapped[float | None] = mapped_column(Float)
    estimated_cycle_minutes_per_unit: Mapped[float | None] = mapped_column(Float)
    estimated_total_production_minutes: Mapped[float | None] = mapped_column(Float)
    customer_priority: Mapped[int | None] = mapped_column(Integer)
    technical_priority: Mapped[int | None] = mapped_column(Integer)
    commercial_priority: Mapped[int | None] = mapped_column(Integer)
    lateness_penalty_per_day: Mapped[float | None] = mapped_column(Float)
    sla_hours: Mapped[float | None] = mapped_column(Float)
    special_instructions: Mapped[str | None] = mapped_column(Text)
    drawing_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    on_hold: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    hold_reason: Mapped[str | None] = mapped_column(Text)
    depends_on_order_ids: Mapped[list[str]] = mapped_column(JSONDict, nullable=False, default=list)
    surface_finish: Mapped[str | None] = mapped_column(String(128))
    technology: Mapped[str | None] = mapped_column(String(128))
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONDict, nullable=False, default=dict)
    #: Last time the ERP sync touched this row (None for locally created rows).
    synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    operations: Mapped[list[OperationRow]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="OperationRow.sequence",
        lazy="select",
    )


class OperationRow(Base, TimestampMixin):
    __tablename__ = "operations"
    __table_args__ = (Index("ix_operations_order_sequence", "order_id", "sequence"),)

    operation_id: Mapped[str] = mapped_column(IdString, primary_key=True)
    order_id: Mapped[str] = mapped_column(
        IdString, ForeignKey("orders.order_id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    operation_type: Mapped[str] = mapped_column(CodeString, nullable=False, index=True)
    machine_group: Mapped[str | None] = mapped_column(String(128), index=True)
    machine_id: Mapped[str | None] = mapped_column(IdString, index=True)
    eligible_machine_ids: Mapped[list[str]] = mapped_column(JSONDict, nullable=False, default=list)
    setup_minutes: Mapped[float | None] = mapped_column(Float)
    cycle_minutes_per_unit: Mapped[float | None] = mapped_column(Float)
    machine_cycle_minutes: Mapped[dict[str, float]] = mapped_column(JSONDict, nullable=False, default=dict)
    quantity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    completed_quantity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    operation_status: Mapped[str] = mapped_column(CodeString, nullable=False, default="pending", index=True)
    prerequisite_operation_id: Mapped[str | None] = mapped_column(IdString)
    material_id: Mapped[str | None] = mapped_column(IdString, index=True)
    material_quantity_per_unit: Mapped[float | None] = mapped_column(Float)
    tooling_ids: Mapped[list[str]] = mapped_column(JSONDict, nullable=False, default=list)
    operator_requirement: Mapped[str | None] = mapped_column(String(128))
    quality_requirement: Mapped[str | None] = mapped_column(String(128))
    setup_family: Mapped[str | None] = mapped_column(String(128))
    estimated_start: Mapped[datetime | None] = mapped_column(UTCDateTime)
    estimated_end: Mapped[datetime | None] = mapped_column(UTCDateTime)
    actual_start: Mapped[datetime | None] = mapped_column(UTCDateTime)
    actual_end: Mapped[datetime | None] = mapped_column(UTCDateTime)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONDict, nullable=False, default=dict)

    order: Mapped[OrderRow] = relationship(back_populates="operations")


__all__ = ["OperationRow", "OrderRow"]
