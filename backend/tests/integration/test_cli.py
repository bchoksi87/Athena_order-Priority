"""``python -m app.cli`` run in-process against a temporary SQLite file.

``migrate`` is additionally exercised against PostgreSQL when ``PPSE_TEST_DATABASE_URL``
is set (the test database is already at head, so the upgrade is a no-op).
"""

from __future__ import annotations

import io
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

import pytest
from sqlalchemy import inspect, text

from app.cli import EXIT_ERROR, EXIT_OK, EXIT_USAGE, main
from app.core.clock import FrozenClock
from app.core.config import Settings
from app.core.db import create_engine_from_url, create_session_factory, session_scope
from app.db.repositories.users import UserRepository
from app.db.seed import DEV_USERS
from app.domain.enums import Role
from synthetic.catalog import DEFAULT_CALENDAR_ID
from synthetic.generator import DEFAULT_AS_OF

pytestmark = pytest.mark.integration

POSTGRES_URL = os.environ.get("PPSE_TEST_DATABASE_URL")


class Result(NamedTuple):
    code: int
    out: str
    err: str


Cli = Callable[..., Result]


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return f"sqlite+pysqlite:///{tmp_path / 'cli.db'}"


@pytest.fixture
def cli_settings(db_url: str) -> Settings:
    return Settings(
        environment="test",
        database_url=db_url,
        log_level="WARNING",
        seed_on_startup=False,
        synthetic_scale="small",
        _env_file=None,  # type: ignore[call-arg]
    )


@pytest.fixture
def cli(cli_settings: Settings, db_url: str) -> Cli:
    """Run ``main()`` with captured streams, a frozen clock and the temporary database."""
    clock = FrozenClock(DEFAULT_AS_OF)

    def run(*args: str) -> Result:
        out, err = io.StringIO(), io.StringIO()
        code = main(
            ["--database-url", db_url, *args], settings=cli_settings, clock=clock, stdout=out, stderr=err
        )
        return Result(code, out.getvalue(), err.getvalue())

    return run


@pytest.fixture
def migrated(cli: Cli) -> Cli:
    result = cli("migrate")
    assert result.code == EXIT_OK, result.err
    return cli


def alembic_version(url: str) -> str:
    engine = create_engine_from_url(url)
    try:
        with engine.connect() as conn:
            return str(conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one())
    finally:
        engine.dispose()


# ------------------------------------------------------------------ migrate


def test_migrate_applies_schema_to_sqlite_file(cli: Cli, db_url: str) -> None:
    result = cli("migrate")
    assert result.code == EXIT_OK
    assert result.out.strip() == f"migrated {db_url} to head"

    engine = create_engine_from_url(db_url)
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    assert {
        "orders",
        "operations",
        "machines",
        "sync_runs",
        "users",
        "system_configs",
        "alembic_version",
    } <= tables
    assert alembic_version(db_url) == "0001"

    again = cli("--json", "migrate")  # already at head: a no-op, still exit 0
    assert again.code == EXIT_OK
    assert json.loads(again.out) == {"command": "migrate", "revision": "head", "database": db_url}


@pytest.mark.skipif(not POSTGRES_URL, reason="PPSE_TEST_DATABASE_URL not set")
def test_migrate_postgres_test_database_is_at_head(cli_settings: Settings) -> None:
    out, err = io.StringIO(), io.StringIO()
    code = main(
        ["--database-url", str(POSTGRES_URL), "--json", "migrate"],
        settings=cli_settings,
        stdout=out,
        stderr=err,
    )
    assert code == EXIT_OK, err.getvalue()
    payload = json.loads(out.getvalue())
    assert payload["revision"] == "head"
    assert payload["database"].startswith("postgresql+psycopg://***@")  # credentials never printed
    assert alembic_version(str(POSTGRES_URL)) == "0001"


# --------------------------------------------------------------------- seed


def test_seed_is_idempotent(migrated: Cli) -> None:
    first = migrated("seed")
    assert first.code == EXIT_OK
    assert first.out.strip() == f"seeded {len(DEV_USERS)} user(s); default config created"

    second = migrated("--json", "seed")
    assert second.code == EXIT_OK
    assert json.loads(second.out) == {"command": "seed", "users_created": [], "config_seeded": False}


def test_seed_before_migrate_fails_cleanly(cli: Cli) -> None:
    result = cli("seed")
    assert result.code == EXIT_ERROR
    assert result.err.startswith("error") and result.out == ""


# --------------------------------------------------------------------- sync


def test_sync_full_and_incremental_then_snapshot_stats(migrated: Cli) -> None:
    result = migrated("sync", "--mode", "full", "--scale", "small")
    assert result.code == EXIT_OK, result.err
    lines = result.out.splitlines()
    assert lines[0].startswith("sync sync_") and "completed (full, connector=mock)" in lines[0]
    assert "fetched:  customer=80, material=24, machine=12, tooling=16, calendar=2, order=301" in lines[1]
    assert "upserted: customer=80" in lines[2]
    assert "issues: 1 {'duplicate_id': 1}" in lines[3]
    assert lines[4].strip() == "reconciliation: ok"

    # nothing changed in the mock ERP since the full run: the incremental slice is empty
    as_json = migrated("--json", "sync", "--mode", "incremental", "--scale", "small")
    assert as_json.code == EXIT_OK, as_json.err
    payload = json.loads(as_json.out)
    assert (
        payload["command"] == "sync" and payload["status"] == "completed" and payload["mode"] == "incremental"
    )
    assert payload["since"] == DEFAULT_AS_OF.isoformat()  # started_at of the completed full run
    assert payload["watermark_source"].startswith("sync_")
    assert set(payload["records_fetched"].values()) == {0}
    assert payload["reconciliation"]["status"] == "ok" and payload["issues"] == []

    stats = migrated("snapshot-stats")
    assert stats.code == EXIT_OK, stats.err
    assert stats.out.splitlines()[0] == f"snapshot as of {DEFAULT_AS_OF.isoformat()} (source=db)"
    assert "  machines     12" in stats.out and "  open_orders  277" in stats.out
    assert "  groups       " in stats.out and "CNC3" in stats.out
    assert "  calendars    CAL-2SHIFT, CAL-3SHIFT" in stats.out

    stats_json = migrated("--json", "snapshot-stats", "--include-closed", "--as-of", "2026-09-15T00:00:00Z")
    assert stats_json.code == EXIT_OK
    payload = json.loads(stats_json.out)
    assert payload["command"] == "snapshot-stats" and payload["source"] == "db"
    assert payload["as_of"] == "2026-09-15T00:00:00+00:00"
    assert payload["summary"]["orders"] == 301 and payload["summary"]["open_orders"] == 277
    assert payload["summary"]["operations"] == 1448 and payload["summary"]["machines"] == 12
    assert payload["default_calendar_id"] == DEFAULT_CALENDAR_ID
    assert "CNC3" in payload["machine_groups"] and payload["calendars"] == ["CAL-2SHIFT", "CAL-3SHIFT"]
    assert payload["customer_rules"] == 0


def test_snapshot_stats_rejects_bad_instant(migrated: Cli) -> None:
    result = migrated("snapshot-stats", "--as-of", "yesterday")
    assert result.code == EXIT_ERROR
    assert result.err.startswith("error (validation_error): invalid ISO-8601 instant")


def test_sync_unknown_connector_fails_cleanly(migrated: Cli) -> None:
    result = migrated("sync", "--connector", "sap")
    assert result.code == EXIT_ERROR
    assert result.err.startswith("error (not_found)") and "'sap'" in result.err


# -------------------------------------------------------------- create-user


def test_create_user_and_reject_duplicate(migrated: Cli, db_url: str) -> None:
    created = migrated(
        "create-user",
        "--username",
        "Bob",
        "--password",
        "secret123",
        "--role",
        "planner",
        "--email",
        "bob@example.com",
    )
    assert created.code == EXIT_OK, created.err
    assert created.out.startswith("created user bob (planner) id=usr_")

    duplicate = migrated(
        "--json", "create-user", "--username", "bob", "--password", "other", "--role", "admin"
    )
    assert duplicate.code == EXIT_ERROR
    assert duplicate.err.startswith("error (conflict): username 'bob' already exists") and duplicate.out == ""

    engine = create_engine_from_url(db_url)
    try:
        with session_scope(create_session_factory(engine)) as session:
            record = UserRepository(session).authenticate("bob", "secret123")
            assert record is not None
            assert (
                record.role is Role.PLANNER
                and record.email == "bob@example.com"
                and record.display_name == "Bob"
            )
            assert UserRepository(session).authenticate("bob", "other") is None
    finally:
        engine.dispose()


# ------------------------------------------------------------------- usage


def test_usage_errors_and_help(cli: Cli, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli().code == EXIT_USAGE  # a command is required
    assert cli("frobnicate").code == EXIT_USAGE
    assert cli("sync", "--mode", "sideways").code == EXIT_USAGE
    assert cli("create-user", "--username", "x").code == EXIT_USAGE  # --password/--role required
    assert cli("--help").code == EXIT_OK
    captured = capsys.readouterr()  # argparse writes usage/help to the process streams
    assert "invalid choice: 'frobnicate'" in captured.err
    assert "{migrate,seed,sync,snapshot-stats,create-user,worker}" in captured.out


def test_worker_is_a_placeholder(cli: Cli) -> None:
    result = cli("worker")
    assert result.code == EXIT_USAGE
    assert result.err.strip() == "background worker not yet implemented" and result.out == ""
