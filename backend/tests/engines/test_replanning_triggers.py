"""detect_events: one event per change type between two snapshots."""

from __future__ import annotations

from datetime import timedelta

from app.domain.config import AlertConfig
from app.domain.enums import (
    CustomerTier,
    MachineStatus,
    MaterialStatus,
    OperationStatus,
    OrderStatus,
    OverrideType,
    QualityStatus,
    ReplanTriggerType,
)
from app.domain.models import TimeWindow
from app.domain.snapshot import PlanningSnapshot
from app.engines.replanning import ReplanEvent, detect_events, manual_event
from tests.engines.factories import (
    NOW,
    at,
    make_customer_rule,
    make_expedite,
    make_lock,
    make_machine,
    make_operation,
    make_order,
    make_override,
    make_plant_snapshot,
)


def _pair() -> tuple[PlanningSnapshot, PlanningSnapshot]:
    previous = make_plant_snapshot(4)
    current = previous.clone()
    current.as_of = at(minutes=30)
    return previous, current


def _by_type(events: list[ReplanEvent]) -> dict[ReplanTriggerType, list[ReplanEvent]]:
    out: dict[ReplanTriggerType, list[ReplanEvent]] = {}
    for e in events:
        out.setdefault(e.type, []).append(e)
    return out


def test_identical_snapshots_produce_no_events() -> None:
    previous, current = _pair()
    assert detect_events(previous, current) == []


def test_new_and_completed_orders() -> None:
    previous, current = _pair()
    new = make_order("O9", requested_delivery_date=at(hours=-1))
    current.orders["O9"] = new
    current.expedites.append(make_expedite("O9"))
    current.overrides.append(make_override("O9", OverrideType.FORCE_NEXT))
    current.orders["O0"].order_status = OrderStatus.COMPLETED
    current.orders["O1"].order_status = OrderStatus.CANCELLED
    del current.orders["O2"]
    current.rebuild_indexes()
    events = _by_type(detect_events(previous, current))
    new_events = events[ReplanTriggerType.NEW_ORDER]
    assert [e.entity_id for e in new_events] == ["O9"]
    assert new_events[0].details["overdue"] is True
    assert new_events[0].details["expedited"] is True and new_events[0].details["forced_next"] is True
    assert new_events[0].occurred_at == at(minutes=30) and new_events[0].order_id == "O9"
    done = {e.entity_id: e.details["reason"] for e in events[ReplanTriggerType.ORDER_COMPLETED]}
    assert done == {"O0": "completed", "O1": "cancelled", "O2": "missing"}
    # expedite + override on the new order also surface as priority change / manual events
    assert {e.details.get("change") for e in events[ReplanTriggerType.CUSTOMER_PRIORITY_CHANGE]} == {
        "expedite"
    }
    assert events[ReplanTriggerType.MANUAL][0].details["override_type"] == "force_next"


def test_machine_status_and_downtime_changes() -> None:
    previous, current = _pair()
    current.machines["CNC-01"].status = MachineStatus.DOWN
    current.machines["CNC-02"].unplanned_downtime.append(TimeWindow(at(hours=1), at(hours=3), "spindle"))
    previous.machines["CNC-02"].planned_downtime.append(TimeWindow(at(hours=5), at(hours=6), "pm"))
    events = _by_type(detect_events(previous, current))
    down = {(e.entity_id, e.details.get("status", "window")) for e in events[ReplanTriggerType.MACHINE_DOWN]}
    assert down == {("CNC-01", "down"), ("CNC-02", "window")}
    up = events[ReplanTriggerType.MACHINE_UP]
    assert [e.entity_id for e in up] == ["CNC-02"] and "cancelled" in up[0].message

    recovered = current.clone()
    recovered.machines["CNC-01"].status = MachineStatus.AVAILABLE
    ups = _by_type(detect_events(current, recovered))[ReplanTriggerType.MACHINE_UP]
    assert [e.entity_id for e in ups] == ["CNC-01"]
    # a past downtime window is not an event
    current.machines["CNC-02"].unplanned_downtime.append(TimeWindow(at(hours=-3), at(hours=-1), "old"))
    again = _by_type(detect_events(previous, current))
    assert len(again[ReplanTriggerType.MACHINE_DOWN]) == 2


def test_new_and_removed_machines() -> None:
    previous, current = _pair()
    current.machines["CNC-03"] = make_machine("CNC-03", calendar_id="CAL")
    del current.machines["CNC-02"]
    current.rebuild_indexes()
    events = _by_type(detect_events(previous, current))
    assert [e.entity_id for e in events[ReplanTriggerType.MACHINE_UP]] == ["CNC-03"]
    assert [e.entity_id for e in events[ReplanTriggerType.MACHINE_DOWN]] == ["CNC-02"]


def test_material_arrivals() -> None:
    previous, current = _pair()
    previous.materials["AL"].available_quantity = 10.0
    current.materials["AL"].available_quantity = 60.0
    previous.orders["O0"].material_status = MaterialStatus.ON_ORDER
    current.orders["O0"].material_status = MaterialStatus.AVAILABLE
    events = _by_type(detect_events(previous, current))[ReplanTriggerType.MATERIAL_ARRIVED]
    assert {(e.entity_type, e.entity_id) for e in events} == {("material", "AL"), ("order", "O0")}
    mat = next(e for e in events if e.entity_type == "material")
    assert mat.details["received_quantity"] == 50.0


def test_quality_failure_and_rework() -> None:
    previous, current = _pair()
    current.orders["O0"].quality_status = QualityStatus.FAILED
    current.orders["O1"].order_status = OrderStatus.REWORK
    current.orders["O2"].quality_status = QualityStatus.HOLD
    current.operations["O3-op1"].operation_status = OperationStatus.REWORK
    events = _by_type(detect_events(previous, current))
    assert {e.entity_id for e in events[ReplanTriggerType.QUALITY_FAILURE]} == {"O0", "O2"}
    rework = {(e.entity_type, e.entity_id) for e in events[ReplanTriggerType.REWORK]}
    assert rework == {("order", "O1"), ("operation", "O3-op1")}
    assert next(e for e in events[ReplanTriggerType.REWORK] if e.entity_type == "operation").order_id == "O3"


def test_production_delays_use_alert_threshold() -> None:
    previous, current = _pair()
    for snap in (previous, current):
        for op in snap.operations.values():
            op.estimated_start, op.estimated_end = NOW, at(hours=2)
    current.operations["O0-op1"].estimated_end = at(hours=4)  # 120 min drift
    current.operations["O1-op1"].estimated_end = at(hours=2, minutes=30)  # 30 min drift
    current.operations["O2-op1"].actual_start = at(hours=2)  # started 2 h after its estimate
    current.operations["O3-op1"].estimated_end = at(hours=1)  # earlier: not a delay
    events = _by_type(detect_events(previous, current))[ReplanTriggerType.PRODUCTION_DELAY]
    assert {e.entity_id: e.details["delay_minutes"] for e in events} == {"O0-op1": 120.0, "O2-op1": 120.0}
    assert all(e.order_id == e.entity_id[:2] for e in events)
    loose = detect_events(previous, current, alerts=AlertConfig(behind_schedule_minutes=10))
    assert {e.entity_id for e in loose if e.type == ReplanTriggerType.PRODUCTION_DELAY} == {
        "O0-op1",
        "O1-op1",
        "O2-op1",
    }
    # a delay that pushes the order past its due date is flagged
    current.orders["O0"].requested_delivery_date = at(hours=3)
    overdue = next(
        e
        for e in detect_events(previous, current)
        if e.type == ReplanTriggerType.PRODUCTION_DELAY and e.entity_id == "O0-op1"
    )
    assert overdue.details["overdue"] is True


def test_running_past_planned_end_is_a_delay() -> None:
    previous, current = _pair()
    for snap in (previous, current):
        op = snap.operations["O0-op1"]
        op.estimated_start, op.estimated_end, op.actual_start = at(hours=-2), at(hours=-1), at(hours=-2)
    current.as_of = at(hours=1)  # two hours past the planned end, still running
    events = [e for e in detect_events(previous, current) if e.type == ReplanTriggerType.PRODUCTION_DELAY]
    assert len(events) == 1 and events[0].details["delay_minutes"] == 120.0
    current.operations["O0-op1"].actual_end = at(minutes=30)
    current.operations["O0-op1"].operation_status = OperationStatus.COMPLETED
    assert not [e for e in detect_events(previous, current) if e.type == ReplanTriggerType.PRODUCTION_DELAY]


def test_customer_priority_and_due_date_changes() -> None:
    previous, current = _pair()
    current.customers["C0"].customer_tier = CustomerTier.STRATEGIC
    current.customers["C1"].escalation_level = 2
    current.customer_rules["C2"] = make_customer_rule("C2", priority_boost_points=15)
    current.orders["O0"].revised_delivery_date = at(hours=1)
    current.orders["O1"].erp_priority = 1
    events = _by_type(detect_events(previous, current))[ReplanTriggerType.CUSTOMER_PRIORITY_CHANGE]
    changes = {(e.entity_type, e.entity_id, e.details["change"]) for e in events}
    assert changes == {
        ("customer", "C0", "customer_master"),
        ("customer", "C1", "customer_master"),
        ("customer", "C2", "customer_rule"),
        ("order", "O0", "due_date"),
        ("order", "O1", "erp_priority"),
    }
    due = next(e for e in events if e.details["change"] == "due_date")
    assert due.details["earlier"] is True and due.details["current"] == at(hours=1)
    assert next(e for e in events if e.entity_id == "C0").details["changes"]["customer_tier"] == (
        "standard",
        "strategic",
    )


def test_new_locks_are_manual_events_and_closed_orders_are_ignored() -> None:
    previous, current = _pair()
    current.locks.append(make_lock("O0", "CNC-01"))
    current.orders["O3"].order_status = OrderStatus.SHIPPED
    previous.orders["O3"].order_status = OrderStatus.SHIPPED
    current.orders["O3"].quality_status = QualityStatus.FAILED  # closed order: no event
    events = detect_events(previous, current)
    assert [e.type for e in events] == [ReplanTriggerType.MANUAL]
    assert events[0].details["lock_type"] == "order" and events[0].order_id == "O0"


def test_events_are_sorted_and_serialisable() -> None:
    previous, current = _pair()
    current.orders["O1"].quality_status = QualityStatus.FAILED
    current.machines["CNC-01"].status = MachineStatus.OFFLINE
    current.orders["O8"] = make_order("O8")
    current.rebuild_indexes()
    events = detect_events(previous, current)
    assert [e.type for e in events] == [
        ReplanTriggerType.NEW_ORDER,
        ReplanTriggerType.MACHINE_DOWN,
        ReplanTriggerType.QUALITY_FAILURE,
    ]
    assert events == detect_events(previous, current)
    payload = events[0].to_dict()
    assert payload["type"] == "new_order" and payload["occurred_at"] == at(minutes=30).isoformat()
    manual = manual_event(NOW, "config v3 published", type=ReplanTriggerType.CONFIG_CHANGE, version=3)
    assert manual.type == ReplanTriggerType.CONFIG_CHANGE and manual.details == {"version": 3}
    assert manual.occurred_at == NOW and manual.entity_type == "system"


def test_operation_events_skip_orders_outside_the_snapshot() -> None:
    previous, current = _pair()
    orphan = make_operation("GHOST", 1, estimated_end=at(hours=1))
    previous.operations["GHOST-op1"] = orphan
    current.operations["GHOST-op1"] = make_operation("GHOST", 1, estimated_end=at(hours=5))
    assert detect_events(previous, current) == []
    assert timedelta(0) == current.as_of - at(minutes=30)
