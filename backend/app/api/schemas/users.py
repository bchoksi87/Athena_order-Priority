"""User administration schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.api.schemas.common import ReasonBody
from app.db.records import UserRecord
from app.domain.enums import Role


class UserResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str
    username: str
    role: Role
    display_name: str
    email: str | None = None
    active: bool
    last_login_at: datetime | None = None
    created_at: datetime | None = None

    @classmethod
    def from_record(cls, u: UserRecord) -> UserResponse:
        return cls(
            user_id=u.user_id,
            username=u.username,
            role=u.role,
            display_name=u.display_name,
            email=u.email,
            active=u.active,
            last_login_at=u.last_login_at,
            created_at=u.created_at,
        )


class UserCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=72)
    role: Role
    display_name: str = Field(min_length=1, max_length=255)
    email: str | None = Field(default=None, max_length=255)
    reason: str | None = Field(default=None, max_length=2000)


class UserUpdateRequest(ReasonBody):
    active: bool = Field(description="False deactivates the account (login refused), True re-activates it")


class ResetPasswordRequest(ReasonBody):
    password: str = Field(min_length=8, max_length=72)


__all__ = ["ResetPasswordRequest", "UserCreateRequest", "UserResponse", "UserUpdateRequest"]
