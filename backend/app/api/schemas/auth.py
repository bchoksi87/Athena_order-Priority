"""Authentication request/response schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import Role


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class UserInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str
    username: str
    role: Role
    display_name: str


class TokenResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Seconds until the token expires")
    role: Role
    user: UserInfo


__all__ = ["LoginRequest", "TokenResponse", "UserInfo"]
