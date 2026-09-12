"""Background jobs: ERP sync, continuous replanning and the alert sweep.

Every job opens its own session from the worker's session factory, binds a
``job`` / ``job_id`` to the structlog context, measures its duration and
**never raises**: a failure is logged (with traceback) and reported in the
returned :class:`JobResult` so the APScheduler loop keeps running.

* ``run_sync_job``   — :class:`SyncAdminService` incremental sync with the
  configured connector (degrades to a full fetch when no completed run exists);
* ``run_replan_job`` — :meth:`ReplanningService.evaluate` with the ``SCHEDULED``
  trigger (the stability rules decide whether anything changes);
* ``run_alert_job``  — :meth:`AnalyticsService.refresh_alerts` (upsert + sweep).

A :class:`JobContext` carries the settings, session factory, clock and a
connector shared by every run of the worker process; passing plain
:class:`Settings` builds a throw-away context (used by ``worker --once``).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.clock import Clock, SystemClock, ensure_utc
from app.core.config import Settings
from app.core.db import create_engine_from_url, create_session_factory, session_scope
from app.core.errors import ValidationError
from app.core.ids import new_id
from app.domain.enums import ReplanTriggerType, SyncMode
from app.integration.connector import ERPConnector
from app.services.analytics_service import AnalyticsService
from app.services.replanning_service import SYSTEM_USER, ReplanningService
from app.services.sync_admin_service import SyncAdminService, build_connector

log = structlog.get_logger(__name__)

JOB_SYNC = "sync"
JOB_REPLAN = "replan"
JOB_ALERTS = "alerts"
JOB_NAMES: tuple[str, ...] = (JOB_SYNC, JOB_REPLAN, JOB_ALERTS)
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"


@dataclass(slots=True)
class JobResult:
    job: str
    job_id: str
    status: str
    started_at: datetime
    finished_at: datetime
    duration_seconds: float
    summary: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.status == STATUS_COMPLETED

    def to_dict(self) -> dict[str, Any]:
        return {
            "job": self.job,
            "job_id": self.job_id,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "duration_seconds": round(self.duration_seconds, 3),
            "summary": dict(self.summary),
            "error": self.error,
        }


@dataclass
class JobContext:
    """What every job needs; one per worker process (or per ``--once`` invocation)."""

    settings: Settings
    session_factory: sessionmaker[Session]
    clock: Clock = field(default_factory=SystemClock)
    connector: ERPConnector | None = None
    engine: Engine | None = None

    @classmethod
    def from_settings(cls, settings: Settings, clock: Clock | None = None) -> JobContext:
        engine = create_engine_from_url(settings.effective_database_url, echo=settings.database_echo)
        return cls(settings, create_session_factory(engine), clock or SystemClock(), engine=engine)

    def get_connector(self) -> ERPConnector:
        if self.connector is None:
            self.connector = build_connector(self.settings, self.clock)
        return self.connector

    def dispose(self) -> None:
        if self.engine is not None:
            self.engine.dispose()
            self.engine = None


def _context(context: JobContext | Settings, clock: Clock | None) -> tuple[JobContext, bool]:
    if isinstance(context, JobContext):
        return context, False
    return JobContext.from_settings(context, clock), True


def _run(job: str, ctx: JobContext, body: Callable[[Session], dict[str, Any]]) -> JobResult:
    job_id = new_id("job")
    started_at = ensure_utc(ctx.clock.now())
    timer = time.perf_counter()
    with structlog.contextvars.bound_contextvars(job=job, job_id=job_id):
        log.info("worker.job_started")
        try:
            with session_scope(ctx.session_factory) as session:
                summary = body(session)
        except Exception as exc:  # the scheduler loop must survive any failure
            duration = time.perf_counter() - timer
            log.exception("worker.job_failed", error=str(exc), error_type=type(exc).__name__)
            return JobResult(
                job=job,
                job_id=job_id,
                status=STATUS_FAILED,
                started_at=started_at,
                finished_at=ensure_utc(ctx.clock.now()),
                duration_seconds=duration,
                error=f"{type(exc).__name__}: {exc}",
            )
        duration = time.perf_counter() - timer
        log.info("worker.job_completed", duration_seconds=round(duration, 3), **_loggable(summary))
        return JobResult(
            job=job,
            job_id=job_id,
            status=STATUS_COMPLETED,
            started_at=started_at,
            finished_at=ensure_utc(ctx.clock.now()),
            duration_seconds=duration,
            summary=summary,
        )


def _loggable(summary: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in summary.items() if isinstance(v, str | int | float | bool) or v is None}


# --------------------------------------------------------------------- jobs


def run_sync_job(
    context: JobContext | Settings,
    mode: SyncMode | str = SyncMode.INCREMENTAL,
    *,
    clock: Clock | None = None,
) -> JobResult:
    """Incremental ERP sync (full when no completed run exists yet) with the configured connector."""
    ctx, owned = _context(context, clock)
    try:

        def body(session: Session) -> dict[str, Any]:
            service = SyncAdminService(session, ctx.clock, ctx.settings, ctx.get_connector())
            summary = service.run(mode, user=SYSTEM_USER)
            return {
                "run_id": summary.run_id,
                "status": summary.status,
                "mode": summary.mode.value,
                "connector": summary.connector,
                "records_fetched": dict(summary.records_fetched),
                "records_upserted": dict(summary.records_upserted),
                "issues": summary.issues_count,
                "reconciliation": summary.reconciliation.status if summary.reconciliation else None,
            }

        return _run(JOB_SYNC, ctx, body)
    finally:
        if owned:
            ctx.dispose()


def run_replan_job(
    context: JobContext | Settings,
    trigger: ReplanTriggerType = ReplanTriggerType.SCHEDULED,
    *,
    clock: Clock | None = None,
) -> JobResult:
    """Continuous replanning evaluation (spec Phase 11); the decision is persisted by the service."""
    ctx, owned = _context(context, clock)
    try:

        def body(session: Session) -> dict[str, Any]:
            outcome = ReplanningService(session, ctx.clock, ctx.settings).evaluate(trigger, SYSTEM_USER)
            return {
                "trigger": outcome.trigger.value,
                "triggered": outcome.triggered,
                "action": outcome.action,
                "reason": outcome.reason,
                "events": len(outcome.events),
                "event_types": outcome.event_types,
                "active_version": outcome.active_version.version_number if outcome.active_version else None,
                "candidate_version": (
                    outcome.candidate_version.version_number if outcome.candidate_version else None
                ),
                "candidate_status": (
                    outcome.candidate_version.status.value if outcome.candidate_version else None
                ),
                "alert_id": outcome.alert.alert_id if outcome.alert else None,
            }

        return _run(JOB_REPLAN, ctx, body)
    finally:
        if owned:
            ctx.dispose()


def run_alert_job(context: JobContext | Settings, *, clock: Clock | None = None) -> JobResult:
    """Evaluate every alert rule on the live context, upsert by dedupe key and resolve stale alerts."""
    ctx, owned = _context(context, clock)
    try:

        def body(session: Session) -> dict[str, Any]:
            refresh = AnalyticsService(session, ctx.clock).refresh_alerts()
            return {
                "as_of": refresh.as_of.isoformat(),
                "evaluated": refresh.evaluated,
                "upserted": refresh.upserted,
                "resolved": refresh.resolved,
                "by_severity": dict(refresh.by_severity),
            }

        return _run(JOB_ALERTS, ctx, body)
    finally:
        if owned:
            ctx.dispose()


def run_job(name: str, context: JobContext | Settings, *, clock: Clock | None = None) -> JobResult:
    """Dispatch by job name (``sync`` / ``replan`` / ``alerts``)."""
    if name == JOB_SYNC:
        return run_sync_job(context, clock=clock)
    if name == JOB_REPLAN:
        return run_replan_job(context, clock=clock)
    if name == JOB_ALERTS:
        return run_alert_job(context, clock=clock)
    raise ValidationError(f"unknown job {name!r}", details={"allowed": list(JOB_NAMES)})


__all__ = [
    "JOB_ALERTS",
    "JOB_NAMES",
    "JOB_REPLAN",
    "JOB_SYNC",
    "STATUS_COMPLETED",
    "STATUS_FAILED",
    "JobContext",
    "JobResult",
    "run_alert_job",
    "run_job",
    "run_replan_job",
    "run_sync_job",
]
