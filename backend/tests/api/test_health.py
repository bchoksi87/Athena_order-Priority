"""``GET /health`` and ``GET /metrics`` with connector, sync, plan and job information."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from app.core.clock import FrozenClock
from app.core.config import Settings
from app.core.db import create_engine_from_url
from app.domain.enums import Role
from app.main import create_app
from tests.api.conftest import API, Seeded

pytestmark = pytest.mark.unit


def test_health_on_an_empty_database(app_client: TestClient) -> None:
    body = app_client.get(f"{API}/health").json()
    assert body["status"] == "ok" and body["database"] == "ok"
    assert body["connector"]["name"] == "mock" and body["connector"]["reachable"] is True
    assert body["last_sync"] is None and body["active_plan"] is None
    assert body["background_jobs"] == {"enabled": False, "running": False, "jobs": []}


def test_health_and_metrics_reflect_sync_plan_and_runs(seeded: Seeded) -> None:
    generated = seeded.post("/schedule/generate", None, role=Role.PLANNER)
    assert generated.status_code == 201
    seeded.session.commit()  # the probe opens its own session
    body = seeded.client.get(f"{API}/health").json()
    assert body["status"] == "ok"
    assert body["last_sync"]["status"] == "completed" and body["last_sync"]["mode"] == "full"
    assert body["active_plan"]["version_number"] == generated.json()["version"]["version_number"]
    assert body["active_plan"]["status"] == "draft"
    assert body["active_plan"]["quality_score"] == generated.json()["version"]["quality_score"]

    metrics = seeded.client.get(f"{API}/metrics").json()
    assert metrics["schedule_generations_total"] == 1 and metrics["replans_total"] == 0
    assert metrics["sync_runs_total"] >= 1 and metrics["sync_runs_failed_total"] == 0
    assert metrics["schedule_versions_by_status"]["draft"] == 2  # fixture version + generated one
    assert metrics["active_plan_version"] == body["active_plan"]["version_number"]
    assert metrics["active_alerts_total"] > 0 and metrics["background_jobs_running"] is False
    assert metrics["requests_total"] >= 2 and "GET /api/v1/health" in metrics["by_path"]


@pytest.fixture
def broken_db_app(settings: Settings, frozen_clock: FrozenClock) -> Iterator[FastAPI]:
    engine: Engine = create_engine_from_url("sqlite+pysqlite:////nonexistent-dir/ppse-broken.db")
    try:
        yield create_app(settings, engine=engine, clock=frozen_clock)
    finally:
        engine.dispose()


def test_health_is_503_only_when_the_database_is_down(broken_db_app: FastAPI) -> None:
    with TestClient(broken_db_app) as client:
        response = client.get(f"{API}/health")
        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "degraded" and body["database"] == "unavailable"
        assert body["connector"]["reachable"] is True
        assert body["active_plan"] is None and body["last_sync"] is None
        assert client.get(f"{API}/metrics").status_code == 200
