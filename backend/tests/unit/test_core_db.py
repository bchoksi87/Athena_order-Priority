"""Engine factory, UTCDateTime, session scope and the request-id middleware."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.db import (
    SQLITE_MEMORY_URL,
    create_all,
    create_engine_from_url,
    create_session_factory,
    is_sqlite_url,
    ping,
    session_scope,
)
from app.core.errors import ConfigurationError
from app.core.logging import REQUEST_ID_HEADER, RequestIdMiddleware, configure_logging, get_logger
from app.db.models import UserRow

pytestmark = pytest.mark.unit


def test_sqlite_memory_engine_uses_static_pool() -> None:
    engine = create_engine_from_url(SQLITE_MEMORY_URL)
    assert isinstance(engine.pool, StaticPool)
    assert is_sqlite_url(SQLITE_MEMORY_URL)
    assert not is_sqlite_url("postgresql+psycopg://x")
    assert ping(engine)
    engine.dispose()


def test_empty_url_rejected() -> None:
    with pytest.raises(ConfigurationError):
        create_engine_from_url("")


def test_ping_reports_failure() -> None:
    engine = create_engine_from_url("postgresql+psycopg://nobody:nothing@127.0.0.1:1/none")
    assert ping(engine) is False
    engine.dispose()


def test_utc_datetime_roundtrips_aware_and_rejects_naive(engine, session: Session) -> None:
    row = UserRow(user_id="u1", username="u1", password_hash="x", role="admin", display_name="U")
    row.last_login_at = datetime(2026, 9, 11, 13, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    session.add(row)
    session.flush()
    session.expire_all()
    loaded = session.get(UserRow, "u1")
    assert loaded is not None
    assert loaded.last_login_at == datetime(2026, 9, 11, 8, 0, tzinfo=UTC)
    assert loaded.last_login_at.tzinfo is UTC
    assert loaded.created_at.tzinfo is not None and loaded.updated_at.tzinfo is not None

    bad = UserRow(user_id="u2", username="u2", password_hash="x", role="admin", display_name="U")
    bad.last_login_at = datetime(2026, 9, 11)
    session.add(bad)
    with pytest.raises(StatementError):
        session.flush()
    session.rollback()


def test_session_scope_commits_and_rolls_back() -> None:
    engine = create_engine_from_url(SQLITE_MEMORY_URL)
    create_all(engine)
    factory = create_session_factory(engine)
    with session_scope(factory) as s:
        s.add(UserRow(user_id="a", username="a", password_hash="x", role="admin", display_name="A"))
    with pytest.raises(RuntimeError), session_scope(factory) as s:
        s.add(UserRow(user_id="b", username="b", password_hash="x", role="admin", display_name="B"))
        s.flush()
        raise RuntimeError("boom")
    with factory() as s:
        assert [r.user_id for r in s.execute(select(UserRow)).scalars()] == ["a"]
    engine.dispose()


def test_sqlite_enforces_foreign_keys(session: Session) -> None:
    from sqlalchemy.exc import IntegrityError

    from app.db.models import OperationRow

    session.add(
        OperationRow(operation_id="op", order_id="missing-order", sequence=1, operation_type="cnc_machining")
    )
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


# ---------------------------------------------------------------- logging


def test_configure_logging_is_idempotent() -> None:
    configure_logging("DEBUG", json_output=True)
    configure_logging("nonsense-level", json_output=False)
    log = get_logger("tests")
    log.info("hello", answer=42)


def test_request_id_middleware_echoes_or_generates() -> None:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    @app.get("/x")
    def x() -> dict[str, str]:
        return {"ok": "yes"}

    client = TestClient(app)
    given = client.get("/x", headers={REQUEST_ID_HEADER: "abc-123"})
    assert given.headers[REQUEST_ID_HEADER] == "abc-123"
    generated = client.get("/x")
    assert generated.headers[REQUEST_ID_HEADER].startswith("req_")
