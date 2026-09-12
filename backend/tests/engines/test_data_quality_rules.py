"""One positive and one negative test per data quality rule (spec Phase 21)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.config import DataQualityConfig
from app.domain.enums import (
    DataQualityCode,
    DataQualitySeverity,
    LockType,
    OperationStatus,
    OrderStatus,
    OverrideType,
    ProcessType,
)
from app.domain.models import CustomerRule, Expedite, PriorityOverride, ScheduleLock
from app.domain.results import DataQualityIssue
from app.domain.snapshot import PlanningSnapshot
from app.engines.data_quality.base import DataQualityContext, DataQualityRule
from app.engines.data_quality.engine import issue_sort_key
from app.engines.data_quality.rules import (
    ConflictingMachineCapabilityRule,
    DuplicateOrderRule,
    ImpossibleProductionTimeRule,
    IncorrectStatusRule,
    InvalidDateRule,
    InvalidRoutingRule,
    MissingCustomerRule,
    MissingCycleTimeRule,
    MissingDueDateRule,
    MissingMachineAssignmentRule,
    MissingMaterialRule,
    MissingSetupTimeRule,
    NegativeQuantityRule,
    UnknownReferenceRule,
)
from tests.engines.factories import (
    NOW,
    make_customer,
    make_dated_order,
    make_machine,
    make_operation,
    make_order_with_routing,
    make_snapshot,
    make_tooling,
)

pytestmark = pytest.mark.unit


def run_rule(
    rule: DataQualityRule, snap: PlanningSnapshot, config: DataQualityConfig | None = None
) -> list[DataQualityIssue]:
    cfg = config or DataQualityConfig()
    snap.rebuild_indexes()
    ctx = DataQualityContext.build(snap, cfg)
    return sorted(rule.run(snap, cfg, ctx), key=issue_sort_key)


def add_order(snap: PlanningSnapshot, order_id: str, **kw: object) -> None:
    """Add a valid CNC order (one op) to the snapshot; kwargs go to make_order."""
    order, ops = make_order_with_routing(order_id, **kw)  # type: ignore[arg-type]
    snap.orders[order.order_id] = order
    for op in ops:
        snap.operations[op.operation_id] = op
    snap.rebuild_indexes()


def only(issues: list[DataQualityIssue], code: DataQualityCode) -> list[DataQualityIssue]:
    return [i for i in issues if i.code == code]


# --------------------------------------------------------------------------- due date


def test_missing_due_date_flags_open_order_without_any_date(clean_snapshot: PlanningSnapshot) -> None:
    add_order(clean_snapshot, "O2", due_in_days=None)
    issues = run_rule(MissingDueDateRule(), clean_snapshot)
    assert [i.entity_id for i in issues] == ["O2"]
    issue = issues[0]
    assert issue.code == DataQualityCode.MISSING_DUE_DATE
    assert issue.severity == DataQualitySeverity.BLOCKING
    assert issue.entity_type == "order"
    assert issue.field_name == "promised_delivery_date"
    assert issue.recommendation and "ERP" in issue.recommendation
    assert issue.details["order_id"] == "O2"


def test_missing_due_date_ignores_valid_and_closed_orders(clean_snapshot: PlanningSnapshot) -> None:
    add_order(clean_snapshot, "O3", due_in_days=None, order_status=OrderStatus.SHIPPED)
    assert run_rule(MissingDueDateRule(), clean_snapshot) == []


# --------------------------------------------------------------------------- invalid dates


def test_invalid_date_detects_due_before_order_and_too_far(clean_snapshot: PlanningSnapshot) -> None:
    add_order(clean_snapshot, "O2", due_in_days=-10)  # order_date is NOW-3d, due NOW-10d
    add_order(clean_snapshot, "O3", due_in_days=800)  # > 2 years
    issues = run_rule(InvalidDateRule(), clean_snapshot)
    problems = {(i.entity_id, i.details["problem"]) for i in issues}
    assert problems == {("O2", "due_before_order"), ("O3", "due_too_far")}
    assert all(i.severity == DataQualitySeverity.WARNING for i in issues)
    assert all(i.field_name == "promised_delivery_date" for i in issues)


def test_invalid_date_naive_due_date_is_blocking_and_skips_comparisons(
    clean_snapshot: PlanningSnapshot,
) -> None:
    add_order(clean_snapshot, "O2", due_in_days=None, promised_delivery_date=datetime(2020, 1, 1))
    issues = run_rule(InvalidDateRule(), clean_snapshot)
    assert len(issues) == 1
    assert issues[0].details["problem"] == "naive_datetime"
    assert issues[0].severity == DataQualitySeverity.BLOCKING  # tied to missing-due-date toggle
    cfg = DataQualityConfig(treat_missing_due_date_as_blocking=False)
    assert run_rule(InvalidDateRule(), clean_snapshot, cfg)[0].severity == DataQualitySeverity.WARNING


def test_invalid_date_on_operations(clean_snapshot: PlanningSnapshot) -> None:
    op = clean_snapshot.operations["O1-10"]
    op.actual_start = NOW - timedelta(hours=1)
    op.actual_end = NOW - timedelta(hours=2)
    op.estimated_start = datetime(2026, 9, 1, 8, 0)  # naive
    op2 = clean_snapshot.operations["O1-20"]
    op2.actual_start = NOW + timedelta(days=1)
    issues = run_rule(InvalidDateRule(), clean_snapshot)
    problems = sorted((i.entity_id, i.details["problem"]) for i in issues)
    assert problems == [
        ("O1-10", "end_before_start"),
        ("O1-10", "naive_datetime"),
        ("O1-20", "start_in_future"),
    ]
    assert all(i.details["order_id"] == "O1" for i in issues)


def test_invalid_date_clean(clean_snapshot: PlanningSnapshot) -> None:
    assert run_rule(InvalidDateRule(), clean_snapshot) == []


# --------------------------------------------------------------------------- cycle / setup


def test_missing_cycle_time_positive_and_fallbacks(clean_snapshot: PlanningSnapshot) -> None:
    op = clean_snapshot.operations["O1-10"]
    op.cycle_minutes_per_unit = None
    issues = run_rule(MissingCycleTimeRule(), clean_snapshot)
    assert [(i.entity_id, i.severity) for i in issues] == [("O1-10", DataQualitySeverity.BLOCKING)]
    assert issues[0].field_name == "cycle_minutes_per_unit"

    op.machine_cycle_minutes = {"M1": 4.0}
    assert run_rule(MissingCycleTimeRule(), clean_snapshot) == []
    op.machine_cycle_minutes = {}
    clean_snapshot.orders["O1"].estimated_cycle_minutes_per_unit = 6.0
    assert run_rule(MissingCycleTimeRule(), clean_snapshot) == []


def test_missing_cycle_time_ignores_done_operations(clean_snapshot: PlanningSnapshot) -> None:
    op = clean_snapshot.operations["O1-10"]
    op.cycle_minutes_per_unit = None
    op.operation_status = OperationStatus.COMPLETED
    op.completed_quantity = op.quantity
    assert run_rule(MissingCycleTimeRule(), clean_snapshot) == []


def test_missing_setup_time_severity_follows_config(clean_snapshot: PlanningSnapshot) -> None:
    clean_snapshot.operations["O1-10"].setup_minutes = None
    issues = run_rule(MissingSetupTimeRule(), clean_snapshot)
    assert [(i.entity_id, i.severity) for i in issues] == [("O1-10", DataQualitySeverity.WARNING)]
    cfg = DataQualityConfig(treat_missing_setup_as_blocking=True)
    assert run_rule(MissingSetupTimeRule(), clean_snapshot, cfg)[0].severity == DataQualitySeverity.BLOCKING


def test_missing_setup_time_order_estimate_is_enough(clean_snapshot: PlanningSnapshot) -> None:
    clean_snapshot.operations["O1-10"].setup_minutes = None
    clean_snapshot.orders["O1"].estimated_setup_minutes = 15.0
    assert run_rule(MissingSetupTimeRule(), clean_snapshot) == []


# --------------------------------------------------------------------------- machine assignment


def test_missing_machine_assignment_no_information(clean_snapshot: PlanningSnapshot) -> None:
    clean_snapshot.operations["O1-10"].machine_group = None
    issues = run_rule(MissingMachineAssignmentRule(), clean_snapshot)
    assert len(issues) == 1
    issue = issues[0]
    assert issue.code == DataQualityCode.MISSING_MACHINE_ASSIGNMENT
    assert issue.severity == DataQualitySeverity.BLOCKING
    assert issue.details["referenced"] == []
    assert issue.details["capable_machine_ids"] == ["M1", "M2"]
    assert issue.recommendation and "M1, M2" in issue.recommendation


def test_missing_machine_assignment_unresolvable_references(clean_snapshot: PlanningSnapshot) -> None:
    op = clean_snapshot.operations["O1-10"]
    op.machine_group = "GHOST"
    op.eligible_machine_ids = {"ZZ9"}
    issues = run_rule(MissingMachineAssignmentRule(), clean_snapshot)
    assert len(issues) == 1
    assert issues[0].details["referenced"] == ["eligible_machine_ids=ZZ9", "machine_group=GHOST"]
    assert "unknown" in issues[0].message


def test_missing_machine_assignment_negative_paths(clean_snapshot: PlanningSnapshot) -> None:
    op = clean_snapshot.operations["O1-10"]
    op.machine_group = None
    op.machine_id = "M1"
    assert run_rule(MissingMachineAssignmentRule(), clean_snapshot) == []
    op.machine_id = None
    clean_snapshot.orders["O1"].machine_group = "CNC"
    assert run_rule(MissingMachineAssignmentRule(), clean_snapshot) == []
    cfg = DataQualityConfig(treat_missing_machine_as_blocking=False)
    clean_snapshot.orders["O1"].machine_group = None
    assert (
        run_rule(MissingMachineAssignmentRule(), clean_snapshot, cfg)[0].severity
        == DataQualitySeverity.WARNING
    )


# --------------------------------------------------------------------------- material


def test_missing_material_on_material_consuming_step(clean_snapshot: PlanningSnapshot) -> None:
    clean_snapshot.operations["O1-10"].material_id = None
    issues = run_rule(MissingMaterialRule(), clean_snapshot)
    assert [(i.entity_id, i.code) for i in issues] == [("O1-10", DataQualityCode.MISSING_MATERIAL)]
    assert issues[0].severity == DataQualitySeverity.BLOCKING
    clean_snapshot.orders["O1"].required_material_id = "MAT1"
    assert run_rule(MissingMaterialRule(), clean_snapshot) == []


def test_missing_material_not_required_for_post_processing_unless_bom_says_so(
    clean_snapshot: PlanningSnapshot,
) -> None:
    deburr = clean_snapshot.operations["O1-20"]
    assert deburr.material_id is None
    assert run_rule(MissingMaterialRule(), clean_snapshot) == []
    deburr.material_quantity_per_unit = 0.5
    assert [i.entity_id for i in run_rule(MissingMaterialRule(), clean_snapshot)] == ["O1-20"]
    deburr.material_quantity_per_unit = None
    strict = MissingMaterialRule(material_processes=frozenset({ProcessType.DEBURRING}))
    assert [i.entity_id for i in run_rule(strict, clean_snapshot)] == ["O1-20"]


# --------------------------------------------------------------------------- quantities


def test_negative_quantity_variants(clean_snapshot: PlanningSnapshot) -> None:
    add_order(clean_snapshot, "O2", quantity=-5)
    add_order(clean_snapshot, "O3", quantity=10, completed_quantity=12)
    add_order(clean_snapshot, "O4", quantity=0)
    clean_snapshot.operations["O1-10"].quantity = -1
    issues = run_rule(NegativeQuantityRule(), clean_snapshot)
    got = {(i.entity_id, i.field_name, i.severity) for i in issues}
    assert got == {
        ("O2", "quantity", DataQualitySeverity.BLOCKING),
        ("O3", "completed_quantity", DataQualitySeverity.WARNING),
        ("O4", "quantity", DataQualitySeverity.WARNING),
        ("O1-10", "quantity", DataQualitySeverity.BLOCKING),
        ("O2-10", "quantity", DataQualitySeverity.BLOCKING),  # routing copies the order quantity
    }


def test_negative_quantity_clean(clean_snapshot: PlanningSnapshot) -> None:
    assert run_rule(NegativeQuantityRule(), clean_snapshot) == []


# --------------------------------------------------------------------------- duplicates


def test_duplicate_order_exact_and_fuzzy(clean_snapshot: PlanningSnapshot) -> None:
    add_order(clean_snapshot, "OA", external_order_ref="SO-1", order_line_id="1")
    add_order(clean_snapshot, "OB", external_order_ref="SO-1", order_line_id="1", part_id="PX")
    add_order(clean_snapshot, "OC", part_id="P7", quantity=4, due_in_days=3)
    add_order(clean_snapshot, "OD", part_id="P7", quantity=4, due_in_days=3.2)  # same due day
    add_order(clean_snapshot, "OE", part_id="P7", quantity=5, due_in_days=3)  # different qty
    issues = run_rule(DuplicateOrderRule(), clean_snapshot)
    got = {(i.entity_id, i.details["match"], tuple(i.details["duplicate_of"])) for i in issues}
    assert got == {
        ("OA", "exact_erp_line", ("OB",)),
        ("OB", "exact_erp_line", ("OA",)),
        ("OC", "same_customer_part_qty_due", ("OD",)),
        ("OD", "same_customer_part_qty_due", ("OC",)),
    }
    assert all(i.severity == DataQualitySeverity.WARNING for i in issues)


def test_duplicate_order_ignores_closed_lookalikes(clean_snapshot: PlanningSnapshot) -> None:
    add_order(clean_snapshot, "OC", part_id="P7", quantity=4, due_in_days=3)
    add_order(clean_snapshot, "OD", part_id="P7", quantity=4, due_in_days=3, order_status=OrderStatus.SHIPPED)
    assert run_rule(DuplicateOrderRule(), clean_snapshot) == []


# --------------------------------------------------------------------------- status


def test_incorrect_status_variants(clean_snapshot: PlanningSnapshot) -> None:
    add_order(clean_snapshot, "O2", order_status=OrderStatus.COMPLETED)  # pending 10
    add_order(clean_snapshot, "O3", order_status=OrderStatus.IN_PRODUCTION)  # no op in progress
    add_order(clean_snapshot, "O4", order_status=OrderStatus.IN_PRODUCTION)
    clean_snapshot.operations["O4-10"].operation_status = OperationStatus.IN_PROGRESS
    add_order(clean_snapshot, "O5")
    op5 = clean_snapshot.operations["O5-10"]
    op5.operation_status = OperationStatus.COMPLETED  # but only 3/10 confirmed
    op5.completed_quantity = 3
    add_order(clean_snapshot, "O6")
    op6 = clean_snapshot.operations["O6-10"]
    op6.completed_quantity = 10  # fully confirmed but still pending -> order also "all steps done"
    issues = run_rule(IncorrectStatusRule(), clean_snapshot)
    got = sorted((i.entity_id, i.field_name) for i in issues)
    assert got == [
        ("O2", "order_status"),
        ("O3", "order_status"),
        ("O5", "order_status"),  # its only step is marked completed -> order should be closed
        ("O5-10", "operation_status"),
        ("O6", "order_status"),
        ("O6-10", "operation_status"),
    ]
    assert all(i.severity == DataQualitySeverity.WARNING for i in issues)
    assert not any(i.entity_id.startswith("O4") for i in issues)


def test_incorrect_status_clean(clean_snapshot: PlanningSnapshot) -> None:
    assert run_rule(IncorrectStatusRule(), clean_snapshot) == []


# --------------------------------------------------------------------------- production time


def test_impossible_production_time_variants(clean_snapshot: PlanningSnapshot) -> None:
    add_order(clean_snapshot, "O2")
    clean_snapshot.operations["O2-10"].cycle_minutes_per_unit = 5000.0  # > 1440
    add_order(clean_snapshot, "O3")
    clean_snapshot.operations["O3-10"].cycle_minutes_per_unit = -1.0
    add_order(clean_snapshot, "O4")
    clean_snapshot.operations["O4-10"].cycle_minutes_per_unit = 0.0
    add_order(clean_snapshot, "O5")
    clean_snapshot.operations["O5-10"].setup_minutes = 3000.0
    add_order(clean_snapshot, "O6")
    clean_snapshot.operations["O6-10"].setup_minutes = -5.0
    add_order(clean_snapshot, "O7", quantity=100_000)  # 5 min * 100k = 347 days
    add_order(clean_snapshot, "O8", quantity=100_000)
    clean_snapshot.operations["O8-10"].cycle_minutes_per_unit = 9999.0  # op flagged -> no total issue
    issues = run_rule(ImpossibleProductionTimeRule(), clean_snapshot)
    got = {(i.entity_id, i.details["problem"], i.severity) for i in issues}
    assert got == {
        ("O2-10", "cycle_too_long", DataQualitySeverity.BLOCKING),
        ("O3-10", "negative_cycle", DataQualitySeverity.BLOCKING),
        ("O4-10", "zero_cycle", DataQualitySeverity.WARNING),
        ("O5-10", "setup_too_long", DataQualitySeverity.WARNING),
        ("O6-10", "negative_setup", DataQualitySeverity.BLOCKING),
        ("O7", "total_too_long", DataQualitySeverity.BLOCKING),
        ("O8-10", "cycle_too_long", DataQualitySeverity.BLOCKING),
    }


def test_impossible_production_time_uses_config_limits(clean_snapshot: PlanningSnapshot) -> None:
    assert run_rule(ImpossibleProductionTimeRule(), clean_snapshot) == []
    cfg = DataQualityConfig(max_cycle_minutes_per_unit=4.0)  # clean op has 5 min/unit
    issues = run_rule(ImpossibleProductionTimeRule(), clean_snapshot, cfg)
    assert {i.entity_id for i in issues} == {"O1-10", "O1-20"}
    assert all(i.details["max_cycle_minutes_per_unit"] == 4.0 for i in issues)
    rule = ImpossibleProductionTimeRule(max_setup_minutes=10.0)  # clean setup is 30
    assert {i.details["problem"] for i in run_rule(rule, clean_snapshot)} == {"setup_too_long"}


# --------------------------------------------------------------------------- customer


def test_missing_customer_variants(clean_snapshot: PlanningSnapshot) -> None:
    add_order(clean_snapshot, "O2", customer_id="GHOST")
    add_order(clean_snapshot, "O3", customer_id="")
    clean_snapshot.customers["C9"] = make_customer("C9", active=False)
    add_order(clean_snapshot, "O4", customer_id="C9")
    add_order(clean_snapshot, "O5", customer_id="GHOST", order_status=OrderStatus.SHIPPED)
    issues = run_rule(MissingCustomerRule(), clean_snapshot)
    assert [i.entity_id for i in issues] == ["O2", "O3", "O4"]
    assert all(i.code == DataQualityCode.MISSING_CUSTOMER and i.field_name == "customer_id" for i in issues)
    assert issues[2].details == {"order_id": "O4", "customer_id": "C9", "active": False}


def test_missing_customer_clean(clean_snapshot: PlanningSnapshot) -> None:
    assert run_rule(MissingCustomerRule(), clean_snapshot) == []


# --------------------------------------------------------------------------- capability


def test_conflicting_capability_process_material_tooling_and_group(clean_snapshot: PlanningSnapshot) -> None:
    add_order(clean_snapshot, "O2")
    clean_snapshot.operations["O2-10"].machine_id = "P1"  # printer for a CNC step, no alternative
    add_order(clean_snapshot, "O3")
    op3 = clean_snapshot.operations["O3-10"]
    op3.machine_id = "P1"
    op3.eligible_machine_ids = {"M1"}  # alternative exists -> warning
    add_order(clean_snapshot, "O4")
    clean_snapshot.machines["M2"].compatible_materials = {"MAT9"}
    clean_snapshot.operations["O4-10"].machine_id = "M2"  # material MAT1 not compatible
    add_order(clean_snapshot, "O5")
    clean_snapshot.tooling["T1"] = make_tooling("T1", compatible_machine_ids={"M2"})
    op5 = clean_snapshot.operations["O5-10"]
    op5.machine_id = "M1"
    op5.tooling_ids = {"T1"}
    add_order(clean_snapshot, "O6")
    clean_snapshot.operations["O6-10"].machine_group = "PRINT"  # group cannot do CNC
    issues = run_rule(ConflictingMachineCapabilityRule(), clean_snapshot)
    got = {(i.entity_id, i.field_name, i.severity) for i in issues}
    assert got == {
        ("O2-10", "machine_id", DataQualitySeverity.BLOCKING),
        ("O3-10", "machine_id", DataQualitySeverity.WARNING),
        ("O4-10", "machine_id", DataQualitySeverity.BLOCKING),
        ("O5-10", "machine_id", DataQualitySeverity.BLOCKING),
        ("O6-10", "machine_group", DataQualitySeverity.WARNING),
    }
    by_id = {i.entity_id: i for i in issues}
    assert "material MAT1" in by_id["O4-10"].message
    assert "tooling T1" in by_id["O5-10"].message
    assert by_id["O3-10"].details["no_alternative"] is False


def test_conflicting_capability_clean_with_explicit_machine(clean_snapshot: PlanningSnapshot) -> None:
    clean_snapshot.operations["O1-10"].machine_id = "M1"
    clean_snapshot.operations["O1-10"].eligible_machine_ids = {"M1", "M2"}
    assert run_rule(ConflictingMachineCapabilityRule(), clean_snapshot) == []


# --------------------------------------------------------------------------- routing


def test_invalid_routing_missing_operations(clean_snapshot: PlanningSnapshot) -> None:
    clean_snapshot.orders["O2"] = make_dated_order("O2", manufacturing_route=[ProcessType.CNC_MACHINING])
    clean_snapshot.orders["O3"] = make_dated_order("O3", order_status=OrderStatus.SHIPPED)
    issues = run_rule(InvalidRoutingRule(), clean_snapshot)
    assert [(i.entity_id, i.code, i.severity) for i in issues] == [
        ("O2", DataQualityCode.MISSING_OPERATIONS, DataQualitySeverity.BLOCKING)
    ]
    assert issues[0].recommendation and "cnc_machining" in issues[0].recommendation


def test_invalid_routing_sequences_prerequisites_and_cycles(clean_snapshot: PlanningSnapshot) -> None:
    add_order(clean_snapshot, "O2")
    clean_snapshot.operations["O2-10b"] = make_operation(
        "O2", 10, operation_id="O2-10b", machine_group="CNC", material_id="MAT1"
    )  # duplicate seq
    add_order(clean_snapshot, "O3")
    clean_snapshot.operations["O3-10"].prerequisite_operation_id = "NOPE"
    add_order(clean_snapshot, "O4")
    clean_snapshot.operations["O4-10"].prerequisite_operation_id = "O1-10"  # other order
    add_order(clean_snapshot, "O5")
    clean_snapshot.operations["O5-10"].prerequisite_operation_id = "O5-10"  # self
    order6, ops6 = make_order_with_routing("O6", steps=(ProcessType.CNC_MACHINING, ProcessType.DEBURRING))
    ops6[0].prerequisite_operation_id = ops6[1].operation_id  # 10 depends on 20 ...
    ops6[1].prerequisite_operation_id = ops6[0].operation_id  # ... and 20 on 10 -> cycle
    clean_snapshot.orders["O6"] = order6
    for op in ops6:
        clean_snapshot.operations[op.operation_id] = op
    add_order(clean_snapshot, "O7", manufacturing_route=[ProcessType.ADDITIVE_3D_PRINTING])
    add_order(clean_snapshot, "O8", depends_on_order_ids={"O9"})
    add_order(clean_snapshot, "O9", depends_on_order_ids={"O8"})
    clean_snapshot.operations["O1-10"].sequence = -1
    issues = run_rule(InvalidRoutingRule(), clean_snapshot)
    got = sorted((i.entity_id, i.details["problem"]) for i in issues)
    assert got == [
        ("O1-10", "negative_sequence"),
        ("O2", "duplicate_sequence"),
        ("O3-10", "unknown_prerequisite"),
        ("O4-10", "cross_order_prerequisite"),
        ("O5-10", "self_prerequisite"),
        ("O6-10", "prerequisite_cycle"),
        ("O6-10", "prerequisite_not_earlier"),
        ("O6-20", "prerequisite_cycle"),
        ("O7", "route_mismatch"),
        ("O8", "dependency_cycle"),
        ("O9", "dependency_cycle"),
    ]
    severities = {i.details["problem"]: i.severity for i in issues}
    assert severities["negative_sequence"] == DataQualitySeverity.WARNING
    assert severities["route_mismatch"] == DataQualitySeverity.WARNING
    assert severities["duplicate_sequence"] == DataQualitySeverity.BLOCKING
    assert severities["dependency_cycle"] == DataQualitySeverity.BLOCKING


def test_invalid_routing_accepts_gaps_and_valid_prerequisites(clean_snapshot: PlanningSnapshot) -> None:
    clean_snapshot.operations["O1-20"].prerequisite_operation_id = "O1-10"
    clean_snapshot.operations["O1-20"].sequence = 200  # gaps are fine
    clean_snapshot.orders["O1"].manufacturing_route = [ProcessType.CNC_MACHINING, ProcessType.DEBURRING]
    assert run_rule(InvalidRoutingRule(), clean_snapshot) == []


# --------------------------------------------------------------------------- references


def test_unknown_reference_variants(clean_snapshot: PlanningSnapshot) -> None:
    op = clean_snapshot.operations["O1-10"]
    op.material_id = "NOPE-MAT"
    op.tooling_ids = {"NOPE-TOOL"}
    op.machine_id = "NOPE-M"  # group CNC still resolves, so only an unknown reference
    op.machine_cycle_minutes = {"NOPE-M2": 3.0}
    order = clean_snapshot.orders["O1"]
    order.required_material_id = "NOPE-MAT2"
    order.tooling_requirement = {"NOPE-TOOL2"}
    order.depends_on_order_ids = {"NOPE-ORDER"}
    order.machine_group = "NOPE-GROUP"
    clean_snapshot.machines["M1"].calendar_id = "NOPE-CAL"
    clean_snapshot.default_calendar_id = "NOPE-DEFAULT"
    clean_snapshot.operations["ORPHAN"] = make_operation(
        "NOPE-ORDER", 10, operation_id="ORPHAN", machine_group="CNC", material_id="MAT1"
    )
    clean_snapshot.customer_rules["NOPE-CUST"] = CustomerRule(customer_id="NOPE-CUST")
    clean_snapshot.locks.append(
        ScheduleLock("L1", LockType.ORDER, "u", NOW, "r", order_id="NOPE-ORDER", machine_id="NOPE-M")
    )
    clean_snapshot.overrides.append(
        PriorityOverride("OV1", "NOPE-ORDER", OverrideType.FORCE_NEXT, "u", NOW, "r")
    )
    clean_snapshot.expedites.append(
        Expedite("EX1", "NOPE-ORDER", "u", NOW, "r", 10.0, NOW, NOW + timedelta(hours=4))
    )
    issues = run_rule(UnknownReferenceRule(), clean_snapshot)
    got = sorted((i.entity_type, i.entity_id, i.field_name, i.details["referenced_id"]) for i in issues)
    assert got == [
        ("customer_rule", "NOPE-CUST", "customer_id", "NOPE-CUST"),
        ("expedite", "EX1", "order_id", "NOPE-ORDER"),
        ("lock", "L1", "machine_id", "NOPE-M"),
        ("lock", "L1", "order_id", "NOPE-ORDER"),
        ("machine", "M1", "calendar_id", "NOPE-CAL"),
        ("operation", "O1-10", "machine_cycle_minutes", "NOPE-M2"),
        ("operation", "O1-10", "machine_id", "NOPE-M"),
        ("operation", "O1-10", "material_id", "NOPE-MAT"),
        ("operation", "O1-10", "tooling_ids", "NOPE-TOOL"),
        ("operation", "ORPHAN", "order_id", "NOPE-ORDER"),
        ("order", "O1", "depends_on_order_ids", "NOPE-ORDER"),
        ("order", "O1", "machine_group", "NOPE-GROUP"),
        ("order", "O1", "required_material_id", "NOPE-MAT2"),
        ("order", "O1", "tooling_requirement", "NOPE-TOOL2"),
        ("override", "OV1", "order_id", "NOPE-ORDER"),
        ("snapshot", "snap-test", "default_calendar_id", "NOPE-DEFAULT"),
    ]
    assert all(i.severity == DataQualitySeverity.WARNING for i in issues)
    assert all(i.details["order_id"] == "O1" for i in issues if i.entity_id == "O1-10")


def test_unknown_reference_clean(clean_snapshot: PlanningSnapshot) -> None:
    assert run_rule(UnknownReferenceRule(), clean_snapshot) == []


def test_every_rule_yields_nothing_on_clean_snapshot(clean_snapshot: PlanningSnapshot) -> None:
    from app.engines.data_quality.rules import default_rules

    for rule in default_rules():
        assert run_rule(rule, clean_snapshot) == [], rule.key


def test_naive_as_of_is_not_required_for_naive_detection() -> None:
    # sanity: the fixture's NOW is aware; naive detection does not depend on as_of
    assert NOW.tzinfo is UTC
    m = make_machine("M1")
    order = make_dated_order("O1", due_in_days=None, promised_delivery_date=datetime(2026, 10, 1))
    snap = make_snapshot(orders=[order], machines=[m])
    issues = run_rule(InvalidDateRule(), snap)
    assert [i.details["problem"] for i in issues] == ["naive_datetime"]
