"""PlanningPipeline end to end on the synthetic "small" plant (seed 42, ~300 orders, 12 machines).

The module-scoped ``result`` is one full run; tests that add overlays
(expedite, override, lock) work on ``snapshot.clone()`` and run again.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta

import pytest

from app.core.clock import FrozenClock
from app.core.errors import NotFoundError
from app.domain.config import SystemConfig
from app.domain.enums import AlertSeverity, AlertType, LockType, OperationStatus, OverrideType, ReadinessState
from app.domain.models import Expedite, PriorityOverride, ScheduleLock, TimeWindow
from app.domain.results import PriorityResult, ScheduleEntry, ScheduleResult
from app.domain.snapshot import PlanningSnapshot
from app.engines.constraints.readiness import SYNTHETIC_OPERATION_SUFFIX
from app.engines.pipeline import (
    DATA_QUALITY_REASON_CODE,
    PipelineResult,
    PlanningPipeline,
    explain_order,
)
from synthetic.generator import SyntheticDataGenerator

SEED = 42
SCALE = "small"
CONFIG = SystemConfig()
STAGES = (
    "data_quality",
    "calendars",
    "constraints",
    "priority_context",
    "priority",
    "schedule",
    "quality",
    "kpis",
    "capacity",
    "bottlenecks",
    "alerts",
)
POINTS_LINE = re.compile(r"^([+-]?\d+(?:\.\d+)?) — ")


def _snapshot() -> PlanningSnapshot:
    return SyntheticDataGenerator(seed=SEED, scale=SCALE).generate().to_snapshot()


@pytest.fixture(scope="module")
def snapshot() -> PlanningSnapshot:
    return _snapshot()


@pytest.fixture(scope="module")
def pipeline(snapshot: PlanningSnapshot) -> PlanningPipeline:
    return PlanningPipeline(FrozenClock(snapshot.as_of))


@pytest.fixture(scope="module")
def result(pipeline: PlanningPipeline, snapshot: PlanningSnapshot) -> PipelineResult:
    return pipeline.run(snapshot, CONFIG)


# ------------------------------------------------------------------ helpers


def _unscheduled_ids(schedule: ScheduleResult) -> set[str]:
    return {u.order_id for u in schedule.unscheduled}


def _fully_scheduled_ids(schedule: ScheduleResult) -> set[str]:
    return {e.order_id for e in schedule.entries} - _unscheduled_ids(schedule)


def _cycle_known(snapshot: PlanningSnapshot, order_id: str) -> bool:
    pending = snapshot.pending_operations_for_order(order_id)
    if not pending:
        return snapshot.orders[order_id].estimated_cycle_minutes_per_unit is not None
    return all(op.cycle_minutes_per_unit is not None or op.machine_cycle_minutes for op in pending)


def _in_progress_orders(snapshot: PlanningSnapshot) -> set[str]:
    return {
        op.order_id
        for op in snapshot.operations.values()
        if op.operation_status is OperationStatus.IN_PROGRESS and op.machine_id is not None and not op.is_done
    }


def _ready_scheduled(result: PipelineResult) -> list[PriorityResult]:
    """Ready, fully scheduled orders sorted by score."""
    scheduled = _fully_scheduled_ids(result.schedule)
    return sorted(
        (
            r
            for r in result.priorities.values()
            if r.readiness is ReadinessState.READY and r.order_id in scheduled
        ),
        key=lambda r: (r.score, r.order_id),
    )


def _first_entry(schedule: ScheduleResult, order_id: str) -> ScheduleEntry:
    entries = schedule.entries_for_order(order_id)
    assert entries, f"{order_id} has no entries"
    return entries[0]


def _entry_key(e: ScheduleEntry) -> tuple[str, str, str, datetime, datetime, datetime, float, bool]:
    return (e.entry_id, e.machine_id, e.order_id, e.setup_start, e.start, e.end, e.priority_score, e.locked)


# --------------------------------------------------------------- the run


def test_pipeline_runs_and_times_every_stage(result: PipelineResult, snapshot: PlanningSnapshot) -> None:
    assert result.run_id.startswith("run_") and result.schedule.run_id == result.run_id
    assert result.generated_at == snapshot.as_of
    assert set(STAGES) <= set(result.timings) and "total" in result.timings
    assert all(t >= 0 for t in result.timings.values())
    assert result.timings["total"] >= sum(result.timings[s] for s in STAGES) * 0.99
    assert result.profile_id == CONFIG.priority_profile.profile_id
    assert result.profile_version == CONFIG.priority_profile.version
    assert (
        result.config_id == CONFIG.scheduling.config_id and result.config_version == CONFIG.scheduling.version
    )
    assert result.scheduler_name == "rule_based" and result.scheduler_version == "1.0.0"
    assert set(result.priorities) == {o.order_id for o in snapshot.open_orders()}
    assert result.schedule.quality is not None and 0 <= result.schedule.quality.score <= 100
    assert result.schedule.metrics.scheduled_orders > 0 and result.schedule.entries
    assert set(result.calendars) == set(snapshot.machines)
    assert result.capacity.dimension == "machine_group" and result.capacity.period == "week"
    assert result.kpis.as_of == snapshot.as_of
    summary = result.summary()
    assert summary["orders_scheduled"] == result.schedule.metrics.scheduled_orders
    assert summary["timings"]["total"] == round(result.timings["total"], 4)


def test_every_open_order_is_scheduled_or_explained(
    result: PipelineResult, snapshot: PlanningSnapshot
) -> None:
    schedule = result.schedule
    open_ids = {o.order_id for o in snapshot.open_orders()}
    scheduled = _fully_scheduled_ids(schedule)
    unscheduled = _unscheduled_ids(schedule)
    assert scheduled | unscheduled == open_ids
    assert not (scheduled & unscheduled)
    for item in schedule.unscheduled:
        assert item.reason_code and item.reason, item
        assert item.order_id in open_ids
    # a ready order with known cycle times, in a schedulable status and clean data is scheduled,
    # or the schedule says why not
    excluded = set(result.dq_excluded_order_ids)
    ready = [
        r.order_id
        for r in result.priorities.values()
        if r.readiness is ReadinessState.READY
        and snapshot.orders[r.order_id].order_status.is_schedulable
        and r.order_id not in excluded
        and _cycle_known(snapshot, r.order_id)
    ]
    assert ready
    placed = 0
    for order_id in ready:
        if order_id in scheduled:
            placed += 1
            pending = {op.operation_id for op in snapshot.pending_operations_for_order(order_id)}
            assert pending <= {e.operation_id for e in schedule.entries_for_order(order_id)}
        else:
            reasons = [u for u in schedule.unscheduled if u.order_id == order_id]
            assert reasons and all(
                u.reason_code in {"no_eligible_machine", "blocked", "missing_cycle_time"} for u in reasons
            )
    assert placed / len(ready) >= 0.8, f"only {placed} of {len(ready)} ready orders scheduled"


def test_no_machine_has_overlapping_entries(result: PipelineResult) -> None:
    by_machine: dict[str, list[ScheduleEntry]] = defaultdict(list)
    for entry in result.schedule.entries:
        by_machine[entry.machine_id].append(entry)
    for machine_id, entries in by_machine.items():
        entries.sort(key=lambda e: (e.setup_start, e.sequence_on_machine))
        assert [e.sequence_on_machine for e in entries] == list(range(1, len(entries) + 1)), machine_id
        for a, b in zip(entries, entries[1:], strict=False):
            assert a.end <= b.setup_start, f"{machine_id}: {a.entry_id} overlaps {b.entry_id}"
        for e in entries:
            assert e.setup_start <= e.start <= e.end


def test_entries_respect_machine_calendars(result: PipelineResult, snapshot: PlanningSnapshot) -> None:
    now = snapshot.as_of
    for entry in result.schedule.entries:
        calendar = result.calendars[entry.machine_id]
        machine = snapshot.machines[entry.machine_id]
        assert entry.setup_start >= now
        assert calendar.is_working(entry.setup_start), entry.entry_id
        # sample points: the middle of every working window the entry spans
        for w in calendar.working_windows(entry.setup_start, entry.end):
            assert calendar.is_working(w.start + (w.end - w.start) / 2)
        busy = calendar.working_minutes_between(entry.setup_start, entry.end)
        assert busy == pytest.approx(entry.setup_minutes + entry.run_minutes, abs=0.01), entry.entry_id
        assert not any(w.contains(entry.setup_start) for w in machine.all_downtime), entry.entry_id


def test_every_entry_belongs_to_an_open_pending_operation(
    result: PipelineResult, snapshot: PlanningSnapshot
) -> None:
    for entry in result.schedule.entries:
        order = snapshot.orders[entry.order_id]
        assert order.is_open and order.order_status.is_schedulable
        assert entry.customer_id == order.customer_id
        op = snapshot.operations.get(entry.operation_id)
        if op is None:
            assert entry.operation_id.endswith(SYNTHETIC_OPERATION_SUFFIX)
            assert not snapshot.operations_for_order(order.order_id)
            continue
        assert op.order_id == order.order_id and not op.is_done
        assert entry.quantity == op.pending_quantity
        assert entry.priority_score == result.priorities[order.order_id].score


# ------------------------------------------------------------- overlays


def test_expedite_propagates_end_to_end(
    pipeline: PlanningPipeline, snapshot: PlanningSnapshot, result: PipelineResult
) -> None:
    ready = [r for r in _ready_scheduled(result) if r.score <= 65]
    target = min(ready, key=lambda r: abs(r.score - 45))
    now = snapshot.as_of
    clone = snapshot.clone()
    clone.expedites.append(
        Expedite(
            "EXP-T",
            target.order_id,
            "tester",
            now,
            "customer escalation",
            30.0,
            now,
            now + timedelta(hours=4),
        )
    )
    after = pipeline.run(clone, CONFIG)
    priority = after.priorities[target.order_id]
    assert priority.score == pytest.approx(target.score + 30.0)
    assert priority.rank is not None and target.rank is not None and priority.rank < target.rank
    assert any(line.startswith("+30 — Expedite:") for line in priority.explanation.splitlines())
    before_entry, after_entry = (
        _first_entry(result.schedule, target.order_id),
        _first_entry(after.schedule, target.order_id),
    )
    assert after_entry.priority_score == pytest.approx(target.score + 30.0)
    assert after_entry.setup_start <= before_entry.setup_start
    assert after_entry.sequence_on_machine <= before_entry.sequence_on_machine


def test_force_next_override_propagates_end_to_end(
    pipeline: PlanningPipeline, snapshot: PlanningSnapshot, result: PipelineResult
) -> None:
    target = _ready_scheduled(result)[0]  # the least urgent ready order
    now = snapshot.as_of
    clone = snapshot.clone()
    clone.overrides.append(
        PriorityOverride("OVR-T", target.order_id, OverrideType.FORCE_NEXT, "tester", now, "run first")
    )
    after = pipeline.run(clone, CONFIG)
    priority = after.priorities[target.order_id]
    assert priority.score == 100.0 and priority.forced_next and priority.rank == 1
    assert priority.explanation.splitlines()[-1] == "Forced next by planner override"
    assert any("Override: Forced next by tester" in line for line in priority.explanation.splitlines())
    entry = _first_entry(after.schedule, target.order_id)
    assert entry.priority_score == 100.0 and "forced next by planner" in entry.placement_reason
    assert entry.setup_start <= _first_entry(result.schedule, target.order_id).setup_start
    # only work that was already running (or planner-locked) may precede it on its machine
    ahead = [
        e
        for e in after.schedule.entries_for_machine(entry.machine_id)
        if e.sequence_on_machine < entry.sequence_on_machine
    ]
    protected = _in_progress_orders(clone) | {lock.order_id for lock in clone.locks if lock.order_id}
    assert all(e.order_id in protected for e in ahead), [(e.order_id, e.placement_reason) for e in ahead]


def test_order_lock_moves_order_to_the_locked_machine(
    pipeline: PlanningPipeline, snapshot: PlanningSnapshot, result: PipelineResult
) -> None:
    locked_machine = "MC-CNC3-02"  # down at as_of with a known return: eligible again afterwards
    returns_at = max(w.end for w in snapshot.machines[locked_machine].all_downtime)
    assert returns_at > snapshot.as_of
    candidates = [
        r
        for r in _ready_scheduled(result)
        if _first_entry(result.schedule, r.order_id).machine_id == "MC-CNC3-01"
        and locked_machine
        in {c.machine_id for c in result.schedule.machine_recommendations[r.order_id].eligible}
    ]
    target = candidates[len(candidates) // 2]
    clone = snapshot.clone()
    clone.locks.append(
        ScheduleLock(
            "LOCK-T",
            LockType.ORDER,
            "tester",
            snapshot.as_of,
            "keep on CNC3-02",
            order_id=target.order_id,
            machine_id=locked_machine,
        )
    )
    after = pipeline.run(clone, CONFIG)
    entries = after.schedule.entries_for_order(target.order_id)
    assert entries and target.order_id not in _unscheduled_ids(after.schedule)
    assert entries[0].machine_id == locked_machine and entries[0].setup_start >= returns_at
    assert "planner lock LOCK-T" in entries[0].placement_reason
    assert len(entries) == len(result.schedule.entries_for_order(target.order_id))
    # the lock pins the CNC step only; deburring / inspection / packing keep their own machines
    assert all(e.machine_id != locked_machine for e in entries[1:])
    # a lock adds no priority adjustment; the score may move because the machine situation changed
    assert [a.kind for a in after.priorities[target.order_id].adjustments] == [
        a.kind for a in target.adjustments
    ]
    availability = next(
        f for f in after.priorities[target.order_id].factors if f.key == "machine_availability"
    )
    assert locked_machine in availability.reason


def test_time_slot_lock_places_order_in_its_window(
    pipeline: PlanningPipeline, snapshot: PlanningSnapshot, result: PipelineResult
) -> None:
    machine_id = "MC-CNC3-01"
    target = next(
        r
        for r in _ready_scheduled(result)
        if _first_entry(result.schedule, r.order_id).machine_id == machine_id
    )
    window = TimeWindow(
        snapshot.as_of + timedelta(days=2, hours=3), snapshot.as_of + timedelta(days=2, hours=9), "reserved"
    )
    clone = snapshot.clone()
    clone.locks.append(
        ScheduleLock(
            "SLOT-T",
            LockType.TIME_SLOT,
            "tester",
            snapshot.as_of,
            "reserved slot",
            order_id=target.order_id,
            machine_id=machine_id,
            window=window,
        )
    )
    after = pipeline.run(clone, CONFIG)
    entry = _first_entry(after.schedule, target.order_id)
    assert entry.machine_id == machine_id and entry.locked
    assert entry.setup_start == after.calendars[machine_id].next_working_time(window.start)
    others = [e for e in after.schedule.entries_for_machine(machine_id) if e.order_id != target.order_id]
    assert all(e.end <= entry.setup_start or e.setup_start >= entry.end for e in others)


# ---------------------------------------------------------- explanations


def test_explanation_lines_resum_to_the_score(result: PipelineResult) -> None:
    for priority in result.priorities.values():
        lines = priority.explanation.splitlines()
        assert lines[0] == f"ORDER #{priority.order_id}" and lines[1] == f"Priority: {priority.score:.0f}"
        points = [float(m.group(1)) for line in lines if (m := POINTS_LINE.match(line))]
        assert len(points) >= len(priority.factors) + len(priority.adjustments)
        assert sum(points) == pytest.approx(priority.score, abs=0.05 * len(points) + 1e-6), priority.order_id
        assert f"Total: {priority.score:.0f}" in lines
        if priority.blocked:
            assert any(line.startswith("Blocked: ") for line in lines)


def test_explain_order_bundles_priority_schedule_and_recommendation(
    result: PipelineResult, snapshot: PlanningSnapshot
) -> None:
    target = _ready_scheduled(result)[-1]
    explanation = explain_order(result, target.order_id)
    assert explanation.priority is result.priorities[target.order_id]
    assert explanation.entries == result.schedule.entries_for_order(target.order_id)
    assert explanation.recommendation is not None
    assert explanation.recommendation.recommended_machine_id == explanation.entries[0].machine_id
    assert explanation.text.startswith(f"ORDER #{target.order_id}\n") and "Schedule:" in explanation.text
    assert explanation.entries[0].placement_reason in explanation.text
    with pytest.raises(NotFoundError):
        explain_order(result, "NO-SUCH-ORDER")
    closed = next(o.order_id for o in snapshot.orders.values() if not o.is_open)
    with pytest.raises(NotFoundError):
        explain_order(result, closed)


# ---------------------------------------------------------- data quality


def test_data_quality_blocked_orders_are_not_scheduled(result: PipelineResult) -> None:
    excluded = result.dq_report.unschedulable_order_ids
    assert excluded and result.dq_excluded_order_ids == excluded
    codes = {u.order_id: u.reason_code for u in result.schedule.unscheduled}
    for order_id in excluded:
        assert not result.schedule.entries_for_order(order_id)
        assert codes[order_id] in {DATA_QUALITY_REASON_CODE, "status_not_schedulable"}
        assert order_id in result.priorities  # still visible in the priority queue
    dq_items = [u for u in result.schedule.unscheduled if u.reason_code == DATA_QUALITY_REASON_CODE]
    assert dq_items and all(u.reason.startswith("blocked by data quality") for u in dq_items)
    text = explain_order(result, dq_items[0].order_id).text
    assert "Not scheduled (data_quality)" in text and "Data quality (blocking" in text
    assert result.dq_report.dashboard().unschedulable_orders == len(excluded)


# -------------------------------------------------------------- analytics


def test_kpis_are_consistent_with_schedule_metrics(
    result: PipelineResult, snapshot: PlanningSnapshot
) -> None:
    kpis, metrics = result.kpis, result.schedule.metrics
    open_orders = snapshot.open_orders()
    assert kpis.total_open_orders == len(open_orders) == len(result.priorities)
    assert kpis.scheduled_orders == metrics.scheduled_orders
    assert kpis.scheduled_orders + kpis.unscheduled_orders == kpis.total_open_orders
    assert kpis.unscheduled_orders == metrics.unscheduled_orders
    assert kpis.machine_utilization_pct == pytest.approx(metrics.overall_utilization_pct)
    assert kpis.overdue_orders == sum(
        1 for o in open_orders if o.due_date is not None and o.due_date < snapshot.as_of
    )
    assert kpis.at_risk_orders >= metrics.late_orders
    assert kpis.revenue_at_risk >= metrics.revenue_at_risk
    assert kpis.blocked_total == sum(1 for r in result.priorities.values() if r.blocked)
    assert kpis.blocked_by_material == sum(
        1 for r in result.priorities.values() if r.readiness is ReadinessState.WAITING_MATERIAL
    )
    assert kpis.capacity_utilization_pct == pytest.approx(result.capacity.utilization_pct)


def test_alerts_include_every_overdue_order(result: PipelineResult, snapshot: PlanningSnapshot) -> None:
    overdue = {
        o.order_id for o in snapshot.open_orders() if o.due_date is not None and o.due_date < snapshot.as_of
    }
    assert overdue
    alerts = [a for a in result.alerts if a.alert_type is AlertType.ORDER_OVERDUE]
    assert {a.order_id for a in alerts} == overdue and len(alerts) == result.kpis.overdue_orders
    assert all(a.severity is AlertSeverity.CRITICAL and a.raised_at == snapshot.as_of for a in alerts)
    keys = [a.dedupe_key for a in result.alerts]
    assert len(keys) == len(set(keys))
    assert result.bottlenecks and any(b.resource_type == "machine_group" for b in result.bottlenecks)


# ------------------------------------------------------ determinism & co


def test_pipeline_is_deterministic(pipeline: PlanningPipeline) -> None:
    first, second = pipeline.run(_snapshot(), CONFIG), pipeline.run(_snapshot(), CONFIG)
    assert first.run_id != second.run_id
    assert [_entry_key(e) for e in first.schedule.entries] == [_entry_key(e) for e in second.schedule.entries]
    assert [(u.order_id, u.operation_id, u.reason_code, u.reason) for u in first.schedule.unscheduled] == [
        (u.order_id, u.operation_id, u.reason_code, u.reason) for u in second.schedule.unscheduled
    ]
    assert {k: (v.score, v.rank, v.explanation) for k, v in first.priorities.items()} == {
        k: (v.score, v.rank, v.explanation) for k, v in second.priorities.items()
    }
    assert [a.dedupe_key for a in first.alerts] == [a.dedupe_key for a in second.alerts]
    assert first.schedule.quality is not None and second.schedule.quality is not None
    assert first.schedule.quality.score == second.schedule.quality.score


def test_previous_entries_freeze_the_lock_window(
    pipeline: PlanningPipeline, snapshot: PlanningSnapshot, result: PipelineResult
) -> None:
    window_end = snapshot.as_of + timedelta(minutes=CONFIG.scheduling.lock_window_minutes)
    frozen = [e for e in result.schedule.entries if e.setup_start < window_end and e.end > snapshot.as_of]
    assert frozen
    rerun = pipeline.run(snapshot, CONFIG, previous_entries=result.schedule.entries)
    by_op = {e.operation_id: e for e in rerun.schedule.entries}
    for e in frozen:
        again = by_op[e.operation_id]
        assert again.locked and (again.machine_id, again.setup_start, again.end) == (
            e.machine_id,
            e.setup_start,
            e.end,
        )
    assert sum(1 for e in rerun.schedule.entries if e.locked) == len(frozen)


def test_simulate_shares_the_pipeline_components(
    pipeline: PlanningPipeline, snapshot: PlanningSnapshot, result: PipelineResult
) -> None:
    machine_id = "MC-CNC5-01"
    hours = 24.0
    sim = pipeline.simulate(
        snapshot, [{"kind": "machine_down", "machine_id": machine_id, "duration_hours": hours}], CONFIG
    )
    assert [_entry_key(e) for e in sim.baseline.entries] == [_entry_key(e) for e in result.schedule.entries]
    assert sim.baseline_priorities == {k: v.score for k, v in result.priorities.items()}
    assert sim.scenarios[0]["kind"] == "machine_down" and sim.scenarios[0]["affected_machine_ids"] == [
        machine_id
    ]
    outage_end = snapshot.as_of + timedelta(hours=hours)
    assert all(e.setup_start >= outage_end for e in sim.scenario.entries if e.machine_id == machine_id)
    assert any(e.machine_id == machine_id for e in result.schedule.entries if e.setup_start < outage_end)
    assert sim.diff.orders_affected >= 1 and sim.diff.summary
    assert {u.order_id for u in sim.scenario.unscheduled if u.reason_code == DATA_QUALITY_REASON_CODE} == set(
        result.dq_excluded_order_ids
    )


def test_describe(pipeline: PlanningPipeline) -> None:
    info = pipeline.describe()
    assert info["scheduler"] == "rule_based" and "rule_based" in info["schedulers_available"]
    assert len(info["priority_factors"]) == 11 and info["data_quality_rules"]
