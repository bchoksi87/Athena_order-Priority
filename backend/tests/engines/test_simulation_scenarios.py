"""Scenario ``apply``: the original snapshot is untouched, the clone carries the change."""

from __future__ import annotations

from datetime import time, timedelta
from typing import Any

import pytest

from app.core.errors import ValidationError
from app.domain.config import PriorityProfile, SchedulingConfig
from app.domain.enums import CustomerTier, MaterialStatus, OrderStatus, ProcessType
from app.domain.models import TimeWindow
from app.domain.snapshot import PlanningSnapshot
from app.engines.simulation import (
    SCENARIO_KINDS,
    ScenarioBase,
    apply_scenarios,
    parse_scenario,
)
from app.engines.simulation.base import SIMULATION_NOTES_KEY
from tests.engines.factories import NOW, at, make_plant_snapshot

PROFILE = PriorityProfile()
CONFIG = SchedulingConfig()


def _apply(snapshot: PlanningSnapshot, data: dict[str, Any]) -> tuple[PlanningSnapshot, ScenarioBase, Any]:
    scenario = parse_scenario(data)
    clone, profile, config = scenario.apply(snapshot, PROFILE, CONFIG)
    return clone, scenario, (profile, config)


def _fingerprint(snapshot: PlanningSnapshot) -> dict[str, Any]:
    return {
        "orders": {
            o.order_id: (o.due_date, o.on_hold, o.material_status, o.pending_quantity)
            for o in snapshot.orders.values()
        },
        "ops": {op.operation_id: op.quantity for op in snapshot.operations.values()},
        "machines": {m.machine_id: (len(m.all_downtime), m.status) for m in snapshot.machines.values()},
        "calendars": {
            c.calendar_id: (list(c.extra_working_days), len(c.overtime_windows))
            for c in snapshot.calendars.values()
        },
        "materials": {
            m.material_id: (m.available_quantity, m.expected_receipt_date)
            for m in snapshot.materials.values()
        },
        "expedites": len(snapshot.expedites),
        "rules": dict(snapshot.customer_rules),
    }


@pytest.fixture
def snapshot() -> PlanningSnapshot:
    return make_plant_snapshot(4)


# --------------------------------------------------------------- machines


def test_machine_down_adds_downtime_only_to_clone(snapshot: PlanningSnapshot) -> None:
    before = _fingerprint(snapshot)
    clone, scenario, _ = _apply(
        snapshot, {"kind": "machine_down", "machine_id": "CNC-01", "duration_hours": 8}
    )
    assert _fingerprint(snapshot) == before
    windows = clone.machines["CNC-01"].unplanned_downtime
    assert len(windows) == 1 and windows[0].start == NOW and windows[0].end == at(hours=8)
    assert clone.machines["CNC-01"].attributes[SIMULATION_NOTES_KEY]
    assert "CNC-01" in scenario.describe() and "8 h" in scenario.describe()


def test_machine_down_explicit_window_and_validation(snapshot: PlanningSnapshot) -> None:
    clone, _, _ = _apply(
        snapshot, {"kind": "machine_down", "machine_id": "CNC-02", "start": at(hours=1), "end": at(hours=3)}
    )
    assert clone.machines["CNC-02"].unplanned_downtime[0].minutes == 120
    with pytest.raises(ValidationError):
        parse_scenario({"kind": "machine_down", "machine_id": "CNC-02"})  # neither end nor duration
    with pytest.raises(ValidationError):
        parse_scenario(
            {"kind": "machine_down", "machine_id": "CNC-02", "duration_hours": 1, "end": at(hours=2)}
        )
    with pytest.raises(ValidationError):
        _apply(snapshot, {"kind": "machine_down", "machine_id": "NOPE", "duration_hours": 1})


def test_add_machine_clones_capabilities_as_fresh_machine(snapshot: PlanningSnapshot) -> None:
    snapshot.machines["CNC-01"].unplanned_downtime.append(TimeWindow(NOW, at(hours=1)))
    snapshot.machines["CNC-01"].current_setup_family = "F1"
    clone, _, _ = _apply(
        snapshot, {"kind": "add_machine", "clone_of_machine_id": "CNC-01", "new_machine_id": "CNC-03"}
    )
    assert "CNC-03" not in snapshot.machines
    new = clone.machines["CNC-03"]
    assert new.machine_group == "CNC" and new.process_type == ProcessType.CNC_MACHINING
    assert new.unplanned_downtime == [] and new.current_setup_family is None
    assert "CNC-03" in {m.machine_id for m in clone.machines_in_group("CNC")}
    with pytest.raises(ValidationError):
        _apply(snapshot, {"kind": "add_machine", "clone_of_machine_id": "CNC-01", "new_machine_id": "CNC-02"})
    with pytest.raises(ValidationError):
        _apply(
            snapshot,
            {
                "kind": "add_machine",
                "clone_of_machine_id": "CNC-01",
                "new_machine_id": "X",
                "calendar_id": "?",
            },
        )


# -------------------------------------------------------------- calendars


def test_extra_working_day_and_shift_change_specs(snapshot: PlanningSnapshot) -> None:
    saturday = (NOW + timedelta(days=5)).date()
    clone, scenario, _ = _apply(snapshot, {"kind": "extra_working_day", "day": saturday.isoformat()})
    assert snapshot.calendars["CAL"].extra_working_days == []
    assert clone.calendars["CAL"].extra_working_days == [saturday]
    assert "Saturday" in scenario.describe()

    clone2, _, _ = _apply(
        snapshot,
        {
            "kind": "extra_shift",
            "day": NOW.date().isoformat(),
            "start": "16:00",
            "end": "20:00",
            "calendar_id": "CAL",
        },
    )
    assert snapshot.calendars["CAL"].overtime_windows == []
    window = clone2.calendars["CAL"].overtime_windows[0]
    assert window.start == NOW.replace(hour=16) and window.minutes == 240
    with pytest.raises(ValidationError):
        _apply(
            snapshot,
            {"kind": "extra_shift", "day": NOW.date(), "start": time(1), "end": time(2), "calendar_id": "?"},
        )


def test_extra_shift_uses_calendar_timezone_and_midnight_crossing() -> None:
    snap = make_plant_snapshot(1)
    snap.calendars["CAL"].timezone = "Asia/Kolkata"
    clone, _, _ = _apply(snap, {"kind": "extra_shift", "day": NOW.date(), "start": "22:00", "end": "02:00"})
    window = clone.calendars["CAL"].overtime_windows[0]
    assert window.start == NOW.replace(hour=16, minute=30)  # 22:00 IST
    assert window.minutes == 240


# -------------------------------------------------------------- materials


def test_material_delay_shifts_receipt_and_marks_orders_waiting(snapshot: PlanningSnapshot) -> None:
    snapshot.materials["AL"].expected_receipt_date = at(days=1)
    for oid in ("O0", "O1"):
        snapshot.orders[oid].material_status = MaterialStatus.ON_ORDER
    snapshot.orders["O2"].material_status = MaterialStatus.AVAILABLE
    clone, scenario, _ = _apply(snapshot, {"kind": "material_delay", "material_id": "AL", "delay_days": 2})
    assert snapshot.materials["AL"].expected_receipt_date == at(days=1)
    assert clone.materials["AL"].expected_receipt_date == at(days=3)
    assert clone.orders["O0"].material_status == MaterialStatus.ON_ORDER
    assert clone.orders["O2"].material_status == MaterialStatus.AVAILABLE  # allocated stock untouched
    assert clone.orders["O3"].material_status == MaterialStatus.ON_ORDER  # UNKNOWN → waiting
    assert "2 day(s) late" in scenario.describe()

    clone2, _, _ = _apply(
        snapshot,
        {
            "kind": "material_delay",
            "material_id": "AL",
            "new_expected_receipt_date": at(days=5),
            "affects_allocated_stock": True,
        },
    )
    assert clone2.orders["O2"].material_status == MaterialStatus.ON_ORDER
    assert (
        clone2.materials["AL"].available_quantity == 0.0 and clone2.materials["AL"].incoming_quantity == 100.0
    )
    with pytest.raises(ValidationError):
        _apply(snapshot, {"kind": "material_delay", "material_id": "AL"})


def test_material_arrival_now_releases_orders(snapshot: PlanningSnapshot) -> None:
    snapshot.materials["AL"].available_quantity = 0.0
    snapshot.materials["AL"].incoming_quantity = 50.0
    snapshot.orders["O0"].material_status = MaterialStatus.UNAVAILABLE
    clone, _, _ = _apply(snapshot, {"kind": "material_arrival", "material_id": "AL"})
    assert snapshot.orders["O0"].material_status == MaterialStatus.UNAVAILABLE
    assert clone.materials["AL"].available_quantity == 50.0 and clone.materials["AL"].incoming_quantity == 0.0
    assert clone.orders["O0"].material_status == MaterialStatus.AVAILABLE
    later, _, _ = _apply(
        snapshot, {"kind": "material_arrival", "material_id": "AL", "arrives_at": at(days=1), "quantity": 20}
    )
    assert later.materials["AL"].expected_receipt_date == at(days=1)
    assert later.orders["O0"].material_status == MaterialStatus.UNAVAILABLE


# ------------------------------------------------------------------ orders


def test_urgent_orders_inline_and_clone(snapshot: PlanningSnapshot) -> None:
    data = {
        "kind": "urgent_orders",
        "orders": [
            {
                "customer_id": "C0",
                "part_id": "P-X",
                "quantity": 5,
                "due": at(hours=3),
                "route": [{"process": "cnc_machining", "setup_minutes": 10, "cycle_minutes_per_unit": 4}],
                "order_value": 50_000,
            },
            {"clone_of": "O3", "due": at(hours=2), "order_id": "O3-RUSH", "expedite": False},
        ],
    }
    clone, scenario, _ = _apply(snapshot, data)
    assert len(snapshot.orders) == 4 and len(snapshot.expedites) == 0
    assert set(clone.orders) == {"O0", "O1", "O2", "O3", "SIM-URGENT-1", "O3-RUSH"}
    new = clone.orders["SIM-URGENT-1"]
    assert new.order_status == OrderStatus.NEW and new.due_date == at(hours=3) and new.is_open
    ops = clone.operations_for_order("SIM-URGENT-1")
    assert len(ops) == 1 and ops[0].quantity == 5 and ops[0].cycle_minutes_per_unit == 4
    rush = clone.orders["O3-RUSH"]
    assert rush.part_id == snapshot.orders["O3"].part_id and rush.due_date == at(hours=2)
    assert clone.operations_for_order("O3-RUSH")[0].operation_id == "O3-RUSH-1"
    active = clone.active_expedites(NOW)
    assert "SIM-URGENT-1" in active and "O3-RUSH" not in active
    assert active["SIM-URGENT-1"].boost_points == PROFILE.expedite.default_boost_points
    assert scenario.describe() == "2 urgent orders arrive"


def test_urgent_orders_validation() -> None:
    with pytest.raises(ValidationError):
        parse_scenario({"kind": "urgent_orders", "orders": [{"customer_id": "C0"}]})
    with pytest.raises(ValidationError):
        parse_scenario({"kind": "urgent_orders", "orders": []})
    snap = make_plant_snapshot(1)
    with pytest.raises(ValidationError):
        _apply(snap, {"kind": "urgent_orders", "orders": [{"clone_of": "NOPE", "due": at(hours=1)}]})


def test_outsource_orders_and_group_quantity(snapshot: PlanningSnapshot) -> None:
    clone, _, _ = _apply(snapshot, {"kind": "outsource", "order_ids": ["O1"]})
    assert snapshot.orders["O1"].is_open
    assert not clone.orders["O1"].is_open and clone.orders["O1"].attributes["outsourced_quantity"] == 10.0
    assert clone.operations["O1-op1"].pending_quantity == 0

    clone2, scenario, _ = _apply(snapshot, {"kind": "outsource", "machine_group": "CNC", "quantity": 15})
    # least urgent first: O3 (fully), then 5 pieces of O2
    assert not clone2.orders["O3"].is_open
    assert clone2.orders["O2"].pending_quantity == 5.0 and clone2.operations["O2-op1"].quantity == 5.0
    assert clone2.orders["O0"].pending_quantity == 10.0
    assert "15 pieces from CNC" in scenario.describe()
    with pytest.raises(ValidationError):
        parse_scenario({"kind": "outsource", "machine_group": "CNC"})


def test_due_date_hold_and_expedite(snapshot: PlanningSnapshot) -> None:
    clone, _, _ = _apply(snapshot, {"kind": "due_date_change", "order_id": "O0", "new_due": at(hours=1)})
    assert snapshot.orders["O0"].due_date == at(hours=6)
    assert clone.orders["O0"].due_date == at(hours=1) and clone.orders["O0"].revised_delivery_date == at(
        hours=1
    )

    held, _, _ = _apply(snapshot, {"kind": "hold_orders", "order_ids": ["O0", "O1"], "reason": "credit"})
    assert not snapshot.orders["O0"].on_hold
    assert held.orders["O0"].on_hold and held.orders["O1"].hold_reason == "credit"

    exp, _, _ = _apply(
        snapshot, {"kind": "expedite_orders", "order_ids": ["O2"], "boost_points": 45, "hours": 2}
    )
    assert not snapshot.expedites
    e = exp.active_expedites(NOW)["O2"]
    assert e.boost_points == 45 and e.expires_at == at(hours=2)
    assert "O2" not in exp.active_expedites(at(hours=3))


# ------------------------------------------------------------- priorities


def test_prioritize_customer_writes_rule(snapshot: PlanningSnapshot) -> None:
    clone, scenario, _ = _apply(
        snapshot,
        {
            "kind": "prioritize_customer",
            "customer_id": "C1",
            "boost_points": 20,
            "tier_override": "strategic",
        },
    )
    assert "C1" not in snapshot.customer_rules
    rule = clone.customer_rules["C1"]
    assert rule.priority_boost_points == 20 and rule.tier_override == CustomerTier.STRATEGIC and rule.active
    assert "C1" in scenario.describe()
    with pytest.raises(ValidationError):
        parse_scenario({"kind": "prioritize_customer", "customer_id": "C1"})
    with pytest.raises(ValidationError):
        _apply(snapshot, {"kind": "prioritize_customer", "customer_id": "ZZ", "boost_points": 1})


def test_weight_change_returns_new_profile_and_keeps_snapshot(snapshot: PlanningSnapshot) -> None:
    before = _fingerprint(snapshot)
    clone, scenario, (profile, config) = _apply(
        snapshot, {"kind": "weight_change", "weights": {"margin": 60, "due_date_urgency": 5}}
    )
    assert _fingerprint(clone) == before and config is CONFIG
    assert profile is not PROFILE and PROFILE.weight_map()["margin"] == pytest.approx(0.05)
    weights = {w.key: w.weight for w in profile.weights}
    assert weights["margin"] == 60 and weights["due_date_urgency"] == 5
    assert profile.weight_map()["margin"] > profile.weight_map()["due_date_urgency"]
    assert "[what-if]" in profile.name
    with pytest.raises(ValidationError):
        parse_scenario({"kind": "weight_change", "weights": {"nope": 1}})
    with pytest.raises(ValidationError):
        parse_scenario({"kind": "weight_change"})


def test_weight_change_with_full_profile_and_zero_weights() -> None:
    full = PriorityProfile(profile_id="P-B", version=7)
    scenario = parse_scenario(
        {"kind": "weight_change", "profile": full.model_dump(), "weights": {"order_value": 50}}
    )
    profile = scenario.apply_profile(PROFILE)  # type: ignore[attr-defined]
    assert profile.profile_id == "P-B" and {w.key: w.weight for w in profile.weights}["order_value"] == 50
    all_zero = parse_scenario({"kind": "weight_change", "weights": {k: 0 for k in PROFILE.weight_map()}})
    with pytest.raises(ValidationError):
        all_zero.apply_profile(PROFILE)  # type: ignore[attr-defined]


# ------------------------------------------------------------------ misc


def test_every_kind_parses_and_apply_scenarios_applies_in_order(snapshot: PlanningSnapshot) -> None:
    assert len(SCENARIO_KINDS) == 13
    scenarios = [
        parse_scenario({"kind": "machine_down", "machine_id": "CNC-01", "duration_hours": 1}),
        parse_scenario({"kind": "add_machine", "clone_of_machine_id": "CNC-01", "new_machine_id": "CNC-09"}),
        parse_scenario(
            {"kind": "machine_down", "machine_id": "CNC-09", "duration_hours": 2}
        ),  # depends on 2nd
        parse_scenario({"kind": "weight_change", "weights": {"order_value": 40}}),
    ]
    before = _fingerprint(snapshot)
    clone, profile, config, effects = apply_scenarios(snapshot, scenarios, PROFILE, CONFIG)
    assert _fingerprint(snapshot) == before
    assert len(clone.machines["CNC-09"].unplanned_downtime) == 1
    assert [e.kind for e in effects] == ["machine_down", "add_machine", "machine_down", "weight_change"]
    assert {w.key: w.weight for w in profile.weights}["order_value"] == 40
    assert effects[1].affected_machine_ids == ["CNC-09"]
    assert parse_scenario(scenarios[0]) is scenarios[0]
    with pytest.raises(ValidationError):
        parse_scenario({"kind": "teleport"})
    assert scenarios[0].to_dict()["kind"] == "machine_down"
