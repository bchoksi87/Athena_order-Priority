"""ORM: versioned configuration tables.

``system_configs`` holds the full :class:`app.domain.config.SystemConfig` JSON
per version (exactly one row is active). ``priority_profiles`` and
``scheduling_configs`` hold the corresponding sub-documents of each version so
that a priority result or schedule can be traced to the rules that produced
it by ``(profile_id, version)`` / ``(config_id, version)``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, IdString, JSONDict, NameString, TimestampMixin


class SystemConfigRow(Base, TimestampMixin):
    __tablename__ = "system_configs"

    config_id: Mapped[str] = mapped_column(IdString, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONDict, nullable=False)
    created_by: Mapped[str | None] = mapped_column(IdString)
    reason: Mapped[str | None] = mapped_column(Text)


class PriorityProfileRow(Base, TimestampMixin):
    __tablename__ = "priority_profiles"
    __table_args__ = (UniqueConstraint("profile_id", "version", name="uq_priority_profiles_id_version"),)

    row_id: Mapped[str] = mapped_column(IdString, primary_key=True)
    profile_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(NameString, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONDict, nullable=False)
    system_config_version: Mapped[int | None] = mapped_column(Integer, index=True)
    created_by: Mapped[str | None] = mapped_column(IdString)


class SchedulingConfigRow(Base, TimestampMixin):
    __tablename__ = "scheduling_configs"
    __table_args__ = (UniqueConstraint("config_id", "version", name="uq_scheduling_configs_id_version"),)

    row_id: Mapped[str] = mapped_column(IdString, primary_key=True)
    config_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(NameString, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONDict, nullable=False)
    system_config_version: Mapped[int | None] = mapped_column(Integer, index=True)
    created_by: Mapped[str | None] = mapped_column(IdString)


__all__ = ["PriorityProfileRow", "SchedulingConfigRow", "SystemConfigRow"]
