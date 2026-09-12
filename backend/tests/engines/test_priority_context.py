"""PriorityContextBuilder: bulk pre-computation (readiness, projections, percentiles, batching)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.domain.config import PriorityProfile, SchedulingConfig
from app.domain.enums import MachineStatus, MaterialStatus, OperationStatus, ProcessType, ReadinessState
from app.engines.constraints import default_constraint_engine
from app.engines.constraints.eligibility import CandidateIndex
from app.engines.priority.context import (
    PriorityContextBuilder,
    compute_batching,
    compute_downstream,
    compute_machine_next_free,
    estimate_remaining_minutes,
)
from tests.engines.factories import (
    NOW,
    at,
    make_calendar_spec,
    make_customer,
    make_machine,
    make_material,
    make_operation,
    make_order,
    make_order_with_routing,
    make_snapshot,
    window,
)


def _plant(orders, operations, machines=None, **kw):
    return make_snapshot(
        orders=orders,
        operations=operations,
        machines=machines
        or [
            make_machine("M1", calendar_id="CAL1"),
            make_machine("M2", calendar_id="CAL1", preferred_rank=1, efficiency=2.0),
            make_machine("D1", ProcessType.DEBURRING, "DEBURRING", calendar_id="CAL1"),
        ],
        calendars=[make_calendar_spec("CAL1")],
        default_calendar_id="CAL1",
        **kw,
    )


def test_machine_next_free_respects_calendar_downtime_and_status() -> None:
    from app.engines.calendar import build_calendars

    machines = [
        make_machine("FREE", calendar_id="CAL1"),
        make_machine("LATER", calendar_id="CAL1", available_from=at(hours=2)),
        make_machine(
            "MAINT",
            calendar_id="CAL1",
            status=MachineStatus.MAINTENANCE,
            planned_downtime=[window(at(hours=-1), 3)],
        ),
        make_machine("DOWN", calendar_id="CAL1", status=MachineStatus.DOWN),
    ]
    snapshot = _plant([], [], machines=machines)
    nf = compute_machine_next_free(snapshot, build_calendars(snapshot), NOW)
    assert nf["FREE"] == NOW
    assert nf["LATER"] == at(hours=2)
    assert nf["MAINT"] == at(hours=2)
    assert "DOWN" not in nf
    night = compute_machine_next_free(snapshot, build_calendars(snapshot), at(hours=12))
    assert night["FREE"] == at(days=1)  # next shift 08:00 tomorrow


def test_remaining_minutes_uses_fastest_machine_and_state_aware_setup() -> None:
    order, ops = make_order_with_routing("O1", steps=(ProcessType.CNC_MACHINING, ProcessType.DEBURRING))
    snapshot = _plant([order], ops)
    config = SchedulingConfig()
    eligible = [snapshot.machines["M1"], snapshot.machines["M2"]]
    minutes, notes = estimate_remaining_minutes(order, snapshot, eligible, CandidateIndex(snapshot), config)
    # op1 on M2 (efficiency 2): 30 setup + 5*10/2 = 55; op2 on D1: 30 + 50 = 80
    assert minutes == pytest.approx(135.0)
    assert notes["remaining_basis"] == "operations" and notes["pending_operations"] == 2


def test_remaining_minutes_in_progress_skips_setup_and_falls_back_to_order_level() -> None:
    order, ops = make_order_with_routing("O1", op_overrides={"operation_status": OperationStatus.IN_PROGRESS})
    snapshot = _plant([order], ops)
    minutes, _ = estimate_remaining_minutes(
        order, snapshot, [snapshot.machines["M1"]], CandidateIndex(snapshot), SchedulingConfig()
    )
    assert minutes == pytest.approx(50.0)
    no_cycle, ops2 = make_order_with_routing(
        "O2",
        op_overrides={"cycle_minutes_per_unit": None},
        estimated_total_production_minutes=200,
        completed_quantity=5,
    )
    snapshot = _plant([no_cycle], ops2)
    minutes, notes = estimate_remaining_minutes(
        no_cycle, snapshot, [], CandidateIndex(snapshot), SchedulingConfig()
    )
    assert minutes == pytest.approx(100.0) and notes["remaining_basis"] == "order_level"
    unknown = make_order("O3")
    snapshot = _plant([unknown], [])
    assert estimate_remaining_minutes(
        unknown, snapshot, [], CandidateIndex(snapshot), SchedulingConfig()
    ) == (None, {"remaining_basis": "unknown"})
    order_only = make_order("O4", estimated_cycle_minutes_per_unit=2.0, estimated_setup_minutes=10.0)
    assert (
        estimate_remaining_minutes(
            order_only, _plant([order_only], []), [], CandidateIndex(snapshot), SchedulingConfig()
        )[0]
        == 30.0
    )


def test_builder_projects_completion_on_calendar_and_after_blockers() -> None:
    order, ops = make_order_with_routing("O1", due_in_days=1)
    snapshot = _plant([order], ops)
    ctx = PriorityContextBuilder(default_constraint_engine()).build(snapshot, PriorityProfile())
    assert ctx.readiness["O1"] is ReadinessState.READY
    assert [m.machine_id for m in ctx.eligible_machines["O1"]] == ["M1", "M2"]
    assert ctx.remaining_minutes["O1"] == pytest.approx(55.0)
    assert ctx.projected_completion["O1"] == NOW + timedelta(minutes=55)
    assert ctx.notes["O1"]["projection_machine_id"] == "M1"
    assert ctx.weights == PriorityProfile().weight_map()
    # Work that does not fit before the 16:00 shift end spills to the next day.
    long_order, long_ops = make_order_with_routing(
        "O2", op_overrides={"cycle_minutes_per_unit": 60.0}, quantity=20.0
    )
    snapshot = _plant([long_order], long_ops)
    ctx = PriorityContextBuilder(default_constraint_engine()).build(snapshot, PriorityProfile())
    # 30 setup + 60 x 20 / efficiency 2 (M2) = 630 min: 480 today, 150 tomorrow from 08:00.
    assert ctx.projected_completion["O2"] == at(days=1, hours=2, minutes=30)
    # Blocked by material arriving in 2 days: projection starts when the blocker resolves.
    blocked, b_ops = make_order_with_routing("O3", material_status=MaterialStatus.ON_ORDER)
    snapshot = _plant([blocked], b_ops, materials=[make_material("MAT1", expected_receipt_date=at(days=2))])
    ctx = PriorityContextBuilder(default_constraint_engine()).build(snapshot, PriorityProfile())
    assert ctx.readiness["O3"] is ReadinessState.WAITING_MATERIAL
    assert ctx.projected_completion["O3"] == at(days=2, minutes=55)


def test_builder_percentiles_and_population_max() -> None:
    a, a_ops = make_order_with_routing("A", order_value=100.0, estimated_margin=10.0, customer_id="C1")
    b, b_ops = make_order_with_routing("B", order_value=300.0, estimated_margin=None, customer_id="C2")
    c, c_ops = make_order_with_routing("C", order_value=None, actual_margin=50.0, customer_id="C2")
    customers = [
        make_customer("C1", customer_revenue=10.0, customer_profitability=0.1),
        make_customer("C2", customer_revenue=20.0),
    ]
    snapshot = _plant([a, b, c], a_ops + b_ops + c_ops, customers=customers)
    ctx = PriorityContextBuilder(default_constraint_engine()).build(snapshot, PriorityProfile())
    assert ctx.order_value_percentile == {"A": 0.25, "B": 0.75}
    assert ctx.margin_percentile == {"A": 0.25, "C": 0.75}
    assert ctx.penalty_percentile == {"A": 0.25, "B": 0.75}
    assert ctx.customer_revenue_percentile == {"C1": 0.25, "C2": 0.75}
    assert ctx.customer_profitability_percentile == {"C1": 0.5}
    assert ctx.population_max == {"order_value": 300.0, "margin": 50.0, "penalty": 3.0}


def test_downstream_value_and_critical_path() -> None:
    a = make_order("A", order_value=1.0, requested_delivery_date=at(days=10))
    b = make_order("B", order_value=100.0, depends_on_order_ids={"A"}, requested_delivery_date=at(hours=3))
    c = make_order("C", order_value=1000.0, depends_on_order_ids={"B"}, requested_delivery_date=at(days=10))
    closed = make_order("Z", order_value=9999.0, depends_on_order_ids={"A"}, completed_quantity=10.0)
    loop = make_order("L", order_value=5.0, depends_on_order_ids={"L"})
    snapshot = make_snapshot([a, b, c, closed, loop])
    values, critical = compute_downstream(snapshot, snapshot.open_orders(), {"A": 60.0, "B": 240.0}, NOW)
    assert values == {"A": 1100.0, "B": 1000.0, "C": 0.0, "L": 0.0}
    assert set(critical) == {"A"}
    assert critical["A"] == "dependent B due in 3 hours needs 5 hours"


def test_batching_share_requires_shared_dimension_and_machine() -> None:
    m1, m2 = make_machine("M1"), make_machine("M2")
    orders = [
        make_order("O1", part_family="F", requested_delivery_date=at(days=1)),
        make_order("O2", part_family="F", requested_delivery_date=at(days=2)),
        make_order("O3", part_family="F", requested_delivery_date=at(days=3)),  # no shared machine
        make_order("O4", part_family="G", requested_delivery_date=at(days=4)),
        make_order("O5", part_family="F", requested_delivery_date=None),  # outside the window
    ]
    materials = {"O2": "ST", "O4": "TI"}
    ops = [make_operation(o.order_id, 1, material_id=materials.get(o.order_id, "AL")) for o in orders]
    snapshot = make_snapshot(orders, ops, [m1, m2])
    eligible = {"O1": [m1, m2], "O2": [m1], "O3": [m2], "O4": [m1], "O5": [m1]}
    share, detail = compute_batching(
        snapshot, snapshot.open_orders(), eligible, ["material", "part_family"], window=4
    )
    assert share["O1"] == pytest.approx(2 / 3)  # O2 (family, M1) and O3 (material+family, M2)
    assert detail["O1"]["dimensions"] == ["material", "part_family"] and detail["O1"][
        "shared_machine_ids"
    ] == ["M1", "M2"]
    assert share["O2"] == pytest.approx(1 / 3)
    assert share["O4"] == 0.0
    assert share["O5"] == pytest.approx(2 / 4)  # not in the window: denominator is the full window
    assert compute_batching(snapshot, [], eligible, ["material"], 4) == ({}, {})


def test_builder_uses_batching_window_param_and_customer_rules() -> None:
    from tests.engines.factories import make_customer_rule, make_profile

    order, ops = make_order_with_routing("O1")
    snapshot = _plant([order], ops)
    snapshot.customer_rules = {
        "C1": make_customer_rule("C1", sla_hours=24),
        "C2": make_customer_rule("C2", active=False),
    }
    profile = make_profile()
    profile.weights[9].params["window_orders"] = 0
    ctx = PriorityContextBuilder(default_constraint_engine()).build(snapshot, profile)
    assert ctx.batching_share == {}
    assert set(ctx.customer_rules) == {"C1"}
    explicit = PriorityContextBuilder(default_constraint_engine()).build(
        snapshot, profile, {"C9": make_customer_rule("C9")}
    )
    assert set(explicit.customer_rules) == {"C9"}
