"""Public operational endpoints: ``GET /health`` and ``GET /metrics``.

``/health`` answers 503 only when the database is unreachable; connector
reachability, the last ERP sync, the active plan and the background job state
are informational (a dead connector degrades the status text, not the code).
``/metrics`` stays JSON: request counters from the in-process registry plus a
few cheap database counts (schedule generations, replans, sync runs).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Any

import structlog
from fastapi import APIRouter, Request, Response, status
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import ClockDep, SettingsDep
from app.api.schemas.health import (
    ActivePlanInfo,
    BackgroundJobsInfo,
    ConnectorHealthInfo,
    HealthResponse,
    JobInfo,
    LastSyncInfo,
    MetricsResponse,
)
from app.core.clock import Clock
from app.core.config import Settings
from app.core.db import ping
from app.db.repositories.alerts import AlertRepository
from app.db.repositories.schedule import OptimizationRunRepository, ScheduleRepository
from app.db.repositories.sync_runs import SyncRunRepository
from app.domain.enums import ScheduleStatus
from app.services.schedule_service import RUN_KIND_REPLAN, RUN_KIND_SCHEDULE, RUN_STATUS_FAILED

log = structlog.get_logger(__name__)

router = APIRouter(tags=["health"])


@dataclass
class MetricsRegistry:
    """Minimal in-process counters (placeholder until a metrics backend is wired).

    Held on ``app.state.metrics``; :class:`RequestIdMiddleware` calls ``observe``
    for every HTTP request. Not module-level state: one registry per app instance.
    """

    requests_total: int = 0
    errors_total: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    by_path: dict[str, int] = field(default_factory=dict)
    counters: dict[str, float] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock, repr=False)

    def observe(self, method: str, path: str, status_code: int) -> None:
        with self._lock:
            self.requests_total += 1
            if status_code >= 500:
                self.errors_total += 1
            bucket = f"{status_code // 100}xx"
            self.by_status[bucket] = self.by_status.get(bucket, 0) + 1
            key = f"{method} {path}"
            self.by_path[key] = self.by_path.get(key, 0) + 1

    def increment(self, name: str, amount: float = 1.0) -> None:
        with self._lock:
            self.counters[name] = self.counters.get(name, 0.0) + amount

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "requests_total": self.requests_total,
                "errors_total": self.errors_total,
                "by_status": dict(self.by_status),
                "by_path": dict(sorted(self.by_path.items())),
                "counters": dict(sorted(self.counters.items())),
            }


# ------------------------------------------------------------------ helpers


def _session_factory(request: Request) -> sessionmaker[Session] | None:
    factory: sessionmaker[Session] | None = getattr(request.app.state, "session_factory", None)
    return factory


def _connector_info(request: Request, settings: Settings, clock: Clock) -> ConnectorHealthInfo:
    from app.api.deps import get_connector

    try:
        health = get_connector(request, settings, clock).health()
    except Exception as exc:  # unreachable / misconfigured connector: report, never fail the probe
        log.warning("health.connector_unreachable", connector=settings.erp_connector, error=str(exc))
        return ConnectorHealthInfo(name=settings.erp_connector, reachable=False, message=str(exc))
    return ConnectorHealthInfo(
        name=health.connector_name,
        reachable=health.healthy,
        message=health.message,
        checked_at=health.checked_at,
        latency_ms=health.latency_ms,
    )


def _db_facts(session: Session) -> tuple[LastSyncInfo | None, ActivePlanInfo | None]:
    last = SyncRunRepository(session).latest()
    last_sync = (
        LastSyncInfo(
            run_id=last.run_id,
            status=last.status,
            mode=last.mode.value,
            started_at=last.started_at,
            finished_at=last.finished_at,
        )
        if last
        else None
    )
    current = ScheduleRepository(session).get_current()
    active = (
        ActivePlanInfo(
            version_number=current.version_number,
            status=current.status.value,
            generated_at=current.generated_at,
            quality_score=(current.quality or {}).get("score"),
        )
        if current
        else None
    )
    return last_sync, active


def _jobs_info(request: Request, settings: Settings) -> BackgroundJobsInfo:
    scheduler = getattr(request.app.state, "scheduler", None)
    running = bool(getattr(scheduler, "running", False)) if scheduler is not None else False
    jobs: list[JobInfo] = []
    if scheduler is not None:
        for job in scheduler.get_jobs():
            jobs.append(
                JobInfo(
                    id=str(job.id),
                    name=str(job.name),
                    next_run_time=getattr(job, "next_run_time", None),
                    trigger=str(job.trigger),
                )
            )
    return BackgroundJobsInfo(enabled=settings.background_jobs_enabled, running=running, jobs=jobs)


def _db_metrics(session: Session) -> dict[str, Any]:
    runs = OptimizationRunRepository(session)
    syncs = SyncRunRepository(session)
    versions = ScheduleRepository(session)
    current = versions.get_current()
    return {
        "schedule_generations_total": runs.count(kind=RUN_KIND_SCHEDULE) + runs.count(kind=RUN_KIND_REPLAN),
        "replans_total": runs.count(kind=RUN_KIND_REPLAN),
        "failed_runs_total": runs.count(status=RUN_STATUS_FAILED),
        "sync_runs_total": syncs.list(limit=1).total,
        "sync_runs_failed_total": len([r for r in syncs.list(limit=1000).items if r.status == "failed"]),
        "schedule_versions_by_status": {s.value: versions.count_versions(s) for s in ScheduleStatus},
        "active_plan_version": current.version_number if current else None,
        "active_alerts_total": AlertRepository(session).list_active(limit=1).total,
    }


# ---------------------------------------------------------------- endpoints


@router.get("/health", response_model=HealthResponse, summary="Liveness/readiness probe")
def health(request: Request, response: Response, settings: SettingsDep, clock: ClockDep) -> HealthResponse:
    engine = getattr(request.app.state, "engine", None)
    db_ok = engine is not None and ping(engine)
    if not db_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    last_sync = active = None
    factory = _session_factory(request)
    if db_ok and factory is not None:
        try:
            with factory() as session:
                last_sync, active = _db_facts(session)
        except Exception as exc:  # schema not migrated yet, for instance
            log.warning("health.db_facts_failed", error=str(exc))
    connector = _connector_info(request, settings, clock)
    return HealthResponse(
        status="ok" if db_ok and connector.reachable else "degraded",
        database="ok" if db_ok else "unavailable",
        version=settings.app_version,
        environment=settings.environment,
        time=clock.now().isoformat(),
        connector=connector,
        last_sync=last_sync,
        active_plan=active,
        background_jobs=_jobs_info(request, settings),
    )


@router.get("/metrics", response_model=MetricsResponse, summary="Simple JSON counters")
def metrics(request: Request) -> MetricsResponse:
    registry: MetricsRegistry | None = getattr(request.app.state, "metrics", None)
    counters = registry.snapshot() if registry else MetricsRegistry().snapshot()
    db: dict[str, Any] = {}
    factory = _session_factory(request)
    if factory is not None:
        try:
            with factory() as session:
                db = _db_metrics(session)
        except Exception as exc:
            log.warning("metrics.db_counters_failed", error=str(exc))
    scheduler = getattr(request.app.state, "scheduler", None)
    db["background_jobs_running"] = bool(getattr(scheduler, "running", False)) if scheduler else False
    return MetricsResponse.from_parts(counters, db)


__all__ = ["MetricsRegistry", "router"]
