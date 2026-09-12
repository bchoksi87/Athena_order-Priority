"""PriorityEngine: adjustments, clamping, blocked cap, risk, ranking and the fairness cap."""

from __future__ import annotations

import pytest

from app.core.clock import FrozenClock
from app.domain.config import AgingConfig, FairnessConfig, PriorityProfile, RiskThresholds
from app.domain.enums import OverrideType, ProcessType, ReadinessState, RiskLevel
from app.engines.priority import PriorityEngine, default_factors
from app.engines.priority.adjustments import (
    aging_adjustment,
    erp_priority_adjustment,
    expedite_adjustment,
    override_adjustments,
    starvation_adjustment,
    waiting_days,
)
from app.engines.priority.engine import assess_risk
from app.engines.priority.ranking import max_slots_per_customer, rank_results
from tests.engines.factories import (
    NOW,
    at,
    make_calendar_spec,
    make_context,
    make_customer_rule,
    make_expedite,
    make_machine,
    make_order,
    make_order_with_routing,
    make_override,
    make_snapshot,
)


@pytest.fixture
def engine() -> PriorityEngine:
    return PriorityEngine(default_factors(), FrozenClock(NOW))


def _plant(orders: list, operations: list, **snapshot_kwargs) -> object:
    return make_snapshot(
        orders=orders,
        operations=operations,
        machines=[
            make_machine("M1", calendar_id="CAL1"),
            make_machine("M2", calendar_id="CAL1", preferred_rank=1),
            make_machine("D1", ProcessType.DEBURRING, "DEBURRING", calendar_id="CAL1"),
        ],
        calendars=[make_calendar_spec("CAL1")],
        default_calendar_id="CAL1",
        **snapshot_kwargs,
    )


# ------------------------------------------------------------- adjustments


def test_waiting_days_prefers_received_date() -> None:
    order = make_order(received_date=at(days=-3), order_date=at(days=-10))
    assert waiting_days(order, NOW) == pytest.approx(3.0)
    assert waiting_days(make_order(order_date=at(days=-10)), NOW) == pytest.approx(10.0)
    assert waiting_days(make_order(), NOW) is None
    assert waiting_days(make_order(received_date=at(days=2)), NOW) == 0.0


def test_erp_priority_points_from_profile() -> None:
    profile = PriorityProfile()
    assert erp_priority_adjustment(make_order(erp_priority=1), profile).points == 10.0
    assert erp_priority_adjustment(make_order(erp_priority=5), profile).points == -6.0
    assert erp_priority_adjustment(make_order(erp_priority=3), profile) is None  # zero points: omitted
    assert erp_priority_adjustment(make_order(erp_priority=9), profile) is None
    assert erp_priority_adjustment(make_order(), profile) is None


def test_aging_starts_after_threshold_and_caps() -> None:
    profile = PriorityProfile()
    assert aging_adjustment(make_order(received_date=at(days=-5)), profile, NOW) is None
    adj = aging_adjustment(make_order(received_date=at(days=-8)), profile, NOW)
    assert adj is not None and adj.points == pytest.approx(6.0) and adj.kind == "aging"
    capped = aging_adjustment(make_order(received_date=at(days=-40)), profile, NOW)
    assert capped is not None and capped.points == 20.0 and "capped at 20" in capped.reason
    disabled = PriorityProfile(aging=AgingConfig(enabled=False))
    assert aging_adjustment(make_order(received_date=at(days=-40)), disabled, NOW) is None
    assert aging_adjustment(make_order(), profile, NOW) is None


def test_starvation_boost_at_threshold() -> None:
    profile = PriorityProfile()
    assert starvation_adjustment(make_order(received_date=at(days=-9.9)), profile, NOW) is None
    adj = starvation_adjustment(make_order(received_date=at(days=-10)), profile, NOW)
    assert adj is not None and adj.points == 25.0 and adj.kind == "fairness"
    off = PriorityProfile(fairness=FairnessConfig(enabled=False))
    assert starvation_adjustment(make_order(received_date=at(days=-30)), off, NOW) is None


def test_expedite_active_capped_and_expired() -> None:
    profile = PriorityProfile()
    order = make_order("O1")
    active = {"O1": make_expedite("O1", boost_points=90)}
    adj = expedite_adjustment(order, active, profile, NOW)
    assert adj is not None and adj.points == 60.0 and "capped at 60" in adj.reason
    assert adj.source_id == "E1" and "expires in 4 hours" in adj.reason
    expired = {"O1": make_expedite("O1", boost_points=30, expires_at=at(hours=-1))}
    assert expedite_adjustment(order, expired, profile, NOW) is None
    assert expedite_adjustment(order, {}, profile, NOW) is None


def test_override_types() -> None:
    order = make_order("O1")
    inc = make_override("O1", OverrideType.INCREASE_PRIORITY, "A", value=7, created_at=at(hours=-3))
    dec = make_override("O1", OverrideType.DECREASE_PRIORITY, "B", value=2, created_at=at(hours=-2))
    adjs, forced = override_adjustments(order, {"O1": [dec, inc]}, 50.0)
    assert [a.points for a in adjs] == [7.0, -2.0] and not forced  # chronological
    assert adjs[0].source_id == "A" and "raised by manager" in adjs[0].reason
    set_abs = make_override("O1", OverrideType.SET_PRIORITY, "S", value=90, created_at=at(hours=-1))
    adjs, forced = override_adjustments(order, {"O1": [inc, set_abs]}, 50.0)
    assert sum(a.points for a in adjs) == pytest.approx(40.0) and not forced
    force = make_override("O1", OverrideType.FORCE_NEXT, "F", created_at=at(hours=-4))
    adjs, forced = override_adjustments(order, {"O1": [force, dec]}, 50.0)
    assert (
        forced
        and adjs[-1].reason.startswith("Forced next by")
        and 50.0 + sum(a.points for a in adjs) == 100.0
    )
    no_value = make_override("O1", OverrideType.INCREASE_PRIORITY, "N", value=None)
    hold = make_override("O1", OverrideType.HOLD_ORDER, "H")
    assert override_adjustments(order, {"O1": [no_value, hold]}, 50.0) == ([], False)


# ---------------------------------------------------------------- evaluate


def test_evaluate_only_open_orders_with_normalised_weights(engine: PriorityEngine) -> None:
    o1, ops1 = make_order_with_routing("O1", due_in_days=1)
    o2, ops2 = make_order_with_routing("O2", due_in_days=1, completed_quantity=10.0)
    results = engine.evaluate(_plant([o1, o2], ops1 + ops2), PriorityProfile())
    assert set(results) == {"O1"}
    r = results["O1"]
    assert [f.key for f in r.factors] == [f.key for f in default_factors()]
    assert sum(f.weight for f in r.factors) == pytest.approx(1.0)
    assert r.base_score == pytest.approx(sum(f.points for f in r.factors))
    assert r.score == pytest.approx(r.base_score + sum(a.points for a in r.adjustments))
    assert r.rank == 1 and r.computed_at == NOW and r.profile_id == "PriorityProfile-A"
    zero = [f for f in r.factors if f.key == "batching_affinity"][0]
    assert zero.weight == 0.0 and zero.points == 0.0 and zero.reason  # still evaluated


def test_adjustments_applied_in_order_and_clamped(engine: PriorityEngine) -> None:
    order, ops = make_order_with_routing("O1", due_in_days=0.2, received_date=at(days=-12), erp_priority=1)
    snapshot = _plant(
        [order],
        ops,
        expedites=[make_expedite("O1", 30)],
        overrides=[make_override("O1", OverrideType.INCREASE_PRIORITY, value=5)],
    )
    rules = {"C1": make_customer_rule("C1", priority_boost_points=4, notes="VIP")}
    r = engine.evaluate(snapshot, PriorityProfile(), rules)["O1"]
    assert [a.kind for a in r.adjustments] == [
        "erp_priority",
        "customer_rule",
        "aging",
        "fairness",
        "expedite",
        "override",
    ]
    raw = r.base_score + sum(a.points for a in r.adjustments)
    assert raw > 100.0 and r.score == 100.0
    assert "Cap" in r.explanation and "Total: 100" in r.explanation


def test_negative_total_clamps_to_zero(engine: PriorityEngine) -> None:
    order, ops = make_order_with_routing("O1", due_in_days=30, order_value=None)
    snapshot = _plant(
        [order], ops, overrides=[make_override("O1", OverrideType.DECREASE_PRIORITY, value=500)]
    )
    r = engine.evaluate(snapshot, PriorityProfile())["O1"]
    assert r.score == 0.0


def test_set_priority_and_force_next(engine: PriorityEngine) -> None:
    o1, ops1 = make_order_with_routing("O1", due_in_days=1)
    o2, ops2 = make_order_with_routing("O2", due_in_days=30)
    snapshot = _plant(
        [o1, o2],
        ops1 + ops2,
        overrides=[
            make_override("O1", OverrideType.SET_PRIORITY, "S", value=42),
            make_override("O2", OverrideType.FORCE_NEXT, "F"),
        ],
    )
    results = engine.evaluate(snapshot, PriorityProfile())
    assert results["O1"].score == pytest.approx(42.0) and not results["O1"].forced_next
    assert results["O2"].score == 100.0 and results["O2"].forced_next and results["O2"].rank == 1
    assert "Forced next by planner override" in results["O2"].explanation


def test_blocked_orders_flagged_and_capped(engine: PriorityEngine) -> None:
    order, ops = make_order_with_routing("O1", due_in_days=1, on_hold=True, hold_reason="credit check")
    snapshot = _plant([order], ops)
    r = engine.evaluate(snapshot, PriorityProfile())["O1"]
    assert r.blocked and r.readiness is ReadinessState.ON_HOLD
    assert r.blocking_reasons == ["order on hold: credit check"]
    assert "Blocked: order on hold: credit check" in r.explanation
    capped = engine.evaluate(snapshot, PriorityProfile(blocked_order_cap=10.0))["O1"]
    assert capped.score == 10.0 and "Blocked order: score capped" in capped.explanation


def test_evaluate_order_uses_given_context(engine: PriorityEngine) -> None:
    order = make_order("O1", requested_delivery_date=at(hours=6))
    ctx = make_context(make_snapshot([order]), readiness={"O1": ReadinessState.READY})
    r = engine.evaluate_order(order, ctx)
    assert r.rank is None and r.explanation.startswith("ORDER #O1")
    assert r.risk_level is RiskLevel.HIGH


def test_duplicate_factor_keys_rejected() -> None:
    factors = default_factors()
    with pytest.raises(ValueError, match="duplicate factor keys"):
        PriorityEngine([*factors, factors[0]], FrozenClock(NOW))


# -------------------------------------------------------------------- risk


def test_assess_risk_levels() -> None:
    thresholds = RiskThresholds()
    snapshot = make_snapshot()

    def risk(order, **ctx_fields):
        return assess_risk(order, make_context(snapshot, **ctx_fields), thresholds)[0]

    assert risk(make_order(requested_delivery_date=None)) is RiskLevel.LOW
    assert risk(make_order(requested_delivery_date=at(hours=-1))) is RiskLevel.CRITICAL
    late = make_order("L", requested_delivery_date=at(days=2))
    assert risk(late, projected_completion={"L": at(days=3)}) is RiskLevel.CRITICAL
    assert risk(late, projected_completion={"L": at(days=1, hours=20)}) is RiskLevel.HIGH
    assert risk(late, projected_completion={"L": at(days=1)}) is RiskLevel.MEDIUM
    far = make_order("F", requested_delivery_date=at(days=10))
    assert risk(far, projected_completion={"F": at(days=1)}) is RiskLevel.LOW
    assert risk(far, remaining_minutes={"F": 60 * 24 * 9.9}) is RiskLevel.HIGH
    assert risk(far, remaining_minutes={"F": 60 * 24 * 11}) is RiskLevel.CRITICAL
    assert risk(make_order(requested_delivery_date=at(hours=30))) is RiskLevel.MEDIUM
    assert risk(make_order(requested_delivery_date=at(hours=6))) is RiskLevel.HIGH
    assert risk(make_order(requested_delivery_date=at(days=5))) is RiskLevel.LOW


def test_result_carries_projection_fields(engine: PriorityEngine) -> None:
    order, ops = make_order_with_routing("O1", due_in_days=0.1)
    r = engine.evaluate(_plant([order], ops), PriorityProfile())["O1"]
    assert r.hours_until_due == pytest.approx(2.4)
    assert r.projected_completion is not None and r.projected_lateness_hours is not None


# ----------------------------------------------------------------- ranking


def test_ranking_is_deterministic_and_tie_broken_by_due_then_id(engine: PriorityEngine) -> None:
    orders, ops = [], []
    for i, days in ((1, 3), (2, 3), (3, 1)):
        o, o_ops = make_order_with_routing(f"O{i}", due_in_days=days)
        orders.append(o)
        ops.extend(o_ops)
    snapshot = _plant(orders, ops)
    first = engine.evaluate(snapshot, PriorityProfile())
    second = engine.evaluate(snapshot, PriorityProfile())
    assert [first[k].rank for k in sorted(first)] == [second[k].rank for k in sorted(second)]
    assert [first[k].score for k in sorted(first)] == [second[k].score for k in sorted(second)]
    assert first["O3"].rank == 1  # most urgent
    assert first["O1"].score == first["O2"].score and first["O1"].rank == 2 and first["O2"].rank == 3


def test_fairness_top_n_share_demotes_excess_orders() -> None:
    fairness = FairnessConfig(top_n=4, max_top_n_share_per_customer=0.5)
    assert max_slots_per_customer(fairness) == 2
    orders = [make_order(f"A{i}", customer_id="A", requested_delivery_date=at(days=i)) for i in range(4)]
    orders += [make_order(f"B{i}", customer_id="B", requested_delivery_date=at(days=i)) for i in range(2)]
    snapshot = make_snapshot(orders)
    ctx = make_context(snapshot)
    engine = PriorityEngine([], FrozenClock(NOW))  # no factors: scores come from overrides only
    results = {o.order_id: engine.evaluate_order(o, ctx) for o in orders}
    for oid, score in (("A0", 90), ("A1", 80), ("A2", 70), ("A3", 60), ("B0", 50), ("B1", 40)):
        results[oid].score = float(score)
    ordered = rank_results(results, snapshot, fairness)
    assert ordered == ["A0", "A1", "B0", "B1", "A2", "A3"]
    assert results["A2"].rank == 5 and results["A2"].score == 70.0  # rank changed, score not
    fair = [a for a in results["A2"].adjustments if a.kind == "fairness"]
    assert len(fair) == 1 and fair[0].points == 0.0 and "already holds 2 of the top 4" in fair[0].reason
    assert not any(a.kind == "fairness" for a in results["A0"].adjustments)


def test_fairness_cap_never_demotes_forced_next_and_respects_disable() -> None:
    orders = [make_order(f"A{i}", customer_id="A") for i in range(3)] + [make_order("B0", customer_id="B")]
    snapshot = make_snapshot(orders)
    ctx = make_context(snapshot)
    engine = PriorityEngine([], FrozenClock(NOW))
    results = {o.order_id: engine.evaluate_order(o, ctx) for o in orders}
    for oid, score in (("A0", 90), ("A1", 80), ("A2", 70), ("B0", 10)):
        results[oid].score = float(score)
    results["A2"].forced_next = True
    fairness = FairnessConfig(top_n=2, max_top_n_share_per_customer=0.5)
    assert rank_results(results, snapshot, fairness) == ["A2", "B0", "A0", "A1"]  # forced A2 uses A's slot
    for r in results.values():
        r.adjustments.clear()
    assert rank_results(results, snapshot, FairnessConfig(enabled=False)) == ["A2", "A0", "A1", "B0"]


def test_evaluate_scales_to_many_orders(engine: PriorityEngine) -> None:
    import time

    orders, ops = [], []
    for i in range(600):
        o, o_ops = make_order_with_routing(
            f"O{i:04d}",
            due_in_days=(i % 20) + 0.5,
            order_value=1000.0 * (i % 37 + 1),
            customer_id=f"C{i % 40}",
        )
        orders.append(o)
        ops.extend(o_ops)
    snapshot = _plant(orders, ops)
    start = time.perf_counter()
    results = engine.evaluate(snapshot, PriorityProfile())
    elapsed = time.perf_counter() - start
    assert len(results) == 600 and sorted(r.rank for r in results.values()) == list(range(1, 601))
    assert elapsed < 5.0
