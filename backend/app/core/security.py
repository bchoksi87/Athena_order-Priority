"""Password hashing, JWT issuance/verification and role checks.

Pure functions plus a small :class:`CurrentUser` value object. FastAPI wiring
(dependencies that read the bearer header) lives in :mod:`app.api.deps`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

from app.core.errors import AuthenticationError, AuthorizationError, ValidationError
from app.domain.enums import ROLE_RANK, Role

#: bcrypt silently truncated passwords beyond this length historically and raises since 5.0.
MAX_PASSWORD_BYTES = 72
TOKEN_TYPE = "access"


@dataclass(slots=True, frozen=True)
class CurrentUser:
    """The authenticated principal attached to a request."""

    user_id: str
    username: str
    role: Role
    display_name: str

    @property
    def rank(self) -> int:
        return ROLE_RANK[self.role]

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "role": self.role.value,
            "display_name": self.display_name,
        }


@dataclass(slots=True, frozen=True)
class TokenPayload:
    """Decoded, validated JWT claims."""

    user: CurrentUser
    issued_at: datetime
    expires_at: datetime


# ---------------------------------------------------------------- passwords


def hash_password(password: str) -> str:
    """Return a bcrypt hash (utf-8 string) for ``password``."""
    raw = password.encode("utf-8")
    if not raw:
        raise ValidationError("password must not be empty")
    if len(raw) > MAX_PASSWORD_BYTES:
        raise ValidationError(f"password must be at most {MAX_PASSWORD_BYTES} bytes")
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time comparison of ``password`` against a stored bcrypt hash."""
    raw = password.encode("utf-8")
    if not raw or len(raw) > MAX_PASSWORD_BYTES or not password_hash:
        return False
    try:
        return bcrypt.checkpw(raw, password_hash.encode("utf-8"))
    except ValueError:
        return False


# --------------------------------------------------------------------- JWT


def create_access_token(
    user: CurrentUser,
    *,
    secret: str,
    algorithm: str,
    expires_minutes: int,
    now: datetime | None = None,
) -> str:
    """Issue a signed JWT carrying the user's identity and role."""
    issued = now or datetime.now(tz=UTC)
    if issued.tzinfo is None:
        raise ValidationError("token issue time must be timezone-aware")
    expires = issued + timedelta(minutes=expires_minutes)
    claims: dict[str, Any] = {
        "sub": user.user_id,
        "username": user.username,
        "role": user.role.value,
        "name": user.display_name,
        "type": TOKEN_TYPE,
        "iat": int(issued.timestamp()),
        "exp": int(expires.timestamp()),
    }
    return jwt.encode(claims, secret, algorithm=algorithm)


def decode_access_token(
    token: str,
    *,
    secret: str,
    algorithm: str,
    now: datetime | None = None,
) -> TokenPayload:
    """Verify signature and expiry and return the embedded principal.

    Raises :class:`AuthenticationError` on any problem (expired, malformed,
    wrong signature, unknown role).
    """
    kwargs: dict[str, Any] = {"options": {"require": ["exp", "iat", "sub"]}}
    if now is not None:
        # PyJWT validates against wall-clock time; emulate a frozen clock via leeway.
        skew = (datetime.now(tz=UTC) - now).total_seconds()
        kwargs["leeway"] = max(0.0, skew)
    try:
        claims = jwt.decode(token, secret, algorithms=[algorithm], **kwargs)
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("token has expired") from exc
    except jwt.PyJWTError as exc:
        raise AuthenticationError("invalid token") from exc

    if claims.get("type") != TOKEN_TYPE:
        raise AuthenticationError("invalid token type")
    try:
        role = Role(str(claims.get("role")))
    except ValueError as exc:
        raise AuthenticationError("token carries an unknown role") from exc

    exp = datetime.fromtimestamp(int(claims["exp"]), tz=UTC)
    if now is not None and now >= exp:
        raise AuthenticationError("token has expired")

    user = CurrentUser(
        user_id=str(claims["sub"]),
        username=str(claims.get("username", "")),
        role=role,
        display_name=str(claims.get("name", "")),
    )
    return TokenPayload(
        user=user,
        issued_at=datetime.fromtimestamp(int(claims["iat"]), tz=UTC),
        expires_at=exp,
    )


# ------------------------------------------------------------------- roles


def has_min_role(user: CurrentUser, role: Role) -> bool:
    """True when ``user`` ranks at or above ``role`` in :data:`ROLE_RANK`."""
    return ROLE_RANK[user.role] >= ROLE_RANK[role]


def has_read_access(user: CurrentUser, min_role: Role) -> bool:
    """Read endpoints: ``min_role`` or higher, or the read-only executive role."""
    return user.role == Role.EXECUTIVE or has_min_role(user, min_role)


def ensure_min_role(user: CurrentUser, role: Role) -> None:
    if not has_min_role(user, role):
        raise AuthorizationError(
            f"role '{user.role.value}' is not allowed; requires '{role.value}' or higher",
            details={"required_role": role.value, "actual_role": user.role.value},
        )


def ensure_read_access(user: CurrentUser, min_role: Role) -> None:
    if not has_read_access(user, min_role):
        raise AuthorizationError(
            f"role '{user.role.value}' may not read this resource; requires '{min_role.value}' or higher",
            details={"required_role": min_role.value, "actual_role": user.role.value},
        )


__all__ = [
    "MAX_PASSWORD_BYTES",
    "CurrentUser",
    "TokenPayload",
    "create_access_token",
    "decode_access_token",
    "ensure_min_role",
    "ensure_read_access",
    "has_min_role",
    "has_read_access",
    "hash_password",
    "verify_password",
]
