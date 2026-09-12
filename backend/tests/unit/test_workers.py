"""Background jobs on SQLite with the mock connector, scheduler wiring and the worker loop."""

from __future__ import annotations

import threading
from collections.abc import Iterator

import pytest
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.core.clock import FrozenClock
from app.core.config import Settings
from app.core.db import SQLITE_MEMORY_URL, create_session_factory
from app.db.repositories import AlertRepository, OrderRepository, ScheduleRepository, SyncRunRepository
from app.db.seed import seed_default_config, seed_users
from app.domain.enums import AlertType, ScheduleStatus
from app.integration.mock_connector import MockERPConnector
from app.workers import (
    JOB_ALERTS,
    JOB_REPLAN,
    JOB_SYNC,
    JobContext,
    build_scheduler,
    describe_jobs,
    run_alert_job,
    run_job,
    run_replan_job,
    run_sync_job,
    shutdown_scheduler,
    start_scheduler,
)
from app.workers.main import run_worker
from synthetic.generator import SyntheticDataGenerator
from tests.conftest import NOW

pytestmark = pytest.mark.unit


@pytest.fixture
def worker_settings() -> Settings:
    return Settings(
        environment="test",
        database_url=SQLITE_MEMORY_URL,
        jwt_secret="unit-test-secret-that-is-long-enough",
        seed_on_startup=False,
        log_level="WARNING",
        sync_interval_minutes=7,
        replan_interval_minutes=13,
        alert_interval_minutes=3,
        _env_file=None,  # type: ignore[call-arg]
    )


@pytest.fixture
def context(engine: Engine, worker_settings: Settings, frozen_clock: FrozenClock) -> Iterator[JobContext]:
    factory = create_session_factory(engine)
    with factory() as session:
        seed_users(session)
        seed_default_config(session)
        session.commit()
    dataset = SyntheticDataGenerator(seed=42, scale="small", as_of=NOW).generate()
    connector = MockERPConnector(dataset, frozen_clock)
    yield JobContext(worker_settings, factory, frozen_clock, connector=connector)


def _session(ctx: JobContext) -> Session:
    return ctx.session_factory()


def test_jobs_run_once_against_sqlite(context: JobContext) -> None:
    synced = run_sync_job(context)
    assert synced.succeeded and synced.job == JOB_SYNC and synced.job_id.startswith("job_")
    assert synced.summary["status"] == "completed" and synced.summary["mode"] == "incremental"
    assert synced.summary["records_upserted"]["order"] > 0  # no watermark yet: degrades to a full fetch
    with _session(context) as session:
        assert OrderRepository(session).count() == synced.summary["records_upserted"]["order"]
        assert SyncRunRepository(session).latest().status == "completed"  # type: ignore[union-attr]

    replanned = run_replan_job(context)
    assert replanned.succeeded and replanned.job == JOB_REPLAN
    summary = replanned.summary
    assert summary["triggered"] is True and summary["trigger"] == "scheduled"
    assert summary["action"] == "awaiting_approval"  # require_approval is True by default
    assert summary["candidate_version"] == 1 and summary["candidate_status"] == "draft"
    assert summary["active_version"] is None and summary["alert_id"]
    assert "no current schedule" in summary["reason"]
    with _session(context) as session:
        version = ScheduleRepository(session).get_version(1)
        assert version.status is ScheduleStatus.DRAFT and version.details["trigger"] == "replan:scheduled"
        assert version.details["replan"]["decision"]["requires_approval"] is True
        alerts = AlertRepository(session).list_active(alert_type=AlertType.SCHEDULE_DISRUPTION)
        assert alerts.total == 1 and alerts.items[0].details["pending_version"] == 1
        assert "awaiting approval" in alerts.items[0].title

    # nothing changed: the next candidate is identical and gets rejected, the pending draft stays
    again = run_replan_job(context)
    assert again.succeeded and again.summary["action"] == "rejected"
    assert again.summary["candidate_version"] == 2 and again.summary["candidate_status"] == "rejected"
    assert "identical" in again.summary["reason"]
    with _session(context) as session:
        assert ScheduleRepository(session).get_version(1).status is ScheduleStatus.DRAFT
        assert ScheduleRepository(session).get_current().version_number == 1  # type: ignore[union-attr]

    swept = run_alert_job(context)
    assert swept.succeeded and swept.job == JOB_ALERTS
    assert swept.summary["evaluated"] > 0 and swept.summary["upserted"] == swept.summary["evaluated"]
    with _session(context) as session:
        pending = AlertRepository(session).list_active(alert_type=AlertType.SCHEDULE_DISRUPTION)
        assert pending.total == 1  # the "awaiting approval" alert survives the sweep while the draft is open

    dispatched = run_job("alerts", context)
    assert dispatched.succeeded and dispatched.job == JOB_ALERTS
    with pytest.raises(Exception, match="unknown job"):
        run_job("frobnicate", context)


def test_failing_job_is_reported_not_raised(context: JobContext) -> None:
    assert isinstance(context.connector, MockERPConnector)
    context.connector.set_failure("ERP link down")
    result = run_sync_job(context)
    assert not result.succeeded and result.status == "failed"
    assert result.error is not None and "ERP link down" in result.error
    assert result.summary == {} and result.duration_seconds >= 0
    payload = result.to_dict()
    assert payload["status"] == "failed" and payload["job"] == JOB_SYNC
    with _session(context) as session:
        latest = SyncRunRepository(session).latest()
        assert latest is not None and latest.status == "failed"  # the sync service kept its trace
    context.connector.set_failure(None)
    assert run_sync_job(context).succeeded


def test_scheduler_uses_configured_intervals(context: JobContext, worker_settings: Settings) -> None:
    scheduler = build_scheduler(worker_settings, context.session_factory, context.clock, context=context)
    assert isinstance(scheduler, BackgroundScheduler) and not scheduler.running
    jobs = {job.id: job for job in scheduler.get_jobs()}
    assert set(jobs) == {JOB_SYNC, JOB_REPLAN, JOB_ALERTS}
    assert jobs[JOB_SYNC].trigger.interval.total_seconds() == 7 * 60
    assert jobs[JOB_REPLAN].trigger.interval.total_seconds() == 13 * 60
    assert jobs[JOB_ALERTS].trigger.interval.total_seconds() == 3 * 60
    for job in jobs.values():
        assert job.coalesce is True and job.max_instances == 1
        assert job.misfire_grace_time >= 60 and job.args == (context,)
    described = {d["id"]: d for d in describe_jobs(scheduler)}
    assert (
        described[JOB_SYNC]["name"] == "ERP synchronisation" and described[JOB_SYNC]["next_run_time"] is None
    )
    start_scheduler(scheduler)
    try:
        assert scheduler.running
        assert all(d["next_run_time"] for d in describe_jobs(scheduler))
    finally:
        shutdown_scheduler(scheduler, wait=False)
    assert not scheduler.running


def test_worker_loop_stops_on_event(context: JobContext, worker_settings: Settings) -> None:
    stop = threading.Event()
    stop.set()
    code = run_worker(
        worker_settings, clock=context.clock, session_factory=context.session_factory, stop_event=stop
    )
    assert code == 0
