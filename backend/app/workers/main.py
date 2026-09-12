"""Worker entry point: ``python -m app.workers.main`` (or ``python -m app.cli worker``).

Builds the APScheduler loop from the settings and blocks until ``SIGTERM`` /
``SIGINT`` (or the injected ``stop_event`` is set), then shuts the scheduler
down gracefully — a running job finishes before the process exits.
"""

from __future__ import annotations

import signal
import sys
import threading
from types import FrameType
from typing import Any

import structlog
from sqlalchemy.orm import Session, sessionmaker

from app.core.clock import Clock, SystemClock
from app.core.config import Settings, get_settings
from app.core.db import create_engine_from_url, create_session_factory
from app.core.logging import configure_logging
from app.workers.jobs import JobContext, JobResult, run_job
from app.workers.scheduler import build_scheduler, describe_jobs, shutdown_scheduler, start_scheduler

log = structlog.get_logger(__name__)

STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT)


def _install_signal_handlers(stop: threading.Event) -> None:
    """Set ``stop`` on SIGTERM/SIGINT; silently skipped outside the main thread (tests)."""

    def _handle(signum: int, _frame: FrameType | None) -> None:
        log.info("worker.signal", signal=signal.Signals(signum).name)
        stop.set()

    for sig in STOP_SIGNALS:
        try:
            signal.signal(sig, _handle)
        except ValueError:  # not in the main thread
            return


def run_worker(
    settings: Settings | None = None,
    *,
    clock: Clock | None = None,
    session_factory: sessionmaker[Session] | None = None,
    stop_event: threading.Event | None = None,
    poll_seconds: float = 1.0,
) -> int:
    """Blocking scheduler loop; returns the process exit code (0)."""
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)
    stop = stop_event or threading.Event()
    engine = None
    if session_factory is None:
        engine = create_engine_from_url(settings.effective_database_url, echo=settings.database_echo)
        session_factory = create_session_factory(engine)
    context = JobContext(settings, session_factory, clock or SystemClock(), engine=engine)
    scheduler = build_scheduler(settings, session_factory, context.clock, context=context)
    _install_signal_handlers(stop)
    start_scheduler(scheduler)
    log.info(
        "worker.started",
        environment=settings.environment,
        connector=settings.erp_connector,
        writeback_mode=settings.writeback_mode.value,
        jobs=describe_jobs(scheduler),
    )
    try:
        while not stop.wait(poll_seconds):
            pass
    finally:
        shutdown_scheduler(scheduler, wait=True)
        context.dispose()
        log.info("worker.stopped")
    return 0


def run_once(name: str, settings: Settings | None = None, *, clock: Clock | None = None) -> JobResult:
    """Run one job and return its result (``worker --once <job>``)."""
    settings = settings or get_settings()
    return run_job(name, settings, clock=clock)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--once" and len(args) > 1:
        result = run_once(args[1])
        payload: dict[str, Any] = result.to_dict()
        sys.stdout.write(f"{payload}\n")
        return 0 if result.succeeded else 1
    return run_worker()


if __name__ == "__main__":  # pragma: no cover - process entry point
    sys.exit(main())


__all__ = ["main", "run_once", "run_worker"]
