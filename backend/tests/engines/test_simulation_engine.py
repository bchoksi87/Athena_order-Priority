"""SimulationEngine end to end, ScheduleDiff arithmetic, summary and money formatting."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from app.core.clock import FrozenClock
from app.domain.config import PriorityProfile, SchedulingConfig
from app.domain.enums import MaterialStatus
from app.domain.results import ScheduleMetrics, SimulationResult, UnscheduledItem
from app.domain.snapshot import PlanningSnapshot
from app.engines.priority import default_priority_engine
from app.engines.scheduling.rule_based import RuleBasedScheduler
from app.engines.simulation import (
    SimulationEngine,
    default_bottlenecks,
    diff_schedules,
    format_money,
    overtime_hours,
    render_summary,
)
from tests.engines.factories import (
    NOW,
    at,
    make_calendar_spec,
    make_entry,
    make_machine,
    make_plant_snapshot,
    make_schedule,
    make_snapshot,
    window,
)

PROFILE = PriorityProfile()
CONFIG = SchedulingConfig()


@pytest.fixture
def engine() -> SimulationEngine:
    clock = FrozenClock(NOW)
    return SimulationEngine(default_priority_engine(clock), RuleBasedScheduler(clock), clock=clock)


@pytest.fixture
def snapshot() -> PlanningSnapshot:
    return make_plant_snapshot(6)


def _run(
    engine: SimulationEngine, snapshot: PlanningSnapshot, *scenarios: dict[str, Any]
) -> SimulationResult:
    return engine.run(snapshot, list(scenarios), PROFILE, CONFIG)


# ------------------------------------------------------------------ engine


def test_machine_down_moves_entries_and_increases_lateness(
    engine: SimulationEngine, snapshot: PlanningSnapshot
) -> None:
    fingerprint = repr(snapshot.machines["CNC-01"].unplanned_downtime)
    result = _run(engine, snapshot, {"kind": "machine_down", "machine_id": "CNC-01", "duration_hours": 8})
    assert repr(snapshot.machines["CNC-01"].unplanned_downtime) == fingerprint  # untouched
    assert {e.machine_id for e in result.baseline.entries} == {"CNC-01", "CNC-02"}
    assert all(e.setup_start >= at(hours=8) for e in result.scenario.entries if e.machine_id == "CNC-01")
    assert result.scenario.metrics.makespan_hours > result.baseline.metrics.makespan_hours
    assert result.diff.late_orders_after >= result.diff.late_orders_before
    assert result.diff.orders_affected >= 1
    assert result.diff.orders_moved_machine >= 1
    assert result.diff.summary.startswith(f"{result.diff.orders_affected} orders affected; late orders")
    assert result.scenarios[0]["kind"] == "machine_down" and result.scenarios[0]["affected_machine_ids"] == [
        "CNC-01"
    ]
    assert result.simulation_id.startswith("sim_") and result.generated_at == NOW
    assert set(result.baseline_priorities) == set(snapshot.orders)


def test_add_machine_reduces_makespan(engine: SimulationEngine, snapshot: PlanningSnapshot) -> None:
    result = _run(
        engine, snapshot, {"kind": "add_machine", "clone_of_machine_id": "CNC-01", "new_machine_id": "CNC-03"}
    )
    assert "CNC-03" not in snapshot.machines
    assert any(e.machine_id == "CNC-03" for e in result.scenario.entries)
    assert result.scenario.metrics.makespan_hours < result.baseline.metrics.makespan_hours
    assert result.diff.utilization_after < result.diff.utilization_before
    assert result.diff.orders_moved_machine >= 1


def test_weight_change_alters_ranking(engine: SimulationEngine) -> None:
    # O0 is overdue but worth the least; O5 is worth the most but due in ten days.
    snapshot = make_plant_snapshot(6, due_hours=[-1.0, 240.0, 240.0, 240.0, 240.0, 240.0])
    for order in snapshot.orders.values():
        order.order_value, order.estimated_margin = 100_000.0, 20_000.0
    snapshot.orders["O5"].order_value, snapshot.orders["O5"].estimated_margin = 110_000.0, 25_000.0
    result = _run(
        engine,
        snapshot,
        {"kind": "weight_change", "weights": {"order_value": 95, "due_date_urgency": 1, "sla_risk": 0}},
    )
    base_rank = sorted(result.baseline_priorities, key=lambda o: -result.baseline_priorities[o])
    what_if_rank = sorted(result.scenario_priorities, key=lambda o: -result.scenario_priorities[o])
    assert base_rank[0] == "O0"
    assert what_if_rank[0] == "O5" and what_if_rank != base_rank
    assert result.scenario.profile_id == result.baseline.profile_id
    assert any("order_value" in n for n in result.scenarios[0]["notes"])


def test_urgent_order_is_inserted_with_high_score(
    engine: SimulationEngine, snapshot: PlanningSnapshot
) -> None:
    result = _run(
        engine,
        snapshot,
        {
            "kind": "urgent_orders",
            "orders": [
                {
                    "customer_id": "C0",
                    "part_id": "P-RUSH",
                    "quantity": 5,
                    "due": at(hours=3),
                    "route": [{"process": "cnc_machining", "setup_minutes": 10, "cycle_minutes_per_unit": 6}],
                    "order_value": 500_000,
                }
            ],
        },
    )
    assert "SIM-URGENT-1" not in snapshot.orders and "SIM-URGENT-1" not in result.baseline_priorities
    score = result.scenario_priorities["SIM-URGENT-1"]
    assert score == max(result.scenario_priorities.values())
    entries = result.scenario.entries_for_order("SIM-URGENT-1")
    assert len(entries) == 1 and entries[0].sequence_on_machine == 1
    delta = next(d for d in result.diff.order_deltas if d.order_id == "SIM-URGENT-1")
    assert delta.baseline_completion is None and delta.scenario_completion is not None
    assert delta.baseline_score is None and delta.scenario_score == score


def test_material_delay_defers_orders_when_blocked_orders_are_scheduled(
    engine: SimulationEngine, snapshot: PlanningSnapshot
) -> None:
    for order in snapshot.orders.values():
        order.material_status = MaterialStatus.ON_ORDER
    snapshot.materials["AL"].expected_receipt_date = at(minutes=30)
    snapshot.materials["AL"].incoming_quantity = 100.0
    config = SchedulingConfig(schedule_blocked_orders=True)
    result = engine.run(
        snapshot, [{"kind": "material_delay", "material_id": "AL", "delay_days": 2}], PROFILE, config
    )
    assert result.baseline.metrics.scheduled_orders == 6
    assert result.scenario.metrics.scheduled_orders == 6
    assert min(e.setup_start for e in result.scenario.entries) >= at(days=2, minutes=30)
    assert result.diff.late_orders_after == 6 and result.diff.orders_newly_late == 6
    assert result.diff.revenue_at_risk_after > result.diff.revenue_at_risk_before == 0
    assert "revenue at risk ₹0 → ₹21 L" in result.diff.summary


def test_material_delay_blocks_orders(engine: SimulationEngine, snapshot: PlanningSnapshot) -> None:
    for order in snapshot.orders.values():
        order.material_status = MaterialStatus.AVAILABLE
    scenario = {
        "kind": "material_delay",
        "material_id": "AL",
        "delay_days": 2,
        "affects_allocated_stock": True,
    }
    result = engine.run(snapshot, [scenario], PROFILE, CONFIG)
    assert result.baseline.metrics.scheduled_orders == 6
    assert result.scenario.metrics.scheduled_orders == 0
    assert {u.reason_code for u in result.scenario.unscheduled} == {"blocked"}
    assert result.diff.orders_affected == 6
    assert result.diff.revenue_at_risk_after == 2_100_000.0 and result.diff.revenue_at_risk_before == 0.0
    assert result.scenarios[0]["affected_order_ids"] == [f"O{i}" for i in range(6)]


def test_extra_working_day_counts_as_overtime() -> None:
    friday = at(days=4)  # Friday 08:00
    clock = FrozenClock(friday)
    engine = SimulationEngine(default_priority_engine(clock), RuleBasedScheduler(clock), clock=clock)
    # 16 orders on two machines: ~10 h each, so about two hours spill past the Friday shift.
    snapshot = make_plant_snapshot(16, due_hours=[8.0] * 16, now=friday)
    saturday = at(days=5)
    result = engine.run(
        snapshot, [{"kind": "extra_working_day", "day": saturday.date().isoformat()}], PROFILE, CONFIG
    )
    saturday_shift = (saturday, saturday + timedelta(hours=8))
    expected = (
        sum(
            max(0.0, (min(e.end, saturday_shift[1]) - max(e.setup_start, saturday_shift[0])).total_seconds())
            for e in result.scenario.entries
        )
        / 3600.0
    )
    assert expected > 0
    assert result.diff.additional_overtime_hours == pytest.approx(expected)
    assert result.scenario.metrics.makespan_hours < result.baseline.metrics.makespan_hours
    assert f"additional overtime +{expected:.1f} h" in result.diff.summary
    assert result.diff.orders_affected >= 1
    assert result.diff.late_orders_after <= result.diff.late_orders_before


def test_multiple_scenarios_apply_in_order(engine: SimulationEngine, snapshot: PlanningSnapshot) -> None:
    result = _run(
        engine,
        snapshot,
        {"kind": "hold_orders", "order_ids": ["O0"]},
        {"kind": "prioritize_customer", "customer_id": "C2", "boost_points": 40},
    )
    assert [s["kind"] for s in result.scenarios] == ["hold_orders", "prioritize_customer"]
    assert result.baseline.entries_for_order("O0") and not result.scenario.entries_for_order("O0")
    held = next(d for d in result.diff.order_deltas if d.order_id == "O0")
    assert held.scenario_completion is None and held.delta_hours is None
    assert result.diff.order_deltas[-1].order_id == "O0"  # None deltas sort last
    assert result.scenario_priorities["O2"] > result.baseline_priorities["O2"]


def test_previous_entries_feed_the_frozen_window_of_both_plans(
    engine: SimulationEngine, snapshot: PlanningSnapshot
) -> None:
    baseline = engine.plan(snapshot, PROFILE, CONFIG).schedule
    assert not any(e.locked for e in baseline.entries)
    result = engine.run(
        snapshot,
        [{"kind": "machine_down", "machine_id": "CNC-02", "start": at(hours=6), "duration_hours": 2}],
        PROFILE,
        CONFIG,
        previous_entries=baseline.entries,
    )
    window_end = at(minutes=CONFIG.lock_window_minutes)
    for plan in (result.baseline, result.scenario):
        frozen = [e for e in plan.entries if e.setup_start < window_end]
        assert frozen and all(e.locked for e in frozen)
    assert [(e.operation_id, e.machine_id, e.setup_start) for e in result.baseline.entries] == [
        (e.operation_id, e.machine_id, e.setup_start) for e in baseline.entries
    ]


def test_run_is_deterministic(engine: SimulationEngine, snapshot: PlanningSnapshot) -> None:
    a = _run(engine, snapshot, {"kind": "machine_down", "machine_id": "CNC-02", "duration_hours": 3})
    b = _run(engine, snapshot, {"kind": "machine_down", "machine_id": "CNC-02", "duration_hours": 3})
    assert a.diff.summary == b.diff.summary and a.diff.order_deltas == b.diff.order_deltas
    assert [(e.machine_id, e.setup_start) for e in a.scenario.entries] == [
        (e.machine_id, e.setup_start) for e in b.scenario.entries
    ]
    assert a.simulation_id != b.simulation_id
    assert engine.describe()["scheduler"] == "rule_based"


def test_injected_bottleneck_fn_is_used(snapshot: PlanningSnapshot) -> None:
    clock = FrozenClock(NOW)
    calls: list[str] = []

    def fake(result: Any, snap: Any) -> list[str]:
        calls.append("x")
        return ["PRINT"]

    engine = SimulationEngine(
        default_priority_engine(clock),
        RuleBasedScheduler(clock),
        clock=clock,
        bottleneck_fn=fake,
        currency="USD",
    )
    result = _run(engine, snapshot, {"kind": "hold_orders", "order_ids": ["O5"]})
    assert result.diff.bottlenecks_before == ["PRINT"] and len(calls) == 2
    assert "$" in result.diff.summary


# -------------------------------------------------------------------- diff


def _plant_for_diff() -> PlanningSnapshot:
    return make_plant_snapshot(4, due_hours=[2.5, 2.5, 2.5, 2.5])


def test_diff_arithmetic_from_hand_built_schedules() -> None:
    snapshot = _plant_for_diff()
    base = make_schedule(
        [
            make_entry("O0", "CNC-01", at(hours=0, minutes=30), setup_minutes=30, run_minutes=60),
            make_entry("O1", "CNC-02", at(hours=0, minutes=30), setup_minutes=30, run_minutes=60),
            make_entry("O2", "CNC-01", at(hours=2), run_minutes=60, sequence_on_machine=2),
            make_entry("O3", "CNC-02", at(hours=2), run_minutes=60, sequence_on_machine=2),
        ]
    )
    base.metrics = ScheduleMetrics(
        late_orders=2,
        on_time_pct=50.0,
        overall_utilization_pct=40.0,
        revenue_at_risk=700_000.0,
        total_setup_hours=1.0,
    )
    what_if = make_schedule(
        [
            make_entry(
                "O0", "CNC-01", at(hours=0, minutes=30), setup_minutes=30, run_minutes=60
            ),  # unchanged
            make_entry(
                "O2", "CNC-02", at(hours=0, minutes=30), setup_minutes=30, run_minutes=60
            ),  # moved machine
            make_entry("O3", "CNC-02", at(hours=2), run_minutes=60, sequence_on_machine=2),  # same
            make_entry(
                "O1", "CNC-02", at(hours=3, minutes=30), run_minutes=60, sequence_on_machine=3
            ),  # resequenced, late
        ],
    )
    what_if.metrics = ScheduleMetrics(
        late_orders=1,
        on_time_pct=75.0,
        overall_utilization_pct=40.0,
        revenue_at_risk=200_000.0,
        total_setup_hours=1.0,
    )
    diff = diff_schedules(
        base, what_if, snapshot, snapshot, {"O0": 50.0, "O1": 40.0}, {"O0": 50.0, "O1": 42.0}
    )
    assert diff.orders_affected == 2
    assert {d.order_id for d in diff.order_deltas} == {"O1", "O2"}
    assert diff.orders_moved_machine == 1 and diff.orders_resequenced == 1
    o1 = next(d for d in diff.order_deltas if d.order_id == "O1")
    assert o1.delta_hours == pytest.approx(3.0) and o1.baseline_score == 40.0 and o1.scenario_score == 42.0
    assert o1.baseline_late is False and o1.scenario_late is True
    o2 = next(d for d in diff.order_deltas if d.order_id == "O2")
    assert o2.delta_hours == pytest.approx(-1.5) and o2.baseline_late is True and o2.scenario_late is False
    assert diff.order_deltas[0].order_id == "O1"  # sorted by |delta| desc
    assert diff.orders_newly_late == 1 and diff.orders_newly_on_time == 1
    assert (diff.late_orders_before, diff.late_orders_after) == (2, 1)
    assert (diff.revenue_at_risk_before, diff.revenue_at_risk_after) == (700_000.0, 200_000.0)
    assert diff.additional_overtime_hours == 0.0
    assert diff.summary == (
        "2 orders affected; late orders 2 → 1; on-time 50.0% → 75.0%; utilisation 40% → 40%; "
        "revenue at risk ₹7 L → ₹2 L"
    )


def test_diff_unscheduled_and_tolerance() -> None:
    snapshot = _plant_for_diff()
    base = make_schedule([make_entry("O0", "CNC-01", at(hours=1)), make_entry("O1", "CNC-02", at(hours=1))])
    what_if = make_schedule(
        [make_entry("O0", "CNC-01", at(hours=1) + timedelta(seconds=30))],  # within tolerance
        unscheduled=[UnscheduledItem("O1", "O1-op1", "no_eligible_machine", "test")],
    )
    diff = diff_schedules(base, what_if, snapshot, snapshot)
    assert diff.orders_affected == 1 and diff.order_deltas[0].order_id == "O1"
    assert diff.order_deltas[0].scenario_completion is None
    assert diff_schedules(base, what_if, snapshot, snapshot, move_tolerance_minutes=0.1).orders_affected == 2
    identical = diff_schedules(base, base, snapshot, snapshot)
    assert identical.orders_affected == 0 and identical.summary.startswith("0 orders affected")


def test_overtime_hours_and_default_bottlenecks() -> None:
    snapshot = make_snapshot(
        machines=[
            make_machine("CNC-01", calendar_id="CAL"),
            make_machine("DEB-01", machine_group="DEB", calendar_id="CAL"),
        ],
        calendars=[make_calendar_spec("CAL", overtime_windows=[window(at(hours=8), 2, "overtime")])],
        default_calendar_id="CAL",
    )
    # 07:00-09:00 run inside the 08:00-16:00 shift, then 16:00-18:00 inside the overtime window
    result = make_schedule(
        [
            make_entry("O0", "CNC-01", at(hours=-1), run_minutes=120),
            make_entry("O1", "CNC-01", at(hours=8), run_minutes=120, sequence_on_machine=2),
            make_entry("O2", "DEB-01", at(hours=1), run_minutes=30),
        ]
    )
    assert overtime_hours(result, snapshot) == pytest.approx(2.0)
    result.metrics = ScheduleMetrics(machine_utilization_pct={"CNC-01": 80.0, "DEB-01": 10.0})
    assert default_bottlenecks(result, snapshot) == ["CNC (80%)", "DEB (10%)"]
    assert default_bottlenecks(result, snapshot, top_n=1) == ["CNC (80%)"]
    result.metrics = ScheduleMetrics()
    assert default_bottlenecks(result, snapshot) == []


# ----------------------------------------------------------------- summary


def test_render_summary_and_money_formatting() -> None:
    assert format_money(420_000) == "₹4.2 L"
    assert format_money(12_500_000) == "₹1.25 Cr"
    assert format_money(45_000) == "₹45,000"
    assert format_money(-150_000) == "-₹1.5 L"
    assert format_money(3_400_000, "USD") == "$3.4M"
    assert format_money(25_000, "EUR") == "€25K"
    assert format_money(950, "CHF") == "CHF 950"
    assert format_money(None) == "n/a"
    from app.domain.results import ScheduleDiff

    diff = ScheduleDiff(
        orders_affected=17,
        orders_moved_machine=3,
        orders_resequenced=5,
        orders_newly_late=4,
        orders_newly_on_time=1,
        late_orders_before=12,
        late_orders_after=15,
        on_time_pct_before=91.3,
        on_time_pct_after=88.0,
        avg_lateness_before=1.0,
        avg_lateness_after=2.0,
        utilization_before=84.2,
        utilization_after=79.4,
        setup_hours_before=3.0,
        setup_hours_after=3.5,
        revenue_at_risk_before=420_000,
        revenue_at_risk_after=610_000,
        margin_at_risk_before=1.0,
        margin_at_risk_after=2.0,
        additional_overtime_hours=6.0,
        bottlenecks_before=["CNC (95%)"],
        bottlenecks_after=["CNC (99%)"],
    )
    assert render_summary(diff) == (
        "17 orders affected; late orders 12 → 15; on-time 91.3% → 88.0%; utilisation 84% → 79%; "
        "revenue at risk ₹4.2 L → ₹6.1 L; additional overtime +6.0 h; bottlenecks CNC (95%) → CNC (99%)"
    )
    diff.orders_affected, diff.additional_overtime_hours, diff.bottlenecks_after = 1, 0.0, ["CNC (95%)"]
    assert render_summary(diff, "USD").startswith("1 order affected; ")
    assert "overtime" not in render_summary(diff) and "bottlenecks" not in render_summary(diff)
