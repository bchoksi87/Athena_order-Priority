"""Operational schemas for ``GET /health`` and ``GET /metrics``."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ConnectorHealthInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    reachable: bool
    message: str = ""
    checked_at: datetime | None = None
    latency_ms: float | None = None


class LastSyncInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    status: str
    mode: str
    started_at: datetime
    finished_at: datetime | None = None


class ActivePlanInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_number: int
    status: str
    generated_at: datetime
    quality_score: float | None = None


class JobInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    next_run_time: datetime | None = None
    trigger: str


class BackgroundJobsInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    running: bool
    jobs: list[JobInfo] = []


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str = Field(description="'ok' or 'degraded' (503 only when the database is down)")
    database: str
    version: str
    environment: str
    writeback_mode: str = Field(
        description="ERP writeback ladder position: read_only | approval | writeback | controlled_auto"
    )
    time: str
    connector: ConnectorHealthInfo | None = None
    last_sync: LastSyncInfo | None = None
    active_plan: ActivePlanInfo | None = None
    background_jobs: BackgroundJobsInfo


class MetricsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requests_total: int
    errors_total: int
    by_status: dict[str, int]
    by_path: dict[str, int]
    counters: dict[str, float]
    schedule_generations_total: int = 0
    replans_total: int = 0
    failed_runs_total: int = 0
    sync_runs_total: int = 0
    sync_runs_failed_total: int = 0
    schedule_versions_by_status: dict[str, int] = {}
    active_plan_version: int | None = None
    active_alerts_total: int = 0
    background_jobs_running: bool = False

    @classmethod
    def from_parts(cls, registry: dict[str, Any], db: dict[str, Any]) -> MetricsResponse:
        return cls(**registry, **db)


__all__ = [
    "ActivePlanInfo",
    "BackgroundJobsInfo",
    "ConnectorHealthInfo",
    "HealthResponse",
    "JobInfo",
    "LastSyncInfo",
    "MetricsResponse",
]
