"""Master-data repositories (customers, orders, machines, materials, tooling, calendars)
and the DbSnapshotBuilder. Parametrised over SQLite and PostgreSQL via ``db_session``."""

from __future__ import annotations

import copy
from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, ValidationError
from app.db.models import DOWNTIME_KIND_UNPLANNED
from app.db.repositories import (
    CalendarRepository,
    CustomerRepository,
    MachineRepository,
    MaterialRepository,
    OrderFilters,
    OrderRepository,
    ToolingRepository,
)
from app.db.snapshot_builder import DbSnapshotBuilder
from app.domain.enums import CustomerTier, MachineStatus, OrderStatus, ProcessType
from app.domain.models import CustomerRule, Machine, Operation, Order, TimeWindow
from tests.conftest import NOW, SampleData

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------- customers


def test_customer_upsert_get_list(loaded_session: Session, sample: SampleData) -> None:
    repo = CustomerRepository(loaded_session)
    assert repo.get("CUST-A") == sample.customers[0]
    assert repo.count() == 2
    assert [c.customer_id for c in repo.list_all()] == ["CUST-A", "CUST-B"]
    page = repo.list(search="aero")
    assert page.total == 1 and page.items[0].customer_id == "CUST-A"
    assert repo.get_many(["CUST-A", "CUST-B", "missing"]).keys() == {"CUST-A", "CUST-B"}
    with pytest.raises(NotFoundError):
        repo.get("nope")

    changed = copy.deepcopy(sample.customers[1])
    changed.customer_name = "Bulk Parts Ltd (renamed)"
    changed.customer_tier = CustomerTier.KEY
    assert repo.upsert([changed]) == 1
    assert repo.get("CUST-B") == changed
    assert repo.count() == 2
    assert repo.upsert([]) == 0


def test_customer_rules(loaded_session: Session, sample: SampleData) -> None:
    repo = CustomerRepository(loaded_session)
    assert repo.get_rule("CUST-A") == sample.customer_rules[0]
    assert repo.get_rule("CUST-B") is None
    assert repo.list_rules() == {"CUST-A": sample.customer_rules[0]}
    updated = CustomerRule("CUST-A", priority_boost_points=12.0, active=False)
    assert repo.save_rule(updated, updated_by="usr_admin", at=NOW) == updated
    assert repo.list_rules(active_only=True) == {}
    assert repo.list_rules(active_only=False) == {"CUST-A": updated}
    with pytest.raises(NotFoundError):
        repo.save_rule(CustomerRule("ghost"))
    assert repo.delete_rule("CUST-A") is True
    assert repo.delete_rule("CUST-A") is False


# ------------------------------------------------------------------- orders


def test_order_upsert_and_get(loaded_session: Session, sample: SampleData) -> None:
    repo = OrderRepository(loaded_session)
    for order in sample.orders:
        assert repo.get(order.order_id) == order
    assert repo.exists("ORD-1") and not repo.exists("ORD-404")
    ops = repo.get_operations("ORD-1")
    assert [o.operation_id for o in ops] == ["ORD-1-10", "ORD-1-20"]
    assert ops[0] == sample.operations[0]
    assert repo.get_operation("ORD-2-10") == sample.operations[2]
    with pytest.raises(NotFoundError):
        repo.get("ORD-404")
    with pytest.raises(NotFoundError):
        repo.get_operation("nope")
    assert repo.get_many(["ORD-1", "ORD-9"]).keys() == {"ORD-1", "ORD-9"}
    grouped = repo.operations_for_orders(["ORD-1", "ORD-2", "ORD-404"])
    assert set(grouped) == {"ORD-1", "ORD-2"}
    assert len(grouped["ORD-1"]) == 2


def test_order_upsert_replaces_operations(loaded_session: Session, sample: SampleData) -> None:
    repo = OrderRepository(loaded_session)
    order = copy.deepcopy(sample.orders[0])
    order.quantity = 20
    new_ops = [
        Operation("ORD-1-10", "ORD-1", 10, ProcessType.CNC_MACHINING, "CNC", quantity=20, setup_minutes=31),
        Operation("ORD-1-30", "ORD-1", 30, ProcessType.INSPECTION, "QA", quantity=20),
    ]
    repo.upsert([order], new_ops, synced_at=NOW)
    ops = repo.get_operations("ORD-1")
    assert [o.operation_id for o in ops] == ["ORD-1-10", "ORD-1-30"]
    assert ops[0].setup_minutes == 31
    assert repo.get("ORD-1").quantity == 20
    # other orders' operations untouched
    assert len(repo.get_operations("ORD-2")) == 1
    # replace_operations=False keeps existing operations that are not resent
    repo.upsert(
        [order],
        [Operation("ORD-1-40", "ORD-1", 40, ProcessType.PACKING, quantity=20)],
        replace_operations=False,
    )
    assert [o.operation_id for o in repo.get_operations("ORD-1")] == ["ORD-1-10", "ORD-1-30", "ORD-1-40"]


def test_order_get_open(loaded_session: Session) -> None:
    repo = OrderRepository(loaded_session)
    orders, ops = repo.get_open(with_operations=True)
    assert [o.order_id for o in orders] == ["ORD-1", "ORD-2", "ORD-3"]
    assert set(ops) == {"ORD-1", "ORD-2", "ORD-3"}
    orders_only, no_ops = repo.get_open()
    assert len(orders_only) == 3 and no_ops == {}


def test_order_list_filters_and_pagination(loaded_session: Session) -> None:
    repo = OrderRepository(loaded_session)
    everything = repo.list()
    assert everything.total == 4
    # ordered by due date (nulls last) then id: ORD-3 (overdue), ORD-1, ORD-2, ORD-9 (no due date)
    assert [o.order_id for o in everything.items] == ["ORD-3", "ORD-1", "ORD-2", "ORD-9"]

    page = repo.list(limit=2)
    assert [o.order_id for o in page.items] == ["ORD-3", "ORD-1"] and page.has_more
    page2 = repo.list(offset=2, limit=2)
    assert [o.order_id for o in page2.items] == ["ORD-2", "ORD-9"] and not page2.has_more

    assert [
        o.order_id for o in repo.list(OrderFilters(statuses=[OrderStatus.RELEASED, OrderStatus.NEW])).items
    ] == ["ORD-3", "ORD-1"]
    assert [o.order_id for o in repo.list(OrderFilters(customer_id="CUST-B")).items] == ["ORD-2", "ORD-9"]
    assert [o.order_id for o in repo.list(OrderFilters(machine_group="AM")).items] == ["ORD-3"]
    cnc = repo.list(OrderFilters(process_type=ProcessType.CNC_MACHINING))
    assert [o.order_id for o in cnc.items] == ["ORD-1", "ORD-2"]  # ORD-9 has process_type OTHER
    assert [
        o.order_id for o in repo.list(OrderFilters(due_from=NOW, due_to=NOW + timedelta(days=3))).items
    ] == ["ORD-1"]
    assert [o.order_id for o in repo.list(OrderFilters(search="so-10")).items] == ["ORD-1"]
    assert [o.order_id for o in repo.list(OrderFilters(search="part-y")).items] == ["ORD-2", "ORD-9"]
    assert [o.order_id for o in repo.list(OrderFilters(search="HOUSING")).items] == ["ORD-2"]
    assert [o.order_id for o in repo.list(OrderFilters(open_only=True)).items] == ["ORD-3", "ORD-1", "ORD-2"]
    assert [o.order_id for o in repo.list(OrderFilters(on_hold=True)).items] == ["ORD-3"]
    assert repo.count(OrderFilters(customer_id="CUST-A")) == 2
    assert repo.status_counts() == {"released": 1, "in_production": 1, "new": 1, "shipped": 1}
    with pytest.raises(ValidationError):
        repo.list(limit=0)
    with pytest.raises(ValidationError):
        repo.list(offset=-1)


def test_order_status_hold_and_delete_missing(loaded_session: Session) -> None:
    repo = OrderRepository(loaded_session)
    assert repo.update_status("ORD-1", OrderStatus.ON_HOLD).order_status is OrderStatus.ON_HOLD
    held = repo.set_hold("ORD-1", True, "customer request")
    assert held.on_hold and held.hold_reason == "customer request"
    released = repo.set_hold("ORD-1", False, None)
    assert not released.on_hold and released.hold_reason is None
    with pytest.raises(NotFoundError):
        repo.set_hold("nope", True, "x")
    assert repo.delete_missing(["ORD-1", "ORD-2"]) == 2
    assert repo.list().total == 2
    assert repo.get_operations("ORD-3") == []  # cascade removed operations


# ----------------------------------------------------------------- machines


def test_machine_repository(loaded_session: Session, sample: SampleData) -> None:
    repo = MachineRepository(loaded_session)
    assert repo.get("CNC-01") == sample.machines[0]
    machines = repo.list_all()
    assert [m.machine_id for m in machines] == ["AM-01", "CNC-01", "CNC-02"]
    assert machines[1].all_downtime == sample.machines[0].all_downtime
    assert [m.machine_id for m in repo.list_all(machine_group="CNC")] == ["CNC-01", "CNC-02"]
    assert [m.machine_id for m in repo.list_all(process_type=ProcessType.ADDITIVE_3D_PRINTING)] == ["AM-01"]
    assert [m.machine_id for m in repo.list_all(status=MachineStatus.MAINTENANCE)] == ["AM-01"]
    assert repo.groups() == ["AM", "CNC"]
    with pytest.raises(NotFoundError):
        repo.get("nope")

    changed = copy.deepcopy(sample.machines[0])
    changed.maintenance_windows = []
    changed.unplanned_downtime = [TimeWindow(NOW, NOW + timedelta(hours=1), "new")]
    changed.status = MachineStatus.DOWN
    repo.upsert([changed])
    reloaded = repo.get("CNC-01")
    assert reloaded == changed
    assert len(reloaded.all_downtime) == 2

    assert repo.set_status("CNC-01", MachineStatus.AVAILABLE).status is MachineStatus.AVAILABLE
    with_dt = repo.add_downtime(
        "CNC-01", DOWNTIME_KIND_UNPLANNED, TimeWindow(NOW, NOW + timedelta(hours=2), "crash")
    )
    assert len(with_dt.unplanned_downtime) == 2
    with pytest.raises(ValidationError):
        repo.add_downtime("CNC-01", "bogus", TimeWindow(NOW, NOW))
    assert repo.upsert([Machine("NEW", "n", "t", ProcessType.PACKING, "PACK")]) == 1
    assert repo.get("NEW").machine_group == "PACK"


def test_material_and_tooling_repositories(loaded_session: Session, sample: SampleData) -> None:
    materials = MaterialRepository(loaded_session)
    assert materials.get("MAT-AL") == sample.materials[0]
    assert [m.material_id for m in materials.list_all()] == ["MAT-AL", "MAT-ST"]
    changed = copy.deepcopy(sample.materials[1])
    changed.available_quantity = 50.0
    materials.upsert([changed])
    assert materials.get("MAT-ST").available_quantity == 50.0
    with pytest.raises(NotFoundError):
        materials.get("nope")

    tooling = ToolingRepository(loaded_session)
    assert tooling.get("TOOL-2") == sample.tooling[1]
    assert [t.tooling_id for t in tooling.list_all()] == ["TOOL-1", "TOOL-2"]
    changed_tool = copy.deepcopy(sample.tooling[1])
    changed_tool.available = True
    tooling.upsert([changed_tool])
    assert tooling.get("TOOL-2").available is True
    with pytest.raises(NotFoundError):
        tooling.get("nope")


def test_calendar_repository(loaded_session: Session, sample: SampleData) -> None:
    repo = CalendarRepository(loaded_session)
    assert repo.get("CAL-PLANT") == sample.calendars[0]
    assert repo.get_default_id() == "CAL-PLANT"
    other = copy.deepcopy(sample.calendars[0])
    other.calendar_id = "CAL-NIGHT"
    other.shifts = other.shifts[1:]
    repo.upsert([other])
    assert [c.calendar_id for c in repo.list_all()] == ["CAL-NIGHT", "CAL-PLANT"]
    repo.set_default("CAL-NIGHT")
    assert repo.get_default_id() == "CAL-NIGHT"
    with pytest.raises(NotFoundError):
        repo.set_default("nope")
    with pytest.raises(NotFoundError):
        repo.get("nope")


# ------------------------------------------------------------------ builder


def test_snapshot_builder_matches_in_memory_snapshot(loaded_session: Session, sample: SampleData) -> None:
    built = DbSnapshotBuilder(loaded_session).build(NOW)
    expected = sample.snapshot()
    assert built.as_of == NOW
    assert built.orders == expected.orders  # closed ORD-9 excluded
    assert built.operations == expected.operations
    assert built.machines == expected.machines
    assert built.materials == expected.materials
    assert built.tooling == expected.tooling
    assert built.calendars == expected.calendars
    assert built.default_calendar_id == "CAL-PLANT"
    assert built.customers == expected.customers
    assert built.customer_rules == expected.customer_rules
    # only overlays still relevant at ``as_of`` are loaded
    assert [lock.lock_id for lock in built.locks] == ["LOCK-1"]
    assert [o.override_id for o in built.overrides] == ["OVR-1"]
    assert {e.expedite_id for e in built.expedites} == {"EXP-1", "EXP-FUTURE"}
    assert built.active_expedites() == {"ORD-1": sample.expedites[0]}
    assert built.source == "db"
    assert [op.operation_id for op in built.operations_for_order("ORD-1")] == ["ORD-1-10", "ORD-1-20"]
    assert built.machines_in_group("CNC")[0].machine_id == "CNC-01"


def test_snapshot_builder_query_count_is_bounded(loaded_session: Session, sample: SampleData) -> None:
    """Twice the data must not mean twice the queries (no N+1)."""
    from sqlalchemy import event

    repo = OrderRepository(loaded_session)
    extra_orders = []
    extra_ops = []
    for i in range(20):
        order = Order(
            f"ORD-X{i}", "CUST-B", "PART-N", quantity=1, requested_delivery_date=NOW + timedelta(days=i)
        )
        extra_orders.append(order)
        extra_ops.append(
            Operation(f"ORD-X{i}-10", order.order_id, 10, ProcessType.CNC_MACHINING, "CNC", quantity=1)
        )
    repo.upsert(extra_orders, extra_ops)

    statements: list[str] = []
    connection = loaded_session.connection()

    def _count(_conn: object, _cursor: object, statement: str, *_args: object) -> None:
        statements.append(statement)

    event.listen(connection, "before_cursor_execute", _count)
    try:
        built = DbSnapshotBuilder(loaded_session).build(NOW)
    finally:
        event.remove(connection, "before_cursor_execute", _count)
    assert len(built.orders) == 23
    assert len(statements) <= 15


def test_snapshot_builder_can_include_closed_orders(loaded_session: Session) -> None:
    built = DbSnapshotBuilder(loaded_session).build(NOW, include_closed_orders=True)
    assert "ORD-9" in built.orders
    assert "ORD-9-10" in built.operations
