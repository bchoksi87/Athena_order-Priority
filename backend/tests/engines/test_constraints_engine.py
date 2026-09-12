"""ConstraintEngine façade: eligibility ordering, penalties, bulk readiness map."""

from __future__ import annotations

import time as _time

from app.domain.config import SchedulingConfig
from app.domain.enums import (
    MachineStatus,
    MaterialStatus,
    OrderStatus,
    ProcessType,
    QualityStatus,
    ReadinessState,
)
from app.engines.constraints import ConstraintEngine, MachineState, default_constraint_engine
from app.engines.constraints.base import ConstraintContext
from app.engines.constraints.eligibility import CandidateIndex, candidate_machines
from tests.engines.factories import (
    NOW,
    at,
    make_lock,
    make_machine,
    make_material,
    make_operation,
    make_order,
    make_order_with_ops,
    make_snapshot,
    make_tooling,
    window,
)


class TestEligibleMachines:
    def test_ordering_and_rejections(self, constraint_engine: ConstraintEngine) -> None:
        machines = [
            make_machine("CNC-03", preferred_rank=0),
            make_machine("CNC-01", preferred_rank=1),
            make_machine("CNC-02", preferred_rank=0, status=MachineStatus.DOWN, compatible_materials={"TI"}),
            make_machine("P1", ProcessType.ADDITIVE_3D_PRINTING, "AM"),
        ]
        order, ops = make_order_with_ops("O1", op_overrides=[{"material_id": "AL"}])
        snap = make_snapshot(orders=[order], operations=ops, machines=machines)
        result = constraint_engine.eligible_machines(ops[0], snap, NOW)
        assert result.operation_id == "O1-op1"
        assert result.eligible_machine_ids == ["CNC-03", "CNC-01"]
        assert list(result.rejected) == ["CNC-02"]  # P1 is not a candidate for CNC work at all
        assert sorted(v.constraint_key for v in result.rejected["CNC-02"]) == [
            "machine_operable",
            "material_compatibility",
        ]

    def test_explicit_list_evaluates_every_constraint(self, constraint_engine: ConstraintEngine) -> None:
        machines = [make_machine("CNC-01"), make_machine("P1", ProcessType.ADDITIVE_3D_PRINTING, "AM")]
        order, ops = make_order_with_ops(
            "O1", op_overrides=[{"eligible_machine_ids": {"CNC-01", "P1", "GHOST"}}]
        )
        snap = make_snapshot(orders=[order], operations=ops, machines=machines)
        result = constraint_engine.eligible_machines(ops[0], snap, NOW)
        assert result.eligible_machine_ids == ["CNC-01"]
        assert [v.constraint_key for v in result.rejected["P1"]] == ["process_capability"]
        cs = candidate_machines(ops[0], order, snap)
        assert cs.basis == "explicit_list" and "unknown: GHOST" in cs.detail

    def test_candidate_index_matches_direct(self) -> None:
        machines = [make_machine(f"CNC-{i:02d}", preferred_rank=i % 3) for i in range(6)]
        snap = make_snapshot(machines=machines)
        index = CandidateIndex(snap)
        op = make_operation()
        assert (
            candidate_machines(op, None, snap, index).machines == candidate_machines(op, None, snap).machines
        )
        assert [m.machine_id for m in index.for_process(ProcessType.CNC_MACHINING)][:2] == [
            "CNC-00",
            "CNC-03",
        ]

    def test_lock_pins_single_machine(self, constraint_engine: ConstraintEngine) -> None:
        order, ops = make_order_with_ops("O1")
        snap = make_snapshot(
            orders=[order],
            operations=ops,
            machines=[make_machine("CNC-01"), make_machine("CNC-02")],
            locks=[make_lock("O1", "CNC-02")],
        )
        result = constraint_engine.eligible_machines(ops[0], snap, NOW)
        assert result.eligible_machine_ids == ["CNC-02"]
        assert result.rejected["CNC-01"][0].constraint_key == "locked_machine_assignment"

    def test_check_machine_lists_all_violations(self, constraint_engine: ConstraintEngine) -> None:
        order = make_order(required_machine_id="CNC-09", machine_group="G")
        snap = make_snapshot(orders=[order])
        ctx = ConstraintContext(snapshot=snap, at=NOW, config=constraint_engine.config, order=order)
        violations = constraint_engine.check_machine(
            make_operation(operation_type=ProcessType.PACKING),
            make_machine(status=MachineStatus.OFFLINE),
            ctx,
        )
        assert [v.constraint_key for v in violations] == [
            "process_capability",
            "explicit_eligibility",
            "machine_group",
            "machine_operable",
        ]


class TestSoftPenalties:
    def test_penalties_and_total_cost(self, constraint_engine: ConstraintEngine) -> None:
        order = make_order(customer_id="C1")
        snap = make_snapshot(orders=[order])
        state = MachineState(
            "CNC-02", NOW, current_material_id="TI", scheduled_minutes=1200, last_customer_id="C1"
        )
        ctx = ConstraintContext(
            snapshot=snap,
            at=NOW,
            config=constraint_engine.config,
            order=order,
            machine_state=state,
            group_average_load_minutes=1000,
        )
        op = make_operation(machine_id="CNC-01", setup_minutes=30, material_id="AL")
        penalties = constraint_engine.soft_penalties(op, make_machine("CNC-02"), ctx)
        keys = [p.key if hasattr(p, "key") else p.constraint_key for p in penalties]
        assert keys == ["preferred_machine", "setup_changeover", "utilization_balance", "customer_sequence"]
        cfg = constraint_engine.config
        expected = (
            cfg.machine_preference.non_preferred_machine_cost_minutes
            + 30 * cfg.setup.setup_penalty_cost_per_minute
        )
        expected += 20 * cfg.machine_preference.utilization_balance_cost_per_pct
        expected -= (
            cfg.setup.default_setup_minutes
            * cfg.setup.same_material_setup_factor
            * cfg.setup.setup_penalty_cost_per_minute
        )
        assert constraint_engine.soft_cost(op, make_machine("CNC-02"), ctx) == expected
        for p in penalties:
            assert p.message  # every penalty explains itself


class TestReadinessFacade:
    def test_single_order_methods(self, constraint_engine: ConstraintEngine) -> None:
        order, ops = make_order_with_ops("O1", quality_status=QualityStatus.HOLD)
        snap = make_snapshot(orders=[order], operations=ops, machines=[make_machine()])
        assert constraint_engine.readiness(order, snap, NOW) is ReadinessState.QUALITY_HOLD
        blockers = constraint_engine.order_blockers(order, snap, NOW)
        assert [b.state for b in blockers] == [ReadinessState.QUALITY_HOLD]
        assert constraint_engine.assess(order, snap, NOW).blocking_reasons == ["quality status hold"]

    def test_bulk_map_equals_per_order(self, constraint_engine: ConstraintEngine) -> None:
        machines = [
            make_machine("CNC-01"),
            make_machine("CNC-02", status=MachineStatus.DOWN),
            make_machine("P1", ProcessType.ADDITIVE_3D_PRINTING, "AM"),
        ]
        orders, ops = [], []
        specs = [
            {},
            {"on_hold": True},
            {"drawing_approved": False},
            {"material_status": MaterialStatus.UNAVAILABLE},
            {"quality_status": QualityStatus.FAILED},
            {"process_type": ProcessType.HEAT_TREATMENT},
            {"depends_on_order_ids": {"O-0"}},
            {"order_status": OrderStatus.COMPLETED},
        ]
        for i, spec in enumerate(specs):
            proc = spec.pop("process_type", ProcessType.CNC_MACHINING)
            o, o_ops = make_order_with_ops(
                f"O-{i}", [proc], op_overrides=[{"tooling_ids": {"T-01"}} if i == 3 else {}], **spec
            )
            orders.append(o)
            ops.extend(o_ops)
        snap = make_snapshot(
            orders=orders, operations=ops, machines=machines, tooling=[make_tooling("T-01", available=False)]
        )
        bulk = constraint_engine.readiness_map(snap, NOW)
        assert list(bulk) == sorted(o.order_id for o in orders)
        for order in orders:
            state, blockers = bulk[order.order_id]
            assert state is constraint_engine.readiness(order, snap, NOW)
            assert blockers == constraint_engine.order_blockers(order, snap, NOW)
        assert {oid: s.value for oid, (s, _) in bulk.items()} == {
            "O-0": "ready",
            "O-1": "on_hold",
            "O-2": "waiting_approval",
            "O-3": "waiting_material",
            "O-4": "quality_hold",
            "O-5": "machine_unavailable",
            "O-6": "waiting_previous_operation",
            "O-7": "other_constraint",
        }
        assert [b.state for b in bulk["O-3"][1]] == [
            ReadinessState.WAITING_MATERIAL,
            ReadinessState.WAITING_TOOLING,
        ]

    def test_bulk_map_is_fast(self, constraint_engine: ConstraintEngine) -> None:
        machines = [
            make_machine(f"CNC-{i:03d}", machine_group=f"G{i % 10}", compatible_materials={"AL", "TI"})
            for i in range(200)
        ]
        orders, ops = [], []
        for i in range(2000):
            o, o_ops = make_order_with_ops(
                f"O-{i:05d}",
                [ProcessType.CNC_MACHINING, ProcessType.CNC_MACHINING],
                machine_group=f"G{i % 10}",
                op_overrides=[{"material_id": "AL", "material_quantity_per_unit": 0.1}, {}],
            )
            orders.append(o)
            ops.extend(o_ops)
        snap = make_snapshot(
            orders=orders, operations=ops, machines=machines, materials=[make_material("AL", 10_000)]
        )
        start = _time.perf_counter()
        result = constraint_engine.readiness_map(snap, NOW)
        elapsed = _time.perf_counter() - start
        assert len(result) == 2000 and all(s is ReadinessState.READY for s, _ in result.values())
        assert elapsed < 3.0, f"readiness_map over 2000 orders took {elapsed:.2f}s"

    def test_describe_and_registry(self) -> None:
        engine = default_constraint_engine(SchedulingConfig(version=7))
        info = engine.describe()
        assert (
            info["config_version"] == 7
            and len(info["hard_constraints"]) == 9
            and len(info["soft_constraints"]) == 6
        )
        assert default_constraint_engine().config.config_id == "SchedulingConfig-A"

    def test_naive_at_is_accepted(self, constraint_engine: ConstraintEngine) -> None:
        order, ops = make_order_with_ops("O1")
        snap = make_snapshot(
            orders=[order], operations=ops, machines=[make_machine(available_from=at(hours=1))]
        )
        naive = NOW.replace(tzinfo=None)
        assert constraint_engine.readiness(order, snap, naive) is ReadinessState.MACHINE_UNAVAILABLE
        blockers = constraint_engine.order_blockers(order, snap, naive)
        assert blockers[0].resolves_at == at(hours=1)
        _ = window  # keep factory import for symmetry with other modules
