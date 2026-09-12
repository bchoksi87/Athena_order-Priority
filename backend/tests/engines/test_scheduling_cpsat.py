"""CP-SAT re-sequencing smoke tests (skipped when OR-Tools is not installed)."""

from __future__ import annotations

import pytest

pytest.importorskip("ortools")

from app.core.clock import FrozenClock  # noqa: E402
from app.domain.config import SchedulingConfig  # noqa: E402
from app.domain.enums import ProcessType  # noqa: E402
from app.engines.calendar import build_calendars  # noqa: E402
from app.engines.constraints import default_constraint_engine  # noqa: E402
from app.engines.scheduling.cpsat import CpSatScheduler  # noqa: E402
from app.engines.scheduling.registry import default_registry  # noqa: E402
from app.engines.scheduling.rule_based import RuleBasedScheduler  # noqa: E402
from tests.engines.factories import (  # noqa: E402
    NOW,
    at,
    make_calendar_spec,
    make_machine,
    make_order_with_ops,
    make_priorities,
    make_snapshot,
)


def _snapshot(urgent_low_priority: bool):
    orders, ops = [], []
    for i, score_due in enumerate([(90, 3), (80, 3), (75, 3), (60, 3)]):
        _, due_days = score_due
        o, os_ = make_order_with_ops(
            f"O{i}",
            (ProcessType.CNC_MACHINING, ProcessType.DEBURRING),
            op_overrides=[
                {"setup_minutes": 30.0, "cycle_minutes_per_unit": 6.0},
                {"setup_minutes": 30.0, "cycle_minutes_per_unit": 6.0},
            ],
            requested_delivery_date=at(days=due_days),
        )
        orders.append(o)
        ops.extend(os_)
    if urgent_low_priority:
        orders[3].requested_delivery_date = at(hours=5)  # low score but very urgent
    machines = [
        make_machine("CNC-01", calendar_id="CAL"),
        make_machine("CNC-02", calendar_id="CAL", preferred_rank=1),
        make_machine("DEB-01", ProcessType.DEBURRING, "DEB", calendar_id="CAL"),
    ]
    return make_snapshot(
        orders=orders,
        operations=ops,
        machines=machines,
        calendars=[make_calendar_spec("CAL")],
        default_calendar_id="CAL",
    )


SCORES = {"O0": 90.0, "O1": 80.0, "O2": 75.0, "O3": 60.0}


class TestCpSat:
    def test_registered(self) -> None:
        registry = default_registry()
        assert "cpsat" in registry.names()
        assert isinstance(registry.create("cpsat", FrozenClock(NOW)), CpSatScheduler)

    def test_reduces_tardiness_without_violating_precedence(self) -> None:
        snap = _snapshot(urgent_low_priority=True)
        config = SchedulingConfig()
        calendars = build_calendars(snap)
        engine = default_constraint_engine(config)
        base = RuleBasedScheduler(FrozenClock(NOW)).schedule(
            snap, make_priorities(SCORES), config, calendars, engine
        )
        cp = CpSatScheduler(FrozenClock(NOW), time_limit_seconds=5.0, top_k_machines=3)
        result = cp.schedule(snap, make_priorities(SCORES), config, calendars, engine)
        assert result.algorithm == "cpsat" and result.algorithm_version == "0.1.0"
        assert result.metrics.total_tardiness_hours <= base.metrics.total_tardiness_hours
        assert result.quality is not None and base.quality is not None
        assert result.quality.score >= base.quality.score
        assert len(result.entries) == len(base.entries)
        # precedence and no-overlap hold after re-sequencing
        for order_id in SCORES:
            op1, op2 = result.entries_for_order(order_id)
            assert op2.setup_start >= op1.end
        for machine_id in ("CNC-01", "CNC-02", "DEB-01"):
            entries = result.entries_for_machine(machine_id)
            for a, b in zip(entries, entries[1:], strict=False):
                assert a.end <= b.setup_start
            assert [e.sequence_on_machine for e in entries] == list(range(1, len(entries) + 1))
        assert any(w.startswith("cpsat: DEB-01") for w in result.warnings)
        moved = [e for e in result.entries if "CP-SAT re-sequenced" in e.placement_reason]
        assert moved and all(e.machine_id == "DEB-01" for e in moved)
        base_o3 = base.entries_for_order("O3")[-1]
        new_o3 = result.entries_for_order("O3")[-1]
        assert base_o3.expected_lateness_hours is not None and base_o3.expected_lateness_hours > 0
        assert new_o3.expected_lateness_hours is not None and new_o3.expected_lateness_hours <= 0
        deb_order = [e.order_id for e in result.entries_for_machine("DEB-01")]
        assert deb_order.index("O3") < deb_order.index("O0") and deb_order.index("O3") < deb_order.index("O2")
        assert result.metrics.late_orders < base.metrics.late_orders

    def test_no_improvement_keeps_rule_based_sequence(self) -> None:
        snap = _snapshot(urgent_low_priority=False)
        config = SchedulingConfig()
        calendars = build_calendars(snap)
        engine = default_constraint_engine(config)
        base = RuleBasedScheduler(FrozenClock(NOW)).schedule(
            snap, make_priorities(SCORES), config, calendars, engine
        )
        result = CpSatScheduler(FrozenClock(NOW), time_limit_seconds=2.0).schedule(
            snap, make_priorities(SCORES), config, calendars, engine
        )
        assert [(e.machine_id, e.order_id) for e in result.entries] == [
            (e.machine_id, e.order_id) for e in base.entries
        ]
        assert result.quality is not None and base.quality is not None
        assert abs(result.quality.score - base.quality.score) < 1e-9

    def test_top_k_zero_is_pure_fallback(self) -> None:
        snap = _snapshot(urgent_low_priority=True)
        config = SchedulingConfig()
        calendars = build_calendars(snap)
        engine = default_constraint_engine(config)
        result = CpSatScheduler(FrozenClock(NOW), top_k_machines=0).schedule(
            snap, make_priorities(SCORES), config, calendars, engine
        )
        assert result.algorithm == "cpsat"
        assert "cpsat: no machine re-sequenced; rule-based schedule returned" in result.warnings

    def test_deterministic(self) -> None:
        snap = _snapshot(urgent_low_priority=True)
        config = SchedulingConfig()
        calendars = build_calendars(snap)
        engine = default_constraint_engine(config)
        cp = CpSatScheduler(FrozenClock(NOW), time_limit_seconds=5.0)
        a = cp.schedule(snap, make_priorities(SCORES), config, calendars, engine)
        b = cp.schedule(snap, make_priorities(SCORES), config, calendars, engine)
        assert a.entries == b.entries
