"""ORM <-> domain mapper roundtrips (through a real SQLite session so JSON and
datetime column handling is exercised, not just the Python conversion)."""

from __future__ import annotations

import copy
from datetime import UTC, timedelta

import pytest
from sqlalchemy.orm import Session

from app.db import mappers
from app.db.models import CalendarSpecRow, MachineRow, OrderRow
from app.domain.enums import CustomerTier, LockType, ProcessType, ReadinessState, RiskLevel
from app.domain.models import (
    Customer,
    CustomerRule,
    Machine,
    Material,
    Order,
    ScheduleLock,
    TimeWindow,
    Tooling,
)
from app.domain.results import FactorScore, PriorityAdjustment, PriorityResult, ScheduleEntry
from tests.conftest import NOW, SampleData

pytestmark = pytest.mark.unit


def _persist(session: Session, row: object) -> None:
    session.add(row)
    session.flush()
    session.expire_all()


def test_customer_roundtrip(session: Session, sample: SampleData) -> None:
    for customer in sample.customers:
        original = copy.deepcopy(customer)
        _persist(session, mappers.customer_to_row(customer))
        row = session.get(type(mappers.customer_to_row(customer)), customer.customer_id)
        assert row is not None
        assert mappers.customer_from_row(row) == original


def test_customer_rule_roundtrip(session: Session, sample: SampleData) -> None:
    _persist(session, mappers.customer_to_row(sample.customers[0]))
    rule = sample.customer_rules[0]
    _persist(session, mappers.customer_rule_to_row(rule))
    row = session.get(type(mappers.customer_rule_to_row(rule)), rule.customer_id)
    assert row is not None
    assert mappers.customer_rule_from_row(row) == rule
    none_rule = CustomerRule("CUST-B")
    assert mappers.customer_rule_from_row(mappers.customer_rule_to_row(none_rule)) == none_rule


def test_order_and_operations_roundtrip(session: Session, sample: SampleData) -> None:
    for order in sample.orders:
        original = copy.deepcopy(order)
        _persist(session, mappers.order_to_row(order))
        row = session.get(OrderRow, order.order_id)
        assert row is not None
        back = mappers.order_from_row(row)
        assert back == original
        assert isinstance(back.tooling_requirement, set)
        assert isinstance(back.depends_on_order_ids, set)
        assert all(d.tzinfo is UTC for d in (back.order_date, back.due_date) if d is not None)
        assert row.due_date == original.due_date  # denormalised effective due date
    for op in sample.operations:
        original_op = copy.deepcopy(op)
        _persist(session, mappers.operation_to_row(op))
        op_row = session.get(type(mappers.operation_to_row(op)), op.operation_id)
        assert op_row is not None
        back_op = mappers.operation_from_row(op_row)
        assert back_op == original_op
        assert isinstance(back_op.tooling_ids, set) and isinstance(back_op.eligible_machine_ids, set)


def test_order_update_in_place(session: Session, sample: SampleData) -> None:
    order = sample.orders[0]
    row = mappers.order_to_row(order)
    _persist(session, row)
    changed = copy.deepcopy(order)
    changed.quantity = 99
    changed.revised_delivery_date = NOW + timedelta(days=30)
    changed.tooling_requirement = {"TOOL-9"}
    mappers.order_to_row(changed, session.get(OrderRow, order.order_id))
    session.flush()
    session.expire_all()
    reloaded = session.get(OrderRow, order.order_id)
    assert reloaded is not None
    assert mappers.order_from_row(reloaded) == changed
    assert reloaded.due_date == NOW + timedelta(days=30)


def test_machine_roundtrip_including_downtime_and_tuple(session: Session, sample: SampleData) -> None:
    for machine in sample.machines:
        original = copy.deepcopy(machine)
        _persist(session, mappers.machine_to_row(machine))
        row = session.get(MachineRow, machine.machine_id)
        assert row is not None
        back = mappers.machine_from_row(row)
        assert back == original
        assert back.max_part_size_mm is None or isinstance(back.max_part_size_mm, tuple)
        assert isinstance(back.compatible_processes, set)
        assert all(isinstance(w, TimeWindow) for w in back.all_downtime)


def test_machine_from_row_with_explicit_downtime(sample: SampleData) -> None:
    machine = sample.machines[0]
    row = mappers.machine_to_row(machine)
    back = mappers.machine_from_row(row, downtime=row.downtime)
    assert back.maintenance_windows == machine.maintenance_windows
    assert back.planned_downtime == machine.planned_downtime
    assert back.unplanned_downtime == machine.unplanned_downtime
    assert mappers.machine_from_row(row, downtime=[]).all_downtime == []


def test_material_and_tooling_roundtrip(session: Session, sample: SampleData) -> None:
    for material in sample.materials:
        original = copy.deepcopy(material)
        _persist(session, mappers.material_to_row(material))
        row = session.get(type(mappers.material_to_row(material)), material.material_id)
        assert row is not None
        assert mappers.material_from_row(row) == original
    for tool in sample.tooling:
        original_tool = copy.deepcopy(tool)
        _persist(session, mappers.tooling_to_row(tool))
        trow = session.get(type(mappers.tooling_to_row(tool)), tool.tooling_id)
        assert trow is not None
        assert mappers.tooling_from_row(trow) == original_tool


def test_calendar_roundtrip(session: Session, sample: SampleData) -> None:
    spec = sample.calendars[0]
    original = copy.deepcopy(spec)
    _persist(session, mappers.calendar_to_row(spec))
    row = session.get(CalendarSpecRow, spec.calendar_id)
    assert row is not None
    back = mappers.calendar_from_row(row)
    assert back == original
    assert back.shifts[1].weekdays == (0, 1, 2, 3, 4, 5)
    assert isinstance(back.shifts[0].weekdays, tuple)
    assert row.shifts[0]["start"] == "06:00:00"
    assert row.holidays == ["2026-10-02"]


def test_overlay_roundtrips(session: Session, sample: SampleData) -> None:
    for lock in sample.locks:
        original = copy.deepcopy(lock)
        row = mappers.lock_to_row(lock)
        _persist(session, row)
        assert mappers.lock_from_row(row) == original
    for override in sample.overrides:
        original_o = copy.deepcopy(override)
        orow = mappers.override_to_row(override)
        _persist(session, orow)
        assert mappers.override_from_row(orow) == original_o
    for expedite in sample.expedites:
        original_e = copy.deepcopy(expedite)
        erow = mappers.expedite_to_row(expedite)
        _persist(session, erow)
        assert mappers.expedite_from_row(erow) == original_e


def test_lock_without_window() -> None:
    lock = ScheduleLock("L", LockType.SEQUENCE, "u", NOW, "seq", sequence_order_ids=["ORD-2", "ORD-1"])
    row = mappers.lock_to_row(lock)
    assert row.window_start is None and row.window_end is None
    assert mappers.lock_from_row(row) == lock


def test_priority_result_roundtrip(session: Session) -> None:
    result = PriorityResult(
        order_id="ORD-1",
        score=87.5,
        base_score=70.0,
        factors=[
            FactorScore(
                "due_date_urgency",
                "Due Date Urgency",
                "bonus",
                95.0,
                0.25,
                23.75,
                "due in 2 days",
                {"hours": 48},
            ),
            FactorScore("setup_efficiency", "Setup Efficiency", "penalty", 20.0, 0.05, -1.0, "changeover"),
        ],
        adjustments=[
            PriorityAdjustment("expedite", 30.0, "rush", "EXP-1"),
            PriorityAdjustment("aging", 2.0, "aged"),
        ],
        readiness=ReadinessState.READY,
        blocked=False,
        blocking_reasons=[],
        risk_level=RiskLevel.HIGH,
        explanation="Score 87.5 because ...",
        profile_id="PriorityProfile-A",
        profile_version=3,
        computed_at=NOW,
        hours_until_due=48.0,
        projected_completion=NOW + timedelta(hours=20),
        projected_lateness_hours=None,
        forced_next=True,
        rank=1,
    )
    original = copy.deepcopy(result)
    row = mappers.priority_result_to_row(result, run_id="run_1")
    _persist(session, row)
    assert mappers.priority_result_from_row(row) == original
    assert row.factors[0]["details"] == {"hours": 48}


def test_schedule_entry_roundtrip(session: Session) -> None:
    from app.db.models import ScheduleVersionRow

    version = ScheduleVersionRow(
        schedule_version_id="sv_1",
        version_number=1,
        status="draft",
        algorithm="rule_based",
        algorithm_version="1.0.0",
        profile_id="P",
        profile_version=1,
        config_version=1,
        generated_at=NOW,
        horizon_start=NOW,
        horizon_end=NOW + timedelta(days=14),
    )
    _persist(session, version)
    entry = ScheduleEntry(
        entry_id="e1",
        machine_id="CNC-01",
        order_id="ORD-1",
        operation_id="ORD-1-10",
        sequence_on_machine=1,
        setup_start=NOW,
        start=NOW + timedelta(minutes=30),
        end=NOW + timedelta(minutes=150),
        setup_minutes=30.0,
        run_minutes=120.0,
        quantity=10.0,
        priority_score=87.5,
        placement_reason="highest priority ready job",
        is_last_operation=False,
        expected_completion=NOW + timedelta(hours=5),
        due_date=NOW + timedelta(days=2),
        expected_lateness_hours=0.0,
        locked=True,
        batch_key="MAT-AL",
        setup_family="F1",
        material_id="MAT-AL",
        customer_id="CUST-A",
    )
    original = copy.deepcopy(entry)
    row = mappers.schedule_entry_to_row(entry, "sv_1")
    _persist(session, row)
    assert mappers.schedule_entry_from_row(row) == original


def test_enum_values_are_stored_as_strings(sample: SampleData) -> None:
    row = mappers.customer_to_row(Customer("C", "N", customer_tier=CustomerTier.KEY))
    assert row.customer_tier == "key"
    mrow = mappers.machine_to_row(sample.machines[0])
    assert mrow.process_type == "cnc_machining" and mrow.compatible_processes == ["deburring"]
    orow = mappers.order_to_row(sample.orders[0])
    assert orow.manufacturing_route == ["cnc_machining", "deburring"]
    assert orow.tooling_requirement == ["TOOL-1"]


def test_defaults_roundtrip_for_minimal_objects() -> None:
    minimal = [
        (Order("O", "C", "P"), mappers.order_to_row, mappers.order_from_row),
        (Machine("M", "n", "t", ProcessType.OTHER, "G"), mappers.machine_to_row, mappers.machine_from_row),
        (Material("MA", "n"), mappers.material_to_row, mappers.material_from_row),
        (Tooling("T", "n"), mappers.tooling_to_row, mappers.tooling_from_row),
    ]
    for obj, to_row, from_row in minimal:
        assert from_row(to_row(obj)) == obj  # type: ignore[operator]
