"""End-to-end: connector -> normalizer -> PlanningSnapshot (in memory)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.clock import FrozenClock
from app.core.errors import IntegrationError
from app.domain.enums import OperationStatus
from app.integration.connector import count_by_entity
from app.integration.mock_connector import MockERPConnector
from app.integration.normalizer import NormalizationIssueCode
from app.integration.reconciliation import reconcile
from app.integration.snapshot_builder import build_snapshot_from_connector, fetch_all
from synthetic.generator import SyntheticDataGenerator

pytestmark = pytest.mark.unit


def test_full_build_matches_dataset() -> None:
    dataset = SyntheticDataGenerator(seed=42, scale="small").generate()
    clock = FrozenClock(dataset.as_of + timedelta(hours=1))
    connector = MockERPConnector(dataset, clock)
    snapshot, issues = build_snapshot_from_connector(connector, clock)
    reference = dataset.to_snapshot()

    assert snapshot.as_of == clock.now()
    assert snapshot.source == "mock"
    assert snapshot.default_calendar_id == dataset.default_calendar_id
    assert snapshot.summary() == reference.summary()
    assert set(snapshot.customers) == set(reference.customers)
    assert set(snapshot.machines) == set(reference.machines)
    # only the injected duplicate order line raises an issue (renamed -DUP1)
    assert {i.code for i in issues} == {NormalizationIssueCode.DUPLICATE_ID}
    duplicates = [o for o in snapshot.orders.values() if "duplicate_of" in o.attributes]
    assert len(duplicates) == dataset.stats.dq_defects["duplicate_order_ref"]

    # production status overlay reproduced in-progress work
    in_progress_ref = {
        op.operation_id
        for op in reference.operations.values()
        if op.operation_status == OperationStatus.IN_PROGRESS
    }
    in_progress = {
        op.operation_id
        for op in snapshot.operations.values()
        if op.operation_status == OperationStatus.IN_PROGRESS
    }
    assert in_progress == in_progress_ref
    assert any("last_progress_report_at" in op.attributes for op in snapshot.operations.values())

    # indexes are usable
    order = next(o for o in snapshot.open_orders() if not o.depends_on_order_ids)
    assert snapshot.next_operation_for_order(order.order_id) is not None
    assert snapshot.machines_in_group("CNC3")

    # reconciliation against what the connector served
    report = reconcile(
        count_by_entity(fetch_all(connector).records),
        {
            "customer": len(snapshot.customers),
            "order": len(snapshot.orders),
            "operation": len(snapshot.operations),
            "machine": len(snapshot.machines),
            "material": len(snapshot.materials),
            "tooling": len(snapshot.tooling),
            "calendar": len(snapshot.calendars),
            "production_status": len(connector.fetch_production_status()),
        },
    )
    assert report.status == "ok"


def test_incremental_build_is_partial() -> None:
    dataset = SyntheticDataGenerator(seed=42, scale="small").generate()
    clock = FrozenClock(dataset.as_of)
    connector = MockERPConnector(dataset, clock)
    clock.advance(hours=1)
    machine = dataset.machines[0]
    connector.set_machine_status(machine.machine_id, machine.status)
    snapshot, issues = build_snapshot_from_connector(connector, clock, since=dataset.as_of)
    assert issues == []
    assert list(snapshot.machines) == [machine.machine_id]
    assert snapshot.orders == {} and snapshot.customers == {}
    assert snapshot.default_calendar_id is None


def test_connector_failure_surfaces_as_integration_error() -> None:
    dataset = SyntheticDataGenerator(seed=42, scale="small").generate()
    clock = FrozenClock(dataset.as_of)
    connector = MockERPConnector(dataset, clock)
    connector.set_failure("timeout")
    with pytest.raises(IntegrationError):
        build_snapshot_from_connector(connector, clock)


def test_unexpected_exception_is_wrapped() -> None:
    class Broken:
        name = "broken"

        def __getattr__(self, name: str):
            def fetch(since=None):
                raise RuntimeError("boom")

            return fetch

    with pytest.raises(IntegrationError) as excinfo:
        fetch_all(Broken())  # type: ignore[arg-type]
    assert excinfo.value.details["entity"] == "customer"
