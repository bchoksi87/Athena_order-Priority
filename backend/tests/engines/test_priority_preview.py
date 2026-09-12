"""Profile preview: what changes when weights change (spec Phase 14)."""

from __future__ import annotations

from app.core.clock import FrozenClock
from app.domain.config import DueDateThresholds, PriorityProfile
from app.engines.priority import PriorityEngine, compare_profiles, default_factors
from app.engines.priority.preview import only_weights_differ, weight_changes
from tests.engines.factories import (
    NOW,
    make_calendar_spec,
    make_machine,
    make_order_with_routing,
    make_profile,
    make_snapshot,
)


def _snapshot():
    orders, ops = [], []
    for i in range(12):
        # even orders: urgent but cheap; odd orders: distant but valuable
        o, o_ops = make_order_with_routing(
            f"O{i:02d}",
            due_in_days=(0.5 + i * 0.1) if i % 2 == 0 else 12 + i,
            order_value=500.0 if i % 2 == 0 else 50_000.0 * (i + 1),
        )
        orders.append(o)
        ops.extend(o_ops)
    return make_snapshot(
        orders,
        ops,
        [make_machine("M1", calendar_id="CAL1")],
        calendars=[make_calendar_spec("CAL1")],
        default_calendar_id="CAL1",
    )


def test_weight_changes_and_only_weights_differ() -> None:
    a = make_profile()
    b = make_profile({"due_date_urgency": 40, "order_value": 0}, profile_id="B")
    assert weight_changes(a, b) == {"due_date_urgency": (25.0, 40.0), "order_value": (10.0, 0.0)}
    assert only_weights_differ(a, b)
    c = b.model_copy(update={"due_date": DueDateThresholds(critical_hours=48)})
    assert not only_weights_differ(a, c)
    disabled = a.model_copy(
        update={"weights": [w.model_copy(update={"enabled": w.key != "margin"}) for w in a.weights]}
    )
    assert weight_changes(a, disabled) == {"margin": (5.0, 0.0)}


def test_compare_profiles_reports_movements_and_summary() -> None:
    engine = PriorityEngine(default_factors(), FrozenClock(NOW))
    snapshot = _snapshot()
    a = make_profile({"due_date_urgency": 60, "order_value": 5})
    b = make_profile({"due_date_urgency": 5, "order_value": 60}, profile_id="Value-heavy")
    cmp = compare_profiles(snapshot, a, b, engine, top_n=6)
    assert cmp.orders_evaluated == 12 and cmp.top_n == 6
    assert set(cmp.top_n_a) == {f"O{i:02d}" for i in range(0, 12, 2)}  # urgent ones under A
    assert cmp.entered_top_n and cmp.left_top_n and len(cmp.entered_top_n) == len(cmp.left_top_n)
    assert all(oid not in cmp.top_n_a for oid in cmp.entered_top_n)
    assert all(cmp.rank_deltas[oid] < 0 for oid in cmp.entered_top_n)
    assert cmp.orders_changed_rank > 0 and cmp.max_abs_score_delta >= cmp.mean_abs_score_delta > 0
    assert cmp.summary == (
        f"Changing Due Date Urgency weight from 60% to 5% and Order Value weight from 5% to 60% "
        f"would move {len(cmp.entered_top_n)} order(s) into the top 6 and {len(cmp.left_top_n)} out"
    )
    assert cmp.details["context_reused"] is True


def test_compare_identical_profiles_and_threshold_change() -> None:
    engine = PriorityEngine(default_factors(), FrozenClock(NOW))
    snapshot = _snapshot()
    same = compare_profiles(snapshot, PriorityProfile(), PriorityProfile(), engine, top_n=5)
    assert same.entered_top_n == [] and same.left_top_n == [] and same.orders_changed_rank == 0
    assert same.summary.startswith("Switching from profile")
    other = PriorityProfile(profile_id="P2", due_date=DueDateThresholds(floor_score=0.0, low_score=0.0))
    changed = compare_profiles(snapshot, PriorityProfile(), other, engine, top_n=5)
    assert changed.details["context_reused"] is False
    assert all(d <= 0 for d in changed.score_deltas.values())
