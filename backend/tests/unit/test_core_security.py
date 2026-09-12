"""Password hashing, JWT and role checks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.errors import AuthenticationError, AuthorizationError, ValidationError
from app.core.security import (
    MAX_PASSWORD_BYTES,
    CurrentUser,
    create_access_token,
    decode_access_token,
    ensure_min_role,
    ensure_read_access,
    has_min_role,
    has_read_access,
    hash_password,
    verify_password,
)
from app.domain.enums import ROLE_RANK, Role

pytestmark = pytest.mark.unit

SECRET = "test-secret"
NOW = datetime(2026, 9, 11, 8, 0, tzinfo=UTC)


def _user(role: Role = Role.PLANNER) -> CurrentUser:
    return CurrentUser("usr_1", "alice", role, "Alice")


# ------------------------------------------------------------- passwords


def test_hash_and_verify_roundtrip() -> None:
    hashed = hash_password("correct horse")
    assert hashed != "correct horse"
    assert hashed.startswith("$2")
    assert verify_password("correct horse", hashed)
    assert not verify_password("wrong", hashed)


def test_hashes_are_salted() -> None:
    assert hash_password("pw") != hash_password("pw")


def test_password_validation() -> None:
    with pytest.raises(ValidationError):
        hash_password("")
    with pytest.raises(ValidationError):
        hash_password("x" * (MAX_PASSWORD_BYTES + 1))
    assert not verify_password("", hash_password("a"))
    assert not verify_password("a", "")
    assert not verify_password("a", "not-a-bcrypt-hash")


# ------------------------------------------------------------------- JWT


def test_token_roundtrip() -> None:
    user = _user(Role.PRODUCTION_MANAGER)
    token = create_access_token(user, secret=SECRET, algorithm="HS256", expires_minutes=30, now=NOW)
    payload = decode_access_token(token, secret=SECRET, algorithm="HS256", now=NOW + timedelta(minutes=5))
    assert payload.user == user
    assert payload.issued_at == NOW
    assert payload.expires_at == NOW + timedelta(minutes=30)


def test_expired_token_rejected_with_frozen_clock() -> None:
    token = create_access_token(_user(), secret=SECRET, algorithm="HS256", expires_minutes=10, now=NOW)
    with pytest.raises(AuthenticationError, match="expired"):
        decode_access_token(token, secret=SECRET, algorithm="HS256", now=NOW + timedelta(minutes=11))


def test_expired_token_rejected_with_wall_clock() -> None:
    long_ago = datetime.now(tz=UTC) - timedelta(days=2)
    token = create_access_token(_user(), secret=SECRET, algorithm="HS256", expires_minutes=10, now=long_ago)
    with pytest.raises(AuthenticationError, match="expired"):
        decode_access_token(token, secret=SECRET, algorithm="HS256")


def test_wrong_secret_and_garbage_rejected() -> None:
    token = create_access_token(_user(), secret=SECRET, algorithm="HS256", expires_minutes=10, now=NOW)
    with pytest.raises(AuthenticationError, match="invalid"):
        decode_access_token(token, secret="other", algorithm="HS256", now=NOW)
    with pytest.raises(AuthenticationError):
        decode_access_token("not.a.token", secret=SECRET, algorithm="HS256", now=NOW)


def test_unknown_role_and_wrong_type_rejected() -> None:
    claims = {
        "sub": "u",
        "role": "god",
        "type": "access",
        "iat": int(NOW.timestamp()),
        "exp": int(NOW.timestamp()) + 600,
    }
    bad_role = jwt.encode(claims, SECRET, algorithm="HS256")
    with pytest.raises(AuthenticationError, match="role"):
        decode_access_token(bad_role, secret=SECRET, algorithm="HS256", now=NOW)
    refresh = jwt.encode({**claims, "role": "planner", "type": "refresh"}, SECRET, algorithm="HS256")
    with pytest.raises(AuthenticationError, match="type"):
        decode_access_token(refresh, secret=SECRET, algorithm="HS256", now=NOW)


def test_missing_required_claims_rejected() -> None:
    token = jwt.encode({"sub": "u", "role": "planner", "type": "access"}, SECRET, algorithm="HS256")
    with pytest.raises(AuthenticationError):
        decode_access_token(token, secret=SECRET, algorithm="HS256", now=NOW)


def test_naive_issue_time_rejected() -> None:
    with pytest.raises(ValidationError):
        create_access_token(
            _user(), secret=SECRET, algorithm="HS256", expires_minutes=1, now=datetime(2026, 1, 1)
        )


# ------------------------------------------------------------------ roles


@pytest.mark.parametrize("role", list(Role))
def test_has_min_role_follows_rank(role: Role) -> None:
    user = _user(role)
    for required in Role:
        assert has_min_role(user, required) == (ROLE_RANK[role] >= ROLE_RANK[required])


def test_executive_gets_read_access_but_no_write() -> None:
    exec_user = _user(Role.EXECUTIVE)
    assert has_read_access(exec_user, Role.PLANNER)
    assert has_read_access(exec_user, Role.ADMIN)
    assert not has_min_role(exec_user, Role.OPERATOR)
    ensure_read_access(exec_user, Role.PRODUCTION_MANAGER)
    with pytest.raises(AuthorizationError):
        ensure_min_role(exec_user, Role.OPERATOR)


def test_operator_cannot_read_planner_resources() -> None:
    op = _user(Role.OPERATOR)
    assert not has_read_access(op, Role.PLANNER)
    with pytest.raises(AuthorizationError) as exc:
        ensure_read_access(op, Role.PLANNER)
    assert exc.value.details == {"required_role": "planner", "actual_role": "operator"}
    assert exc.value.status_code == 403


def test_admin_passes_everything() -> None:
    admin = _user(Role.ADMIN)
    for role in Role:
        ensure_min_role(admin, role)
        ensure_read_access(admin, role)
    assert admin.rank == ROLE_RANK[Role.ADMIN]
    assert admin.to_dict()["role"] == "admin"
