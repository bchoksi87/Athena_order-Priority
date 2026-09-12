"""Metrics and quality math on hand-built schedule results."""

from __future__ import annotations

from datetime import timedelta

from app.domain.config import SchedulingConfig
from app.domain.results import ScheduleEntry, ScheduleMetrics, ScheduleResult, UnscheduledItem
from app.engines.calendar import MachineCalendar
from app.engines.scheduling.metrics import compute_metrics, machine_utilization, order_lateness_hours
from app.engines.scheduling.quality import compare_schedules, compute_quality, quality_components
from tests.engines.factories import NOW, at, make_calendar_spec, make_machine, make_order, make_snapshot


def _entry(
    order_id: str, machine_id: str, start_h: float, setup: float, run: float, seq: int = 1
) -> ScheduleEntry:
    setup_start = at(hours=start_h)
    start = setup_start + timedelta(minutes=setup)
    return ScheduleEntry(
        entry_id=f"e_{order_id}",
        machine_id=machine_id,
        order_id=order_id,
        operation_id=f"{order_id}-op1",
        sequence_on_machine=seq,
        setup_start=setup_start,
        start=start,
        end=start + timedelta(minutes=run),
        setup_minutes=setup,
        run_minutes=run,
        quantity=10.0,
        priority_score=50.0,
        placement_reason="test",
        is_last_operation=True,
    )


def _result(
    entries: list[ScheduleEntry], unscheduled: list[UnscheduledItem] | None = None, days: int = 1
) -> ScheduleResult:
    return ScheduleResult(
        algorithm="rule_based",
        algorithm_version="1.0.0",
        profile_id="P",
        profile_version=1,
        config_version=1,
        generated_at=NOW,
        horizon_start=NOW,
        horizon_end=at(days=days),
        entries=entries,
        unscheduled=unscheduled or [],
    )


class TestMetrics:
    def test_on_time_late_at_risk_and_money(self, scheduling_config: SchedulingConfig) -> None:
        orders = [
            make_order("ON", requested_delivery_date=at(hours=20), order_value=100.0, estimated_margin=10.0),
            make_order("RISK", requested_delivery_date=at(hours=6), order_value=200.0, estimated_margin=20.0),
            make_order("LATE", requested_delivery_date=at(hours=1), order_value=300.0, estimated_margin=30.0),
            make_order("NODUE", requested_delivery_date=None, order_value=400.0),
            make_order("UNSCHED", order_value=500.0, estimated_margin=50.0),
            make_order("PARTIAL", order_value=600.0, estimated_margin=60.0),
        ]
        snap = make_snapshot(
            orders=orders,
            machines=[make_machine("M1", calendar_id="CAL")],
            calendars=[make_calendar_spec("CAL")],
            default_calendar_id="CAL",
        )
        entries = [
            _entry("ON", "M1", 0, 30, 60, 1),  # ends 09:30, due +20h -> on time, slack 18.5h
            _entry("RISK", "M1", 1.5, 0, 60, 2),  # ends 10:30, due +6h (14:00) -> slack 3.5h < 8
            _entry("LATE", "M1", 2.5, 30, 60, 3),  # ends 12:00, due 09:00 -> 3h late
            _entry("NODUE", "M1", 4, 0, 60, 4),
            _entry("PARTIAL", "M1", 5, 0, 60, 5),
        ]
        unscheduled = [
            UnscheduledItem("UNSCHED", None, "no_eligible_machine", "x"),
            UnscheduledItem("PARTIAL", "PARTIAL-op2", "missing_cycle_time", "x"),
        ]
        m = compute_metrics(
            _result(entries, unscheduled),
            snap,
            {"M1": MachineCalendar(make_calendar_spec("CAL"))},
            scheduling_config,
        )
        assert m.scheduled_orders == 4 and m.unscheduled_orders == 2 and m.scheduled_operations == 5
        assert m.on_time_orders == 2 and m.late_orders == 1 and m.orders_at_risk == 1
        assert m.on_time_pct == 100.0 * 2 / 3
        assert m.avg_lateness_hours == 3.0 and m.max_lateness_hours == 3.0 and m.total_tardiness_hours == 3.0
        assert m.total_setup_hours == 1.0 and m.total_run_hours == 5.0 and m.setup_count == 2
        assert m.makespan_hours == 6.0
        assert m.revenue_scheduled == 100 + 200 + 300 + 400
        assert m.revenue_at_risk == 300 + 500 + 600 and m.margin_at_risk == 30 + 50 + 60
        # 6 busy hours of an 8 h working day inside a 1-day horizon
        assert m.machine_utilization_pct == {"M1": 75.0} and m.overall_utilization_pct == 75.0
        assert abs(m.wip_orders_avg - 6.0 / 24.0) < 1e-9

    def test_empty_schedule(self, scheduling_config: SchedulingConfig) -> None:
        snap = make_snapshot(machines=[make_machine("M1")])
        m = compute_metrics(
            _result([]), snap, {"M1": MachineCalendar(make_calendar_spec("CAL"))}, scheduling_config
        )
        assert m == ScheduleMetrics(machine_utilization_pct={"M1": 0.0})

    def test_utilization_counts_only_working_time_in_horizon(self) -> None:
        cal = MachineCalendar(make_calendar_spec("CAL"))
        # entry spans Monday 15:00 -> Tuesday 09:00 wall clock but only 2 working hours
        e = _entry("A", "M1", 7, 0, 120)
        e.end = at(days=1, hours=1)
        per_machine, overall = machine_utilization({"M1": [e]}, {"M1": cal, "M2": cal}, NOW, at(days=2))
        assert per_machine == {"M1": 100.0 * 120 / 960, "M2": 0.0}
        assert overall == 100.0 * 120 / 1920

    def test_lateness_helper(self) -> None:
        assert order_lateness_hours(at(hours=2), NOW) == 2.0
        assert order_lateness_hours(NOW, at(hours=2)) == -2.0
        assert order_lateness_hours(NOW, None) is None


class TestQuality:
    def test_components_and_weighted_score(self) -> None:
        config = SchedulingConfig(at_risk_slack_hours=8.0)
        m = ScheduleMetrics(
            scheduled_orders=100,
            on_time_pct=94.0,
            avg_lateness_hours=8.0,
            overall_utilization_pct=87.0,
            total_run_hours=81.0,
            total_setup_hours=19.0,
            orders_at_risk=14,
        )
        components = quality_components(m, config)
        assert components == {
            "on_time_delivery": 94.0,
            "lateness": 50.0,  # one at-risk slack of average lateness halves the component
            "utilization": 87.0,
            "setup_efficiency": 81.0,
            "at_risk": 86.0,
        }
        q = compute_quality(
            _result([]) if False else ScheduleResult("a", "1", "p", 1, 1, NOW, NOW, at(days=1), metrics=m),
            config,
        )
        expected = 0.4 * 94 + 0.2 * 50 + 0.15 * 87 + 0.15 * 81 + 0.1 * 86
        assert abs(q.score - expected) < 1e-9
        assert q.weights == {
            "on_time_delivery": 0.4,
            "lateness": 0.2,
            "utilization": 0.15,
            "setup_efficiency": 0.15,
            "at_risk": 0.1,
        }
        assert (
            q.summary
            == "On-time 94% · Utilization 87% · Setup efficiency 81% · Avg lateness 8.0 h · At risk 14"
        )

    def test_weights_normalised_and_unknown_keys_ignored(self) -> None:
        config = SchedulingConfig(quality_weights={"on_time_delivery": 3.0, "utilization": 1.0, "bogus": 5.0})
        m = ScheduleMetrics(scheduled_orders=1, on_time_pct=100.0, overall_utilization_pct=20.0)
        q = compute_quality(ScheduleResult("a", "1", "p", 1, 1, NOW, NOW, at(days=1), metrics=m), config)
        assert q.weights == {"on_time_delivery": 0.75, "utilization": 0.25}
        assert q.score == 0.75 * 100 + 0.25 * 20

    def test_perfect_empty_components_never_exceed_100(self) -> None:
        components = quality_components(ScheduleMetrics(), SchedulingConfig())
        assert all(0.0 <= v <= 100.0 for v in components.values())
        assert components["setup_efficiency"] == 100.0 and components["at_risk"] == 100.0

    def test_compare_schedules(self) -> None:
        config = SchedulingConfig()
        a = ScheduleResult(
            "a",
            "1",
            "p",
            1,
            1,
            NOW,
            NOW,
            at(days=1),
            metrics=ScheduleMetrics(
                scheduled_orders=10,
                on_time_pct=87.0,
                avg_lateness_hours=8.2,
                overall_utilization_pct=76.0,
                total_setup_hours=126.0,
                late_orders=3,
            ),
        )
        b = ScheduleResult(
            "b",
            "1",
            "p",
            1,
            1,
            NOW,
            NOW,
            at(days=1),
            metrics=ScheduleMetrics(
                scheduled_orders=10,
                on_time_pct=94.0,
                avg_lateness_hours=2.4,
                overall_utilization_pct=87.0,
                total_setup_hours=101.0,
                late_orders=1,
            ),
        )
        a.quality, b.quality = compute_quality(a, config), compute_quality(b, config)
        diff = compare_schedules(a, b)
        assert diff["on_time_pct"] == {
            "label": "On-time delivery",
            "before": 87.0,
            "after": 94.0,
            "delta": 7.0,
        }
        assert diff["late_orders"]["delta"] == -2
        assert diff["quality_score"]["delta"] is not None and diff["quality_score"]["delta"] > 0
        assert "On-time delivery: 87% → 94%" in diff["summary"]
        assert "Average lateness: 8.2h → 2.4h" in diff["summary"]
        assert "Machine utilization: 76% → 87%" in diff["summary"]
        assert "Setup hours: 126 → 101" in diff["summary"]
        assert diff["summary"].startswith("Schedule quality:")
