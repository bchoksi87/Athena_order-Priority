"""MockERPConnector: raw record shape, since-filtering, mutations, registry."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.clock import FrozenClock
from app.core.errors import IntegrationError, NotFoundError
from app.domain.enums import MachineStatus, OperationStatus, OrderStatus, ProcessType
from app.domain.models import Operation, Order, TimeWindow
from app.integration.codes import compose_order_id
from app.integration.connector import ConnectorRegistry, ERPConnector, RawRecord
from app.integration.mock_connector import MockERPConnector
from synthetic.generator import SyntheticDataGenerator, SyntheticDataset

pytestmark = pytest.mark.unit


@pytest.fixture()
def dataset() -> SyntheticDataset:
    return SyntheticDataGenerator(seed=42, scale="small").generate()


@pytest.fixture()
def clock(dataset: SyntheticDataset) -> FrozenClock:
    return FrozenClock(dataset.as_of)


@pytest.fixture()
def connector(dataset: SyntheticDataset, clock: FrozenClock) -> MockERPConnector:
    return MockERPConnector(dataset, clock)


def test_satisfies_protocol(connector: MockERPConnector) -> None:
    assert isinstance(connector, ERPConnector)
    assert connector.name == "mock"
    health = connector.health()
    assert health.healthy and health.details["orders"] == len(connector.fetch_orders())


def test_records_look_like_erp_rows(connector: MockERPConnector, dataset: SyntheticDataset) -> None:
    orders = connector.fetch_orders()
    assert len(orders) == len(dataset.orders)
    record = orders[0]
    assert isinstance(record, RawRecord) and record.entity == "order" and record.source == "MOCK-ERP"
    payload = record.payload
    assert {"ORDER_NO", "LINE_NO", "CUST_CODE", "DUE_DT" if False else "REQ_DT", "QTY", "STATUS"} <= set(
        payload
    )
    assert payload["STATUS"] in {
        "NEW",
        "REL",
        "PLN",
        "SCH",
        "MTW",
        "TLW",
        "WIP",
        "PCM",
        "QIN",
        "RWK",
        "CMP",
        "PKD",
        "SHP",
        "HLD",
    }
    assert isinstance(payload["REQ_DT"], str)
    assert payload["DWG_APPROVED"] in ("Y", "N")
    assert ">" in payload["ROUTE"]
    assert record.updated_at.tzinfo is not None

    machine = connector.fetch_machines()[0].payload
    assert machine["STATUS"] in {"AVL", "RUN", "DWN", "MNT", "OFF"}
    assert float(machine["EFFICIENCY_PCT"]) > 10  # percent, not ratio
    assert isinstance(machine["MAINT_WINDOWS"], list)

    customer = connector.fetch_customers()[0].payload
    assert customer["TIER_CODE"] in "ABCD" and customer["ACTIVE"] in ("Y", "N")

    calendar = connector.fetch_calendars()
    assert {c.payload["IS_DEFAULT"] for c in calendar} == {"Y", "N"}
    assert calendar[0].payload["SHIFTS"][0]["START"] == "06:00"

    ops = connector.fetch_operations()
    assert len(ops) == len(dataset.operations)
    assert ops[0].payload["OP_STATUS"] in {"PND", "RDY", "SCH", "WIP", "CMP", "HLD", "RWK", "CAN"}

    progress = connector.fetch_production_status()
    assert progress and all(r.entity == "production_status" for r in progress)
    assert all(r.payload["REPORTED_AT"] for r in progress)


def test_since_filtering(connector: MockERPConnector, dataset: SyntheticDataset) -> None:
    assert connector.fetch_orders(since=dataset.as_of) == []
    assert connector.fetch_machines(since=dataset.as_of) == []
    recent = connector.fetch_orders(since=dataset.as_of - timedelta(days=10))
    assert 0 < len(recent) < len(dataset.orders)
    assert all(r.updated_at > dataset.as_of - timedelta(days=10) for r in recent)
    assert connector.fetch_operations(since=dataset.as_of) == []
    assert connector.fetch_production_status(since=dataset.as_of) == []


def test_add_order_is_visible_incrementally(
    connector: MockERPConnector, dataset: SyntheticDataset, clock: FrozenClock
) -> None:
    clock.advance(hours=1)
    order_id = compose_order_id("SO2609-99999", 1)
    order = Order(
        order_id=order_id,
        customer_id=dataset.customers[0].customer_id,
        part_id="P-TEST-1",
        order_line_id="1",
        external_order_ref="SO2609-99999",
        quantity=5,
        order_status=OrderStatus.RELEASED,
        requested_delivery_date=dataset.as_of + timedelta(days=3),
        manufacturing_route=[ProcessType.CNC_MACHINING],
    )
    op = Operation("OP-TEST-1", order_id, 10, ProcessType.CNC_MACHINING, machine_group="CNC3", quantity=5)
    connector.add_order(order, [op])
    new_orders = connector.fetch_orders(since=dataset.as_of)
    assert [r.external_id for r in new_orders] == [order_id]
    assert new_orders[0].updated_at == clock.now()
    assert [r.external_id for r in connector.fetch_operations(since=dataset.as_of)] == ["OP-TEST-1"]
    assert len(connector.fetch_orders()) == len(dataset.orders) + 1


def test_machine_and_material_mutations(
    connector: MockERPConnector, dataset: SyntheticDataset, clock: FrozenClock
) -> None:
    clock.advance(minutes=30)
    machine = dataset.machines[0]
    window = TimeWindow(clock.now(), clock.now() + timedelta(hours=8), "breakdown")
    connector.set_machine_status(machine.machine_id, MachineStatus.DOWN, downtime=window)
    changed = connector.fetch_machines(since=dataset.as_of)
    assert [r.external_id for r in changed] == [machine.machine_id]
    assert changed[0].payload["STATUS"] == "DWN"
    assert changed[0].payload["UNPLANNED_DOWN"][-1]["REASON"] == "breakdown"

    short = next(m for m in dataset.materials if m.available_quantity <= 0)
    connector.receive_material(short.material_id, short.incoming_quantity)
    changed_materials = connector.fetch_materials(since=dataset.as_of)
    assert [r.external_id for r in changed_materials] == [short.material_id]
    assert float(changed_materials[0].payload["QTY_ONHAND"]) > 0
    assert changed_materials[0].payload["QTY_INCOMING"] == 0.0
    assert changed_materials[0].payload["EXPECTED_DT"] == ""

    with pytest.raises(NotFoundError):
        connector.set_machine_status("MC-NOPE", MachineStatus.DOWN)
    with pytest.raises(NotFoundError):
        connector.receive_material("MAT-NOPE", 1.0)


def test_report_progress_feeds_production_status(
    connector: MockERPConnector, dataset: SyntheticDataset, clock: FrozenClock
) -> None:
    clock.advance(hours=2)
    pending = next(op for op in dataset.operations if op.operation_status == OperationStatus.READY)
    connector.report_progress(pending.operation_id, OperationStatus.IN_PROGRESS, completed_quantity=1.0)
    rows = connector.fetch_production_status(since=dataset.as_of)
    assert [r.external_id for r in rows] == [pending.operation_id]
    assert rows[0].payload["OP_STATUS"] == "WIP" and rows[0].payload["QTY_DONE"] == 1.0
    connector.report_progress(pending.operation_id, OperationStatus.COMPLETED)
    assert pending.completed_quantity == pending.quantity and pending.actual_end == clock.now()


def test_failure_mode(connector: MockERPConnector) -> None:
    connector.set_failure("network down")
    assert not connector.health().healthy
    with pytest.raises(IntegrationError):
        connector.fetch_orders()
    connector.set_failure(None)
    assert connector.fetch_orders()


def test_capabilities_declared(connector: MockERPConnector) -> None:
    caps = connector.capabilities()
    assert caps.supports_incremental and not caps.supports_webhooks
    assert caps.has("order", "order_id") and caps.has("order", "requested_delivery_date")
    assert caps.has("operation", "cycle_minutes_per_unit") and caps.has("machine", "machine_group")


def test_registry_creates_mock(clock: FrozenClock, dataset: SyntheticDataset) -> None:
    registry = ConnectorRegistry.default()
    assert registry.names() == ["mock"]
    connector = registry.create("mock", clock, dataset=dataset)
    assert isinstance(connector, MockERPConnector)
    generated = registry.create("mock", clock, seed=3, scale="small")
    assert len(generated.fetch_customers()) == 80
    with pytest.raises(NotFoundError):
        registry.create("sap", clock)
