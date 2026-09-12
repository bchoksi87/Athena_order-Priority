"""SQLAlchemy engine/session plumbing shared by the app, Alembic and tests.

* :func:`create_engine_from_url` builds an engine for PostgreSQL or SQLite
  (SQLite in-memory gets a ``StaticPool`` so every session sees one database).
* :class:`Base` is the declarative base for every ORM model.
* :class:`UTCDateTime` guarantees timezone-aware UTC datetimes on both backends
  (SQLite drops tzinfo; PostgreSQL returns the connection's zone).
* :class:`TimestampMixin` adds ``created_at``/``updated_at`` with server defaults.
* :func:`get_session` is the FastAPI dependency (commit on success, rollback on error).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from sqlalchemy import DateTime, create_engine, event, func
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator

from app.core.errors import ConfigurationError

SQLITE_MEMORY_URL = "sqlite+pysqlite:///:memory:"


class UTCDateTime(TypeDecorator[datetime]):
    """``DateTime(timezone=True)`` that always yields aware UTC values.

    Naive inputs are rejected (the contract mandates aware datetimes); aware
    inputs are normalised to UTC before binding. Values read back from SQLite
    (which stores no zone) are re-tagged as UTC.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise TypeError(f"expected datetime, got {type(value).__name__}")
        if value.tzinfo is None:
            raise ValueError("naive datetime bound to UTCDateTime column; all datetimes must be aware UTC")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class Base(DeclarativeBase):
    """Declarative base for all ORM models (``Base.metadata`` drives Alembic)."""


class TimestampMixin:
    """``created_at``/``updated_at`` maintained by the database and the ORM."""

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, server_default=func.now(), default=lambda: datetime.now(tz=UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.now(tz=UTC),
        onupdate=lambda: datetime.now(tz=UTC),
    )


def is_sqlite_url(url: str) -> bool:
    return url.startswith("sqlite")


def create_engine_from_url(url: str, *, echo: bool = False) -> Engine:
    """Create an engine with backend-appropriate options.

    SQLite: ``check_same_thread=False`` (FastAPI may hand a session to another
    thread) and, for ``:memory:``, a :class:`StaticPool` so all sessions share the
    single in-memory database. Foreign keys are enforced to mirror PostgreSQL.
    """
    if not url:
        raise ConfigurationError("database URL is empty")
    kwargs: dict[str, Any] = {"echo": echo, "future": True}
    if is_sqlite_url(url):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url or url.rstrip("/") in ("sqlite://", "sqlite+pysqlite://"):
            kwargs["poolclass"] = StaticPool
    else:
        kwargs["pool_pre_ping"] = True
    engine = create_engine(url, **kwargs)
    if is_sqlite_url(url):

        @event.listens_for(engine, "connect")
        def _enable_sqlite_fks(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def create_all(engine: Engine) -> None:
    """Create every table from ``Base.metadata`` (tests only; production uses Alembic)."""
    import app.db.models  # noqa: F401 - ensure all models are registered on Base

    Base.metadata.create_all(engine)


def drop_all(engine: Engine) -> None:
    import app.db.models  # noqa: F401

    Base.metadata.drop_all(engine)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Unit-of-work: commit when the block completes, rollback on exception."""
    session = factory()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def get_session(request: Request) -> Iterator[Session]:
    """FastAPI dependency yielding a session bound to ``app.state.session_factory``."""
    factory: sessionmaker[Session] | None = getattr(request.app.state, "session_factory", None)
    if factory is None:
        raise ConfigurationError("database session factory is not initialised on the application")
    with session_scope(factory) as session:
        yield session


def ping(engine: Engine) -> bool:
    """Return True when a trivial query succeeds."""
    from sqlalchemy import text

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:  # health probe reports any failure as unhealthy
        return False


__all__ = [
    "SQLITE_MEMORY_URL",
    "Base",
    "TimestampMixin",
    "UTCDateTime",
    "create_all",
    "create_engine_from_url",
    "create_session_factory",
    "drop_all",
    "get_session",
    "is_sqlite_url",
    "ping",
    "session_scope",
]
