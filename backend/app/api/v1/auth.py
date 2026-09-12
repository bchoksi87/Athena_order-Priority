"""``POST /auth/login`` and ``GET /auth/me``."""

from __future__ import annotations

import structlog
from fastapi import APIRouter

from app.api.deps import ClockDep, CurrentUserDep, SessionDep, SettingsDep
from app.api.schemas.auth import LoginRequest, TokenResponse, UserInfo
from app.core.errors import AuthenticationError
from app.core.security import CurrentUser, create_access_token
from app.db.repositories.users import UserRepository

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse, summary="Exchange credentials for a JWT")
def login(body: LoginRequest, session: SessionDep, settings: SettingsDep, clock: ClockDep) -> TokenResponse:
    repo = UserRepository(session)
    record = repo.authenticate(body.username, body.password)
    if record is None:
        log.info("login_failed", username=body.username)
        raise AuthenticationError("invalid username or password")
    now = clock.now()
    repo.record_login(record.user_id, now)
    user = CurrentUser(
        user_id=record.user_id,
        username=record.username,
        role=record.role,
        display_name=record.display_name,
    )
    token = create_access_token(
        user,
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        expires_minutes=settings.jwt_expire_minutes,
        now=now,
    )
    log.info("login_succeeded", user_id=user.user_id, role=user.role.value)
    return TokenResponse(
        access_token=token,
        expires_in=settings.jwt_expire_minutes * 60,
        role=user.role,
        user=UserInfo(**user.to_dict()),
    )


@router.get("/me", response_model=UserInfo, summary="The authenticated principal")
def me(user: CurrentUserDep) -> UserInfo:
    return UserInfo(**user.to_dict())


__all__ = ["router"]
