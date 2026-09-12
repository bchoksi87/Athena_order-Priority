"""ORM: ``users``."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, CodeString, IdString, NameString, TimestampMixin, UTCDateTime


class UserRow(Base, TimestampMixin):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(IdString, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(CodeString, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(NameString, nullable=False)
    email: Mapped[str | None] = mapped_column(NameString)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


__all__ = ["UserRow"]
