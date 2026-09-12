"""Tests for analytics.kpis: executive KPIs incl. plant-timezone day boundaries."""

from __future__ import annotations

import pytest

from app.domain.config import SchedulingConfig
from app.domain.enums import MachineStatus, OrderStatus, ReadinessState, RiskLevel
from app.engines.analytics.kpis import compute_executive_kpis, historical_otd_pct
from app.engines.calendar import build_calendars
from tests.engines.factories import (
    NOW,
    at,
    make_calendar_spec,
    make_customer,
    make_entry,
    make_machine,
    make_order_with_ops,
    make_priorities,
    make_priority,
    make_schedule,
    make_snapshot,
    make_unscheduled,
)

CFG = SchedulingConfig()
# NOW is Monday 2026-09-07 08:00 UTC = 13:30 IST; the plant calendar below is in Asia/Kolkata.
IST_CAL = make_calendar_spec("CAL", timezone="Asia/Kolkata")


def _plant(orders_with_ops, machines=(), customers=()):  # type: ignore[no-untyped-def]
    orders = [o for o, _ in orders_with_ops]
    ops = [op for _, ops in orders_with_ops for op in ops]
    return make_snapshot(
        orders=orders,
        operations=ops,
        machines=list(machines) or [make_machine("CNC-01", calendar_id="CAL")],
        customers=list(customers),
        calendars=[IST_CAL],
        default_calendar_id="CAL",
    )


def test_due_today_tomorrow_week_boundaries_in_plant_timezone() -> None:
    snap = _plant(
        [
            make_order_with_ops("T1", requested_delivery_date=at(hours=10)),  # 23:30 IST today
            make_order_with_ops("T2", requested_delivery_date=at(hours=10, minutes=30)),  # 00:00 IST tomorrow
            make_order_with_ops("T3", requested_delivery_date=at(hours=34)),  # 23:30 IST tomorrow
            make_order_with_ops("T4", requested_delivery_date=at(hours=34, minutes=30)),  # Wednesday
            make_order_with_ops(
                "W1", requested_delivery_date=at(days=6, hours=10, minutes=29)
            ),  # Sun 23:59 IST
            make_order_with_ops(
                "W2", requested_delivery_date=at(days=6, hours=10, minutes=30)
            ),  # Mon next week
            make_order_with_ops("OD", requested_delivery_date=at(minutes=-1)),  # overdue, not "today"
            make_order_with_ops("ND", requested_delivery_date=None),
            make_order_with_ops("CL", requested_delivery_date=at(hours=1), order_status=OrderStatus.SHIPPED),
        ]
    )
    kpis = compute_executive_kpis(snap, {}, None, NOW, CFG, build_calendars(snap))
    assert kpis.total_open_orders == 8
    assert kpis.total_pending_quantity == 80.0
    assert kpis.orders_due_today == 1
    assert kpis.orders_due_tomorrow == 2
    assert kpis.orders_due_this_week == 5  # T1..T4 + W1
    assert kpis.overdue_orders == 1
    assert kpis.as_of == NOW


def test_blocked_counts_from_priorities_with_status_fallback() -> None:
    snap = _plant(
        [
            make_order_with_ops("M"),
            make_order_with_ops("T"),
            make_order_with_ops("X"),
            make_order_with_ops("A"),
            make_order_with_ops("R"),
            make_order_with_ops("F1", order_status=OrderStatus.TOOLING_WAITING),  # no priority result
            make_order_with_ops("F2", drawing_approved=False),
        ]
    )
    priorities = {
        "M": make_priority("M", readiness=ReadinessState.WAITING_MATERIAL, blocked=True),
        "T": make_priority("T", readiness=ReadinessState.WAITING_TOOLING, blocked=True),
        "X": make_priority("X", readiness=ReadinessState.MACHINE_UNAVAILABLE, blocked=True),
        "A": make_priority("A", readiness=ReadinessState.WAITING_APPROVAL, blocked=True),
        "R": make_priority("R"),
    }
    kpis = compute_executive_kpis(snap, priorities, None, NOW, CFG, build_calendars(snap))
    assert (kpis.blocked_by_material, kpis.blocked_by_tooling, kpis.blocked_by_machine) == (1, 2, 1)
    assert kpis.waiting_for_approval == 2
    assert kpis.blocked_total == 6


def test_historical_otd_weighted_by_open_orders() -> None:
    snap = _plant(
        [
            make_order_with_ops("A", customer_id="C1"),
            make_order_with_ops("B", customer_id="C1"),
            make_order_with_ops("C", customer_id="C2"),
            make_order_with_ops("D", customer_id="C3"),
        ],
        customers=[
            make_customer("C1", historical_on_time_delivery=0.9),
            make_customer("C2", historical_on_time_delivery=0.5),
            make_customer("C3"),
        ],
    )
    assert historical_otd_pct(snap) == 100.0 * (0.9 * 2 + 0.5) / 3
    unknown = _plant([make_order_with_ops("A")])
    assert historical_otd_pct(unknown) is None
    assert compute_executive_kpis(unknown, {}, None, NOW, CFG, {}).on_time_delivery_pct is None


def test_schedule_driven_kpis() -> None:
    snap = _plant(
        [
            make_order_with_ops(
                "ON", requested_delivery_date=at(days=3), order_value=100.0, estimated_margin=10.0
            ),
            make_order_with_ops(
                "LATE", requested_delivery_date=at(hours=1), order_value=200.0, estimated_margin=20.0
            ),
            make_order_with_ops("UNS", order_value=300.0, estimated_margin=30.0),
        ]
    )
    calendars = build_calendars(snap)
    schedule = make_schedule(
        [
            make_entry("ON", start=NOW, run_minutes=120, operation_id="ON-op1"),
            make_entry(
                "LATE", start=at(hours=2), run_minutes=120, operation_id="LATE-op1", sequence_on_machine=2
            ),
        ],
        [make_unscheduled("UNS", "no_eligible_machine")],
    )
    kpis = compute_executive_kpis(
        snap, make_priorities({"ON": 50, "LATE": 60, "UNS": 40}), schedule, NOW, CFG, calendars
    )
    assert kpis.scheduled_orders == 2 and kpis.unscheduled_orders == 1
    assert kpis.expected_on_time_delivery_pct == 50.0
    assert kpis.at_risk_orders == 2  # late + unscheduled
    assert kpis.revenue_at_risk == 500.0 and kpis.margin_at_risk == 50.0
    # occupied = calendar working minutes inside the entries (IST shift 08:00-16:00 = 02:30-10:30 UTC,
    # so the second entry only overlaps the shift for 30 min); available = working hours in the horizon.
    calendar = calendars["CNC-01"]
    busy_hours = (
        calendar.working_minutes_between(NOW, at(hours=2))
        + calendar.working_minutes_between(at(hours=2), at(hours=4))
    ) / 60.0
    available = calendar.available_hours(NOW, at(days=14))
    assert busy_hours == 2.5
    assert kpis.machine_utilization_pct is not None
    assert abs(kpis.machine_utilization_pct - 100.0 * busy_hours / available) < 1e-6
    assert kpis.capacity_utilization_pct is not None
    # capacity adds the unscheduled order's estimate (30 + 6 x 10 = 90 min) to the scheduled hours
    assert abs(kpis.capacity_utilization_pct - 100.0 * (busy_hours + 1.5) / available) < 1e-6


def test_without_schedule_falls_back_to_projection_and_erp_utilization() -> None:
    snap = _plant(
        [
            make_order_with_ops("A", requested_delivery_date=at(days=1)),
            make_order_with_ops("B", requested_delivery_date=at(days=1)),
        ],
        machines=[
            make_machine("M1", calendar_id="CAL", utilization=0.8),
            make_machine("M2", calendar_id="CAL", utilization=0.4, status=MachineStatus.DOWN),
        ],
    )
    priorities = {
        "A": make_priority("A", projected_completion=at(hours=5), projected_lateness_hours=-19.0),
        "B": make_priority(
            "B", projected_completion=at(days=2), projected_lateness_hours=24.0, risk_level=RiskLevel.CRITICAL
        ),
    }
    kpis = compute_executive_kpis(snap, priorities, None, NOW, CFG, {})
    assert kpis.scheduled_orders == 0 and kpis.unscheduled_orders == 2
    assert kpis.expected_on_time_delivery_pct == 50.0
    assert kpis.machine_utilization_pct == pytest.approx(60.0)
    assert kpis.capacity_utilization_pct is None  # no calendars -> no available hours
    assert kpis.at_risk_orders == 1 and kpis.revenue_at_risk == 10_000.0
