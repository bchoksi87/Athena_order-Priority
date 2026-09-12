"""Operational command line: ``python -m app.cli <command>``.

Commands
--------
``migrate``         apply Alembic migrations (``upgrade head`` by default)
``seed``            create the development users and the default configuration
``sync``            run the ERP synchronisation (mock connector by default)
``snapshot-stats``  build the planning snapshot from the database and print its summary
``create-user``     add a local user account
``worker``          background worker (placeholder until the worker is implemented; exits 2)

Every command reads :class:`~app.core.config.Settings` (``PPSE_*`` environment
variables / ``.env``); ``--database-url`` overrides the database for one call.
Exit codes: 0 success, 1 application error (message on stderr), 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import IO, Any

import structlog
from sqlalchemy.engine import Engine

from app.core.clock import Clock, SystemClock, ensure_utc
from app.core.config import Settings, SyntheticScale, get_settings
from app.core.db import create_engine_from_url, create_session_factory, session_scope
from app.core.errors import AppError, ValidationError
from app.core.logging import configure_logging
from app.domain.enums import Role, SyncMode

log = structlog.get_logger(__name__)

BACKEND_DIR = Path(__file__).resolve().parent.parent
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


# ------------------------------------------------------------------ parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="PPSE operations CLI")
    parser.add_argument("--database-url", help="override PPSE_DATABASE_URL for this invocation")
    parser.add_argument("--log-level", default=None, help="override PPSE_LOG_LEVEL (e.g. DEBUG)")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON output")
    sub = parser.add_subparsers(dest="command", required=True)

    migrate = sub.add_parser("migrate", help="apply database migrations")
    migrate.add_argument("--revision", default="head", help="target revision (default: head)")

    sub.add_parser("seed", help="seed development users and the default configuration")

    sync = sub.add_parser("sync", help="synchronise the local store from the ERP connector")
    sync.add_argument("--mode", choices=[m.value for m in SyncMode], default=SyncMode.FULL.value)
    sync.add_argument("--connector", default=None, help="connector name (default: PPSE_ERP_CONNECTOR)")
    sync.add_argument(
        "--scale", choices=["small", "medium", "large"], default=None, help="mock dataset scale"
    )
    sync.add_argument("--seed", type=int, default=None, help="mock dataset seed")
    sync.add_argument("--dq-defect-ratio", type=float, default=None, help="mock data-quality defect ratio")
    sync.add_argument(
        "--prune", action="store_true", help="full mode: delete orders the ERP no longer serves"
    )
    sync.add_argument("--retry-attempts", type=int, default=None, help="connector retry attempts (default 3)")

    stats = sub.add_parser("snapshot-stats", help="build the planning snapshot from the database")
    stats.add_argument("--as-of", default=None, help="ISO-8601 instant (default: now, UTC)")
    stats.add_argument("--include-closed", action="store_true", help="include closed orders")

    user = sub.add_parser("create-user", help="create a local user account")
    user.add_argument("--username", required=True)
    user.add_argument("--password", required=True)
    user.add_argument("--role", required=True, choices=[r.value for r in Role])
    user.add_argument("--display-name", default=None)
    user.add_argument("--email", default=None)

    sub.add_parser("worker", help="run the background worker (not yet implemented)")
    return parser


# ---------------------------------------------------------------- commands


class CliContext:
    """Resolved settings plus lazily created engine, shared by the commands."""

    def __init__(
        self,
        settings: Settings,
        database_url: str,
        out: IO[str],
        err: IO[str],
        clock: Clock,
        as_json: bool,
    ) -> None:
        self.settings = settings
        self.database_url = database_url
        self.out = out
        self.err = err
        self.clock = clock
        self.as_json = as_json
        self._engine: Engine | None = None

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            self._engine = create_engine_from_url(self.database_url, echo=self.settings.database_echo)
        return self._engine

    def dispose(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None

    def emit(self, payload: dict[str, Any], text: str) -> None:
        self.out.write(json.dumps(payload, indent=2, sort_keys=True, default=str) if self.as_json else text)
        self.out.write("\n")


def cmd_migrate(ctx: CliContext, args: argparse.Namespace) -> int:
    from alembic.config import Config

    from alembic import command

    # alembic/env.py honours ``-x db_url=...``; the programmatic equivalent is ``cmd_opts.x``.
    config = Config(str(ALEMBIC_INI), cmd_opts=argparse.Namespace(x=[f"db_url={ctx.database_url}"]))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    log.info("cli.migrate", revision=args.revision, database=_redact(ctx.database_url))
    command.upgrade(config, args.revision)
    ctx.emit(
        {"command": "migrate", "revision": args.revision, "database": _redact(ctx.database_url)},
        f"migrated {_redact(ctx.database_url)} to {args.revision}",
    )
    return EXIT_OK


def cmd_seed(ctx: CliContext, _args: argparse.Namespace) -> int:
    from app.db.seed import seed_default_config, seed_users

    with session_scope(create_session_factory(ctx.engine)) as session:
        users = seed_users(session)
        config_seeded = seed_default_config(session)
    payload = {
        "command": "seed",
        "users_created": [u.username for u in users],
        "config_seeded": config_seeded,
    }
    ctx.emit(
        payload, f"seeded {len(users)} user(s); default config {'created' if config_seeded else 'present'}"
    )
    return EXIT_OK


def cmd_sync(ctx: CliContext, args: argparse.Namespace) -> int:
    from app.integration.connector import ConnectorRegistry
    from app.integration.retry import RetryPolicy
    from app.integration.sync_service import SyncOptions, SyncService

    settings = ctx.settings
    name = args.connector or settings.erp_connector
    options: dict[str, Any] = {}
    if name == "mock":
        scale: SyntheticScale = args.scale or settings.synthetic_scale
        options = {"seed": args.seed if args.seed is not None else settings.synthetic_seed, "scale": scale}
        if args.dq_defect_ratio is not None:
            options["dq_defect_ratio"] = args.dq_defect_ratio
    connector = ConnectorRegistry.default().create(name, ctx.clock, **options)
    retry = RetryPolicy(attempts=args.retry_attempts) if args.retry_attempts else RetryPolicy()
    sync_options = SyncOptions(retry=retry, prune_missing_orders=args.prune, triggered_by="cli")

    with session_scope(create_session_factory(ctx.engine)) as session:
        summary = SyncService(session, connector, ctx.clock, options=sync_options).run(args.mode)
    recon = summary.reconciliation.status if summary.reconciliation else "n/a"
    text = (
        f"sync {summary.run_id} {summary.status} ({summary.mode.value}, connector={summary.connector}) "
        f"in {summary.duration_seconds:.2f}s\n"
        f"  fetched:  {_fmt_counts(summary.records_fetched)}\n"
        f"  upserted: {_fmt_counts(summary.records_upserted)}\n"
        f"  issues: {summary.issues_count} {summary.issue_counts() or ''}\n"
        f"  reconciliation: {recon}"
    )
    ctx.emit({"command": "sync", **summary.to_dict(max_issues=50)}, text)
    return EXIT_OK


def cmd_snapshot_stats(ctx: CliContext, args: argparse.Namespace) -> int:
    from app.db.snapshot_builder import DbSnapshotBuilder

    as_of = _parse_instant(args.as_of) if args.as_of else ctx.clock.now()
    with session_scope(create_session_factory(ctx.engine)) as session:
        snapshot = DbSnapshotBuilder(session).build(as_of, include_closed_orders=args.include_closed)
    summary = snapshot.summary()
    groups = sorted({m.machine_group for m in snapshot.machines.values()})
    payload = {
        "command": "snapshot-stats",
        "as_of": as_of.isoformat(),
        "source": snapshot.source,
        "default_calendar_id": snapshot.default_calendar_id,
        "summary": summary,
        "machine_groups": groups,
        "calendars": sorted(snapshot.calendars),
        "customer_rules": len(snapshot.customer_rules),
    }
    lines = [f"snapshot as of {as_of.isoformat()} (source={snapshot.source})"]
    lines.extend(f"  {key:<12} {value}" for key, value in summary.items())
    lines.append(f"  {'groups':<12} {', '.join(groups) or '-'}")
    lines.append(f"  {'calendars':<12} {', '.join(sorted(snapshot.calendars)) or '-'}")
    ctx.emit(payload, "\n".join(lines))
    return EXIT_OK


def cmd_create_user(ctx: CliContext, args: argparse.Namespace) -> int:
    from app.db.repositories.users import UserRepository

    with session_scope(create_session_factory(ctx.engine)) as session:
        record = UserRepository(session).create(
            username=args.username,
            password=args.password,
            role=Role(args.role),
            display_name=args.display_name or args.username,
            email=args.email,
        )
    payload = {
        "command": "create-user",
        "user_id": record.user_id,
        "username": record.username,
        "role": record.role.value,
    }
    ctx.emit(payload, f"created user {record.username} ({record.role.value}) id={record.user_id}")
    return EXIT_OK


def cmd_worker(ctx: CliContext, _args: argparse.Namespace) -> int:
    """Placeholder: the APScheduler-based worker (sync/replan intervals) is not wired yet."""
    ctx.err.write("background worker not yet implemented\n")
    return EXIT_USAGE


COMMANDS = {
    "migrate": cmd_migrate,
    "seed": cmd_seed,
    "sync": cmd_sync,
    "snapshot-stats": cmd_snapshot_stats,
    "create-user": cmd_create_user,
    "worker": cmd_worker,
}


# ------------------------------------------------------------------- main


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    clock: Clock | None = None,
    stdout: IO[str] | None = None,
    stderr: IO[str] | None = None,
) -> int:
    """Entry point. Returns the process exit code instead of calling ``sys.exit``."""
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    parser = build_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:  # argparse already printed usage/help
        return int(exc.code) if isinstance(exc.code, int) else EXIT_USAGE

    resolved = settings or get_settings()
    configure_logging(args.log_level or resolved.log_level, resolved.log_json)
    database_url = args.database_url or resolved.effective_database_url
    ctx = CliContext(resolved, database_url, out, err, clock or SystemClock(), args.json)
    try:
        return COMMANDS[args.command](ctx, args)
    except AppError as exc:
        log.error(
            "cli.failed", command=args.command, error=exc.code, message=exc.message, details=exc.details
        )
        err.write(f"error ({exc.code}): {exc.message}\n")
        return EXIT_ERROR
    except Exception as exc:  # any other failure is reported, never a traceback for operators
        log.exception("cli.crashed", command=args.command, error=str(exc))
        err.write(f"error: {exc}\n")
        return EXIT_ERROR
    finally:
        ctx.dispose()


# ---------------------------------------------------------------- helpers


def _parse_instant(text: str) -> datetime:
    try:
        return ensure_utc(datetime.fromisoformat(text))
    except ValueError as exc:
        raise ValidationError(f"invalid ISO-8601 instant {text!r}") from exc


def _fmt_counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{k}={v}" for k, v in counts.items()) or "-"


def _redact(url: str) -> str:
    """Hide credentials in a database URL for logs and output."""
    if "@" not in url or "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    _credentials, host = rest.rsplit("@", 1)
    return f"{scheme}://***@{host}"


if __name__ == "__main__":  # pragma: no cover - exercised through subprocess tests
    sys.exit(main())
