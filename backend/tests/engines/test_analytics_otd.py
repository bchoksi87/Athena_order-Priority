"""Tests for analytics.otd: historical and projected on-time delivery."""

from __future__ import annotations

from datetime import date

from app.domain.enums import CustomerTier, OperationStatus, OrderStatus, ProcessType
from app.engines.analytics.otd import actual_completion, on_time_delivery
from tests.engines.factories import (
    NOW,
    at,
    make_customer,
    make_entry,
    make_order_with_ops,
    make_schedule,
    make_snapshot,
    make_unscheduled,
)


def _delivered(order_id: str, due, ended, **overrides):  # type: ignore[no-untyped-def]
    fields = {"order_status": OrderStatus.SHIPPED, "requested_delivery_date": due, "completed_quantity": 10.0}
    fields.update(overrides)
    order, ops = make_order_with_ops(order_id, **fields)
    for op in ops:
        op.operation_status = OperationStatus.COMPLETED
        op.completed_quantity = op.quantity
        op.actual_start = ended and at(hours=-2, base=ended)
        op.actual_end = ended
    return order, ops


def test_historical_otd_by_tier_process_and_day() -> None:
    on_time, on_time_ops = _delivered("A", due=at(days=-2), ended=at(days=-3), customer_id="C1")
    late, late_ops = make_order_with_ops(
        "B", order_status=OrderStatus.COMPLETED, requested_delivery_date=at(days=-5), customer_id="C2"
    )
    for op in late_ops:
        op.actual_end = at(days=-4)
    old, old_ops = _delivered("OLD", due=at(days=-40), ended=at(days=-45))
    cancelled, cancelled_ops = _delivered(
        "X", due=at(days=-1), ended=at(days=-1), order_status=OrderStatus.CANCELLED
    )
    nodue, nodue_ops = _delivered("ND", due=None, ended=at(days=-1))
    attr, attr_ops = make_order_with_ops(
        "ATTR",
        order_status=OrderStatus.PACKED,
        requested_delivery_date=at(days=-1),
        attributes={"actual_completion_date": at(days=-1, hours=-1)},
        process_type=ProcessType.ADDITIVE_3D_PRINTING,
        customer_id="C2",
    )
    snap = make_snapshot(
        orders=[on_time, late, old, cancelled, nodue, attr],
        operations=on_time_ops + late_ops + old_ops + cancelled_ops + nodue_ops + attr_ops,
        customers=[make_customer("C1", customer_tier=CustomerTier.STRATEGIC), make_customer("C2")],
    )
    report = on_time_delivery(snap, None, NOW, window_days=30)
    assert report.historical.total == 3 and report.historical.on_time == 2
    assert report.historical_pct == 200.0 / 3
    assert report.projected_pct is None
    assert report.historical_by_tier["strategic"].pct == 100.0
    assert (
        report.historical_by_tier["standard"].on_time == 1
        and report.historical_by_tier["standard"].total == 2
    )
    assert report.historical_by_process["CNC Machining"].total == 2
    assert report.historical_by_process["Additive 3D Printing"].total == 1
    assert report.notes["outside_window"] == 1 and report.notes["delivered_without_due_date"] == 1
    days = [p.day for p in report.trend]
    assert days == sorted(days) and date(2026, 9, 4) in days  # NOW - 3 days
    assert "67%" in report.summary()


def test_projected_otd_from_fully_scheduled_orders_only() -> None:
    good, good_ops = make_order_with_ops("G", requested_delivery_date=at(days=2))
    bad, bad_ops = make_order_with_ops("B", requested_delivery_date=at(hours=1))
    partial, partial_ops = make_order_with_ops("P", (ProcessType.CNC_MACHINING, ProcessType.DEBURRING))
    snap = make_snapshot(orders=[good, bad, partial], operations=good_ops + bad_ops + partial_ops)
    schedule = make_schedule(
        [
            make_entry("G", start=NOW, run_minutes=60, operation_id="G-op1"),
            make_entry("B", start=NOW, run_minutes=120, operation_id="B-op1"),
            make_entry("P", start=NOW, run_minutes=60, operation_id="P-op1"),
        ],
        [make_unscheduled("P", "no_eligible_machine", operation_id="P-op2")],
    )
    report = on_time_delivery(snap, schedule, NOW, window_days=7)
    assert report.projected.total == 2 and report.projected.on_time == 1
    assert report.projected_pct == 50.0
    assert report.projected_by_tier["standard"].late == 1
    assert len(report.trend) == 1 and report.trend[0].projected is not None
    assert report.trend[0].projected.total == 2 and report.trend[0].historical is None


def test_actual_completion_requires_every_step_or_attribute() -> None:
    order, ops = make_order_with_ops(
        "O", (ProcessType.CNC_MACHINING, ProcessType.DEBURRING), order_status=OrderStatus.COMPLETED
    )
    ops[0].actual_end = at(days=-1)
    snap = make_snapshot(orders=[order], operations=ops)
    assert actual_completion(order, snap) == at(days=-1)  # partial timestamps: best effort
    report = on_time_delivery(snap, None, NOW, 30)
    assert report.historical.total == 1
    order.attributes["actual_end"] = at(hours=-1)
    assert actual_completion(order, snap) == at(hours=-1)  # attribute wins when steps are incomplete
    no_ops, _ = make_order_with_ops("N", order_status=OrderStatus.COMPLETED)
    snap2 = make_snapshot(orders=[no_ops])
    assert actual_completion(no_ops, snap2) is None
    assert on_time_delivery(snap2, None, NOW, 30).notes["delivered_without_completion_date"] == 1
