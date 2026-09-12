"""DataQualityEngine / DataQualityReport: summary, dashboard, severity switches,
determinism and a scale check (spec Phase 21)."""

from __future__ import annotations

import time
from dataclasses import asdict
from datetime import timedelta

import pytest

from app.domain.config import DataQualityConfig
from app.domain.enums import DataQualityCode, DataQualitySeverity, OrderStatus, ProcessType
from app.domain.snapshot import PlanningSnapshot
from app.engines.data_quality import DataQualityEngine, DataQualityReport, default_rules
from app.engines.data_quality.engine import CODE_LABELS, DASHBOARD_PRECEDENCE
from app.engines.data_quality.rules import MissingCycleTimeRule, MissingDueDateRule
from tests.engines.factories import (
    NOW,
    make_calendar_spec,
    make_customer,
    make_dated_order,
    make_machine,
    make_material,
    make_operation,
    make_order_with_routing,
    make_snapshot,
)

pytestmark = pytest.mark.unit


def add_order(snap: PlanningSnapshot, order_id: str, **kw: object) -> None:
    order, ops = make_order_with_routing(order_id, **kw)  # type: ignore[arg-type]
    snap.orders[order.order_id] = order
    for op in ops:
        snap.operations[op.operation_id] = op
    snap.rebuild_indexes()


def test_clean_snapshot_has_no_issues(clean_snapshot: PlanningSnapshot, dq_config: DataQualityConfig) -> None:
    report = DataQualityEngine().run(clean_snapshot, dq_config)
    assert report.issues == []
    assert list(report) == [] and len(report) == 0
    assert report.unschedulable_order_ids == []
    assert report.by_order == {}
    assert report.orders_checked == 1 and report.operations_checked == 2
    assert report.rules_run == [r.key for r in default_rules()]
    summary = report.summary()
    assert summary["total_issues"] == 0
    assert set(summary["by_code"]) == {c.value for c in DataQualityCode}
    assert set(summary["by_severity"]) == {s.value for s in DataQualitySeverity}
    assert summary["unschedulable_orders"] == 0 and summary["open_orders"] == 1
    dash = report.dashboard()
    assert dash.unschedulable_orders == 0 and dash.reasons == []
    assert dash.headline.startswith("All open orders pass")


def test_default_rules_cover_every_code() -> None:
    keys = {r.key for r in default_rules()}
    expected = {c.value for c in DataQualityCode} - {DataQualityCode.MISSING_OPERATIONS.value}
    assert keys == expected  # MISSING_OPERATIONS is emitted by invalid_routing
    assert set(CODE_LABELS) == set(DataQualityCode)
    assert set(DASHBOARD_PRECEDENCE) == set(DataQualityCode)


def test_unschedulable_by_order_and_summary(
    clean_snapshot: PlanningSnapshot, dq_config: DataQualityConfig
) -> None:
    add_order(clean_snapshot, "O2")
    clean_snapshot.operations["O2-10"].cycle_minutes_per_unit = None  # blocking
    add_order(clean_snapshot, "O3")
    clean_snapshot.operations["O3-10"].setup_minutes = None  # warning by default
    add_order(  # closed and consistent: ignored by every rule
        clean_snapshot, "O4", due_in_days=None, order_status=OrderStatus.SHIPPED, completed_quantity=10
    )
    add_order(  # closed but carries a blocking code: reported, not unschedulable
        clean_snapshot, "O5", quantity=-1, order_status=OrderStatus.SHIPPED, op_overrides={"quantity": 5}
    )
    report = DataQualityEngine().run(clean_snapshot, dq_config)

    assert report.unschedulable_order_ids == ["O2"]
    assert set(report.by_order) == {"O2", "O3", "O5"}
    assert [i.code for i in report.by_order["O2"]] == [DataQualityCode.MISSING_CYCLE_TIME]
    assert [i.code for i in report.blocking_issues_for("O2")] == [DataQualityCode.MISSING_CYCLE_TIME]
    assert report.blocking_issues_for("O3") == []
    assert report.issues_for("O3")[0].severity == DataQualitySeverity.WARNING

    summary = report.summary()
    assert summary["total_issues"] == 3
    assert summary["by_code"]["missing_cycle_time"] == 1
    assert summary["by_code"]["missing_setup_time"] == 1
    assert summary["by_code"]["negative_quantity"] == 1
    assert summary["by_severity"] == {"blocking": 2, "warning": 1, "info": 0}
    assert summary["orders_with_issues"] == 3
    assert summary["unschedulable_orders"] == 1
    assert summary["orders_checked"] == 5 and summary["open_orders"] == 3


def test_dashboard_partitions_orders_by_primary_reason(
    clean_snapshot: PlanningSnapshot, dq_config: DataQualityConfig
) -> None:
    clean_snapshot.orders["A"] = make_dated_order("A")  # no ops -> missing operations
    clean_snapshot.orders["A2"] = make_dated_order("A2")
    add_order(clean_snapshot, "B", due_in_days=None)  # missing due date + missing cycle time
    clean_snapshot.operations["B-10"].cycle_minutes_per_unit = None
    add_order(clean_snapshot, "C")
    clean_snapshot.operations["C-10"].machine_group = None  # missing machine
    add_order(clean_snapshot, "D")
    clean_snapshot.operations["D-10"].setup_minutes = None  # warning only
    clean_snapshot.rebuild_indexes()

    report = DataQualityEngine().run(clean_snapshot, dq_config)
    dash = report.dashboard()
    assert dash.open_orders == 6 and dash.unschedulable_orders == 4
    assert [(r.code, r.orders) for r in dash.reasons] == [
        (DataQualityCode.MISSING_OPERATIONS, 2),
        (DataQualityCode.MISSING_CYCLE_TIME, 1),  # wins over missing due date for B
        (DataQualityCode.MISSING_MACHINE_ASSIGNMENT, 1),
    ]
    assert sum(r.orders for r in dash.reasons) == dash.unschedulable_orders
    assert dash.headline == (
        "4 orders cannot be scheduled because: 2 missing operations, 1 missing cycle time, "
        "1 missing machine information"
    )
    assert dash.orders_by_code == {
        "missing_cycle_time": 1,
        "missing_due_date": 1,
        "missing_machine_assignment": 1,
        "missing_operations": 2,
    }
    assert dash.warnings_by_code == {"missing_setup_time": 1}
    as_dict = dash.to_dict()
    assert as_dict["reasons"][0] == {"code": "missing_operations", "label": "missing operations", "orders": 2}
    assert as_dict["headline"] == dash.headline


def test_singular_headline(clean_snapshot: PlanningSnapshot, dq_config: DataQualityConfig) -> None:
    add_order(clean_snapshot, "B", due_in_days=None)
    dash = DataQualityEngine().run(clean_snapshot, dq_config).dashboard()
    assert dash.headline == "1 order cannot be scheduled because: 1 missing due date"


def test_severity_config_switches(clean_snapshot: PlanningSnapshot) -> None:
    add_order(clean_snapshot, "B", due_in_days=None)
    clean_snapshot.operations["B-10"].cycle_minutes_per_unit = None
    clean_snapshot.operations["B-10"].setup_minutes = None
    clean_snapshot.operations["B-10"].machine_group = None
    strict = DataQualityConfig(treat_missing_setup_as_blocking=True)
    lax = DataQualityConfig(
        treat_missing_setup_as_blocking=False,
        treat_missing_cycle_as_blocking=False,
        treat_missing_due_date_as_blocking=False,
        treat_missing_machine_as_blocking=False,
    )
    engine = DataQualityEngine()
    strict_report = engine.run(clean_snapshot, strict)
    assert {i.severity for i in strict_report.by_order["B"]} == {DataQualitySeverity.BLOCKING}
    assert strict_report.unschedulable_order_ids == ["B"]
    lax_report = engine.run(clean_snapshot, lax)
    assert {i.severity for i in lax_report.by_order["B"]} == {DataQualitySeverity.WARNING}
    assert lax_report.unschedulable_order_ids == []
    assert len(lax_report.by_order["B"]) == 4


def test_engine_severity_overrides(clean_snapshot: PlanningSnapshot, dq_config: DataQualityConfig) -> None:
    clean_snapshot.operations["O1-10"].material_id = None
    engine = DataQualityEngine(
        severity_overrides={DataQualityCode.MISSING_MATERIAL: DataQualitySeverity.WARNING}
    )
    report = engine.run(clean_snapshot, dq_config)
    assert [i.severity for i in report.issues] == [DataQualitySeverity.WARNING]
    assert report.unschedulable_order_ids == []


def test_custom_rule_list_and_duplicate_keys(
    clean_snapshot: PlanningSnapshot, dq_config: DataQualityConfig
) -> None:
    add_order(clean_snapshot, "B", due_in_days=None)
    clean_snapshot.operations["B-10"].cycle_minutes_per_unit = None
    engine = DataQualityEngine(rules=[MissingDueDateRule()])
    report = engine.run(clean_snapshot, dq_config)
    assert [i.code for i in report.issues] == [DataQualityCode.MISSING_DUE_DATE]
    assert report.rules_run == ["missing_due_date"]
    assert [r.key for r in engine.rules] == ["missing_due_date"]
    with pytest.raises(ValueError, match="duplicate"):
        DataQualityEngine(rules=[MissingCycleTimeRule(), MissingCycleTimeRule()])


def _messy_snapshot(reverse: bool) -> PlanningSnapshot:
    orders, ops = [], []
    for i in range(30):
        order, routing = make_order_with_routing(
            f"O{i:03d}",
            steps=(ProcessType.CNC_MACHINING, ProcessType.DEBURRING),
            due_in_days=None if i % 5 == 0 else 3 + i,
            customer_id="C1" if i % 7 else "GHOST",
        )
        if i % 3 == 0:
            routing[0].cycle_minutes_per_unit = None
        if i % 4 == 0:
            routing[0].machine_group = "GHOST"
        if i % 6 == 0:
            routing[1].setup_minutes = None
        orders.append(order)
        ops.extend(routing)
    if reverse:
        orders.reverse()
        ops.reverse()
    return make_snapshot(
        orders=orders,
        operations=ops,
        machines=[make_machine("M1"), make_machine("D1", ProcessType.DEBURRING, "DEBURRING")],
        customers=[make_customer("C1")],
        materials=[make_material("MAT1")],
        calendars=[make_calendar_spec("CAL1")],
    )


def test_determinism_across_runs_and_insertion_order(dq_config: DataQualityConfig) -> None:
    engine = DataQualityEngine()
    a = engine.run(_messy_snapshot(reverse=False), dq_config)
    b = engine.run(_messy_snapshot(reverse=True), dq_config)
    c = engine.run(_messy_snapshot(reverse=False), dq_config)
    assert a.issues and [asdict(i) for i in a.issues] == [asdict(i) for i in b.issues]
    assert [asdict(i) for i in a.issues] == [asdict(i) for i in c.issues]
    keys = [(i.entity_type, i.entity_id, i.code.value, i.field_name or "") for i in a.issues]
    assert keys == sorted(keys)
    assert a.unschedulable_order_ids == b.unschedulable_order_ids
    assert a.dashboard().headline == b.dashboard().headline


def test_report_iteration_matches_contract_list_shape(
    clean_snapshot: PlanningSnapshot, dq_config: DataQualityConfig
) -> None:
    add_order(clean_snapshot, "B", due_in_days=None)
    report: DataQualityReport = DataQualityEngine().run(clean_snapshot, dq_config)
    issues = list(report)  # contract §6.6 callers expecting a list still work
    assert len(issues) == len(report) == 1
    assert report.duration_ms >= 0.0
    assert report.as_of == NOW


@pytest.mark.slow
def test_twenty_thousand_orders_under_two_seconds() -> None:
    n_orders = 20_000
    machines = [make_machine(f"M{i}", ProcessType.CNC_MACHINING, "CNC") for i in range(20)]
    machines += [make_machine(f"P{i}", ProcessType.ADDITIVE_3D_PRINTING, "PRINT") for i in range(10)]
    machines += [make_machine(f"D{i}", ProcessType.DEBURRING, "DEBURRING") for i in range(5)]
    customers = [make_customer(f"C{i}") for i in range(800)]
    materials = [make_material(f"MAT{i}") for i in range(50)]
    orders, ops = [], []
    for i in range(n_orders):
        oid = f"ORD{i:06d}"
        due = None if i % 50 == 0 else 1 + (i % 30)
        order = make_dated_order(
            oid,
            customer_id=f"C{i % 800}" if i % 97 else "GHOST",
            part_id=f"P{i % 500}",
            quantity=1 + (i % 40),
            due_in_days=due,
            order_status=OrderStatus.SHIPPED if i % 10 == 9 else OrderStatus.RELEASED,
        )
        orders.append(order)
        first = ProcessType.CNC_MACHINING if i % 2 else ProcessType.ADDITIVE_3D_PRINTING
        group = "CNC" if i % 2 else "PRINT"
        op1 = make_operation(
            oid,
            10,
            first,
            operation_id=f"{oid}-10",
            machine_group=None if i % 23 == 0 else group,
            cycle_minutes_per_unit=None if i % 17 == 0 else 3.0 + (i % 5),
            setup_minutes=None if i % 13 == 0 else 20.0,
            material_id=None if i % 29 == 0 else f"MAT{i % 50}",
            quantity=order.quantity,
        )
        op2 = make_operation(
            oid,
            20,
            ProcessType.DEBURRING,
            operation_id=f"{oid}-20",
            machine_group="DEBURRING",
            material_id=None,
            quantity=order.quantity,
            prerequisite_operation_id=f"{oid}-10",
        )
        ops.extend((op1, op2))
    snap = make_snapshot(
        orders=orders,
        operations=ops,
        machines=machines,
        customers=customers,
        materials=materials,
        calendars=[make_calendar_spec("CAL1")],
        default_calendar_id="CAL1",
        as_of=NOW + timedelta(0),
    )
    engine = DataQualityEngine()
    started = time.perf_counter()
    report = engine.run(snap, DataQualityConfig())
    elapsed = time.perf_counter() - started
    assert report.issues, "the generated snapshot is expected to contain defects"
    assert report.dashboard().unschedulable_orders > 0
    assert elapsed < 2.0, f"data quality run took {elapsed:.2f}s for {n_orders} orders"
