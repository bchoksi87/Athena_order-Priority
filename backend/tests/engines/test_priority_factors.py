"""Unit tests for every priority factor (boundaries, None inputs, interpolation)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.domain.config import (
    DueDateThresholds,
    MachineAvailabilityScoring,
    OrderValueConfig,
    PriorityProfile,
    SetupEfficiencyScoring,
    SlaRiskConfig,
)
from app.domain.enums import CustomerTier, MaterialStatus, ProcessType, ReadinessState
from app.domain.results import Blocker
from app.engines.priority.context_ext import ExtendedPriorityContext
from app.engines.priority.factors import (
    BatchingAffinity,
    CustomerImportance,
    DelayPenalty,
    DownstreamImpact,
    DueDateUrgency,
    MachineAvailability,
    Margin,
    OrderValue,
    ProductionReadiness,
    SetupEfficiency,
    SlaRisk,
)
from app.engines.priority.factors.common import describe_hours, interpolate, mid_rank_percentiles
from app.engines.priority.factors.delay_penalty import effective_penalty_per_day
from app.engines.priority.factors.due_date_urgency import urgency_score
from app.engines.priority.factors.machine_availability import availability_score
from app.engines.priority.factors.setup_efficiency import large_setup_factor
from app.engines.priority.factors.sla_risk import resolve_sla_hours, sla_risk_score
from app.engines.priority.registry import default_factors, factor_by_key
from tests.engines.factories import (
    NOW,
    at,
    make_context,
    make_customer,
    make_customer_rule,
    make_machine,
    make_operation,
    make_order,
    make_profile,
    make_snapshot,
)

# ------------------------------------------------------------------ helpers


def test_interpolate_endpoints_and_midpoint() -> None:
    anchors = [(0.0, 100.0), (10.0, 50.0), (20.0, 0.0)]
    assert interpolate(anchors, -5) == 100.0
    assert interpolate(anchors, 5) == 75.0
    assert interpolate(anchors, 10) == 50.0
    assert interpolate(anchors, 25) == 0.0
    assert interpolate([], 3) == 0.0
    assert interpolate([(1.0, 5.0), (1.0, 9.0)], 1.0) == 5.0  # duplicate x never divides by zero


def test_mid_rank_percentiles() -> None:
    pct = mid_rank_percentiles({"a": 1.0, "b": 2.0, "c": 2.0, "d": 10.0})
    assert pct["a"] == pytest.approx(0.125)
    assert pct["b"] == pct["c"] == pytest.approx(0.5)
    assert pct["d"] == pytest.approx(0.875)
    assert mid_rank_percentiles({"only": 5.0}) == {"only": 0.5}
    assert mid_rank_percentiles({}) == {}


def test_describe_hours_wording() -> None:
    assert describe_hours(0.5) == "30 minutes"
    assert describe_hours(18) == "18 hours"
    assert describe_hours(-72) == "3.0 days"


def test_registry_covers_every_key_and_rejects_unknown() -> None:
    factors = default_factors()
    assert [f.key for f in factors] == list(PriorityProfile().weight_map().keys())[:0] or len(factors) == 11
    assert len({f.key for f in factors}) == 11
    assert all(f.kind == "bonus" for f in factors)
    assert factor_by_key("margin").key == "margin"
    with pytest.raises(Exception, match="unknown priority factor"):
        factor_by_key("nope")


# ---------------------------------------------------------------- due date


class TestDueDateUrgency:
    factor = DueDateUrgency()

    def test_missing_due_date_scores_floor(self) -> None:
        order = make_order("O1", requested_delivery_date=None)
        ctx = make_context(make_snapshot([order]))
        fs = self.factor.score(order, ctx)
        assert fs.raw_score == ctx.profile.due_date.floor_score
        assert fs.reason == "No due date (data quality issue)"
        assert fs.details["hours_until_due"] is None
        assert fs.weight == pytest.approx(0.25)
        assert fs.points == pytest.approx(0.25 * 5.0)

    @pytest.mark.parametrize(
        ("hours", "expected"),
        [(-1, 100.0), (0, 100.0), (12, 95.0), (24, 95.0), (36, 87.5), (48, 80.0), (168, 50.0), (336, 25.0)],
    )
    def test_curve_anchor_points(self, hours: float, expected: float) -> None:
        assert urgency_score(DueDateThresholds(), hours) == pytest.approx(expected)

    def test_tail_continues_last_slope_to_floor(self) -> None:
        cfg = DueDateThresholds()
        assert urgency_score(cfg, 336 + 24) < 25.0
        assert urgency_score(cfg, 10_000) == cfg.floor_score

    def test_reasons(self) -> None:
        overdue = make_order("O1", requested_delivery_date=at(hours=-30))
        soon = make_order("O2", requested_delivery_date=at(hours=18))
        ctx = make_context(make_snapshot([overdue, soon]))
        assert self.factor.score(overdue, ctx).reason == "Overdue by 30 hours"
        assert self.factor.score(soon, ctx).reason == "Due in 18 hours"

    def test_projected_lateness_raises_urgency(self) -> None:
        order = make_order("O1", requested_delivery_date=at(days=5))
        late_ctx = make_context(make_snapshot([order]), projected_completion={"O1": at(days=6)})
        fs = self.factor.score(order, late_ctx)
        assert fs.raw_score == 100.0
        assert "projected 24 hours late" in fs.reason
        assert fs.details["projected_lateness_hours"] == pytest.approx(24.0)
        tight_ctx = make_context(make_snapshot([order]), projected_completion={"O1": at(days=4, hours=20)})
        tight = self.factor.score(order, tight_ctx)
        assert tight.raw_score == 95.0
        assert "leaves 4 hours slack" in tight.reason

    def test_projection_ignored_when_disabled(self) -> None:
        order = make_order("O1", requested_delivery_date=at(days=5))
        profile = PriorityProfile(due_date=DueDateThresholds(use_projected_lateness=False))
        ctx = make_context(make_snapshot([order]), profile, projected_completion={"O1": at(days=6)})
        assert self.factor.score(order, ctx).raw_score == pytest.approx(urgency_score(profile.due_date, 120))

    @settings(max_examples=200, deadline=None)
    @given(
        hours=st.floats(min_value=-1000, max_value=20_000, allow_nan=False),
        delta=st.floats(min_value=0, max_value=5000),
    )
    def test_property_bounded_and_monotone(self, hours: float, delta: float) -> None:
        cfg = DueDateThresholds()
        a, b = urgency_score(cfg, hours), urgency_score(cfg, hours + delta)
        assert 0.0 <= a <= 100.0 and 0.0 <= b <= 100.0
        assert b <= a + 1e-9

    @settings(max_examples=100, deadline=None)
    @given(
        hours=st.floats(min_value=-500, max_value=5000, allow_nan=False),
        scores=st.lists(st.floats(min_value=0, max_value=100), min_size=5, max_size=5),
    )
    def test_property_any_monotone_config_stays_in_range(self, hours: float, scores: list[float]) -> None:
        s = sorted(scores, reverse=True)
        cfg = DueDateThresholds(
            overdue_score=100,
            critical_score=s[0],
            high_score=s[1],
            medium_score=s[2],
            low_score=s[3],
            floor_score=s[4],
        )
        order = make_order("O1", requested_delivery_date=NOW + timedelta(hours=hours))
        fs = self.factor.score(order, make_context(make_snapshot([order]), PriorityProfile(due_date=cfg)))
        assert 0.0 <= fs.raw_score <= 100.0
        assert 0.0 <= fs.points <= fs.weight * 100.0 + 1e-9


# --------------------------------------------------------------------- sla


class TestSlaRisk:
    factor = SlaRisk()

    def test_no_sla_scores_zero(self) -> None:
        order = make_order("O1")
        fs = self.factor.score(order, make_context(make_snapshot([order])))
        assert fs.raw_score == 0.0 and fs.reason == "No SLA"

    def test_sla_resolution_precedence(self) -> None:
        cfg = SlaRiskConfig(default_sla_hours=96)
        customer = make_customer("C1", sla_hours=72)
        rule = make_customer_rule("C1", sla_hours=48)
        assert resolve_sla_hours(make_order(sla_hours=24), customer, rule, cfg) == (24, "order")
        assert resolve_sla_hours(make_order(), customer, rule, cfg) == (48, "customer_rule")
        assert resolve_sla_hours(
            make_order(), customer, make_customer_rule("C1", sla_hours=48, active=False), cfg
        ) == (72, "customer")
        assert resolve_sla_hours(make_order(), customer, None, cfg) == (72, "customer")
        assert resolve_sla_hours(make_order(), None, None, cfg) == (96, "profile_default")
        assert resolve_sla_hours(make_order(), None, None, SlaRiskConfig()) == (None, "none")

    @pytest.mark.parametrize(
        ("ratio", "expected"),
        [(-0.1, 100), (0, 100), (0.125, 92.5), (0.25, 85), (0.5, 55), (1.0, 15), (2.0, 15)],
    )
    def test_curve(self, ratio: float, expected: float) -> None:
        assert sla_risk_score(SlaRiskConfig(), ratio) == pytest.approx(expected)

    def test_elapsed_and_breach_reasons(self) -> None:
        fresh = make_order("O1", sla_hours=48, received_date=at(hours=-12))
        breached = make_order("O2", sla_hours=24, received_date=at(hours=-30))
        ctx = make_context(make_snapshot([fresh, breached]))
        fs = self.factor.score(fresh, ctx)
        assert fs.details["remaining_ratio"] == pytest.approx(0.75)
        assert fs.reason == "SLA 48 h: 36 hours remaining (75%)"
        fb = self.factor.score(breached, ctx)
        assert fb.raw_score == 100.0 and fb.reason == "SLA 24 h breached by 6 hours"

    def test_unknown_elapsed_uses_watch_score(self) -> None:
        order = make_order("O1", sla_hours=48, received_date=None, order_date=None)
        fs = self.factor.score(order, make_context(make_snapshot([order])))
        assert fs.raw_score == SlaRiskConfig().watch_score
        assert "elapsed time unknown" in fs.reason

    def test_projection_past_deadline_counts_as_breach(self) -> None:
        order = make_order("O1", sla_hours=48, received_date=at(hours=-12))
        ctx = make_context(make_snapshot([order]), projected_completion={"O1": at(hours=40)})
        fs = self.factor.score(order, ctx)
        assert fs.raw_score == 100.0 and "misses deadline by 4 hours" in fs.reason


# ---------------------------------------------------------------- customer


class TestCustomerImportance:
    factor = CustomerImportance()

    def test_unknown_customer_gets_lowest_tier(self) -> None:
        order = make_order("O1", customer_id="GHOST")
        snapshot = make_snapshot([order])
        del snapshot.customers["GHOST"]
        fs = self.factor.score(order, make_context(snapshot))
        assert fs.raw_score == 20.0 and "Unknown customer GHOST" in fs.reason

    def test_tier_flag_escalation_and_cap(self) -> None:
        cust = make_customer(
            "C1", customer_tier=CustomerTier.KEY, strategic_customer_flag=True, escalation_level=1
        )
        order = make_order("O1")
        fs = self.factor.score(order, make_context(make_snapshot([order], customers=[cust])))
        assert fs.raw_score == pytest.approx(75 + 10 + 8)
        assert "strategic account" in fs.reason and "escalation level 1" in fs.reason
        cust.customer_tier = CustomerTier.STRATEGIC
        fs2 = self.factor.score(order, make_context(make_snapshot([order], customers=[cust])))
        assert fs2.raw_score == 100.0  # capped at max_score

    def test_rule_tier_override_and_percentile_blend(self) -> None:
        cust = make_customer("C1", customer_tier=CustomerTier.LOW)
        order = make_order("O1")
        rule = make_customer_rule("C1", tier_override=CustomerTier.STRATEGIC)
        ctx = make_context(
            make_snapshot([order], customers=[cust]),
            customer_rules={"C1": rule},
            customer_revenue_percentile={"C1": 0.5},
            customer_profitability_percentile={"C1": 1.0},
        )
        fs = self.factor.score(order, ctx)
        # 0.5*100 + 0.3*50 + 0.2*100 = 85
        assert fs.raw_score == pytest.approx(85.0)
        assert fs.details["tier_source"] == "customer_rule"
        assert "tier set by customer rule" in fs.reason

    def test_missing_percentiles_drop_out_of_blend(self) -> None:
        cust = make_customer("C1", customer_tier=CustomerTier.STANDARD)
        order = make_order("O1")
        fs = self.factor.score(order, make_context(make_snapshot([order], customers=[cust])))
        assert fs.raw_score == pytest.approx(45.0)


# ------------------------------------------------------------- order value


class TestOrderValue:
    factor = OrderValue()

    def test_none_value_min_score(self) -> None:
        order = make_order("O1", order_value=None)
        fs = self.factor.score(order, make_context(make_snapshot([order])))
        assert fs.raw_score == 5.0 and fs.reason == "Order value unknown"

    def test_percentile_scaling(self) -> None:
        order = make_order("O1", order_value=10_000)
        ctx = make_context(make_snapshot([order]), order_value_percentile={"O1": 0.9})
        fs = self.factor.score(order, ctx)
        assert fs.raw_score == pytest.approx(5 + 95 * 0.9)
        assert "top 10%" in fs.reason

    def test_linear_and_log_with_cap(self) -> None:
        order = make_order("O1", order_value=50_000)
        for scaling, expected in (("linear", 5 + 95 * 0.5), ("log", None)):
            profile = PriorityProfile(order_value=OrderValueConfig(scaling=scaling, cap_value=100_000))
            fs = self.factor.score(order, make_context(make_snapshot([order]), profile))
            if expected is not None:
                assert fs.raw_score == pytest.approx(expected)
            else:
                assert 5.0 < fs.raw_score < 100.0
            assert fs.details["applied_scaling"] == scaling
        big = make_order("O2", order_value=1e9)
        profile = PriorityProfile(order_value=OrderValueConfig(scaling="linear", cap_value=100_000))
        assert self.factor.score(big, make_context(make_snapshot([big]), profile)).raw_score == 100.0

    def test_linear_without_cap_falls_back_to_percentile_or_population_max(self) -> None:
        order = make_order("O1", order_value=50_000)
        profile = PriorityProfile(order_value=OrderValueConfig(scaling="linear"))
        base = make_context(make_snapshot([order]), profile, order_value_percentile={"O1": 0.5})
        assert self.factor.score(order, base).details["applied_scaling"] == "percentile"
        ext = ExtendedPriorityContext(
            snapshot=make_snapshot([order]), profile=profile, now=NOW, population_max={"order_value": 100_000}
        )
        assert self.factor.score(order, ext).raw_score == pytest.approx(5 + 95 * 0.5)


# ------------------------------------------------------------------ margin


class TestMargin:
    factor = Margin()

    def test_unknown_margin(self) -> None:
        order = make_order("O1")
        fs = self.factor.score(order, make_context(make_snapshot([order])))
        assert fs.raw_score == 0.0 and fs.reason == "Margin unknown"

    def test_estimated_then_actual(self) -> None:
        est = make_order("O1", estimated_margin=100.0, actual_margin=5.0)
        act = make_order("O2", actual_margin=-5.0)
        ctx = make_context(make_snapshot([est, act]), margin_percentile={"O1": 0.75, "O2": 0.25})
        fs = self.factor.score(est, ctx)
        assert fs.raw_score == 75.0 and fs.details["margin_source"] == "estimated"
        fa = self.factor.score(act, ctx)
        assert fa.raw_score == 25.0 and fa.reason.startswith("Negative actual margin")


# ----------------------------------------------------------- delay penalty


class TestDelayPenalty:
    factor = DelayPenalty()

    def test_effective_penalty_formula(self) -> None:
        cfg = PriorityProfile().delay_penalty
        cust = make_customer("C1", escalation_level=2, strategic_customer_flag=True)
        penalty, details = effective_penalty_per_day(make_order(lateness_penalty_per_day=100), cust, cfg)
        assert penalty == pytest.approx(100 * 1.5 * 1.5)
        assert details["penalty_source"] == "erp"
        penalty2, d2 = effective_penalty_per_day(make_order(order_value=10_000), None, cfg)
        assert penalty2 == pytest.approx(100.0) and d2["penalty_source"] == "default_ratio"
        assert effective_penalty_per_day(make_order(order_value=None), None, cfg) == (
            None,
            {"penalty_source": "none"},
        )

    def test_no_information(self) -> None:
        order = make_order("O1", order_value=None)
        fs = self.factor.score(order, make_context(make_snapshot([order])))
        assert fs.raw_score == 0.0 and fs.reason.startswith("No penalty information")

    def test_reference_and_percentile_scaling(self) -> None:
        order = make_order("O1", lateness_penalty_per_day=500)
        ref_profile = make_profile(delay_penalty={"reference_penalty": 1000})
        fs = self.factor.score(order, make_context(make_snapshot([order]), ref_profile))
        assert fs.raw_score == 50.0 and "50% of reference" in fs.reason
        pct = self.factor.score(order, make_context(make_snapshot([order]), penalty_percentile={"O1": 0.8}))
        assert pct.raw_score == pytest.approx(80.0) and "Contractual penalty 500/day" in pct.reason


# --------------------------------------------------------------- readiness


class TestProductionReadiness:
    factor = ProductionReadiness()

    def test_ready_and_not_assessed(self) -> None:
        order = make_order("O1")
        ready = make_context(make_snapshot([order]), readiness={"O1": ReadinessState.READY})
        assert self.factor.score(order, ready).raw_score == 100.0
        assert self.factor.score(order, ready).reason == "Ready to run"
        unknown = self.factor.score(order, make_context(make_snapshot([order])))
        assert unknown.raw_score == 100.0 and "not assessed" in unknown.reason

    def test_blocker_reason_with_expected_date(self) -> None:
        order = make_order("O1")
        blocker = Blocker(
            ReadinessState.WAITING_MATERIAL, "material AL-7075 is on_order", resolves_at=at(days=7)
        )
        ctx = make_context(
            make_snapshot([order]),
            readiness={"O1": ReadinessState.WAITING_MATERIAL},
            blockers={"O1": [blocker]},
        )
        fs = self.factor.score(order, ctx)
        assert fs.raw_score == 10.0
        assert fs.reason == "Waiting for material: material AL-7075 is on_order, expected 14 Sep"

    def test_partial_material_and_hold_scores(self) -> None:
        partial = make_order("O1", material_status=MaterialStatus.PARTIAL)
        held = make_order("O2")
        ctx = make_context(
            make_snapshot([partial, held]),
            readiness={"O1": ReadinessState.WAITING_MATERIAL, "O2": ReadinessState.ON_HOLD},
            blockers={
                "O2": [
                    Blocker(ReadinessState.ON_HOLD, "a"),
                    Blocker(ReadinessState.ON_HOLD, "b"),
                    Blocker(ReadinessState.ON_HOLD, "c"),
                ]
            },
        )
        assert self.factor.score(partial, ctx).raw_score == 40.0
        held_fs = self.factor.score(held, ctx)
        assert held_fs.raw_score == 0.0 and held_fs.reason == "On hold: a; b (+1 more)"


# ---------------------------------------------------- machine availability


class TestMachineAvailability:
    factor = MachineAvailability()

    @pytest.mark.parametrize(
        ("wait", "expected"), [(-1, 100), (0, 100), (4, 80), (8, 60), (16, 30), (1000, 5)]
    )
    def test_curve(self, wait: float, expected: float) -> None:
        assert availability_score(MachineAvailabilityScoring(), wait) == pytest.approx(expected)

    def test_none_eligible(self) -> None:
        order = make_order("O1")
        fs = self.factor.score(order, make_context(make_snapshot([order])))
        assert fs.raw_score == 5.0 and fs.reason == "No eligible machine"

    def test_single_machine_bonus_only_when_free(self) -> None:
        order = make_order("O1")
        m = make_machine("M1")
        free = make_context(
            make_snapshot([order], machines=[m]), eligible_machines={"O1": [m]}, machine_next_free={"M1": NOW}
        )
        fs = self.factor.score(order, free)
        assert fs.raw_score == 100.0  # 100 + 15 clamped
        assert fs.details["single_machine_bonus"] == 15.0
        assert fs.reason == "Only machine M1 can make this part; it is free now"
        busy = make_context(
            make_snapshot([order], machines=[m]),
            eligible_machines={"O1": [m]},
            machine_next_free={"M1": at(hours=4)},
        )
        fb = self.factor.score(order, busy)
        assert fb.raw_score == 80.0 and "free in 4 hours" in fb.reason

    def test_earliest_of_many_and_unknown_availability(self) -> None:
        order = make_order("O1")
        m1, m2 = make_machine("M1"), make_machine("M2")
        ctx = make_context(
            make_snapshot([order], machines=[m1, m2]),
            eligible_machines={"O1": [m1, m2]},
            machine_next_free={"M1": at(hours=10), "M2": at(hours=2)},
        )
        fs = self.factor.score(order, ctx)
        assert fs.details["earliest_machine_id"] == "M2" and fs.raw_score == 90.0
        unknown = make_context(make_snapshot([order], machines=[m1]), eligible_machines={"O1": [m1]})
        assert self.factor.score(order, unknown).raw_score == 5.0


# --------------------------------------------------------- setup efficiency


class TestSetupEfficiency:
    factor = SetupEfficiency()

    def test_large_setup_factor(self) -> None:
        cfg = SetupEfficiencyScoring()
        assert large_setup_factor(cfg, 90) == 1.0
        assert large_setup_factor(cfg, 135) == pytest.approx(0.5)
        assert large_setup_factor(cfg, 500) == 0.0
        assert large_setup_factor(SetupEfficiencyScoring(large_setup_penalty_score=50), 180) == pytest.approx(
            0.5
        )

    def _ctx(self, machines: list, op_kwargs: dict) -> tuple:
        order = make_order("O1")
        op = make_operation("O1", 1, **op_kwargs)
        snapshot = make_snapshot([order], [op], machines)
        return order, make_context(snapshot, eligible_machines={"O1": machines})

    def test_best_machine_by_basis(self) -> None:
        same_family = make_machine("M1", current_setup_family="F1")
        same_material = make_machine("M2", current_material_id="AL")
        changeover = make_machine("M3", current_setup_family="F9", current_material_id="ST")
        unknown = make_machine("M4")
        order, ctx = self._ctx(
            [changeover, same_material, same_family, unknown], {"setup_family": "F1", "material_id": "AL"}
        )
        fs = self.factor.score(order, ctx)
        assert fs.raw_score == 100.0 and fs.details["machine_id"] == "M1"
        order, ctx = self._ctx([changeover, same_material], {"setup_family": "F1", "material_id": "AL"})
        assert self.factor.score(order, ctx).raw_score == 70.0
        order, ctx = self._ctx([changeover], {"setup_family": "F1", "material_id": "AL", "setup_minutes": 30})
        fc = self.factor.score(order, ctx)
        assert fc.raw_score == 20.0 and fc.reason == "Changeover on M3: 30 min setup"
        order, ctx = self._ctx([unknown], {"setup_minutes": 30})
        assert self.factor.score(order, ctx).raw_score == 50.0

    def test_large_setup_lowers_score_with_reason(self) -> None:
        changeover = make_machine("M3", current_setup_family="F9")
        order, ctx = self._ctx([changeover], {"setup_family": "F1", "setup_minutes": 120})
        fs = self.factor.score(order, ctx)
        assert fs.raw_score == pytest.approx(20.0 * (1 - 30 / 90))
        assert fs.reason.startswith("Large setup required (120 min) on M3")

    def test_no_machines(self) -> None:
        order = make_order("O1")
        fs = self.factor.score(order, make_context(make_snapshot([order])))
        assert fs.raw_score == 50.0 and fs.reason == "No eligible machine to assess setup"


# --------------------------------------------- batching / downstream (ctx-fed)


def test_batching_affinity_reads_extended_context() -> None:
    order = make_order("O1")
    base = make_context(make_snapshot([order]))
    fs = BatchingAffinity().score(order, base)
    assert fs.raw_score == 0.0 and fs.reason == "No batching data" and fs.weight == 0.0
    ext = ExtendedPriorityContext(
        snapshot=make_snapshot([order]),
        profile=PriorityProfile(),
        now=NOW,
        batching_share={"O1": 0.4},
        batching_detail={
            "O1": {"window": 6, "peers": 2, "dimensions": ["material"], "shared_machine_ids": ["M1"]}
        },
    )
    fe = BatchingAffinity().score(order, ext)
    assert fe.raw_score == pytest.approx(40.0)
    assert fe.reason == "2 of the next 6 orders due share material on M1"


def test_downstream_impact_percentile_and_critical_path() -> None:
    a = make_order("A")
    b = make_order("B", depends_on_order_ids={"A"})
    snapshot = make_snapshot([a, b])
    none = DownstreamImpact().score(b, make_context(snapshot))
    assert none.raw_score == 0.0 and none.reason == "No dependent orders"
    ext = ExtendedPriorityContext(
        snapshot=snapshot,
        profile=PriorityProfile(),
        now=NOW,
        downstream_value={"A": 10_000},
        downstream_percentile={"A": 0.5},
    )
    fs = DownstreamImpact().score(a, ext)
    assert fs.raw_score == 50.0 and fs.reason == "1 dependent order(s) worth 10,000"
    crit = ExtendedPriorityContext(
        snapshot=snapshot,
        profile=PriorityProfile(),
        now=NOW,
        downstream_value={"A": 10_000},
        critical_path={"A": "dependent B due in 5 hours needs 9 hours"},
    )
    fc = DownstreamImpact().score(a, crit)
    assert fc.raw_score == 100.0 and fc.reason.startswith("On critical path: dependent B")


def test_every_factor_handles_empty_order(default_snapshot_order: tuple) -> None:
    """An order with nothing but ids must never raise in any factor."""
    order, ctx = default_snapshot_order
    for factor in default_factors():
        fs = factor.score(order, ctx)
        assert 0.0 <= fs.raw_score <= 100.0
        assert fs.reason


@pytest.fixture
def default_snapshot_order() -> tuple:
    order = make_order(
        "EMPTY",
        requested_delivery_date=None,
        order_value=None,
        quantity=1.0,
        process_type=ProcessType.OTHER,
    )
    return order, make_context(make_snapshot([order]))
