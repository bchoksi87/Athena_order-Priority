"""Shared pytest fixtures.

* ``settings``            – test :class:`Settings` (SQLite in-memory, fixed JWT secret)
* ``engine`` / ``session`` – fresh SQLite in-memory schema per test
* ``db_session``          – parametrised over SQLite and (when ``PPSE_TEST_DATABASE_URL``
                            is set) PostgreSQL; the PostgreSQL variant runs inside a
                            transaction that is rolled back after each test
* ``frozen_clock``        – :class:`FrozenClock` at 2026-09-11T08:00Z
* ``app_client``          – ``TestClient`` bound to the SQLite session with seeded users
* ``auth_headers``        – ``auth_headers(role)`` -> bearer header for the seeded user
* ``sample``              – small in-memory domain dataset (orders, machines, ...)
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.clock import FrozenClock
from app.core.config import Settings, reset_settings_cache
from app.core.db import SQLITE_MEMORY_URL, create_all, create_engine_from_url
from app.core.security import CurrentUser, create_access_token
from app.db.records import UserRecord
from app.db.seed import DEV_USERS, seed_default_config, seed_users
from app.domain.enums import (
    CustomerTier,
    LockType,
    MachineStatus,
    MaterialStatus,
    OperationStatus,
    OrderStatus,
    OverrideType,
    PaymentRisk,
    ProcessType,
    Role,
)
from app.domain.models import (
    CalendarSpec,
    Customer,
    CustomerRule,
    Expedite,
    Machine,
    Material,
    Operation,
    Order,
    PriorityOverride,
    ScheduleLock,
    Shift,
    TimeWindow,
    Tooling,
)
from app.domain.snapshot import PlanningSnapshot

NOW = datetime(2026, 9, 11, 8, 0, tzinfo=UTC)
TEST_JWT_SECRET = "unit-test-secret"
POSTGRES_URL = os.environ.get("PPSE_TEST_DATABASE_URL")


# ------------------------------------------------------------------ settings


@pytest.fixture
def settings() -> Iterator[Settings]:
    reset_settings_cache()
    yield Settings(
        environment="test",
        database_url=SQLITE_MEMORY_URL,
        jwt_secret=TEST_JWT_SECRET,
        jwt_expire_minutes=60,
        seed_on_startup=False,
        log_level="WARNING",
        _env_file=None,  # type: ignore[call-arg]
    )
    reset_settings_cache()


@pytest.fixture
def frozen_clock() -> FrozenClock:
    return FrozenClock(NOW)


# ------------------------------------------------------------------ database


@pytest.fixture
def engine() -> Iterator[Engine]:
    eng = create_engine_from_url(SQLITE_MEMORY_URL)
    create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine, expire_on_commit=False) as s:
        yield s
        s.rollback()


@pytest.fixture(scope="session")
def _postgres_engine() -> Iterator[Engine | None]:
    if not POSTGRES_URL:
        yield None
        return
    eng = create_engine_from_url(POSTGRES_URL)
    try:
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - environment dependent
        eng.dispose()
        pytest.skip(f"PostgreSQL test database unreachable: {exc}")
    create_all(eng)  # no-op when migrations are at head; creates missing tables otherwise
    yield eng
    eng.dispose()


@pytest.fixture(params=["sqlite", "postgres"])
def db_session(
    request: pytest.FixtureRequest, engine: Engine, _postgres_engine: Engine | None
) -> Iterator[Session]:
    """A session against SQLite (always) or PostgreSQL (when configured, else skipped)."""
    if request.param == "sqlite":
        with Session(engine, expire_on_commit=False) as s:
            yield s
            s.rollback()
        return
    if _postgres_engine is None:
        pytest.skip("PPSE_TEST_DATABASE_URL not set")
    connection = _postgres_engine.connect()
    transaction = connection.begin()
    s = Session(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
    try:
        yield s
    finally:
        s.close()
        transaction.rollback()
        connection.close()


# ----------------------------------------------------------------- app/auth


@pytest.fixture
def seeded_users(session: Session) -> dict[Role, UserRecord]:
    created = seed_users(session)
    seed_default_config(session)
    session.commit()
    return {u.role: u for u in created}


@pytest.fixture
def app(settings: Settings, engine: Engine, session: Session, frozen_clock: FrozenClock) -> FastAPI:
    from app.main import create_app

    application = create_app(settings, engine=engine, clock=frozen_clock)

    def _override_db() -> Iterator[Session]:
        yield session

    application.dependency_overrides[get_db] = _override_db
    return application


@pytest.fixture
def app_client(app: FastAPI, seeded_users: dict[Role, UserRecord]) -> Iterator[TestClient]:
    with TestClient(app) as client:
        yield client


@pytest.fixture
def auth_headers(
    settings: Settings, seeded_users: dict[Role, UserRecord], frozen_clock: FrozenClock
) -> Callable[[Role], dict[str, str]]:
    def _headers(role: Role) -> dict[str, str]:
        record = seeded_users[role]
        user = CurrentUser(record.user_id, record.username, record.role, record.display_name)
        token = create_access_token(
            user,
            secret=settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
            expires_minutes=settings.jwt_expire_minutes,
            now=frozen_clock.now(),
        )
        return {"Authorization": f"Bearer {token}"}

    return _headers


@pytest.fixture
def dev_passwords() -> dict[str, str]:
    return {u.username: u.password for u in DEV_USERS}


# -------------------------------------------------------------- sample data


@dataclass
class SampleData:
    """A coherent mini-plant: 2 customers, 3 machines, 4 orders (one closed), resources, overlays."""

    customers: list[Customer] = field(default_factory=list)
    orders: list[Order] = field(default_factory=list)
    operations: list[Operation] = field(default_factory=list)
    machines: list[Machine] = field(default_factory=list)
    materials: list[Material] = field(default_factory=list)
    tooling: list[Tooling] = field(default_factory=list)
    calendars: list[CalendarSpec] = field(default_factory=list)
    locks: list[ScheduleLock] = field(default_factory=list)
    overrides: list[PriorityOverride] = field(default_factory=list)
    expedites: list[Expedite] = field(default_factory=list)
    customer_rules: list[CustomerRule] = field(default_factory=list)

    def snapshot(self, as_of: datetime = NOW) -> PlanningSnapshot:
        snap = PlanningSnapshot(
            as_of=as_of,
            customers={c.customer_id: c for c in self.customers},
            orders={o.order_id: o for o in self.orders if o.is_open},
            operations={
                op.operation_id: op
                for op in self.operations
                if op.order_id in {o.order_id for o in self.orders if o.is_open}
            },
            machines={m.machine_id: m for m in self.machines},
            materials={m.material_id: m for m in self.materials},
            tooling={t.tooling_id: t for t in self.tooling},
            calendars={c.calendar_id: c for c in self.calendars},
            default_calendar_id="CAL-PLANT",
            locks=list(self.locks),
            overrides=list(self.overrides),
            expedites=list(self.expedites),
            customer_rules={r.customer_id: r for r in self.customer_rules},
            source="test",
        )
        snap.rebuild_indexes()
        return snap


def build_sample(now: datetime = NOW) -> SampleData:
    customers = [
        Customer(
            "CUST-A",
            "Aero Dynamics",
            customer_tier=CustomerTier.STRATEGIC,
            strategic_customer_flag=True,
            customer_revenue=1_250_000.0,
            sla_hours=48.0,
            payment_risk=PaymentRisk.LOW,
            attributes={"segment": "aerospace"},
        ),
        Customer("CUST-B", "Bulk Parts Ltd", customer_tier=CustomerTier.STANDARD, customer_priority=4),
    ]
    machines = [
        Machine(
            "CNC-01",
            "Haas VF-2",
            "3-axis mill",
            ProcessType.CNC_MACHINING,
            "CNC",
            calendar_id="CAL-PLANT",
            compatible_processes={ProcessType.DEBURRING},
            compatible_materials={"MAT-AL", "MAT-ST"},
            max_part_size_mm=(760.0, 400.0, 500.0),
            tooling_configuration={"TOOL-1"},
            maintenance_windows=[TimeWindow(now + timedelta(days=2), now + timedelta(days=2, hours=4), "PM")],
            planned_downtime=[TimeWindow(now + timedelta(days=5), now + timedelta(days=6), "holiday")],
            unplanned_downtime=[TimeWindow(now - timedelta(hours=3), now - timedelta(hours=1), "spindle")],
            preferred_rank=0,
            attributes={"axes": 3},
        ),
        Machine("CNC-02", "DMG 5x", "5-axis mill", ProcessType.CNC_MACHINING, "CNC", preferred_rank=1),
        Machine(
            "AM-01",
            "EOS M290",
            "SLM",
            ProcessType.ADDITIVE_3D_PRINTING,
            "AM",
            status=MachineStatus.MAINTENANCE,
            available_from=now + timedelta(hours=6),
        ),
    ]
    materials = [
        Material(
            "MAT-AL",
            "Aluminium 7075",
            "metal",
            available_quantity=120.0,
            reserved_quantity=20.0,
            compatible_machine_ids={"CNC-01", "CNC-02"},
        ),
        Material(
            "MAT-ST",
            "Steel 316L",
            "metal",
            available_quantity=0.0,
            expected_receipt_date=now + timedelta(days=1),
        ),
    ]
    tooling = [
        Tooling("TOOL-1", "10mm end mill", compatible_machine_ids={"CNC-01", "CNC-02"}, setup_minutes=15.0),
        Tooling("TOOL-2", "Fixture F7", available=False, available_from=now + timedelta(days=1)),
    ]
    calendars = [
        CalendarSpec(
            "CAL-PLANT",
            "Plant 2-shift",
            timezone="Asia/Kolkata",
            shifts=[
                Shift("A", time(6, 0), time(14, 0)),
                Shift("B", time(14, 0), time(22, 0), (0, 1, 2, 3, 4, 5)),
            ],
            holidays=[date(2026, 10, 2)],
            overtime_windows=[TimeWindow(now + timedelta(days=1), now + timedelta(days=1, hours=2), "OT")],
            extra_working_days=[date(2026, 9, 13)],
        )
    ]
    orders = [
        Order(
            "ORD-1",
            "CUST-A",
            "PART-X",
            external_order_ref="SO-1001",
            part_name="Bracket",
            part_family="brackets",
            order_date=now - timedelta(days=3),
            promised_delivery_date=now + timedelta(days=2),
            quantity=10,
            order_status=OrderStatus.RELEASED,
            erp_priority=1,
            order_value=50_000.0,
            estimated_margin=0.3,
            process_type=ProcessType.CNC_MACHINING,
            manufacturing_route=[ProcessType.CNC_MACHINING, ProcessType.DEBURRING],
            machine_group="CNC",
            required_material_id="MAT-AL",
            tooling_requirement={"TOOL-1"},
            depends_on_order_ids=set(),
            attributes={"drawing": "DWG-9"},
        ),
        Order(
            "ORD-2",
            "CUST-B",
            "PART-Y",
            part_name="Housing",
            requested_delivery_date=now + timedelta(days=7),
            revised_delivery_date=now + timedelta(days=9),
            quantity=5,
            completed_quantity=2,
            order_status=OrderStatus.IN_PRODUCTION,
            process_type=ProcessType.CNC_MACHINING,
            machine_group="CNC",
            required_material_id="MAT-ST",
            material_status=MaterialStatus.UNAVAILABLE,
            depends_on_order_ids={"ORD-1"},
        ),
        Order(
            "ORD-3",
            "CUST-A",
            "PART-Z",
            requested_delivery_date=now - timedelta(days=1),
            quantity=1,
            order_status=OrderStatus.NEW,
            process_type=ProcessType.ADDITIVE_3D_PRINTING,
            machine_group="AM",
            on_hold=True,
            hold_reason="awaiting drawing",
            drawing_approved=False,
        ),
        Order(
            "ORD-9", "CUST-B", "PART-Y", quantity=3, completed_quantity=3, order_status=OrderStatus.SHIPPED
        ),
    ]
    operations = [
        Operation(
            "ORD-1-10",
            "ORD-1",
            10,
            ProcessType.CNC_MACHINING,
            "CNC",
            setup_minutes=30,
            cycle_minutes_per_unit=12,
            quantity=10,
            material_id="MAT-AL",
            tooling_ids={"TOOL-1"},
            machine_cycle_minutes={"CNC-02": 9.5},
            setup_family="F1",
        ),
        Operation(
            "ORD-1-20",
            "ORD-1",
            20,
            ProcessType.DEBURRING,
            "CNC",
            setup_minutes=5,
            cycle_minutes_per_unit=2,
            quantity=10,
            prerequisite_operation_id="ORD-1-10",
        ),
        Operation(
            "ORD-2-10",
            "ORD-2",
            10,
            ProcessType.CNC_MACHINING,
            "CNC",
            machine_id="CNC-02",
            setup_minutes=45,
            cycle_minutes_per_unit=20,
            quantity=5,
            completed_quantity=2,
            operation_status=OperationStatus.IN_PROGRESS,
            actual_start=now - timedelta(hours=2),
        ),
        Operation("ORD-3-10", "ORD-3", 10, ProcessType.ADDITIVE_3D_PRINTING, "AM", quantity=1),
        Operation(
            "ORD-9-10",
            "ORD-9",
            10,
            ProcessType.CNC_MACHINING,
            "CNC",
            quantity=3,
            completed_quantity=3,
            operation_status=OperationStatus.COMPLETED,
        ),
    ]
    locks = [
        ScheduleLock(
            "LOCK-1",
            LockType.MACHINE,
            "usr_planner",
            now,
            "keep CNC-01 sequence",
            machine_id="CNC-01",
            window=TimeWindow(now, now + timedelta(hours=4), "lock"),
        ),
        ScheduleLock(
            "LOCK-OLD",
            LockType.TIME_SLOT,
            "usr_planner",
            now - timedelta(days=2),
            "expired",
            machine_id="CNC-02",
            window=TimeWindow(now - timedelta(days=2), now - timedelta(days=1)),
        ),
    ]
    overrides = [
        PriorityOverride(
            "OVR-1", "ORD-2", OverrideType.INCREASE_PRIORITY, "usr_manager", now, "customer call", value=15.0
        ),
        PriorityOverride(
            "OVR-EXP",
            "ORD-1",
            OverrideType.SET_PRIORITY,
            "usr_manager",
            now - timedelta(days=1),
            "old",
            value=90.0,
            expires_at=now - timedelta(hours=1),
        ),
    ]
    expedites = [
        Expedite("EXP-1", "ORD-1", "usr_manager", now, "rush", 30.0, now, now + timedelta(hours=4)),
        Expedite(
            "EXP-FUTURE",
            "ORD-3",
            "usr_manager",
            now,
            "later",
            20.0,
            now + timedelta(days=1),
            now + timedelta(days=2),
        ),
    ]
    rules = [
        CustomerRule(
            "CUST-A",
            sla_hours=36.0,
            tier_override=CustomerTier.STRATEGIC,
            priority_boost_points=5.0,
            notes="key account",
        )
    ]
    return SampleData(
        customers,
        orders,
        operations,
        machines,
        materials,
        tooling,
        calendars,
        locks,
        overrides,
        expedites,
        rules,
    )


@pytest.fixture
def sample() -> SampleData:
    return build_sample()


def load_sample(session: Session, sample: SampleData, now: datetime = NOW) -> None:
    """Persist ``sample`` through the repositories (used by integration tests)."""
    from app.db.repositories import (
        CalendarRepository,
        CustomerRepository,
        ExpediteRepository,
        LockRepository,
        MachineRepository,
        MaterialRepository,
        OrderRepository,
        OverrideRepository,
        ToolingRepository,
    )

    CustomerRepository(session).upsert(sample.customers, synced_at=now)
    OrderRepository(session).upsert(sample.orders, sample.operations, synced_at=now)
    MachineRepository(session).upsert(sample.machines, synced_at=now)
    MaterialRepository(session).upsert(sample.materials, synced_at=now)
    ToolingRepository(session).upsert(sample.tooling, synced_at=now)
    calendars = CalendarRepository(session)
    calendars.upsert(sample.calendars)
    calendars.set_default("CAL-PLANT")
    locks, overrides, expedites = (
        LockRepository(session),
        OverrideRepository(session),
        ExpediteRepository(session),
    )
    for lock in sample.locks:
        locks.add(lock)
    for override in sample.overrides:
        overrides.add(override)
    for expedite in sample.expedites:
        expedites.add(expedite)
    customers = CustomerRepository(session)
    for rule in sample.customer_rules:
        customers.save_rule(rule, updated_by="usr_planner", at=now)
    session.flush()


@pytest.fixture
def loaded_session(db_session: Session, sample: SampleData) -> Session:
    load_sample(db_session, sample)
    return db_session


def as_dict(obj: Any) -> dict[str, Any]:
    from app.db.snapshot_codec import to_jsonable

    return dict(to_jsonable(obj))
