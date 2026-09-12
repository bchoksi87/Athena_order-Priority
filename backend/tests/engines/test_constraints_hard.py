"""Each hard constraint in isolation."""

from __future__ import annotations

import pytest

from app.domain.config import SchedulingConfig
from app.domain.enums import LockType, MachineStatus, OperationStatus, OverrideType, ProcessType
from app.engines.constraints.base import ConstraintContext
from app.engines.constraints.hard import (
    ExplicitEligibility,
    LockedMachineAssignment,
    MachineGroup,
    MachineOperable,
    MaterialCompatibility,
    PartSize,
    ProcessCapability,
    QuantityRestriction,
    ToolingCompatibility,
    default_hard_constraints,
    part_dimensions,
    pinned_machine_for_order,
)
from tests.engines.factories import (
    NOW,
    at,
    make_lock,
    make_machine,
    make_material,
    make_operation,
    make_order,
    make_override,
    make_snapshot,
    make_tooling,
    window,
)


def ctx_for(order, snapshot=None, **kw) -> ConstraintContext:
    snap = snapshot or make_snapshot(orders=[order])
    return ConstraintContext(snapshot=snap, at=NOW, config=SchedulingConfig(), order=order, **kw)


class TestProcessCapability:
    def test_native_and_compatible_process(self) -> None:
        c = ProcessCapability()
        op = make_operation(operation_type=ProcessType.CNC_MACHINING)
        ctx = ctx_for(make_order())
        assert c.check(op, make_machine(), ctx) is None
        printer = make_machine(
            "P1", ProcessType.ADDITIVE_3D_PRINTING, "AM", compatible_processes={ProcessType.CNC_MACHINING}
        )
        assert c.check(op, printer, ctx) is None

    def test_unsupported_process(self) -> None:
        v = ProcessCapability().check(
            make_operation(operation_type=ProcessType.HEAT_TREATMENT), make_machine(), ctx_for(make_order())
        )
        assert v is not None and v.constraint_key == "process_capability"
        assert v.details["required"] == "heat_treatment"


class TestExplicitEligibility:
    def test_explicit_list(self) -> None:
        c = ExplicitEligibility()
        op = make_operation(eligible_machine_ids={"CNC-01", "CNC-02"})
        ctx = ctx_for(make_order())
        assert c.check(op, make_machine("CNC-01"), ctx) is None
        v = c.check(op, make_machine("CNC-03"), ctx)
        assert v is not None and v.details["eligible_machine_ids"] == ["CNC-01", "CNC-02"]

    def test_order_required_machine(self) -> None:
        order = make_order(required_machine_id="CNC-02")
        ctx = ctx_for(order)
        assert ExplicitEligibility().check(make_operation(), make_machine("CNC-02"), ctx) is None
        v = ExplicitEligibility().check(make_operation(), make_machine("CNC-01"), ctx)
        assert v is not None and "requires machine CNC-02" in v.message

    def test_preferred_machine_is_not_hard_unless_in_progress(self) -> None:
        ctx = ctx_for(make_order())
        op = make_operation(machine_id="CNC-01")
        assert ExplicitEligibility().check(op, make_machine("CNC-02"), ctx) is None
        running = make_operation(machine_id="CNC-01", operation_status=OperationStatus.IN_PROGRESS)
        assert ExplicitEligibility().check(running, make_machine("CNC-01"), ctx) is None
        v = ExplicitEligibility().check(running, make_machine("CNC-02"), ctx)
        assert v is not None and "in progress" in v.message

    def test_order_missing_from_snapshot_is_tolerated(self) -> None:
        ctx = ConstraintContext(snapshot=make_snapshot(), at=NOW, config=SchedulingConfig())
        assert ExplicitEligibility().check(make_operation(), make_machine(), ctx) is None


class TestMachineGroup:
    def test_op_group_and_order_group(self) -> None:
        c = MachineGroup()
        ctx = ctx_for(make_order(machine_group="CNC-LARGE"))
        assert c.check(make_operation(), make_machine(machine_group="CNC-LARGE"), ctx) is None
        v = c.check(make_operation(), make_machine(machine_group="CNC"), ctx)
        assert v is not None and v.details == {"required_group": "CNC-LARGE", "machine_group": "CNC"}
        # operation group wins over order group
        assert c.check(make_operation(machine_group="CNC"), make_machine(machine_group="CNC"), ctx) is None

    def test_explicit_list_disables_group_check(self) -> None:
        ctx = ctx_for(make_order(machine_group="X"))
        assert (
            MachineGroup().check(make_operation(eligible_machine_ids={"CNC-01"}), make_machine(), ctx) is None
        )

    def test_no_group_anywhere(self) -> None:
        assert MachineGroup().check(make_operation(), make_machine(), ctx_for(make_order())) is None


class TestMaterialCompatibility:
    def test_machine_side(self) -> None:
        c = MaterialCompatibility()
        ctx = ctx_for(make_order())
        machine = make_machine(compatible_materials={"AL-6061"})
        assert c.check(make_operation(material_id="AL-6061"), machine, ctx) is None
        v = c.check(make_operation(material_id="TI-64"), machine, ctx)
        assert v is not None and v.details["side"] == "machine.compatible_materials"

    def test_material_side(self) -> None:
        material = make_material("TI-64", compatible_machine_ids={"CNC-09"})
        order = make_order(required_material_id="TI-64")
        ctx = ctx_for(order, make_snapshot(orders=[order], materials=[material]))
        v = MaterialCompatibility().check(make_operation(), make_machine("CNC-01"), ctx)
        assert v is not None and v.details["side"] == "material.compatible_machine_ids"
        assert MaterialCompatibility().check(make_operation(), make_machine("CNC-09"), ctx) is None

    def test_unknown_sides_do_not_block(self) -> None:
        ctx = ctx_for(make_order())
        assert (
            MaterialCompatibility().check(make_operation(material_id="X"), make_machine(), ctx) is None
        )  # no machine list
        assert (
            MaterialCompatibility().check(make_operation(), make_machine(compatible_materials={"A"}), ctx)
            is None
        )  # no material


class TestToolingCompatibility:
    def test_incompatible_tool(self) -> None:
        tool = make_tooling("T-01", compatible_machine_ids={"CNC-02"})
        order = make_order()
        ctx = ctx_for(order, make_snapshot(orders=[order], tooling=[tool]))
        v = ToolingCompatibility().check(make_operation(tooling_ids={"T-01"}), make_machine("CNC-01"), ctx)
        assert v is not None and v.details["incompatible_tooling_ids"] == ["T-01"]
        assert (
            ToolingCompatibility().check(make_operation(tooling_ids={"T-01"}), make_machine("CNC-02"), ctx)
            is None
        )

    def test_order_level_tooling_used_when_op_has_none(self) -> None:
        tool = make_tooling("T-01", compatible_machine_ids={"CNC-02"})
        order = make_order(tooling_requirement={"T-01"})
        ctx = ctx_for(order, make_snapshot(orders=[order], tooling=[tool]))
        assert ToolingCompatibility().check(make_operation(), make_machine("CNC-01"), ctx) is not None

    def test_unknown_tool_or_unrestricted_tool_ignored(self) -> None:
        order = make_order()
        ctx = ctx_for(order, make_snapshot(orders=[order], tooling=[make_tooling("T-02")]))
        assert (
            ToolingCompatibility().check(make_operation(tooling_ids={"T-01", "T-02"}), make_machine(), ctx)
            is None
        )


class TestMachineOperable:
    @pytest.mark.parametrize("status", [MachineStatus.AVAILABLE, MachineStatus.RUNNING])
    def test_operable(self, status: MachineStatus) -> None:
        assert (
            MachineOperable().check(make_operation(), make_machine(status=status), ctx_for(make_order()))
            is None
        )

    @pytest.mark.parametrize("status", [MachineStatus.DOWN, MachineStatus.OFFLINE])
    def test_down_or_offline(self, status: MachineStatus) -> None:
        v = MachineOperable().check(make_operation(), make_machine(status=status), ctx_for(make_order()))
        assert v is not None and "no scheduled return" in v.message

    def test_down_with_known_end_still_violates_but_reports_return(self) -> None:
        machine = make_machine(
            status=MachineStatus.DOWN, unplanned_downtime=[window(at(hours=-1), 5, "breakdown")]
        )
        v = MachineOperable().check(make_operation(), machine, ctx_for(make_order()))
        assert v is not None and v.details["resolves_at"] == at(hours=4)

    def test_maintenance_with_end_is_eligible(self) -> None:
        machine = make_machine(
            status=MachineStatus.MAINTENANCE, maintenance_windows=[window(at(hours=-1), 3, "pm")]
        )
        assert MachineOperable().check(make_operation(), machine, ctx_for(make_order())) is None

    def test_maintenance_without_end_violates(self) -> None:
        machine = make_machine(
            status=MachineStatus.MAINTENANCE, maintenance_windows=[window(at(days=-2), 3, "old")]
        )
        v = MachineOperable().check(make_operation(), machine, ctx_for(make_order()))
        assert v is not None and v.details == {"status": "maintenance"}


class TestPartSize:
    def test_fits_with_rotation(self) -> None:
        order = make_order(attributes={"part_size_mm": [500, 100, 100]})
        machine = make_machine(max_part_size_mm=(200.0, 600.0, 150.0))
        assert PartSize().check(make_operation(), machine, ctx_for(order)) is None

    def test_too_large(self) -> None:
        order = make_order(attributes={"part_length_mm": 700, "part_width_mm": 100, "part_height_mm": 100})
        machine = make_machine(max_part_size_mm=(600.0, 600.0, 600.0))
        v = PartSize().check(make_operation(), machine, ctx_for(order))
        assert v is not None and v.details["part_size_mm"] == [700.0, 100.0, 100.0]

    def test_unknown_dimensions_do_not_block(self) -> None:
        machine = make_machine(max_part_size_mm=(10.0, 10.0, 10.0))
        assert PartSize().check(make_operation(), machine, ctx_for(make_order())) is None
        assert (
            PartSize().check(
                make_operation(), machine, ctx_for(make_order(attributes={"part_size_mm": [1, "x", 3]}))
            )
            is None
        )
        assert part_dimensions(make_order(attributes={"part_length_mm": 5})) is None
        assert part_dimensions(make_order(attributes={"part_size_mm": (1, 2, 3)})) == (1.0, 2.0, 3.0)


class TestQuantityRestriction:
    def test_bounds(self) -> None:
        c = QuantityRestriction()
        ctx = ctx_for(make_order())
        machine = make_machine(attributes={"min_batch_qty": 5, "max_batch_qty": 50})
        assert c.check(make_operation(quantity=10), machine, ctx) is None
        assert c.check(make_operation(quantity=2), machine, ctx) is not None
        assert c.check(make_operation(quantity=100), machine, ctx) is not None
        assert (
            c.check(make_operation(quantity=100, completed_quantity=60), machine, ctx) is None
        )  # pending 40

    def test_no_restrictions_or_bad_values(self) -> None:
        assert (
            QuantityRestriction().check(make_operation(quantity=1), make_machine(), ctx_for(make_order()))
            is None
        )
        machine = make_machine(attributes={"min_batch_qty": "many"})
        assert QuantityRestriction().check(make_operation(quantity=1), machine, ctx_for(make_order())) is None


class TestLockedMachineAssignment:
    def test_order_lock_pins_machine(self) -> None:
        order = make_order()
        snap = make_snapshot(orders=[order], locks=[make_lock("O1", "CNC-02")])
        ctx = ctx_for(order, snap)
        assert LockedMachineAssignment().check(make_operation(), make_machine("CNC-02"), ctx) is None
        v = LockedMachineAssignment().check(make_operation(), make_machine("CNC-01"), ctx)
        assert v is not None and v.details == {"pinned_machine_id": "CNC-02", "source": "lock:L1"}

    def test_override_pins_machine_and_lock_wins(self) -> None:
        order = make_order()
        ov = make_override("O1", OverrideType.LOCK_MACHINE_ASSIGNMENT, target_machine_id="CNC-03")
        snap = make_snapshot(orders=[order], overrides=[ov])
        assert pinned_machine_for_order(snap, "O1", NOW) == ("CNC-03", "override:OV1")
        snap2 = make_snapshot(
            orders=[order], overrides=[ov], locks=[make_lock("O1", "CNC-02", created_at=at(days=-3))]
        )
        assert pinned_machine_for_order(snap2, "O1", NOW) == ("CNC-02", "lock:L1")

    def test_inactive_expired_or_other_locks_ignored(self) -> None:
        order = make_order()
        locks = [
            make_lock("O1", "CNC-02", active=False, lock_id="a"),
            make_lock("O1", "CNC-02", lock_id="b", window=window(at(days=-2), 1)),
            make_lock("O1", "CNC-02", lock_id="c", lock_type=LockType.TIME_SLOT),
            make_lock("O9", "CNC-02", lock_id="d"),
        ]
        ov = make_override(
            "O1", OverrideType.LOCK_MACHINE_ASSIGNMENT, target_machine_id="CNC-03", expires_at=at(hours=-0.5)
        )
        snap = make_snapshot(orders=[order], locks=locks, overrides=[ov])
        assert pinned_machine_for_order(snap, "O1", NOW) is None

    def test_latest_lock_wins(self) -> None:
        order = make_order()
        locks = [
            make_lock("O1", "CNC-02", lock_id="old", created_at=at(days=-1)),
            make_lock("O1", "CNC-05", lock_id="new"),
        ]
        assert pinned_machine_for_order(make_snapshot(orders=[order], locks=locks), "O1", NOW) == (
            "CNC-05",
            "lock:new",
        )


def test_default_hard_constraints_keys_unique() -> None:
    keys = [c.key for c in default_hard_constraints()]
    assert len(keys) == 9 and len(set(keys)) == 9
