"""Each soft constraint and the shared setup estimate."""

from __future__ import annotations

from app.domain.config import BatchingRules, MachinePreferenceRules, SchedulingConfig, SetupRules
from app.engines.constraints.base import ConstraintContext, MachineState
from app.engines.constraints.soft import (
    BatchPreference,
    CustomerSequencePreference,
    EnergyCost,
    PreferredMachine,
    SetupChangeover,
    UtilizationBalance,
    batch_bonus_minutes,
    default_soft_constraints,
    estimate_setup,
)
from tests.engines.factories import NOW, make_machine, make_operation, make_order, make_snapshot, make_tooling


def ctx_for(order, snapshot=None, config=None, **kw) -> ConstraintContext:
    return ConstraintContext(
        snapshot=snapshot or make_snapshot(orders=[order]),
        at=NOW,
        config=config or SchedulingConfig(),
        order=order,
        **kw,
    )


def state(**kw) -> MachineState:
    return MachineState(machine_id="CNC-01", next_free=NOW, **kw)


class TestEstimateSetup:
    def test_same_family_uses_family_factor(self) -> None:
        cfg = SchedulingConfig(setup=SetupRules(same_family_setup_factor=0.2))
        est = estimate_setup(
            make_operation(setup_minutes=50, setup_family="F"),
            make_order(),
            make_machine(),
            state(current_setup_family="F"),
            cfg,
        )
        assert est.minutes == 10 and est.basis == "same_family" and est.base_source == "operation"

    def test_same_material_uses_material_factor(self) -> None:
        est = estimate_setup(
            make_operation(setup_minutes=40, material_id="AL"),
            make_order(),
            make_machine(),
            state(current_material_id="AL"),
            SchedulingConfig(),
        )
        assert est.minutes == 20 and est.basis == "same_material"

    def test_changeover_and_unknown(self) -> None:
        cfg = SchedulingConfig()
        est = estimate_setup(
            make_operation(setup_minutes=40, material_id="AL"),
            make_order(),
            make_machine(),
            state(current_material_id="TI"),
            cfg,
        )
        assert est.minutes == 40 and est.basis == "changeover"
        est = estimate_setup(
            make_operation(setup_minutes=40, material_id="AL"), make_order(), make_machine(), state(), cfg
        )
        assert est.basis == "unknown" and est.minutes == 40

    def test_fallback_base_from_order_then_config(self) -> None:
        cfg = SchedulingConfig(setup=SetupRules(default_setup_minutes=25))
        est = estimate_setup(
            make_operation(setup_minutes=None),
            make_order(estimated_setup_minutes=15),
            make_machine(),
            None,
            cfg,
        )
        assert est.minutes == 15 and est.base_source == "order"
        est = estimate_setup(make_operation(setup_minutes=None), make_order(), make_machine(), None, cfg)
        assert est.minutes == 25 and est.base_source == "default"

    def test_erp_machine_state_used_without_scheduler_state(self) -> None:
        machine = make_machine(current_setup_family="F")
        est = estimate_setup(
            make_operation(setup_minutes=60, setup_family="F"),
            make_order(),
            machine,
            None,
            SchedulingConfig(),
        )
        assert est.minutes == 0 and est.basis == "same_family"

    def test_tooling_setup_added_when_not_mounted(self) -> None:
        order = make_order()
        snap = make_snapshot(
            orders=[order],
            tooling=[make_tooling("T-01", setup_minutes=12), make_tooling("T-02", setup_minutes=7)],
        )
        ctx = ctx_for(order, snap)
        op = make_operation(setup_minutes=30, tooling_ids={"T-01", "T-02"})
        est = estimate_setup(op, order, make_machine(), state(mounted_tooling={"T-01"}), ctx.config, ctx)
        assert est.tooling_minutes == 7 and est.minutes == 37
        assert "tooling setup" in est.reason


class TestPreferredMachine:
    def test_costs(self) -> None:
        cfg = SchedulingConfig(
            machine_preference=MachinePreferenceRules(non_preferred_machine_cost_minutes=45)
        )
        ctx = ctx_for(make_order(), config=cfg)
        op = make_operation(machine_id="CNC-01")
        assert (
            PreferredMachine().penalty(op, make_machine("CNC-01"), ctx) is None
        )  # preferred cost 0 -> nothing
        p = PreferredMachine().penalty(op, make_machine("CNC-02"), ctx)
        assert p is not None and p.cost == 45 and p.details["preferred_machine_id"] == "CNC-01"
        assert PreferredMachine().penalty(make_operation(), make_machine("CNC-02"), ctx) is None

    def test_preferred_cost_when_configured(self) -> None:
        cfg = SchedulingConfig(machine_preference=MachinePreferenceRules(preferred_machine_cost=3))
        p = PreferredMachine().penalty(
            make_operation(machine_id="CNC-01"), make_machine("CNC-01"), ctx_for(make_order(), config=cfg)
        )
        assert p is not None and p.cost == 3


class TestSetupChangeover:
    def test_penalty_scales_with_cost_per_minute(self) -> None:
        cfg = SchedulingConfig(setup=SetupRules(setup_penalty_cost_per_minute=2.0))
        ctx = ctx_for(make_order(), config=cfg, machine_state=state(current_material_id="TI"))
        p = SetupChangeover().penalty(make_operation(setup_minutes=30, material_id="AL"), make_machine(), ctx)
        assert p is not None and p.cost == 60 and p.details["basis"] == "changeover"
        assert "30 min setup" in p.message

    def test_no_penalty_when_no_setup_needed(self) -> None:
        cfg = SchedulingConfig(setup=SetupRules(same_family_setup_factor=0.0))
        ctx = ctx_for(make_order(), config=cfg, machine_state=state(current_setup_family="F"))
        assert (
            SetupChangeover().penalty(make_operation(setup_minutes=30, setup_family="F"), make_machine(), ctx)
            is None
        )


class TestUtilizationBalance:
    def test_above_average_costs(self) -> None:
        cfg = SchedulingConfig(
            machine_preference=MachinePreferenceRules(utilization_balance_cost_per_pct=0.5)
        )
        ctx = ctx_for(
            make_order(),
            config=cfg,
            machine_state=state(scheduled_minutes=1500),
            group_average_load_minutes=1000,
        )
        p = UtilizationBalance().penalty(make_operation(), make_machine(), ctx)
        assert p is not None and p.cost == 25 and p.details["pct_above"] == 50

    def test_below_average_or_missing_data(self) -> None:
        ctx = ctx_for(
            make_order(), machine_state=state(scheduled_minutes=500), group_average_load_minutes=1000
        )
        assert UtilizationBalance().penalty(make_operation(), make_machine(), ctx) is None
        assert UtilizationBalance().penalty(make_operation(), make_machine(), ctx_for(make_order())) is None
        ctx0 = ctx_for(make_order(), machine_state=state(scheduled_minutes=5), group_average_load_minutes=0)
        assert UtilizationBalance().penalty(make_operation(), make_machine(), ctx0) is None


class TestEnergyCost:
    def test_rate_times_run_hours(self) -> None:
        cfg = SchedulingConfig(
            machine_preference=MachinePreferenceRules(energy_cost_per_hour={"CNC-01": 12.0})
        )
        op = make_operation(quantity=10, cycle_minutes_per_unit=6)  # 60 min run
        p = EnergyCost().penalty(op, make_machine("CNC-01"), ctx_for(make_order(), config=cfg))
        assert p is not None and p.cost == 12.0
        assert EnergyCost().penalty(op, make_machine("CNC-02"), ctx_for(make_order(), config=cfg)) is None
        assert (
            EnergyCost().penalty(
                make_operation(cycle_minutes_per_unit=None),
                make_machine("CNC-01"),
                ctx_for(make_order(), config=cfg),
            )
            is None
        )


class TestSequenceAndBatch:
    def test_customer_sequence_bonus(self) -> None:
        c = CustomerSequencePreference(bonus_minutes=5)
        order = make_order(customer_id="C7")
        ctx = ctx_for(order, machine_state=state(last_customer_id="C7"))
        p = c.penalty(make_operation(), make_machine(), ctx)
        assert p is not None and p.cost == -5
        assert (
            c.penalty(
                make_operation(), make_machine(), ctx_for(order, machine_state=state(last_customer_id="C8"))
            )
            is None
        )
        assert c.penalty(make_operation(), make_machine(), ctx_for(order)) is None
        assert CustomerSequencePreference(0).penalty(make_operation(), make_machine(), ctx) is None

    def test_batch_preference_counts_matching_dimensions(self) -> None:
        cfg = SchedulingConfig(
            batching=BatchingRules(dimensions=["material", "part_family", "tool", "customer"])
        )
        order = make_order(part_family="BRACKET", tooling_requirement={"T-01"})
        ctx = ctx_for(
            order,
            config=cfg,
            machine_state=state(
                current_material_id="AL", last_part_family="BRACKET", mounted_tooling={"T-01", "T-09"}
            ),
        )
        p = BatchPreference(bonus_minutes_per_dimension=4).penalty(
            make_operation(material_id="AL"), make_machine(), ctx
        )
        assert (
            p is not None
            and p.cost == -12
            and p.details["matched_dimensions"] == ["material", "part_family", "tool"]
        )

    def test_batch_preference_disabled_or_no_match(self) -> None:
        cfg = SchedulingConfig(batching=BatchingRules(enabled=False))
        ctx = ctx_for(make_order(part_family="X"), config=cfg, machine_state=state(last_part_family="X"))
        assert BatchPreference(4).penalty(make_operation(), make_machine(), ctx) is None
        ctx = ctx_for(
            make_order(part_family="X"), machine_state=state(last_part_family="Y", current_material_id="AL")
        )
        assert BatchPreference(4).penalty(make_operation(material_id="TI"), make_machine(), ctx) is None


def test_default_soft_constraints_and_bonus_derivation() -> None:
    cfg = SchedulingConfig(
        setup=SetupRules(
            default_setup_minutes=40, same_material_setup_factor=0.25, setup_penalty_cost_per_minute=2
        )
    )
    assert batch_bonus_minutes(cfg) == 20
    keys = [c.key for c in default_soft_constraints(cfg)]
    assert keys == [
        "preferred_machine",
        "setup_changeover",
        "utilization_balance",
        "energy_cost",
        "customer_sequence",
        "batch_preference",
    ]
