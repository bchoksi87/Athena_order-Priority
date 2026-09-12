"""FastAPI dependencies: settings, DB session, clock, current user and role guards.

Other routers import from here only; nothing here holds module-level state
(everything hangs off ``request.app.state`` which ``create_app`` populates).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.clock import Clock, SystemClock
from app.core.config import Settings, get_settings
from app.core.db import get_session
from app.core.errors import AuthenticationError
from app.core.logging import bind_request_context
from app.core.security import CurrentUser, decode_access_token, ensure_min_role, ensure_read_access
from app.domain.enums import Role

_bearer = HTTPBearer(auto_error=False)


def get_app_settings(request: Request) -> Settings:
    """Settings attached to the app at start-up (falls back to the cached global)."""
    settings: Settings | None = getattr(request.app.state, "settings", None)
    return settings or get_settings()


def get_clock(request: Request) -> Clock:
    clock: Clock | None = getattr(request.app.state, "clock", None)
    return clock or SystemClock()


def get_db(request: Request) -> Iterator[Session]:
    yield from get_session(request)


def get_request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> CurrentUser:
    """Validate the bearer token and return the principal; binds ``user_id`` to logs."""
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise AuthenticationError("missing bearer token")
    payload = decode_access_token(
        credentials.credentials,
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        now=clock.now(),
    )
    user = payload.user
    request_id = getattr(request.state, "request_id", None)
    if request_id:
        bind_request_context(request_id, user_id=user.user_id, role=user.role.value)
    return user


def require_min_role(role: Role) -> Callable[..., CurrentUser]:
    """Dependency factory: the caller must hold ``role`` or a higher one."""

    def _guard(user: Annotated[CurrentUser, Depends(get_current_user)]) -> CurrentUser:
        ensure_min_role(user, role)
        return user

    return _guard


def require_read_access(min_role: Role) -> Callable[..., CurrentUser]:
    """Dependency factory for read endpoints: ``min_role``+ **or** the executive role."""

    def _guard(user: Annotated[CurrentUser, Depends(get_current_user)]) -> CurrentUser:
        ensure_read_access(user, min_role)
        return user

    return _guard


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
SessionDep = Annotated[Session, Depends(get_db)]
ClockDep = Annotated[Clock, Depends(get_clock)]
CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]

__all__ = [
    "ClockDep",
    "CurrentUserDep",
    "SessionDep",
    "SettingsDep",
    "get_app_settings",
    "get_clock",
    "get_current_user",
    "get_db",
    "get_request_id",
    "require_min_role",
    "require_read_access",
]
