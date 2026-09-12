"""Tests for analytics.bottleneck: detection, severity ladder, ordering and recommendations."""

from __future__ import annotations

from app.domain.config import AlertConfig, SchedulingConfig
from app.domain.enums import OperationStatus, ReadinessState, RiskLevel
from app.domain.results import Bottleneck
from app.engines.analytics.bottleneck import bottleneck_sort_key, capacity_severity, find_bottlenecks
from app.engines.calendar import build_calendars
from tests.engines.factories import (
    NOW,
    at,
    make_calendar_spec,
    make_machine,
    make_material,
    make_order_with_ops,
    make_priorities,
    make_priority,
    make_snapshot,
    make_tooling,
)

CFG = SchedulingConfig(horizon_days=7)
ALERTS = AlertConfig()  # bottleneck 90 %, overload 95 %, material 3 days ahead
CAL = make_calendar_spec("CAL")  # 40 h per week on one machine


def _plant(orders_with_ops, machines=None, **extra):  # type: ignore[no-untyped-def]
    orders = [o for o, _ in orders_with_ops]
    ops = [op for _, ops in orders_with_ops for op in ops]
    return make_snapshot(
        orders=orders,
        operations=ops,
        machines=machines if machines is not None else [make_machine("CNC-01", calendar_id="CAL")],
        calendars=[CAL],
        default_calendar_id="CAL",
        **extra,
    )


def test_capacity_severity_ladder() -> None:
    assert capacity_severity(125.0, 10.0, ALERTS) is RiskLevel.CRITICAL
    assert capacity_severity(80.0, 5.0, ALERTS) is RiskLevel.HIGH  # shortfall in one week only
    assert capacity_severity(96.0, 0.0, ALERTS) is RiskLevel.HIGH
    assert capacity_severity(90.0, 0.0, ALERTS) is RiskLevel.MEDIUM
    assert capacity_severity(89.9, 0.0, ALERTS) is None


def test_overloaded_group_is_critical_with_recommendation() -> None:
    # 5 orders x (30 + 60 x 10 = 630 min) = 52.5 h against 40 h this week
    plant = _plant(
        [make_order_with_ops(f"O{i}", op_overrides=[{"cycle_minutes_per_unit": 60.0}]) for i in range(5)]
    )
    found = find_bottlenecks(
        plant,
        make_priorities({f"O{i}": 50 for i in range(5)}),
        None,
        build_calendars(plant),
        NOW,
        CFG,
        ALERTS,
    )
    assert [(b.resource_type, b.resource_id) for b in found] == [
        ("machine_group", "CNC"),
        ("machine", "CNC-01"),
        ("process", "cnc_machining"),
    ]
    group = found[0]
    assert group.severity is RiskLevel.CRITICAL
    assert group.utilization_pct == 131.25 and group.capacity_shortfall_hours == 12.5
    assert group.orders_waiting == 5 and group.revenue_at_risk == 0.0
    assert (
        group.recommendation
        == "Add 13 machine hours on CNC this week (e.g. Saturday shift) to clear 5 waiting order(s)"
    )
    assert found[2].resource_name == "CNC Machining"


def test_high_utilisation_without_shortfall_is_medium_and_started_orders_do_not_wait() -> None:
    # 18.25 h + 17.75 h (no setup while in progress) = 36 h of 40 h = 90 % -> MEDIUM
    plant = _plant(
        [
            make_order_with_ops(
                "A", op_overrides=[{"cycle_minutes_per_unit": 106.5}], order_value=1000.0
            ),  # 30 + 1065 = 18.25 h
            make_order_with_ops(
                "B",
                op_overrides=[
                    {"cycle_minutes_per_unit": 106.5, "operation_status": OperationStatus.IN_PROGRESS}
                ],
            ),
        ]
    )
    priorities = {
        "A": make_priority("A", projected_completion=at(days=6), projected_lateness_hours=24.0),
        "B": make_priority("B"),
    }
    found = find_bottlenecks(plant, priorities, None, build_calendars(plant), NOW, CFG, ALERTS)
    group = next(b for b in found if b.resource_type == "machine_group")
    assert group.severity is RiskLevel.MEDIUM
    assert group.capacity_shortfall_hours == 0.0 and group.orders_waiting == 1
    assert group.revenue_at_risk == 1000.0
    assert group.recommendation.startswith("CNC is at 90% utilisation with 1 order(s) waiting")


def test_no_bottleneck_when_lightly_loaded() -> None:
    plant = _plant([make_order_with_ops("A")])
    assert (
        find_bottlenecks(plant, make_priorities({"A": 50}), None, build_calendars(plant), NOW, CFG, ALERTS)
        == []
    )


def test_material_and_tooling_bottlenecks() -> None:
    soon, soon_ops = make_order_with_ops(
        "S", requested_delivery_date=at(days=1), op_overrides=[{"material_id": "MAT1"}]
    )
    later, later_ops = make_order_with_ops(
        "L", requested_delivery_date=at(days=10), op_overrides=[{"material_id": "MAT2"}]
    )
    tool, tool_ops = make_order_with_ops(
        "T", requested_delivery_date=at(days=10), op_overrides=[{"tooling_ids": {"T1", "T2"}}]
    )
    plant = _plant(
        [(soon, soon_ops), (later, later_ops), (tool, tool_ops)],
        materials=[make_material("MAT1", 0.0, incoming_quantity=5.0, expected_receipt_date=at(days=2))],
        tooling=[make_tooling("T1", available=False, maintenance_status="repair"), make_tooling("T2")],
    )
    priorities = {
        "S": make_priority("S", readiness=ReadinessState.WAITING_MATERIAL, blocked=True),
        "L": make_priority("L", readiness=ReadinessState.WAITING_MATERIAL, blocked=True),
        "T": make_priority("T", readiness=ReadinessState.WAITING_TOOLING, blocked=True),
    }
    found = find_bottlenecks(plant, priorities, None, build_calendars(plant), NOW, CFG, ALERTS)
    by_id = {(b.resource_type, b.resource_id): b for b in found}
    mat1 = by_id[("material", "MAT1")]
    assert mat1.severity is RiskLevel.CRITICAL and mat1.orders_waiting == 1 and mat1.utilization_pct == 100.0
    assert mat1.recommendation.startswith(
        "Expedite delivery of MAT1: 1 order(s) (2 h of work) blocked; 0 kg free, 5 kg incoming expected "
        "2026-09-09"
    )
    assert by_id[("material", "MAT2")].severity is RiskLevel.HIGH
    t1 = by_id[("tooling", "T1")]
    assert t1.severity is RiskLevel.HIGH and ("tooling", "T2") not in by_id  # only the unusable tool
    assert t1.recommendation.startswith("Restore tooling T1 (repair) to release 1 blocked order(s)")
    assert found[0] is mat1  # CRITICAL first
    assert (
        find_bottlenecks(
            plant, priorities, None, build_calendars(plant), NOW, CFG, ALERTS, min_orders_blocked=2
        )
        == []
    )


def test_sort_key_orders_by_severity_then_shortfall_then_utilisation() -> None:
    def bn(kind: str, rid: str, sev: RiskLevel, short: float, util: float) -> Bottleneck:
        return Bottleneck(kind, rid, rid, util, 0, short, 0.0, 0.0, sev, "")

    items = [
        bn("machine", "m-low", RiskLevel.HIGH, 0.0, 96.0),
        bn("machine_group", "g-big", RiskLevel.CRITICAL, 5.0, 110.0),
        bn("material", "mat", RiskLevel.CRITICAL, 0.0, 100.0),
        bn("machine", "m-short", RiskLevel.HIGH, 3.0, 80.0),
        bn("process", "p", RiskLevel.MEDIUM, 0.0, 92.0),
    ]
    ordered = sorted(items, key=bottleneck_sort_key)
    assert [b.resource_id for b in ordered] == ["g-big", "mat", "m-short", "m-low", "p"]
