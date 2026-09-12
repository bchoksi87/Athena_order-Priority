"""SyncService end to end: MockERPConnector -> normalizer -> repositories -> ``sync_runs``.

The happy path runs against SQLite and, when ``PPSE_TEST_DATABASE_URL`` is set,
PostgreSQL (``db_session``); the remaining scenarios use the SQLite session.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.core.clock import FrozenClock
from app.core.errors import IntegrationError, NotFoundError, ValidationError
from app.db.repositories import (
    CalendarRepository,
    CustomerRepository,
    MachineRepository,
    MaterialRepository,
    OrderRepository,
    SyncRunRepository,
    ToolingRepository,
)
from app.db.snapshot_builder import DbSnapshotBuilder
from app.domain.enums import MachineStatus, OperationStatus, OrderStatus, ProcessType, SyncMode
from app.domain.models import Machine, Operation, Order, TimeWindow
from app.integration.codes import compose_order_id
from app.integration.connector import ConnectorCapabilities, ConnectorHealth, RawRecord
from app.integration.mock_connector import (
    MockERPConnector,
    machine_payload,
    operation_payload,
    order_payload,
    production_status_payload,
)
from app.integration.reconciliation import ReconciliationThresholds
from app.integration.retry import RetryPolicy
from app.integration.sync_service import (
    SYNC_STATUS_COMPLETED,
    SYNC_STATUS_FAILED,
    SyncIssueCode,
    SyncOptions,
    SyncService,
)
from synthetic.generator import SyntheticDataGenerator, SyntheticDataset

pytestmark = pytest.mark.integration

ENTITIES = (
    "customer",
    "material",
    "machine",
    "tooling",
    "calendar",
    "order",
    "operation",
    "production_status",
)


def no_sleep(_seconds: float) -> None:
    """Retry back-off stub: tests never wait."""


# ----------------------------------------------------------------- fixtures


@pytest.fixture
def dataset() -> SyntheticDataset:
    # Function scoped: the mock connector mutates the dataset objects it wraps.
    return SyntheticDataGenerator(seed=42, scale="small").generate()


@pytest.fixture
def clock(dataset: SyntheticDataset) -> FrozenClock:
    return FrozenClock(dataset.as_of)


@pytest.fixture
def connector(dataset: SyntheticDataset, clock: FrozenClock) -> MockERPConnector:
    return MockERPConnector(dataset, clock)


def make_service(session: Session, connector: Any, clock: FrozenClock, **options: Any) -> SyncService:
    return SyncService(session, connector, clock, options=SyncOptions(**options), sleep=no_sleep)


def dataset_counts(dataset: SyntheticDataset, connector: MockERPConnector) -> dict[str, int]:
    """What the mock ERP serves for a full fetch, per entity."""
    return {
        "customer": len(dataset.customers),
        "material": len(dataset.materials),
        "machine": len(dataset.machines),
        "tooling": len(dataset.tooling),
        "calendar": len(dataset.calendars),
        "order": len(dataset.orders),
        "operation": len(dataset.operations),
        "production_status": len(connector.fetch_production_status()),
    }


def zero_counts(**overrides: int) -> dict[str, int]:
    return {entity: overrides.get(entity, 0) for entity in ENTITIES}


class StubConnector:
    """Serves hand-built raw records (ignores ``since``); lets tests craft referential problems."""

    name = "stub"

    def __init__(self, records: dict[str, list[RawRecord]], at: datetime) -> None:
        self._records = records
        self._at = at

    def _get(self, entity: str) -> list[RawRecord]:
        return list(self._records.get(entity, []))

    def fetch_customers(self, since: datetime | None = None) -> list[RawRecord]:
        return self._get("customer")

    def fetch_orders(self, since: datetime | None = None) -> list[RawRecord]:
        return self._get("order")

    def fetch_operations(self, since: datetime | None = None) -> list[RawRecord]:
        return self._get("operation")

    def fetch_machines(self, since: datetime | None = None) -> list[RawRecord]:
        return self._get("machine")

    def fetch_materials(self, since: datetime | None = None) -> list[RawRecord]:
        return self._get("material")

    def fetch_tooling(self, since: datetime | None = None) -> list[RawRecord]:
        return self._get("tooling")

    def fetch_calendars(self, since: datetime | None = None) -> list[RawRecord]:
        return self._get("calendar")

    def fetch_production_status(self, since: datetime | None = None) -> list[RawRecord]:
        return self._get("production_status")

    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(connector_name=self.name)

    def health(self) -> ConnectorHealth:
        return ConnectorHealth(self.name, True, self._at)


def raw(entity: str, external_id: str, payload: dict[str, Any], at: datetime) -> RawRecord:
    return RawRecord(entity, external_id, payload, at, "STUB-ERP")


# -------------------------------------------------------------- full sync


def test_full_sync_persists_dataset_and_records_run(
    db_session: Session, dataset: SyntheticDataset, connector: MockERPConnector, clock: FrozenClock
) -> None:
    summary = make_service(db_session, connector, clock, triggered_by="test").run("full")
    expected = dataset_counts(dataset, connector)

    assert summary.succeeded and summary.status == SYNC_STATUS_COMPLETED
    assert summary.mode is SyncMode.FULL and summary.since is None and summary.watermark_source == "none"
    assert summary.connector == "mock" and summary.started_at == clock.now() == summary.finished_at
    assert summary.records_fetched == expected
    # the normalizer keeps every record of the small dataset; the only issue is the injected
    # duplicate order line (kept under a -DUP1 suffix for the Data Quality Engine)
    assert summary.records_upserted == expected
    assert summary.issue_counts() == {"duplicate_id": dataset.stats.dq_defects["duplicate_order_ref"]}
    assert {i.stage for i in summary.issues} == {"normalize"}
    assert summary.reconciliation is not None and summary.reconciliation.status == "ok"
    assert summary.stored_totals == {
        entity: expected[entity]
        for entity in ("customer", "order", "operation", "machine", "material", "tooling", "calendar")
    }
    assert summary.pruned_orders == 0

    # stored rows match what the connector served
    assert CustomerRepository(db_session).count() == expected["customer"]
    orders = OrderRepository(db_session)
    assert orders.count() == expected["order"]
    assert orders.count_operations() == expected["operation"]
    assert len(MachineRepository(db_session).list_all()) == expected["machine"]
    assert len(MaterialRepository(db_session).list_all()) == expected["material"]
    assert len(ToolingRepository(db_session).list_all()) == expected["tooling"]
    calendars = CalendarRepository(db_session)
    assert len(calendars.list_all()) == expected["calendar"]
    assert calendars.get_default_id() == dataset.default_calendar_id

    duplicate = next(o for o in dataset.orders if "duplicate_of" in o.attributes)
    stored_duplicate = orders.get(f"{duplicate.attributes['duplicate_of']}-DUP1")
    assert stored_duplicate.attributes["duplicate_of"] == duplicate.attributes["duplicate_of"]
    assert stored_duplicate.attributes["erp_row_id"] == duplicate.order_id

    # the production-status feed reproduced the in-progress work on the stored operations
    in_progress = {
        op.operation_id for op in dataset.operations if op.operation_status is OperationStatus.IN_PROGRESS
    }
    stored_ops = orders.operations_for_orders({op.order_id for op in dataset.operations})
    stored_in_progress = {
        op.operation_id
        for ops in stored_ops.values()
        for op in ops
        if op.operation_status is OperationStatus.IN_PROGRESS
    }
    assert stored_in_progress == in_progress
    assert all(
        "last_progress_report_at" in op.attributes
        for ops in stored_ops.values()
        for op in ops
        if op.operation_id in in_progress
    )

    # the sync_runs row mirrors the summary
    runs = SyncRunRepository(db_session)
    record = runs.get(summary.run_id)
    assert record.status == SYNC_STATUS_COMPLETED and record.mode is SyncMode.FULL
    assert record.connector == "mock" and record.triggered_by == "test"
    assert record.started_at == clock.now() and record.finished_at == clock.now() and record.since is None
    assert record.records_fetched == expected and record.records_upserted == expected
    assert record.issues_count == summary.issues_count == 1
    assert record.error_message is None
    assert record.details["watermark_source"] == "none"
    assert record.details["issue_counts"] == summary.issue_counts()
    assert record.details["reconciliation"]["status"] == "ok"
    assert record.details["stored_totals"] == summary.stored_totals
    assert record.details["issues_truncated"] is False
    latest = runs.latest_successful_started_at()
    assert latest is not None and latest.run_id == summary.run_id


def test_full_sync_is_idempotent(
    session: Session, dataset: SyntheticDataset, connector: MockERPConnector, clock: FrozenClock
) -> None:
    first = make_service(session, connector, clock).run(SyncMode.FULL)
    clock.advance(minutes=30)
    second = make_service(session, connector, clock).run(SyncMode.FULL)

    assert second.records_upserted == first.records_upserted == dataset_counts(dataset, connector)
    assert second.stored_totals == first.stored_totals
    assert second.reconciliation is not None and second.reconciliation.status == "ok"
    orders = OrderRepository(session)
    assert orders.count() == len(dataset.orders)
    assert orders.count_operations() == len(dataset.operations)
    runs = SyncRunRepository(session)
    page = runs.list()
    assert page.total == 2 and all(r.status == SYNC_STATUS_COMPLETED for r in page.items)
    assert page.items[0].run_id == second.run_id  # newest first
    latest = runs.latest_successful_started_at()
    assert latest is not None and latest.run_id == second.run_id


# ------------------------------------------------------------ incremental


def test_incremental_sync_applies_only_the_changed_slice(
    session: Session, dataset: SyntheticDataset, connector: MockERPConnector, clock: FrozenClock
) -> None:
    first = make_service(session, connector, clock).run("full")
    clock.advance(hours=1)

    # mutate the ERP through the mock's helpers (the sync service itself never writes to it)
    order_id = compose_order_id("SO2609-99999", 1)
    order = Order(
        order_id,
        dataset.customers[0].customer_id,
        "P-TEST-1",
        order_line_id="1",
        external_order_ref="SO2609-99999",
        quantity=5,
        order_status=OrderStatus.RELEASED,
        requested_delivery_date=clock.now() + timedelta(days=3),
        process_type=ProcessType.CNC_MACHINING,
        manufacturing_route=[ProcessType.CNC_MACHINING],
        machine_group="CNC3",
    )
    operation = Operation(
        "OP-TEST-1", order_id, 10, ProcessType.CNC_MACHINING, machine_group="CNC3", quantity=5
    )
    connector.add_order(order, [operation])
    machine = dataset.machines[0]
    breakdown = TimeWindow(clock.now(), clock.now() + timedelta(hours=8), "breakdown")
    connector.set_machine_status(machine.machine_id, MachineStatus.DOWN, downtime=breakdown)
    short = next(m for m in dataset.materials if m.available_quantity <= 0)
    connector.receive_material(short.material_id, 25.0)
    ready = next(op for op in dataset.operations if op.operation_status is OperationStatus.READY)
    connector.report_progress(ready.operation_id, OperationStatus.IN_PROGRESS, completed_quantity=1.0)
    reported_at = clock.now()
    clock.advance(minutes=5)

    second = make_service(session, connector, clock).run("incremental")

    assert second.succeeded and second.mode is SyncMode.INCREMENTAL
    assert second.since == first.started_at and second.watermark_source == first.run_id
    assert second.records_fetched == zero_counts(
        material=1, machine=1, order=1, operation=2, production_status=1
    )
    assert second.records_upserted == second.records_fetched
    assert sum(second.records_fetched.values()) == 6  # not a re-upsert of the whole dataset
    assert second.stored_totals == {}  # incremental runs do not count the store
    assert second.issues == []
    assert second.reconciliation is not None and second.reconciliation.status == "ok"

    orders = OrderRepository(session)
    stored_order = orders.get(order_id)
    assert stored_order.order_status is OrderStatus.RELEASED and stored_order.quantity == 5
    assert [op.operation_id for op in orders.get_operations(order_id)] == ["OP-TEST-1"]
    stored_machine = MachineRepository(session).get(machine.machine_id)
    assert stored_machine.status is MachineStatus.DOWN
    assert breakdown in stored_machine.unplanned_downtime
    assert (
        MaterialRepository(session).get(short.material_id).available_quantity == short.available_quantity > 0
    )
    progressed = orders.get_operation(ready.operation_id)
    assert progressed.operation_status is OperationStatus.IN_PROGRESS
    assert progressed.completed_quantity == 1.0 and progressed.actual_start == reported_at
    assert progressed.attributes["last_progress_report_at"] == reported_at.isoformat()
    # untouched rows stay: totals grew only by the added order/operation
    assert orders.count() == len(dataset.orders) + 1
    assert orders.count_operations() == len(dataset.operations) + 1
    assert CustomerRepository(session).count() == len(dataset.customers)

    runs = SyncRunRepository(session).list()
    assert [(r.mode, r.status) for r in runs.items] == [
        (SyncMode.INCREMENTAL, SYNC_STATUS_COMPLETED),
        (SyncMode.FULL, SYNC_STATUS_COMPLETED),
    ]
    assert runs.items[0].since == first.started_at


def test_incremental_without_completed_run_fetches_everything(
    session: Session, dataset: SyntheticDataset, connector: MockERPConnector, clock: FrozenClock
) -> None:
    summary = make_service(session, connector, clock).run("incremental")
    assert summary.succeeded and summary.mode is SyncMode.INCREMENTAL
    assert summary.since is None and summary.watermark_source == "none"
    assert summary.records_fetched == dataset_counts(dataset, connector)
    assert OrderRepository(session).count() == len(dataset.orders)
    assert SyncRunRepository(session).get(summary.run_id).details["watermark_source"] == "none"


# ---------------------------------------------------------------- failures


def test_connector_failure_marks_run_failed_after_retries(
    session: Session, connector: MockERPConnector, clock: FrozenClock
) -> None:
    connector.set_failure("network down")
    sleeps: list[float] = []
    service = SyncService(
        session,
        connector,
        clock,
        options=SyncOptions(retry=RetryPolicy(attempts=3, base_delay_seconds=0.5)),
        sleep=sleeps.append,
    )

    with pytest.raises(IntegrationError) as info:
        service.run("full")

    assert info.value.details["attempts"] == 3
    assert info.value.details["operation"] == "mock.fetch_customers"
    assert "network down" in info.value.message
    assert sleeps == [0.5, 1.0]  # exponential back-off between the three attempts, never slept for real

    runs = SyncRunRepository(session)
    record = runs.latest()
    assert record is not None and record.status == SYNC_STATUS_FAILED
    assert record.error_message is not None and "network down" in record.error_message
    assert record.started_at == clock.now() and record.finished_at == clock.now()
    assert record.records_fetched == {} and record.records_upserted == {} and record.issues_count == 0
    assert record.details["status"] == SYNC_STATUS_FAILED and record.details["error_message"]
    assert CustomerRepository(session).count() == 0
    # a failed run never becomes the incremental watermark
    assert runs.latest_successful_started_at() is None
    connector.set_failure(None)
    recovered = make_service(session, connector, clock).run("incremental")
    assert recovered.succeeded and recovered.since is None and recovered.watermark_source == "none"


def test_failure_during_upsert_rolls_back_partial_writes(
    session: Session, connector: MockERPConnector, clock: FrozenClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulate a crash after the first entity was written: nothing survives, the run is failed."""

    def exploding_upsert(self: SyncService, batch: Any, summary: Any, mode: Any, synced_at: Any) -> None:
        self._customers.upsert(batch.customers, synced_at=synced_at)
        assert CustomerRepository(session).count() > 0  # rows are pending in the transaction
        raise RuntimeError("disk full")

    monkeypatch.setattr(SyncService, "_upsert", exploding_upsert)
    service = make_service(session, connector, clock)

    with pytest.raises(IntegrationError) as info:
        service.run("full")

    assert "disk full" in info.value.message and isinstance(info.value.__cause__, RuntimeError)
    assert CustomerRepository(session).count() == 0
    assert OrderRepository(session).count() == 0
    record = SyncRunRepository(session).get(info.value.details["run_id"])
    assert record.status == SYNC_STATUS_FAILED and "disk full" in (record.error_message or "")
    assert record.records_fetched["customer"] > 0  # the fetch stage had completed


def test_invalid_mode_is_rejected_before_a_run_is_recorded(
    session: Session, connector: MockERPConnector, clock: FrozenClock
) -> None:
    with pytest.raises(ValidationError):
        make_service(session, connector, clock).run("sideways")
    assert SyncRunRepository(session).list().total == 0


# -------------------------------------------------------------- validation


def test_validation_issues_are_recorded_and_referential_gaps_handled(
    session: Session, dataset: SyntheticDataset, connector: MockERPConnector, clock: FrozenClock
) -> None:
    make_service(session, connector, clock).run("full")
    clock.advance(hours=2)
    at = clock.now()
    orders_by_id = {o.order_id: o for o in dataset.orders}

    # order referencing an unknown customer (kept), with an operation on an unknown machine (kept)
    ghost_customer_order = Order(
        compose_order_id("SO2609-77777", 1),
        "CUST-GHOST",
        "P-77777",
        order_line_id="1",
        external_order_ref="SO2609-77777",
        quantity=3,
        order_status=OrderStatus.RELEASED,
    )
    ghost_machine_op = Operation(
        "OP-77777-10", ghost_customer_order.order_id, 10, ProcessType.CNC_MACHINING, machine_id="MC-GHOST"
    )
    # order the normalizer rejects (required PART_NO missing): never stored
    broken_order = Order(
        compose_order_id("SO2609-88888", 1),
        dataset.customers[0].customer_id,
        "P-88888",
        order_line_id="1",
        external_order_ref="SO2609-88888",
    )
    broken_payload = order_payload(broken_order)
    broken_payload["PART_NO"] = ""
    # operation of an order that is neither fetched nor stored (skipped) and a progress report for it
    orphan_order = Order(
        "SO2609-66666-01", "CUST-X", "P", order_line_id="1", external_order_ref="SO2609-66666"
    )
    orphan_op = Operation("OP-GHOST-1", orphan_order.order_id, 10, ProcessType.DEBURRING)
    orphan_progress = replace(orphan_op, operation_status=OperationStatus.IN_PROGRESS)
    # machine referencing an unknown calendar (kept)
    new_machine = Machine(
        "MC-NEW-01", "New mill", "3-axis", ProcessType.CNC_MACHINING, "CNC3", calendar_id="CAL-GHOST"
    )
    # progress for a stored operation that is not part of this slice (applied against the store) ...
    stored_op = next(op for op in dataset.operations if op.operation_status is OperationStatus.READY)
    completed = replace(
        stored_op,
        operation_status=OperationStatus.COMPLETED,
        completed_quantity=stored_op.quantity,
        actual_end=at,
    )
    # ... and for an operation nobody knows (skipped)
    nowhere_order = Order("NOWHERE-01", "C", "P", order_line_id="1", external_order_ref="NOWHERE")
    nowhere_op = Operation(
        "OP-NOWHERE",
        nowhere_order.order_id,
        1,
        ProcessType.OTHER,
        operation_status=OperationStatus.IN_PROGRESS,
    )

    stub = StubConnector(
        {
            "machine": [raw("machine", new_machine.machine_id, machine_payload(new_machine), at)],
            "order": [
                raw("order", ghost_customer_order.order_id, order_payload(ghost_customer_order), at),
                raw("order", broken_order.order_id, broken_payload, at),
            ],
            "operation": [
                raw(
                    "operation",
                    ghost_machine_op.operation_id,
                    operation_payload(ghost_machine_op, ghost_customer_order),
                    at,
                ),
                raw("operation", orphan_op.operation_id, operation_payload(orphan_op, orphan_order), at),
            ],
            "production_status": [
                raw(
                    "production_status",
                    completed.operation_id,
                    production_status_payload(completed, orders_by_id[stored_op.order_id], at),
                    at,
                ),
                raw(
                    "production_status",
                    nowhere_op.operation_id,
                    production_status_payload(nowhere_op, nowhere_order, at),
                    at,
                ),
                raw(
                    "production_status",
                    orphan_progress.operation_id,
                    production_status_payload(orphan_progress, orphan_order, at),
                    at,
                ),
            ],
        },
        at,
    )
    strict = ReconciliationThresholds(warning_pct=1.0, mismatch_pct=25.0, absolute_tolerance=0)
    summary = make_service(session, stub, clock, max_stored_issues=3, reconciliation=strict).run(
        "incremental"
    )

    assert summary.succeeded
    assert summary.records_fetched == zero_counts(machine=1, order=2, operation=2, production_status=3)
    assert summary.records_upserted == zero_counts(machine=1, order=1, operation=1, production_status=1)
    assert summary.issue_counts() == {
        "missing_required": 1,
        SyncIssueCode.UNKNOWN_CALENDAR: 1,
        SyncIssueCode.UNKNOWN_CUSTOMER: 1,
        SyncIssueCode.UNKNOWN_MACHINE: 1,
        SyncIssueCode.UNKNOWN_OPERATION: 2,
        SyncIssueCode.UNKNOWN_ORDER: 1,
    }
    by_code = {(i.code, i.external_id): i for i in summary.issues}
    assert by_code[("missing_required", broken_order.order_id)].stage == "normalize"
    assert by_code[(SyncIssueCode.UNKNOWN_CUSTOMER, ghost_customer_order.order_id)].field == "customer_id"
    assert by_code[(SyncIssueCode.UNKNOWN_MACHINE, "OP-77777-10")].field == "machine_id"
    assert by_code[(SyncIssueCode.UNKNOWN_ORDER, "OP-GHOST-1")].stage == "validate"
    assert by_code[(SyncIssueCode.UNKNOWN_CALENDAR, "MC-NEW-01")].message.startswith("calendar 'CAL-GHOST'")
    assert {
        (SyncIssueCode.UNKNOWN_OPERATION, "OP-NOWHERE"),
        (SyncIssueCode.UNKNOWN_OPERATION, "OP-GHOST-1"),
    } <= set(by_code)
    # the dropped slice is flagged by reconciliation, the run still completes
    assert summary.reconciliation is not None and summary.reconciliation.status == "mismatch"
    flagged = {d.entity: d.status for d in summary.reconciliation.deltas if d.status != "ok"}
    assert flagged == {"order": "mismatch", "operation": "mismatch", "production_status": "mismatch"}

    orders = OrderRepository(session)
    assert orders.get(ghost_customer_order.order_id).customer_id == "CUST-GHOST"
    assert [op.machine_id for op in orders.get_operations(ghost_customer_order.order_id)] == ["MC-GHOST"]
    assert not orders.exists(broken_order.order_id)
    with pytest.raises(NotFoundError):
        orders.get_operation("OP-GHOST-1")
    assert MachineRepository(session).get("MC-NEW-01").calendar_id == "CAL-GHOST"
    progressed = orders.get_operation(stored_op.operation_id)
    assert progressed.operation_status is OperationStatus.COMPLETED
    assert progressed.completed_quantity == stored_op.quantity and progressed.actual_end == at
    assert progressed.attributes["last_progress_report_at"] == at.isoformat()

    record = SyncRunRepository(session).get(summary.run_id)
    assert record.issues_count == 7
    assert record.details["issue_counts"] == summary.issue_counts()
    assert len(record.details["issues"]) == 3 and record.details["issues_truncated"] is True
    assert record.details["issues"][0] == summary.issues[0].to_dict()
    assert record.details["reconciliation"]["status"] == "mismatch"


def test_full_sync_reconciles_against_the_store_and_can_prune(
    session: Session, dataset: SyntheticDataset, connector: MockERPConnector, clock: FrozenClock
) -> None:
    make_service(session, connector, clock).run("full")
    orders = OrderRepository(session)
    legacy = Order("LEGACY-01", dataset.customers[0].customer_id, "P-OLD", quantity=1)
    orders.upsert(
        [legacy], [Operation("LEGACY-01-10", "LEGACY-01", 10, ProcessType.OTHER)], synced_at=clock.now()
    )
    clock.advance(minutes=10)

    kept = make_service(session, connector, clock).run("full")
    assert kept.pruned_orders == 0 and orders.exists("LEGACY-01")
    assert kept.stored_totals["order"] == len(dataset.orders) + 1
    assert kept.reconciliation is not None
    order_delta = next(d for d in kept.reconciliation.deltas if d.entity == "order")
    assert (order_delta.connector_count, order_delta.stored_count, order_delta.status) == (
        len(dataset.orders),
        len(dataset.orders) + 1,
        "ok",  # one stale row is within the absolute tolerance
    )

    pruned = make_service(session, connector, clock, prune_missing_orders=True).run("full")
    assert pruned.pruned_orders == 1 and not orders.exists("LEGACY-01")
    with pytest.raises(NotFoundError):
        orders.get_operation("LEGACY-01-10")  # cascaded with its order
    assert pruned.stored_totals["order"] == len(dataset.orders)
    assert pruned.stored_totals["operation"] == len(dataset.operations)
    assert SyncRunRepository(session).get(pruned.run_id).details["pruned_orders"] == 1


# ---------------------------------------------------------------- snapshot


def test_db_snapshot_after_sync_matches_dataset(
    session: Session, dataset: SyntheticDataset, connector: MockERPConnector, clock: FrozenClock
) -> None:
    make_service(session, connector, clock).run("full")
    reference = dataset.to_snapshot()
    builder = DbSnapshotBuilder(session)

    everything = builder.build(clock.now(), include_closed_orders=True)
    assert everything.source == "db" and everything.as_of == clock.now()
    assert everything.default_calendar_id == dataset.default_calendar_id
    expected = dict(reference.summary())
    expected["customers"] = len(
        {o.customer_id for o in dataset.orders}
    )  # only referenced customers are loaded
    assert everything.summary() == expected
    assert set(everything.machines) == set(reference.machines)
    assert set(everything.materials) == set(reference.materials)
    assert set(everything.tooling) == set(reference.tooling)
    assert set(everything.calendars) == set(reference.calendars)
    assert everything.machines_in_group("CNC3")

    # the planning view carries open orders only (the injected duplicate is stored as -DUP1)
    planning = builder.build(clock.now())
    assert (
        planning.summary()["orders"]
        == planning.summary()["open_orders"]
        == reference.summary()["open_orders"]
    )
    reference_open = {o.order_id for o in reference.open_orders() if "duplicate_of" not in o.attributes}
    planning_open = {o.order_id for o in planning.open_orders()}
    assert reference_open <= planning_open and len(planning_open) == len(reference.open_orders())
    assert {op.order_id for op in planning.operations.values()} <= planning_open
    order = next(o for o in planning.open_orders() if not o.depends_on_order_ids)
    assert planning.next_operation_for_order(order.order_id) is not None
