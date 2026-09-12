"""Readiness: every state, precedence, resolves_at, notes for unknown data."""

from __future__ import annotations

from app.domain.config import SchedulingConfig
from app.domain.enums import (
    MachineStatus,
    MaterialStatus,
    OperationStatus,
    OrderStatus,
    OverrideType,
    ProcessType,
    QualityStatus,
    ReadinessState,
)
from app.domain.results import Blocker
from app.engines.constraints.readiness import (
    READINESS_PRECEDENCE,
    assess_order,
    compute_blockers,
    machine_available_at,
    readiness_state,
    synthesize_operation,
)
from tests.engines.factories import (
    NOW,
    at,
    make_machine,
    make_material,
    make_operation,
    make_order,
    make_order_with_ops,
    make_override,
    make_snapshot,
    make_tooling,
    window,
)

CFG = SchedulingConfig()


def states(blockers: list[Blocker]) -> list[ReadinessState]:
    return [b.state for b in blockers]


def snapshot_with(order_kwargs=None, op_kwargs=None, machines=None, **snapshot_kwargs):
    order, ops = make_order_with_ops("O1", op_overrides=[op_kwargs or {}], **(order_kwargs or {}))
    snap = make_snapshot(
        orders=[order],
        operations=ops,
        machines=machines if machines is not None else [make_machine()],
        **snapshot_kwargs,
    )
    return order, snap


class TestReadinessStatePrecedence:
    def test_empty_is_ready(self) -> None:
        assert readiness_state([]) is ReadinessState.READY

    def test_precedence_order(self) -> None:
        order = list(READINESS_PRECEDENCE)
        assert order[0] is ReadinessState.ON_HOLD and order[-1] is ReadinessState.READY
        for i, higher in enumerate(order[:-1]):
            for lower in order[i + 1 : -1]:
                blockers = [Blocker(lower, "low"), Blocker(higher, "high")]
                assert readiness_state(blockers) is higher

    def test_combined_blockers_yield_highest(self) -> None:
        order, snap = snapshot_with(
            order_kwargs={
                "on_hold": True,
                "drawing_approved": False,
                "quality_status": QualityStatus.HOLD,
                "material_status": MaterialStatus.UNAVAILABLE,
            },
        )
        blockers = compute_blockers(order, snap, NOW, CFG)
        assert set(states(blockers)) == {
            ReadinessState.ON_HOLD,
            ReadinessState.QUALITY_HOLD,
            ReadinessState.WAITING_APPROVAL,
            ReadinessState.WAITING_MATERIAL,
        }
        assert readiness_state(blockers) is ReadinessState.ON_HOLD


class TestReady:
    def test_plain_order_is_ready(self) -> None:
        order, snap = snapshot_with()
        a = assess_order(order, snap, NOW, CFG)
        assert a.state is ReadinessState.READY and a.blockers == [] and not a.blocked
        assert a.eligible_machine_ids == ["CNC-01"] and a.next_operation_id == "O1-op1"
        assert a.notes["material_unknown"] is True and a.notes["machines_available_now"] == ["CNC-01"]

    def test_order_without_operations_uses_synthesized_operation(self) -> None:
        order = make_order(
            process_type=ProcessType.ADDITIVE_3D_PRINTING,
            required_material_id="PA12",
            tooling_requirement={"T-01"},
        )
        printer = make_machine("P1", ProcessType.ADDITIVE_3D_PRINTING, "AM")
        snap = make_snapshot(orders=[order], machines=[printer, make_machine()])
        a = assess_order(order, snap, NOW, CFG)
        assert a.state is ReadinessState.READY and a.eligible_machine_ids == ["P1"]
        assert a.notes["no_operations"] is True and a.next_operation_id is None
        synth = synthesize_operation(order)
        assert synth.operation_type is ProcessType.ADDITIVE_3D_PRINTING and synth.material_id == "PA12"
        assert synth.tooling_ids == {"T-01"} and synth.pending_quantity == 10


class TestClosedAndHold:
    def test_closed_statuses_short_circuit(self) -> None:
        for status in (OrderStatus.COMPLETED, OrderStatus.SHIPPED, OrderStatus.CANCELLED, OrderStatus.PACKED):
            order, snap = snapshot_with(order_kwargs={"order_status": status, "on_hold": True})
            blockers = compute_blockers(order, snap, NOW, CFG)
            assert states(blockers) == [ReadinessState.OTHER_CONSTRAINT]
            assert status.value in blockers[0].message

    def test_nothing_pending(self) -> None:
        order, snap = snapshot_with(order_kwargs={"completed_quantity": 10})
        assert states(compute_blockers(order, snap, NOW, CFG)) == [ReadinessState.OTHER_CONSTRAINT]

    def test_on_hold_flag_status_and_override(self) -> None:
        order, snap = snapshot_with(order_kwargs={"on_hold": True, "hold_reason": "customer request"})
        b = compute_blockers(order, snap, NOW, CFG)
        assert states(b) == [ReadinessState.ON_HOLD] and "customer request" in b[0].message
        order, snap = snapshot_with(order_kwargs={"order_status": OrderStatus.ON_HOLD})
        assert readiness_state(compute_blockers(order, snap, NOW, CFG)) is ReadinessState.ON_HOLD
        hold = make_override("O1", OverrideType.HOLD_ORDER, expires_at=at(hours=6))
        order, snap = snapshot_with(overrides=[hold])
        b = compute_blockers(order, snap, NOW, CFG)
        assert states(b) == [ReadinessState.ON_HOLD] and b[0].resolves_at == at(hours=6)

    def test_release_hold_override_supersedes_hold(self) -> None:
        hold = make_override("O1", OverrideType.HOLD_ORDER, override_id="h", created_at=at(hours=-2))
        release = make_override("O1", OverrideType.RELEASE_HOLD, override_id="r", created_at=at(hours=-1))
        order, snap = snapshot_with(overrides=[hold, release])
        assert compute_blockers(order, snap, NOW, CFG) == []
        expired_hold = make_override("O1", OverrideType.HOLD_ORDER, expires_at=at(hours=-0.1))
        order, snap = snapshot_with(overrides=[expired_hold])
        assert compute_blockers(order, snap, NOW, CFG) == []


class TestQualityAndApproval:
    def test_quality_hold_and_failed(self) -> None:
        for status in (QualityStatus.HOLD, QualityStatus.FAILED):
            order, snap = snapshot_with(order_kwargs={"quality_status": status})
            assert states(compute_blockers(order, snap, NOW, CFG)) == [ReadinessState.QUALITY_HOLD]
        order, snap = snapshot_with(order_kwargs={"quality_status": QualityStatus.REWORK})
        assert compute_blockers(order, snap, NOW, CFG) == []

    def test_drawing_not_approved(self) -> None:
        order, snap = snapshot_with(order_kwargs={"drawing_approved": False})
        assert states(compute_blockers(order, snap, NOW, CFG)) == [ReadinessState.WAITING_APPROVAL]


class TestMaterial:
    def test_erp_status_unavailable_with_receipt_date(self) -> None:
        material = make_material("AL", available_quantity=0, expected_receipt_date=at(days=2))
        order, snap = snapshot_with(
            order_kwargs={"material_status": MaterialStatus.ON_ORDER},
            op_kwargs={"material_id": "AL"},
            materials=[material],
        )
        b = compute_blockers(order, snap, NOW, CFG)
        assert states(b) == [ReadinessState.WAITING_MATERIAL] and b[0].resolves_at == at(days=2)
        order, snap = snapshot_with(order_kwargs={"material_status": MaterialStatus.UNAVAILABLE})
        b = compute_blockers(order, snap, NOW, CFG)
        assert b[0].resolves_at is None and "(unspecified)" in b[0].message

    def test_erp_available_trusted_even_if_quantity_low(self) -> None:
        material = make_material("AL", available_quantity=1)
        order, snap = snapshot_with(
            order_kwargs={"material_status": MaterialStatus.AVAILABLE},
            op_kwargs={"material_id": "AL", "material_quantity_per_unit": 2},
            materials=[material],
        )
        assert compute_blockers(order, snap, NOW, CFG) == []

    def test_quantity_check_pending_times_per_unit(self) -> None:
        material = make_material(
            "AL",
            available_quantity=30,
            reserved_quantity=5,
            incoming_quantity=100,
            expected_receipt_date=at(days=3),
        )
        op = {
            "material_id": "AL",
            "material_quantity_per_unit": 2.0,
            "quantity": 20,
            "completed_quantity": 5,
        }  # need 30, free 25
        order, snap = snapshot_with(
            order_kwargs={"material_status": MaterialStatus.PARTIAL}, op_kwargs=op, materials=[material]
        )
        b = compute_blockers(order, snap, NOW, CFG)
        assert states(b) == [ReadinessState.WAITING_MATERIAL]
        assert (
            b[0].resolves_at == at(days=3)
            and b[0].details["required_quantity"] == 30
            and b[0].details["free_quantity"] == 25
        )
        material.available_quantity = 40  # free 35 >= 30
        assert compute_blockers(order, snap, NOW, CFG) == []

    def test_shortfall_not_covered_by_incoming_has_no_resolve_date(self) -> None:
        material = make_material(
            "AL", available_quantity=1, incoming_quantity=1, expected_receipt_date=at(days=3)
        )
        order, snap = snapshot_with(
            op_kwargs={"material_id": "AL", "material_quantity_per_unit": 1.0}, materials=[material]
        )
        b = compute_blockers(order, snap, NOW, CFG)
        assert states(b) == [ReadinessState.WAITING_MATERIAL] and b[0].resolves_at is None

    def test_unknown_material_flagged_not_blocking(self) -> None:
        order, snap = snapshot_with(op_kwargs={"material_id": "GHOST", "material_quantity_per_unit": 1.0})
        a = assess_order(order, snap, NOW, CFG)
        assert a.state is ReadinessState.READY and a.notes["material_not_in_snapshot"] == "GHOST"
        order, snap = snapshot_with(
            op_kwargs={"material_id": "AL"}, materials=[make_material("AL", available_quantity=0)]
        )
        a = assess_order(order, snap, NOW, CFG)
        assert a.state is ReadinessState.READY and a.notes["material_quantity_per_unit_unknown"] == "AL"

    def test_order_level_material_used_when_op_has_none(self) -> None:
        material = make_material("TI", available_quantity=0)
        order, snap = snapshot_with(
            order_kwargs={"required_material_id": "TI"},
            op_kwargs={"material_quantity_per_unit": 1.0},
            materials=[material],
        )
        assert states(compute_blockers(order, snap, NOW, CFG)) == [ReadinessState.WAITING_MATERIAL]


class TestTooling:
    def test_unavailable_tool_with_return_date(self) -> None:
        tool = make_tooling("T-01", available=False, available_from=at(days=1), maintenance_status="regrind")
        order, snap = snapshot_with(op_kwargs={"tooling_ids": {"T-01"}}, tooling=[tool])
        b = compute_blockers(order, snap, NOW, CFG)
        assert (
            states(b) == [ReadinessState.WAITING_TOOLING]
            and b[0].resolves_at == at(days=1)
            and "regrind" in b[0].message
        )

    def test_life_exhausted(self) -> None:
        tool = make_tooling("T-01", expected_life=100, current_usage=100)
        order, snap = snapshot_with(op_kwargs={"tooling_ids": {"T-01"}}, tooling=[tool])
        b = compute_blockers(order, snap, NOW, CFG)
        assert (
            states(b) == [ReadinessState.WAITING_TOOLING]
            and "life exhausted" in b[0].message
            and b[0].resolves_at is None
        )

    def test_available_tool_with_future_available_from(self) -> None:
        tool = make_tooling("T-01", available_from=at(hours=5))
        order, snap = snapshot_with(order_kwargs={"tooling_requirement": {"T-01"}}, tooling=[tool])
        b = compute_blockers(order, snap, NOW, CFG)
        assert states(b) == [ReadinessState.WAITING_TOOLING] and b[0].resolves_at == at(hours=5)
        assert compute_blockers(order, snap, at(hours=5), CFG) == []

    def test_unknown_tool_is_a_note(self) -> None:
        order, snap = snapshot_with(op_kwargs={"tooling_ids": {"T-99"}})
        a = assess_order(order, snap, NOW, CFG)
        assert a.state is ReadinessState.READY and a.notes["unknown_tooling_ids"] == ["T-99"]

    def test_one_blocker_per_tool(self) -> None:
        tools = [
            make_tooling("T-01", available=False),
            make_tooling("T-02", available=False),
            make_tooling("T-03"),
        ]
        order, snap = snapshot_with(op_kwargs={"tooling_ids": {"T-01", "T-02", "T-03"}}, tooling=tools)
        b = compute_blockers(order, snap, NOW, CFG)
        assert [x.details["tooling_id"] for x in b] == ["T-01", "T-02"]


class TestPreviousOperation:
    def test_dependency_order_open(self) -> None:
        dep, dep_ops = make_order_with_ops(
            "D1",
            op_overrides=[{"operation_status": OperationStatus.IN_PROGRESS, "estimated_end": at(hours=3)}],
        )
        order, ops = make_order_with_ops("O1", depends_on_order_ids={"D1"})
        snap = make_snapshot(orders=[order, dep], operations=[*ops, *dep_ops], machines=[make_machine()])
        b = compute_blockers(order, snap, NOW, CFG)
        assert states(b) == [ReadinessState.WAITING_PREVIOUS_OPERATION]
        assert b[0].resolves_at == at(hours=3) and b[0].details == {"order_id": "D1", "pending_operations": 1}

    def test_dependency_completed_or_unknown(self) -> None:
        dep = make_order("D1", order_status=OrderStatus.COMPLETED, completed_quantity=10)
        order, ops = make_order_with_ops("O1", depends_on_order_ids={"D1", "GHOST"})
        snap = make_snapshot(orders=[order, dep], operations=ops, machines=[make_machine()])
        a = assess_order(order, snap, NOW, CFG)
        assert a.state is ReadinessState.READY and a.notes["unknown_dependency_order_ids"] == ["GHOST"]

    def test_dependency_cancelled_is_other_constraint(self) -> None:
        dep = make_order("D1", order_status=OrderStatus.CANCELLED)
        order, ops = make_order_with_ops("O1", depends_on_order_ids={"D1"})
        snap = make_snapshot(orders=[order, dep], operations=ops, machines=[make_machine()])
        assert states(compute_blockers(order, snap, NOW, CFG)) == [ReadinessState.OTHER_CONSTRAINT]

    def test_prerequisite_operation_in_other_order(self) -> None:
        other_op = make_operation(
            "X1", 1, operation_status=OperationStatus.SCHEDULED, estimated_end=at(hours=2)
        )
        order, ops = make_order_with_ops("O1", op_overrides=[{"prerequisite_operation_id": "X1-op1"}])
        snap = make_snapshot(
            orders=[order, make_order("X1")], operations=[*ops, other_op], machines=[make_machine()]
        )
        b = compute_blockers(order, snap, NOW, CFG)
        assert states(b) == [ReadinessState.WAITING_PREVIOUS_OPERATION] and b[0].resolves_at == at(hours=2)
        other_op.operation_status = OperationStatus.COMPLETED
        assert compute_blockers(order, snap, NOW, CFG) == []

    def test_unknown_prerequisite_is_a_note(self) -> None:
        order, ops = make_order_with_ops("O1", op_overrides=[{"prerequisite_operation_id": "nope"}])
        snap = make_snapshot(orders=[order], operations=ops, machines=[make_machine()])
        a = assess_order(order, snap, NOW, CFG)
        assert a.state is ReadinessState.READY and a.notes["unknown_prerequisite_operation_id"] == "nope"

    def test_next_pending_operation_is_used(self) -> None:
        order, ops = make_order_with_ops(
            "O1",
            [ProcessType.CNC_MACHINING, ProcessType.DEBURRING],
            op_overrides=[{"operation_status": OperationStatus.COMPLETED}, {"tooling_ids": {"T-01"}}],
        )
        snap = make_snapshot(
            orders=[order],
            operations=ops,
            machines=[make_machine(), make_machine("DB-1", ProcessType.DEBURRING, "FIN")],
            tooling=[make_tooling("T-01", available=False)],
        )
        a = assess_order(order, snap, NOW, CFG)
        assert a.next_operation_id == "O1-op2" and a.state is ReadinessState.WAITING_TOOLING
        assert a.eligible_machine_ids == ["DB-1"]


class TestMachineUnavailable:
    def test_no_capable_machine(self) -> None:
        order, snap = snapshot_with(machines=[make_machine("P1", ProcessType.ADDITIVE_3D_PRINTING, "AM")])
        b = compute_blockers(order, snap, NOW, CFG)
        assert states(b) == [ReadinessState.MACHINE_UNAVAILABLE] and "(none)" in b[0].message
        assert b[0].details["candidates"] == 0 and b[0].resolves_at is None

    def test_down_machines_with_known_return_wait_for_the_earliest(self) -> None:
        # DOWN with a known return stays eligible (the calendar removes the outage); the order
        # waits for the earliest return. OFFLINE with no return is rejected outright.
        machines = [
            make_machine(
                "CNC-01", status=MachineStatus.DOWN, unplanned_downtime=[window(at(hours=-1), 9, "crash")]
            ),
            make_machine("CNC-02", status=MachineStatus.OFFLINE),
            make_machine(
                "CNC-03", status=MachineStatus.DOWN, unplanned_downtime=[window(at(hours=-1), 4, "crash")]
            ),
        ]
        order, snap = snapshot_with(machines=machines)
        a = assess_order(order, snap, NOW, CFG)
        assert a.state is ReadinessState.MACHINE_UNAVAILABLE and a.eligible_machine_ids == [
            "CNC-01",
            "CNC-03",
        ]
        b = a.blockers
        assert b[0].resolves_at == at(hours=3) and b[0].details["earliest_machine_id"] == "CNC-03"
        assert "2 eligible machine(s) down/unavailable" in b[0].message
        later = assess_order(order, snap, at(hours=2), CFG)
        assert later.state is ReadinessState.MACHINE_UNAVAILABLE and later.blockers[0].resolves_at == at(
            hours=3
        )

    def test_all_candidates_rejected_without_return(self) -> None:
        machines = [
            make_machine("CNC-01", status=MachineStatus.DOWN),
            make_machine("CNC-02", status=MachineStatus.OFFLINE),
        ]
        order, snap = snapshot_with(machines=machines)
        b = compute_blockers(order, snap, NOW, CFG)
        assert states(b) == [ReadinessState.MACHINE_UNAVAILABLE]
        assert b[0].resolves_at is None and b[0].details["rejections"] == {"machine_operable": 2}
        assert "2 candidate(s)" in b[0].message

    def test_eligible_but_in_maintenance_now(self) -> None:
        machines = [
            make_machine(
                "CNC-01",
                status=MachineStatus.MAINTENANCE,
                maintenance_windows=[window(at(hours=-2), 8, "pm")],
            ),
            make_machine("CNC-02", available_from=at(hours=2)),
        ]
        order, snap = snapshot_with(machines=machines)
        a = assess_order(order, snap, NOW, CFG)
        assert a.state is ReadinessState.MACHINE_UNAVAILABLE and a.eligible_machine_ids == [
            "CNC-01",
            "CNC-02",
        ]
        assert (
            a.blockers[0].resolves_at == at(hours=2)
            and a.blockers[0].details["earliest_machine_id"] == "CNC-02"
        )
        assert assess_order(order, snap, at(hours=2), CFG).state is ReadinessState.READY

    def test_group_with_no_machines(self) -> None:
        order, snap = snapshot_with(order_kwargs={"machine_group": "LASER"})
        b = compute_blockers(order, snap, NOW, CFG)
        assert states(b) == [ReadinessState.MACHINE_UNAVAILABLE] and "no machines in group" in b[0].message

    def test_machine_available_at_chains_windows(self) -> None:
        m = make_machine(
            available_from=at(hours=1),
            maintenance_windows=[window(at(hours=1), 2, "a"), window(at(hours=3), 1, "b")],
        )
        assert machine_available_at(m, NOW) == at(hours=4)
        assert machine_available_at(make_machine(), NOW) == NOW
