"""Spec Phase 30 simulation tests on the synthetic "medium" plant (~5,000 orders, 45 machines).

Every scenario goes through :class:`PlanningPipeline` (``run`` for mutated
snapshots, ``simulate`` for what-if scenarios), so the whole engine chain is
exercised: data quality, calendars, constraints, priorities, scheduling,
analytics and — for production delay / rework — the replanning decision.
The timing test prints the per-stage timings of the baseline run.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta

import pytest

from app.core.clock import FrozenClock
from app.domain.config import SystemConfig
from app.domain.enums import (
    MaterialStatus,
    OperationStatus,
    OrderStatus,
    QualityStatus,
    ReadinessState,
    ReplanTriggerType,
)
from app.domain.results import ScheduleResult
from app.domain.snapshot import PlanningSnapshot
from app.engines.pipeline import PipelineResult, PlanningPipeline
from app.engines.replanning import ReplanEvent, ReplanningEngine
from app.engines.simulation import diff_schedules
from synthetic.generator import SyntheticDataGenerator

pytestmark = [pytest.mark.simulation, pytest.mark.slow]

SEED = 42
SCALE = "medium"
CONFIG = SystemConfig()
MAX_PIPELINE_SECONDS = 60.0
MAX_PRIORITY_SECONDS = 5.0
PRIORITY_STAGES = ("priority_context", "priority")


@pytest.fixture(scope="module")
def snapshot() -> PlanningSnapshot:
    return SyntheticDataGenerator(seed=SEED, scale=SCALE).generate().to_snapshot()


@pytest.fixture(scope="module")
def pipeline(snapshot: PlanningSnapshot) -> PlanningPipeline:
    return PlanningPipeline(FrozenClock(snapshot.as_of))


@pytest.fixture(scope="module")
def baseline(pipeline: PlanningPipeline, snapshot: PlanningSnapshot) -> PipelineResult:
    return pipeline.run(snapshot, CONFIG)


# ------------------------------------------------------------------ helpers


def _scheduled_ids(schedule: ScheduleResult) -> set[str]:
    return {e.order_id for e in schedule.entries} - {u.order_id for u in schedule.unscheduled}


def _lateness_hours(schedule: ScheduleResult, snapshot: PlanningSnapshot, order_ids: set[str]) -> float:
    completion = schedule.order_completion()
    total = 0.0
    for order_id in order_ids:
        order, done = snapshot.orders.get(order_id), completion.get(order_id)
        if order is not None and done is not None and order.due_date is not None:
            total += max(0.0, (done - order.due_date).total_seconds() / 3600.0)
    return total


def _first_starts(schedule: ScheduleResult) -> dict[str, datetime]:
    out: dict[str, datetime] = {}
    for e in schedule.entries:
        if e.order_id not in out or e.setup_start < out[e.order_id]:
            out[e.order_id] = e.setup_start
    return out


def _replanning() -> ReplanningEngine:
    return ReplanningEngine(CONFIG.replanning, CONFIG.scheduling.stability)


# ------------------------------------------------------------------- timing


def test_pipeline_timings_on_medium_scale(baseline: PipelineResult, snapshot: PlanningSnapshot) -> None:
    summary = snapshot.summary()
    print(
        f"\nmedium scale: {summary['orders']} orders / {summary['open_orders']} open / "
        f"{summary['operations']} operations / {summary['machines']} machines"
    )
    for stage, seconds in baseline.timings.items():
        print(f"  {stage:<18} {seconds:8.3f} s")
    print(
        f"  scheduled {baseline.schedule.metrics.scheduled_orders}, entries {len(baseline.schedule.entries)}"
    )
    assert summary["orders"] >= 5_000
    priority_seconds = sum(baseline.timings[s] for s in PRIORITY_STAGES)
    assert priority_seconds < MAX_PRIORITY_SECONDS, f"priority evaluation took {priority_seconds:.1f}s"
    assert baseline.timings["total"] < MAX_PIPELINE_SECONDS, f"pipeline took {baseline.timings['total']:.1f}s"


# ---------------------------------------------------------- machine failure


def test_machine_failure_moves_work_off_the_machine(
    pipeline: PlanningPipeline, snapshot: PlanningSnapshot, baseline: PipelineResult
) -> None:
    hours = 48.0
    outage_end = snapshot.as_of + timedelta(hours=hours)
    load = Counter(e.machine_id for e in baseline.schedule.entries if e.setup_start < outage_end)
    machine_id, _ = load.most_common(1)[0]
    group = snapshot.machines[machine_id].machine_group
    group_orders = {
        e.order_id
        for e in baseline.schedule.entries
        if snapshot.machines[e.machine_id].machine_group == group
    }
    affected = [
        e for e in baseline.schedule.entries if e.machine_id == machine_id and e.setup_start < outage_end
    ]
    assert affected

    sim = pipeline.simulate(
        snapshot, [{"kind": "machine_down", "machine_id": machine_id, "duration_hours": hours}], CONFIG
    )
    scenario = sim.scenario
    assert sim.scenarios[0]["affected_machine_ids"] == [machine_id]
    assert not [e for e in scenario.entries if e.machine_id == machine_id and e.setup_start < outage_end]
    by_op = {e.operation_id: e for e in scenario.entries}
    unscheduled = {u.order_id for u in scenario.unscheduled}
    for e in affected:
        moved = by_op.get(e.operation_id)
        assert (
            moved is not None and (moved.machine_id != machine_id or moved.setup_start >= outage_end)
        ) or (e.order_id in unscheduled)
    assert sim.diff.orders_affected >= len({e.order_id for e in affected})
    # losing a machine for two days never helps the group's orders
    assert _lateness_hours(scenario, snapshot, group_orders) >= _lateness_hours(
        baseline.schedule, snapshot, group_orders
    )
    assert scenario.metrics.total_tardiness_hours >= baseline.schedule.metrics.total_tardiness_hours
    assert sim.diff.late_orders_after >= sim.diff.late_orders_before


# --------------------------------------------------------- material shortage


def test_material_shortage_blocks_orders_and_delays_completion(
    pipeline: PlanningPipeline, snapshot: PlanningSnapshot, baseline: PipelineResult
) -> None:
    scheduled = _scheduled_ids(baseline.schedule)
    users = Counter(
        snapshot.orders[oid].required_material_id
        for oid in scheduled
        if snapshot.orders[oid].required_material_id is not None
        and snapshot.orders[oid].material_status in (MaterialStatus.AVAILABLE, MaterialStatus.PARTIAL)
    )
    material_id, _ = users.most_common(1)[0]
    sim = pipeline.simulate(
        snapshot,
        [
            {
                "kind": "material_delay",
                "material_id": material_id,
                "delay_days": 10,
                "affects_allocated_stock": True,
            }
        ],
        CONFIG,
    )
    affected = set(sim.scenarios[0]["affected_order_ids"])
    assert affected and affected & scheduled
    items = {u.order_id: u for u in sim.scenario.unscheduled}
    completion_before = baseline.schedule.order_completion()
    completion_after = sim.scenario.order_completion()
    for order_id in affected & scheduled:
        assert not sim.scenario.entries_for_order(order_id)
        item = items[order_id]
        assert item.reason_code == "blocked" and item.readiness is ReadinessState.WAITING_MATERIAL
        assert material_id in item.reason
        assert order_id in completion_before and order_id not in completion_after
    deltas = {d.order_id: d for d in sim.diff.order_deltas}
    assert all(deltas[oid].scenario_completion is None for oid in affected & scheduled)
    assert sim.diff.orders_affected >= len(affected & scheduled)
    assert sim.scenario.metrics.revenue_at_risk >= baseline.schedule.metrics.revenue_at_risk


# ------------------------------------------------------- urgent / new order


def _clone_source(baseline: PipelineResult) -> str:
    scheduled = _scheduled_ids(baseline.schedule)
    ready = [r for r in baseline.priorities.values() if not r.blocked and r.order_id in scheduled]
    return min(ready, key=lambda r: (abs(r.score - 50.0), r.order_id)).order_id


def test_urgent_order_scores_high_and_is_scheduled_early(
    pipeline: PlanningPipeline, snapshot: PlanningSnapshot, baseline: PipelineResult
) -> None:
    source = _clone_source(baseline)
    due = snapshot.as_of + timedelta(days=1)
    sim = pipeline.simulate(
        snapshot,
        [
            {
                "kind": "urgent_orders",
                "orders": [{"order_id": "URG-1", "clone_of": source, "due": due.isoformat()}],
            }
        ],
        CONFIG,
    )
    assert "URG-1" not in snapshot.orders and "URG-1" not in sim.baseline_priorities
    score = sim.scenario_priorities["URG-1"]
    assert score >= 90.0 and score > sim.scenario_priorities[source] + 30.0
    assert sum(1 for s in sim.scenario_priorities.values() if s > score) < 10  # top ten
    entries = sim.scenario.entries_for_order("URG-1")
    pending = snapshot.pending_operations_for_order(source)
    assert len(entries) == len(pending) and "URG-1" not in {u.order_id for u in sim.scenario.unscheduled}
    starts = _first_starts(sim.scenario)
    assert starts["URG-1"] < snapshot.as_of + timedelta(days=7)
    assert starts["URG-1"] < _first_starts(baseline.schedule)[source]
    ordered = sorted(starts.values())
    assert starts["URG-1"] <= ordered[len(ordered) // 5]  # among the earliest 20 % of order starts
    assert entries[0].priority_score == score
    # nothing less urgent runs ahead of it on its machine: only work already in progress and
    # orders that score at least as high (overdue orders clamped at 100 rank first by due date)
    in_progress = {
        op.order_id
        for op in snapshot.operations.values()
        if op.operation_status is OperationStatus.IN_PROGRESS and op.machine_id is not None and not op.is_done
    }
    ahead = [
        e
        for e in sim.scenario.entries_for_machine(entries[0].machine_id)
        if e.sequence_on_machine < entries[0].sequence_on_machine
    ]
    assert all(e.order_id in in_progress or sim.scenario_priorities[e.order_id] >= score for e in ahead)


def test_new_order_is_scheduled_behind_more_urgent_work(
    pipeline: PlanningPipeline, snapshot: PlanningSnapshot, baseline: PipelineResult
) -> None:
    source = _clone_source(baseline)
    due = snapshot.as_of + timedelta(days=25)
    sim = pipeline.simulate(
        snapshot,
        [
            {
                "kind": "urgent_orders",
                "orders": [
                    {"order_id": "NEW-1", "clone_of": source, "due": due.isoformat(), "expedite": False}
                ],
            }
        ],
        CONFIG,
    )
    score = sim.scenario_priorities["NEW-1"]
    assert score < 70.0
    entries = sim.scenario.entries_for_order("NEW-1")
    assert len(entries) == len(snapshot.pending_operations_for_order(source))
    assert "NEW-1" not in {u.order_id for u in sim.scenario.unscheduled}
    starts = _first_starts(sim.scenario)
    assert starts["NEW-1"] >= min(starts.values())
    # work that clearly outranks the new order is (almost) untouched by its arrival: the only
    # movement comes from percentile-based factors shifting near-tied scores by a few thousandths
    before = _first_starts(baseline.schedule)
    clearly_ahead = [oid for oid, s in sim.baseline_priorities.items() if s >= score + 20.0 and oid in before]
    assert clearly_ahead
    moved = [oid for oid in clearly_ahead if starts.get(oid) != before[oid]]
    assert 1.0 - len(moved) / len(clearly_ahead) >= 0.8, f"{len(moved)} of {len(clearly_ahead)} moved"
    assert all(abs(starts[oid] - before[oid]) < timedelta(days=3) for oid in moved)
    assert (
        max(abs(sim.scenario_priorities[oid] - sim.baseline_priorities[oid]) for oid in clearly_ahead) < 0.1
    )
    assert sim.diff.late_orders_after <= sim.diff.late_orders_before + 1


# -------------------------------------------------------- production delay


def test_production_delay_pushes_downstream_and_triggers_replan(
    pipeline: PlanningPipeline, snapshot: PlanningSnapshot, baseline: PipelineResult
) -> None:
    scheduled = _scheduled_ids(baseline.schedule)
    running = [
        op
        for op in snapshot.operations.values()
        if op.operation_status is OperationStatus.IN_PROGRESS
        and op.machine_id is not None
        and op.order_id in scheduled
        and op.cycle_minutes_per_unit is not None
    ]
    op = max(running, key=lambda o: (o.pending_quantity * (o.cycle_minutes_per_unit or 0.0), o.operation_id))
    before = next(e for e in baseline.schedule.entries if e.operation_id == op.operation_id)

    delayed = snapshot.clone()
    slow = delayed.operations[op.operation_id]
    slow.completed_quantity = 0.0  # the pieces counted as done must be re-run
    slow.cycle_minutes_per_unit = (slow.cycle_minutes_per_unit or 0.0) * 1.5
    slow.machine_cycle_minutes = {}
    slow.estimated_end = None
    result = pipeline.run(delayed, CONFIG)
    entries = result.schedule.entries_for_order(op.order_id)
    after = next(e for e in entries if e.operation_id == op.operation_id)
    assert after.machine_id == before.machine_id == op.machine_id
    assert after.run_minutes > before.run_minutes and after.end > before.end
    assert all(e.setup_start >= after.end for e in entries if e.operation_id != op.operation_id)

    event = ReplanEvent(
        ReplanTriggerType.PRODUCTION_DELAY,
        "operation",
        op.operation_id,
        snapshot.as_of,
        f"{op.operation_id} running behind plan",
        order_id=op.order_id,
        machine_id=op.machine_id,
        details={"overdue": True},
    )
    engine = _replanning()
    assert engine.should_trigger([event])
    decision = engine.evaluate([event], baseline.schedule, result.schedule, snapshot.as_of, snapshot=delayed)
    assert decision.should_replan and decision.changed_entries >= 1
    assert "production_delay" in decision.triggers and op.order_id in decision.reason
    diff = diff_schedules(baseline.schedule, result.schedule, snapshot, delayed)
    assert diff.orders_affected >= 1


# ------------------------------------------------------------------ rework


def test_rework_reopens_an_operation_and_triggers_replan(
    pipeline: PlanningPipeline, snapshot: PlanningSnapshot, baseline: PipelineResult
) -> None:
    scheduled = _scheduled_ids(baseline.schedule)
    candidates = [
        o
        for o in snapshot.orders.values()
        if o.order_status is OrderStatus.IN_PRODUCTION
        and o.order_id in scheduled
        and o.required_machine_id is None
        and any(
            op.operation_status is OperationStatus.COMPLETED
            for op in snapshot.operations_for_order(o.order_id)
        )
    ]
    order = min(candidates, key=lambda o: o.order_id)
    done = next(
        op
        for op in snapshot.operations_for_order(order.order_id)
        if op.operation_status is OperationStatus.COMPLETED
    )

    rework = snapshot.clone()
    reopened = rework.operations[done.operation_id]
    reopened.operation_status = OperationStatus.REWORK
    reopened.completed_quantity = 0.0
    reopened.actual_end = None
    rework.orders[order.order_id].order_status = OrderStatus.REWORK
    rework.orders[order.order_id].quality_status = QualityStatus.REWORK
    result = pipeline.run(rework, CONFIG)

    before, after = (
        baseline.schedule.entries_for_order(order.order_id),
        result.schedule.entries_for_order(order.order_id),
    )
    assert order.order_id not in {u.order_id for u in result.schedule.unscheduled}
    assert len(after) == len(before) + 1
    assert any(e.operation_id == done.operation_id for e in after)
    assert result.priorities[order.order_id].readiness is not ReadinessState.QUALITY_HOLD
    reopened_entry = next(e for e in after if e.operation_id == done.operation_id)
    assert reopened_entry.quantity == done.quantity and reopened_entry.run_minutes > 0
    assert sum(e.run_minutes for e in after) > sum(e.run_minutes for e in before)
    # the rework step runs before the steps that follow it in the route
    assert all(e.setup_start >= reopened_entry.end for e in after if e.operation_id != done.operation_id)

    event = ReplanEvent(
        ReplanTriggerType.REWORK, "order", order.order_id, snapshot.as_of, "rework", order_id=order.order_id
    )
    decision = _replanning().evaluate(
        [event], baseline.schedule, result.schedule, snapshot.as_of, snapshot=rework
    )
    assert decision.should_replan and decision.requires_approval and "rework" in decision.reason


# ------------------------------------------------------- capacity increase


def test_capacity_increase_does_not_increase_tardiness(
    pipeline: PlanningPipeline, snapshot: PlanningSnapshot, baseline: PipelineResult
) -> None:
    # add a machine to the group whose machines carry the most scheduled hours each
    machines_in_group = Counter(m.machine_group for m in snapshot.machines.values())
    load: dict[str, float] = defaultdict(float)
    entries_on: Counter[str] = Counter()
    for e in baseline.schedule.entries:
        load[snapshot.machines[e.machine_id].machine_group] += e.setup_minutes + e.run_minutes
        entries_on[e.machine_id] += 1
    group = max(load, key=lambda g: load[g] / machines_in_group[g])
    source = max(
        (m for m in snapshot.machines.values() if m.machine_group == group),
        key=lambda m: entries_on[m.machine_id],
    )
    new_id = f"MC-{group}-SIM"

    sim = pipeline.simulate(
        snapshot,
        [{"kind": "add_machine", "clone_of_machine_id": source.machine_id, "new_machine_id": new_id}],
        CONFIG,
    )
    assert new_id not in snapshot.machines
    assert any(e.machine_id == new_id for e in sim.scenario.entries)
    assert any(note.startswith("Added machine") for note in sim.scenarios[0]["notes"])
    before, after = sim.baseline.metrics, sim.scenario.metrics
    assert after.total_tardiness_hours <= before.total_tardiness_hours
    assert after.total_tardiness_hours < before.total_tardiness_hours  # the extra capacity is used
    assert after.late_orders <= before.late_orders
    assert after.makespan_hours <= before.makespan_hours
    assert sim.diff.orders_newly_late == 0 and sim.diff.orders_moved_machine >= 1
