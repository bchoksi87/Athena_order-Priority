"""Setup computation: factors, defaults, tooling, in-progress operations."""

from __future__ import annotations

from app.domain.config import SchedulingConfig
from app.domain.enums import OperationStatus
from app.engines.constraints.base import MachineState
from app.engines.scheduling.setup import IN_PROGRESS_REASON, compute_setup, compute_setup_minutes
from tests.engines.factories import NOW, make_machine, make_operation, make_order, make_snapshot, make_tooling


def _state(**overrides: object) -> MachineState:
    fields: dict[str, object] = {"machine_id": "CNC-01", "next_free": NOW}
    fields.update(overrides)
    return MachineState(**fields)  # type: ignore[arg-type]


class TestSetupFactors:
    def test_same_family_uses_family_factor(self, scheduling_config: SchedulingConfig) -> None:
        op = make_operation(setup_minutes=40.0, setup_family="F1", material_id="AL")
        minutes, reason = compute_setup_minutes(
            op, make_machine(), _state(current_setup_family="F1", current_material_id="ST"), scheduling_config
        )
        assert minutes == 40.0 * scheduling_config.setup.same_family_setup_factor == 0.0
        assert "same setup family 'F1'" in reason and "[40 min x 0]" in reason

    def test_same_material_uses_material_factor(self, scheduling_config: SchedulingConfig) -> None:
        op = make_operation(setup_minutes=40.0, setup_family="F1", material_id="AL")
        minutes, reason = compute_setup_minutes(
            op, make_machine(), _state(current_setup_family="F2", current_material_id="AL"), scheduling_config
        )
        assert minutes == 40.0 * scheduling_config.setup.same_material_setup_factor == 20.0
        assert "same material 'AL'" in reason

    def test_changeover_is_full_setup(self, scheduling_config: SchedulingConfig) -> None:
        op = make_operation(setup_minutes=40.0, setup_family="F1", material_id="AL")
        minutes, reason = compute_setup_minutes(
            op, make_machine(), _state(current_setup_family="F2", current_material_id="ST"), scheduling_config
        )
        assert minutes == 40.0
        assert "changeover" in reason

    def test_material_falls_back_to_order_required_material(
        self, scheduling_config: SchedulingConfig
    ) -> None:
        order = make_order("O1", required_material_id="AL")
        op = make_operation(setup_minutes=40.0, material_id=None)
        minutes, _ = compute_setup_minutes(
            op, make_machine(), _state(current_material_id="AL"), scheduling_config, order=order
        )
        assert minutes == 20.0


class TestSetupDefaults:
    def test_missing_setup_uses_config_default_and_says_so(self) -> None:
        config = SchedulingConfig()
        config.setup.default_setup_minutes = 45.0
        op = make_operation(setup_minutes=None)
        minutes, reason = compute_setup_minutes(op, make_machine(), _state(), config)
        assert minutes == 45.0
        assert "default 45 min used" in reason and "no ERP setup time" in reason

    def test_order_level_estimate_before_default(self, scheduling_config: SchedulingConfig) -> None:
        order = make_order("O1", estimated_setup_minutes=12.0)
        op = make_operation(setup_minutes=None)
        minutes, reason = compute_setup_minutes(op, make_machine(), _state(), scheduling_config, order=order)
        assert minutes == 12.0 and "order-level" in reason

    def test_unknown_machine_state_assumes_full_setup(self, scheduling_config: SchedulingConfig) -> None:
        op = make_operation(setup_minutes=30.0, setup_family="F1")
        minutes, reason = compute_setup_minutes(op, make_machine(), _state(), scheduling_config)
        assert minutes == 30.0 and "unknown" in reason


class TestTooling:
    def test_unmounted_tooling_adds_its_setup(self, scheduling_config: SchedulingConfig) -> None:
        tool = make_tooling("T-01", setup_minutes=15.0)
        op = make_operation(setup_minutes=30.0, tooling_ids={"T-01"})
        snap = make_snapshot(
            orders=[make_order("O1")], operations=[op], machines=[make_machine()], tooling=[tool]
        )
        est = compute_setup(op, snap.machines["CNC-01"], _state(), scheduling_config, snapshot=snap)
        assert est.minutes == 45.0 and est.tooling_minutes == 15.0
        assert "tooling setup" in est.reason

    def test_mounted_tooling_costs_nothing(self, scheduling_config: SchedulingConfig) -> None:
        tool = make_tooling("T-01", setup_minutes=15.0)
        op = make_operation(setup_minutes=30.0, tooling_ids={"T-01"})
        snap = make_snapshot(
            orders=[make_order("O1")], operations=[op], machines=[make_machine()], tooling=[tool]
        )
        est = compute_setup(
            op, snap.machines["CNC-01"], _state(mounted_tooling={"T-01"}), scheduling_config, snapshot=snap
        )
        assert est.minutes == 30.0 and est.tooling_minutes == 0.0

    def test_without_snapshot_tooling_is_ignored(self, scheduling_config: SchedulingConfig) -> None:
        op = make_operation(setup_minutes=30.0, tooling_ids={"T-01"})
        minutes, _ = compute_setup_minutes(op, make_machine(), _state(), scheduling_config)
        assert minutes == 30.0


class TestInProgress:
    def test_in_progress_on_same_machine_needs_no_setup(self, scheduling_config: SchedulingConfig) -> None:
        op = make_operation(
            setup_minutes=30.0, operation_status=OperationStatus.IN_PROGRESS, machine_id="CNC-01"
        )
        minutes, reason = compute_setup_minutes(op, make_machine("CNC-01"), _state(), scheduling_config)
        assert minutes == 0.0 and reason == IN_PROGRESS_REASON

    def test_in_progress_elsewhere_still_needs_setup(self, scheduling_config: SchedulingConfig) -> None:
        op = make_operation(
            setup_minutes=30.0, operation_status=OperationStatus.IN_PROGRESS, machine_id="CNC-02"
        )
        minutes, _ = compute_setup_minutes(op, make_machine("CNC-01"), _state(), scheduling_config)
        assert minutes == 30.0

    def test_negative_setup_is_clamped(self, scheduling_config: SchedulingConfig) -> None:
        op = make_operation(setup_minutes=-5.0)
        minutes, _ = compute_setup_minutes(op, make_machine(), _state(), scheduling_config)
        assert minutes == 0.0
