"""APScheduler 3 wiring for the background jobs (contract §2: in-process in dev, worker container in prod).

``build_scheduler`` registers three interval jobs from the settings —
``sync`` (``sync_interval_minutes``), ``replan`` (``replan_interval_minutes``)
and ``alerts`` (``alert_interval_minutes``) — with ``coalesce`` (missed runs
collapse into one), ``max_instances=1`` (a slow run never overlaps itself) and
a misfire grace of one interval. Jobs share one :class:`JobContext` (settings,
session factory, clock, connector). Nothing here is module-level state: the
caller owns the scheduler it builds.
"""

from __future__ import annotations

from typing import Any

import structlog
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session, sessionmaker

from app.core.clock import Clock, SystemClock
from app.core.config import Settings
from app.workers.jobs import (
    JOB_ALERTS,
    JOB_REPLAN,
    JOB_SYNC,
    JobContext,
    run_alert_job,
    run_replan_job,
    run_sync_job,
)

log = structlog.get_logger(__name__)

JOB_DEFAULTS: dict[str, Any] = {"coalesce": True, "max_instances": 1}


def job_intervals(settings: Settings) -> dict[str, int]:
    """Minutes between runs per job id."""
    return {
        JOB_SYNC: settings.sync_interval_minutes,
        JOB_REPLAN: settings.replan_interval_minutes,
        JOB_ALERTS: settings.alert_interval_minutes,
    }


def build_scheduler(
    settings: Settings,
    session_factory: sessionmaker[Session],
    clock: Clock | None = None,
    *,
    context: JobContext | None = None,
) -> BackgroundScheduler:
    """A configured (not yet started) :class:`BackgroundScheduler` with the three interval jobs."""
    ctx = context or JobContext(settings, session_factory, clock or SystemClock())
    scheduler = BackgroundScheduler(timezone="UTC", job_defaults=dict(JOB_DEFAULTS))
    intervals = job_intervals(settings)
    jobs = (
        (JOB_SYNC, "ERP synchronisation", run_sync_job),
        (JOB_REPLAN, "Continuous replanning", run_replan_job),
        (JOB_ALERTS, "Alert sweep", run_alert_job),
    )
    for job_id, name, func in jobs:
        minutes = intervals[job_id]
        scheduler.add_job(
            func,
            IntervalTrigger(minutes=minutes, timezone="UTC"),
            args=[ctx],
            id=job_id,
            name=name,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=max(60, minutes * 60),
            replace_existing=True,
        )
    log.info("worker.scheduler_built", intervals_minutes=intervals)
    return scheduler


def start_scheduler(scheduler: BackgroundScheduler) -> None:
    if not scheduler.running:
        scheduler.start()
        log.info("worker.scheduler_started", jobs=[j.id for j in scheduler.get_jobs()])


def shutdown_scheduler(scheduler: BackgroundScheduler, *, wait: bool = True) -> None:
    if scheduler.running:
        scheduler.shutdown(wait=wait)
        log.info("worker.scheduler_stopped")


def describe_jobs(scheduler: BackgroundScheduler) -> list[dict[str, Any]]:
    return [
        {
            "id": job.id,
            "name": job.name,
            "trigger": str(job.trigger),
            "next_run_time": (
                job.next_run_time.isoformat() if getattr(job, "next_run_time", None) is not None else None
            ),
        }
        for job in scheduler.get_jobs()
    ]


__all__ = [
    "JOB_DEFAULTS",
    "build_scheduler",
    "describe_jobs",
    "job_intervals",
    "shutdown_scheduler",
    "start_scheduler",
]
