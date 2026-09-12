"""Tests for analytics.risk: the shared "orders at risk" helper."""

from __future__ import annotations

from app.domain.config import SchedulingConfig
from app.domain.enums import OrderStatus, RiskLevel
from app.engines.analytics.risk import orders_at_risk, risk_sort_key
from tests.engines.factories import (
    NOW,
    at,
    make_entry,
    make_order_with_ops,
    make_priorities,
    make_priority,
    make_schedule,
    make_snapshot,
    make_unscheduled,
)

CFG = SchedulingConfig()  # at_risk_slack_hours = 8


def _snapshot(*orders_with_ops):  # type: ignore[no-untyped-def]
    orders = [o for o, _ in orders_with_ops]
    ops = [op for _, ops in orders_with_ops for op in ops]
    return make_snapshot(orders=orders, operations=ops)


def test_schedule_lateness_is_critical_and_counts_money() -> None:
    snap = _snapshot(
        make_order_with_ops(
            "O1", requested_delivery_date=at(hours=2), order_value=500.0, estimated_margin=50.0
        )
    )
    schedule = make_schedule([make_entry("O1", start=at(hours=1), run_minutes=120)])  # ends at +3h
    report = orders_at_risk(make_priorities({"O1": 50}), schedule, snap, CFG)
    item = report.by_order["O1"]
    assert item.basis == "schedule" and item.scheduled
    assert item.projected_lateness_hours == 1.0
    assert item.late and item.at_risk and item.risk_level is RiskLevel.CRITICAL
    assert report.revenue_at_risk == 500.0 and report.margin_at_risk == 50.0
    assert report.late_orders == 1 and report.at_risk_orders == 1
    assert "1.0 h late (schedule)" in item.reason


def test_tight_slack_is_high_but_not_money_at_risk() -> None:
    snap = _snapshot(make_order_with_ops("O1", requested_delivery_date=at(hours=5)))
    schedule = make_schedule([make_entry("O1", start=NOW, run_minutes=60)])  # 4 h slack < 8
    report = orders_at_risk(make_priorities({"O1": 50}), schedule, snap, CFG)
    item = report.by_order["O1"]
    assert not item.late and item.at_risk and item.risk_level is RiskLevel.HIGH
    assert item.slack_hours == 4.0
    assert report.revenue_at_risk == 0.0
    assert "slack" in item.reason


def test_comfortable_placement_caps_naive_priority_risk() -> None:
    snap = _snapshot(make_order_with_ops("O1", requested_delivery_date=at(days=5)))
    schedule = make_schedule([make_entry("O1", start=NOW, run_minutes=60)])
    priorities = {"O1": make_priority("O1", risk_level=RiskLevel.CRITICAL, projected_completion=at(days=6))}
    item = orders_at_risk(priorities, schedule, snap, CFG).by_order["O1"]
    assert item.basis == "schedule"
    assert item.risk_level is RiskLevel.MEDIUM and not item.at_risk


def test_priority_projection_used_without_schedule() -> None:
    snap = _snapshot(make_order_with_ops("O1", requested_delivery_date=at(days=1)))
    priorities = {
        "O1": make_priority(
            "O1",
            projected_completion=at(days=1, hours=2),
            projected_lateness_hours=2.0,
            risk_level=RiskLevel.HIGH,
        )
    }
    report = orders_at_risk(priorities, None, snap, CFG)
    item = report.by_order["O1"]
    assert item.basis == "priority" and not item.scheduled
    assert item.projected_lateness_hours == 2.0 and item.late
    assert item.risk_level is RiskLevel.CRITICAL
    assert report.revenue_at_risk == 10_000.0


def test_overdue_without_projection_reports_lower_bound() -> None:
    snap = _snapshot(make_order_with_ops("O1", requested_delivery_date=at(hours=-3)))
    item = orders_at_risk({}, None, snap, CFG).by_order["O1"]
    assert item.basis == "due_date"
    assert item.projected_lateness_hours == 3.0 and item.late
    assert item.reason.startswith("Overdue by 3.0 h")


def test_unscheduled_in_scope_counts_money_out_of_scope_does_not() -> None:
    snap = _snapshot(
        make_order_with_ops("O1", order_value=100.0),
        make_order_with_ops("O2", order_value=200.0),
    )
    schedule = make_schedule(
        [], [make_unscheduled("O1", "blocked"), make_unscheduled("O2", "status_not_schedulable")]
    )
    report = orders_at_risk(make_priorities({"O1": 50, "O2": 50}), schedule, snap, CFG)
    assert report.revenue_at_risk == 100.0
    assert report.by_order["O1"].risk_level is RiskLevel.HIGH and report.by_order["O1"].at_risk
    assert report.by_order["O2"].risk_level is RiskLevel.LOW and not report.by_order["O2"].at_risk
    assert report.unscheduled_orders == 2
    assert report.money_at_risk_ids() == ["O1"]


def test_partially_scheduled_order_is_not_scheduled() -> None:
    from app.domain.enums import ProcessType

    snap = _snapshot(make_order_with_ops("O1", (ProcessType.CNC_MACHINING, ProcessType.DEBURRING)))
    schedule = make_schedule(
        [make_entry("O1", start=NOW, run_minutes=60, operation_id="O1-op1")],
        [make_unscheduled("O1", "no_eligible_machine", operation_id="O1-op2")],
    )
    item = orders_at_risk(make_priorities({"O1": 50}), schedule, snap, CFG).by_order["O1"]
    assert not item.scheduled and item.unscheduled_reason == "no_eligible_machine"
    assert item.basis in ("priority", "due_date", "unknown")


def test_closed_orders_ignored_and_no_due_date_handled() -> None:
    closed, closed_ops = make_order_with_ops("C", order_status=OrderStatus.COMPLETED)
    nodue, nodue_ops = make_order_with_ops("N", requested_delivery_date=None)
    snap = make_snapshot(orders=[closed, nodue], operations=closed_ops + nodue_ops)
    report = orders_at_risk({}, None, snap, CFG)
    assert [i.order_id for i in report.items] == ["N"]
    item = report.items[0]
    assert item.basis == "unknown" and item.reason == "No due date" and not item.at_risk


def test_items_sorted_by_severity_then_lateness_revenue_id() -> None:
    snap = _snapshot(
        make_order_with_ops("A", requested_delivery_date=at(hours=1), order_value=10.0),
        make_order_with_ops("B", requested_delivery_date=at(hours=1), order_value=10.0),
        make_order_with_ops("C", requested_delivery_date=at(hours=3), order_value=10.0),
        make_order_with_ops("D", requested_delivery_date=at(days=5), order_value=10.0),
    )
    schedule = make_schedule(
        [
            make_entry("A", start=NOW, run_minutes=120, operation_id="A-op1"),  # 1 h late
            make_entry("B", start=NOW, run_minutes=180, operation_id="B-op1"),  # 2 h late
            make_entry("C", start=NOW, run_minutes=60, operation_id="C-op1"),  # 2 h slack -> high
            make_entry("D", start=NOW, run_minutes=60, operation_id="D-op1"),  # comfortable
        ]
    )
    report = orders_at_risk(make_priorities({k: 50 for k in "ABCD"}), schedule, snap, CFG)
    assert [i.order_id for i in report.items] == ["B", "A", "C", "D"]
    assert report.items == sorted(report.items, key=risk_sort_key)
    assert report.overdue_orders == 0
