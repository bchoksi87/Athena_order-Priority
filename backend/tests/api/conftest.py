"""API test fixtures.

``seeded_client`` boots the FastAPI app (root ``app_client``) on top of a SQLite
database populated from the synthetic generator at scale "small" through the
ERP sync (falling back to direct repository upserts), then stores one real
priority run and one rule-based schedule version so order/machine endpoints
have data. ``seeded`` exposes handy identifiers picked from that dataset.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.clock import FrozenClock
from app.db.records import UserRecord
from app.db.repositories import (
    CalendarRepository,
    ConfigRepository,
    CustomerRepository,
    MachineRepository,
    MaterialRepository,
    OrderRepository,
    PriorityResultRepository,
    ScheduleRepository,
    ToolingRepository,
)
from app.domain.enums import OrderStatus, ReadinessState, Role, ScheduleStatus
from app.domain.results import PriorityResult
from app.engines.calendar.builder import build_calendars
from app.engines.constraints.registry import default_constraint_engine
from app.engines.priority.engine import PriorityEngine
from app.engines.priority.registry import default_factors
from app.engines.scheduling.rule_based import RuleBasedScheduler
from app.services.snapshot_service import SnapshotService
from synthetic.generator import SyntheticDataGenerator, SyntheticDataset
from tests.conftest import NOW

PRIORITY_RUN_ID = "run_api_test"
API = "/api/v1"


@dataclass
class Seeded:
    client: TestClient
    session: Session
    clock: FrozenClock
    dataset: SyntheticDataset
    headers: Callable[[Role], dict[str, str]]
    users: dict[Role, UserRecord]
    results: dict[str, PriorityResult]
    schedule_version: int
    order_id: str  # open, ready, not on hold, scheduled
    closed_order_id: str
    held_order_id: str  # on hold in the ERP
    customer_id: str
    machine_id: str

    def get(self, path: str, role: Role = Role.ADMIN, **params: Any) -> Any:
        response = self.client.get(f"{API}{path}", headers=self.headers(role), params=params or None)
        assert response.status_code == 200, response.text
        return response.json()

    def post(self, path: str, body: dict[str, Any] | None = None, role: Role = Role.ADMIN) -> Any:
        return self.client.post(f"{API}{path}", headers=self.headers(role), json=body)

    def put(self, path: str, body: dict[str, Any], role: Role = Role.ADMIN) -> Any:
        return self.client.put(f"{API}{path}", headers=self.headers(role), json=body)

    def patch(self, path: str, body: dict[str, Any], role: Role = Role.ADMIN) -> Any:
        return self.client.patch(f"{API}{path}", headers=self.headers(role), json=body)

    def delete(self, path: str, body: dict[str, Any] | None = None, role: Role = Role.ADMIN) -> Any:
        return self.client.request("DELETE", f"{API}{path}", headers=self.headers(role), json=body)

    def audit(self, **filters: Any) -> list[dict[str, Any]]:
        return list(self.get("/audit", page_size=500, **filters)["items"])


def _sync_dataset(session: Session, dataset: SyntheticDataset, clock: FrozenClock) -> str:
    """Load the dataset through the ERP sync when it works, else through the repositories."""
    try:
        from app.integration.mock_connector import MockERPConnector
        from app.integration.sync_service import SyncService

        summary = SyncService(session, MockERPConnector(dataset, clock), clock).run("full")
        if summary.status == "completed" and OrderRepository(session).count() > 0:
            return "sync_service"
    except Exception:  # pragma: no cover - defensive: the sync service is developed concurrently
        pass
    session.rollback()
    _load_via_repositories(session, dataset, clock.now())
    return "repositories"


def _load_via_repositories(session: Session, dataset: SyntheticDataset, now: datetime) -> None:
    CustomerRepository(session).upsert(dataset.customers, synced_at=now)
    OrderRepository(session).upsert(dataset.orders, dataset.operations, synced_at=now)
    MachineRepository(session).upsert(dataset.machines, synced_at=now)
    MaterialRepository(session).upsert(dataset.materials, synced_at=now)
    ToolingRepository(session).upsert(dataset.tooling, synced_at=now)
    calendars = CalendarRepository(session)
    calendars.upsert(dataset.calendars)
    calendars.set_default(dataset.default_calendar_id)
    session.flush()


@pytest.fixture
def seeded(
    app_client: TestClient,
    session: Session,
    frozen_clock: FrozenClock,
    seeded_users: dict[Role, UserRecord],
    auth_headers: Callable[[Role], dict[str, str]],
) -> Seeded:
    dataset = SyntheticDataGenerator(seed=42, scale="small", as_of=NOW).generate()
    _sync_dataset(session, dataset, frozen_clock)

    snapshot = SnapshotService(session, frozen_clock).load_snapshot()
    config = ConfigRepository(session).get_active()
    engine = PriorityEngine(default_factors(), frozen_clock)
    results = engine.evaluate(snapshot, config.priority_profile, snapshot.customer_rules)
    PriorityResultRepository(session).save_run(PRIORITY_RUN_ID, results.values())

    schedule = RuleBasedScheduler(frozen_clock).schedule(
        snapshot,
        results,
        config.scheduling,
        build_calendars(snapshot),
        default_constraint_engine(config.scheduling),
    )
    info = ScheduleRepository(session).create_version(
        schedule, generated_by=seeded_users[Role.PLANNER].user_id, status=ScheduleStatus.DRAFT
    )
    session.flush()

    scheduled = {e.order_id for e in schedule.entries}
    ready = sorted(
        (
            r
            for r in results.values()
            if r.readiness is ReadinessState.READY
            and r.order_id in scheduled
            and not snapshot.orders[r.order_id].on_hold
        ),
        key=lambda r: (r.rank or 0, r.order_id),
    )
    assert ready, "expected at least one ready, scheduled order in the synthetic dataset"
    closed = next(o for o in dataset.orders if o.order_status in (OrderStatus.SHIPPED, OrderStatus.COMPLETED))
    held = next(o for o in dataset.orders if o.on_hold and o.is_open)
    return Seeded(
        client=app_client,
        session=session,
        clock=frozen_clock,
        dataset=dataset,
        headers=auth_headers,
        users=seeded_users,
        results=results,
        schedule_version=info.version_number,
        order_id=ready[0].order_id,
        closed_order_id=closed.order_id,
        held_order_id=held.order_id,
        customer_id=snapshot.orders[ready[0].order_id].customer_id,
        machine_id=dataset.machines[0].machine_id,
    )


@pytest.fixture
def seeded_client(seeded: Seeded) -> TestClient:
    return seeded.client


__all__ = ["API", "PRIORITY_RUN_ID", "Seeded"]
