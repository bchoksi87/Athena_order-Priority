"""RuleBasedScheduler end to end: ordering, locks, frozen window, dependencies, calendars, determinism."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

import pytest

from app.core.clock import FrozenClock
from app.core.errors import NotFoundError
from app.domain.config import SchedulingConfig
from app.domain.enums import (
    LockType,
    MachineStatus,
    MaterialStatus,
    OperationStatus,
    OrderStatus,
    ProcessType,
    ReadinessState,
)
from app.domain.models import Machine, Operation, Order, ScheduleLock
from app.domain.results import PriorityResult, ScheduleEntry, ScheduleResult
from app.domain.snapshot import PlanningSnapshot
from app.engines.calendar import MachineCalendar, build_calendars
from app.engines.constraints import MachineState, default_constraint_engine
from app.engines.scheduling.registry import SchedulerRegistry, default_registry
from app.engines.scheduling.rule_based import RuleBasedScheduler
from app.engines.scheduling.setup import compute_setup
from app.engines.scheduling.state import apply_entry_to_state
from tests.engines.factories import (
    NOW,
    at,
    make_calendar_spec,
    make_lock,
    make_machine,
    make_material,
    make_operation,
    make_order,
    make_order_with_ops,
    make_priorities,
    make_snapshot,
    window,
)

CNC, DEB = ProcessType.CNC_MACHINING, ProcessType.DEBURRING


def _plant(**overrides: Any) -> list[Machine]:
    """Two CNC machines and one deburring machine on the default 08-16 Mon-Fri calendar."""
    machines = [
        make_machine("CNC-01", calendar_id="CAL"),
        make_machine("CNC-02", calendar_id="CAL", preferred_rank=1),
        make_machine("DEB-01", DEB, "DEB", calendar_id="CAL"),
    ]
    for m in machines:
        for k, v in overrides.items():
            setattr(m, k, v)
    return machines


def _order(
    order_id: str,
    *,
    steps: Sequence[ProcessType] = (CNC,),
    ops: Sequence[dict[str, Any]] | None = None,
    **overrides: Any,
) -> tuple[Order, list[Operation]]:
    fields: dict[str, Any] = {"requested_delivery_date": at(days=3), "quantity": 10.0}
    fields.update(overrides)
    op_defaults = [{"setup_minutes": 30.0, "cycle_minutes_per_unit": 6.0} for _ in steps]
    if ops:
        for base, extra in zip(op_defaults, ops, strict=False):
            base.update(extra)
    return make_order_with_ops(order_id, steps, op_overrides=op_defaults, **fields)


def _snapshot(
    orders: Iterable[tuple[Order, list[Operation]]], machines: list[Machine] | None = None, **kw: Any
) -> PlanningSnapshot:
    pairs = list(orders)
    return make_snapshot(
        orders=[o for o, _ in pairs],
        operations=[op for _, ops in pairs for op in ops],
        machines=machines if machines is not None else _plant(),
        calendars=[make_calendar_spec("CAL")],
        default_calendar_id="CAL",
        **kw,
    )


def _run(
    snapshot: PlanningSnapshot,
    scores: dict[str, float] | None = None,
    config: SchedulingConfig | None = None,
    *,
    priorities: dict[str, PriorityResult] | None = None,
    previous: Sequence[ScheduleEntry] | None = None,
    scheduler: RuleBasedScheduler | None = None,
) -> ScheduleResult:
    config = config or SchedulingConfig()
    if priorities is None:
        priorities = make_priorities(scores or {oid: 50.0 for oid in snapshot.orders})
    scheduler = scheduler or RuleBasedScheduler(FrozenClock(NOW))
    return scheduler.schedule(
        snapshot,
        priorities,
        config,
        build_calendars(snapshot),
        default_constraint_engine(config),
        previous_entries=previous,
    )


def _assert_no_overlap(result: ScheduleResult) -> None:
    for machine_id in {e.machine_id for e in result.entries}:
        entries = result.entries_for_machine(machine_id)
        for a, b in zip(entries, entries[1:], strict=False):
            assert a.end <= b.setup_start, f"{a.entry_id} overlaps {b.entry_id} on {machine_id}"
        assert [e.sequence_on_machine for e in entries] == list(range(1, len(entries) + 1))


def _assert_calendar_respected(result: ScheduleResult, calendars: dict[str, MachineCalendar]) -> None:
    for entry in result.entries:
        cal = calendars[entry.machine_id]
        assert cal.is_working(entry.setup_start), entry
        span = (entry.end - entry.setup_start).total_seconds()
        for k in range(0, 41):
            t = entry.setup_start + timedelta(seconds=span * k / 40)
            if t >= entry.end:
                break
            if not cal.is_working(t):
                # non-working time may be *skipped* by an entry but never *consumed*
                assert (
                    cal.working_minutes_between(entry.setup_start, entry.end)
                    == entry.setup_minutes + entry.run_minutes
                )
                break


class TestBasics:
    def test_priority_order_sequence_and_reasons(self) -> None:
        snap = _snapshot(
            [_order("LOW"), _order("HIGH"), _order("MID")],
            machines=[make_machine("CNC-01", calendar_id="CAL")],
        )
        result = _run(snap, {"LOW": 20, "HIGH": 90, "MID": 55})
        assert [e.order_id for e in result.entries] == ["HIGH", "MID", "LOW"]
        assert [e.sequence_on_machine for e in result.entries] == [1, 2, 3]
        first = result.entries[0]
        assert first.entry_id == "ent_HIGH-op1" and first.priority_score == 90
        assert first.setup_start == NOW and first.start == at(minutes=30) and first.end == at(minutes=90)
        assert first.is_last_operation and first.expected_completion == first.end
        assert first.due_date == at(days=3) and first.expected_lateness_hours == -(72 - 1.5)
        assert first.placement_reason.startswith("Rank 1 (score 90.0) → CNC-01: Only eligible machine")
        assert "30 min setup: machine state unknown, full setup assumed" in first.placement_reason
        assert result.entries[1].setup_start == first.end and result.entries[1].setup_minutes == 30.0
        assert result.metrics.scheduled_orders == 3 and result.metrics.on_time_pct == 100.0
        assert result.quality is not None and result.quality.score > 0
        assert result.algorithm == "rule_based" and result.algorithm_version == "1.0.0"
        assert result.profile_id == "PriorityProfile-A" and result.generated_at == NOW
        assert result.horizon_end == at(days=14)
        assert set(result.machine_recommendations) == {"LOW", "HIGH", "MID"}
        assert result.machine_recommendations["HIGH"].recommended_machine_id == "CNC-01"
        _assert_no_overlap(result)

    def test_multi_operation_routing_respects_sequence_and_last_flag(self) -> None:
        snap = _snapshot([_order("O1", steps=(CNC, DEB))])
        result = _run(snap)
        op1, op2 = result.entries_for_order("O1")
        assert op1.machine_id == "CNC-01" and op2.machine_id == "DEB-01"
        assert op2.setup_start >= op1.end
        assert not op1.is_last_operation and op2.is_last_operation
        assert op1.expected_completion == op2.end == op2.expected_completion
        assert (
            "O1" in result.machine_recommendations
            and result.machine_recommendations["O1"].operation_id == "O1-op1"
        )

    def test_parallel_machines_balance_by_earliest_completion(self) -> None:
        snap = _snapshot([_order(f"O{i}") for i in range(4)])
        result = _run(snap, {f"O{i}": 90 - i for i in range(4)})
        by_machine = {m: [e.order_id for e in result.entries_for_machine(m)] for m in ("CNC-01", "CNC-02")}
        assert by_machine == {"CNC-01": ["O0", "O2"], "CNC-02": ["O1", "O3"]}
        _assert_no_overlap(result)

    def test_forced_next_goes_first(self) -> None:
        snap = _snapshot([_order("A"), _order("B")], machines=[make_machine("CNC-01", calendar_id="CAL")])
        priorities = make_priorities({"A": 90, "B": 10})
        priorities["B"].forced_next = True
        result = _run(snap, priorities=priorities)
        assert [e.order_id for e in result.entries] == ["B", "A"]
        assert "forced next by planner" in result.entries[0].placement_reason

    def test_in_progress_operation_stays_on_its_machine_without_setup(self) -> None:
        running = _order(
            "RUN",
            ops=[
                {
                    "operation_status": OperationStatus.IN_PROGRESS,
                    "machine_id": "CNC-02",
                    "completed_quantity": 4.0,
                }
            ],
        )
        snap = _snapshot([running, _order("OTHER")])
        result = _run(snap, {"RUN": 10, "OTHER": 90})
        entry = result.entries_for_order("RUN")[0]
        assert entry.machine_id == "CNC-02" and entry.setup_minutes == 0.0 and entry.setup_start == NOW
        assert entry.run_minutes == 36.0 and entry.quantity == 6.0
        assert "operation in progress" in entry.placement_reason
        assert result.entries_for_machine("CNC-02")[0].order_id == "RUN"

    def test_order_without_routing_gets_synthetic_operation(self) -> None:
        order = make_order(
            "NOROUTE",
            requested_delivery_date=at(days=2),
            estimated_setup_minutes=10.0,
            estimated_cycle_minutes_per_unit=3.0,
        )
        snap = make_snapshot(
            orders=[order],
            machines=_plant(),
            calendars=[make_calendar_spec("CAL")],
            default_calendar_id="CAL",
        )
        result = _run(snap)
        assert len(result.entries) == 1
        e = result.entries[0]
        assert e.operation_id == "NOROUTE__order_level" and e.setup_minutes == 10.0 and e.run_minutes == 30.0
        assert e.is_last_operation and e.expected_completion == e.end

    def test_deterministic(self) -> None:
        snap = _snapshot(
            [
                _order(
                    f"O{i}", steps=(CNC, DEB), part_family=f"F{i % 2}", ops=[{"material_id": f"M{i % 3}"}, {}]
                )
                for i in range(12)
            ]
        )
        scores = {f"O{i}": 50 + (i * 7) % 20 for i in range(12)}
        a, b = _run(snap, scores), _run(snap, scores)
        assert a.entries == b.entries and a.unscheduled == b.unscheduled
        assert a.metrics == b.metrics and a.warnings == b.warnings

    def test_batching_pulls_same_material_job_forward(self) -> None:
        orders = [
            _order("A", ops=[{"material_id": "AL", "setup_family": "AL"}]),
            _order("B", ops=[{"material_id": "ST", "setup_family": "ST"}]),
            _order("C", ops=[{"material_id": "AL", "setup_family": "AL"}]),
        ]
        snap = _snapshot(orders, machines=[make_machine("CNC-01", calendar_id="CAL")])
        result = _run(snap, {"A": 90, "B": 80, "C": 75})
        assert [e.order_id for e in result.entries] == ["A", "C", "B"]
        assert (
            result.entries[1].setup_minutes == 0.0
            and "batched: pulled forward" in result.entries[1].placement_reason
        )
        assert any("pulled forward by batching" in w for w in result.warnings)
        config = SchedulingConfig()
        config.batching.enabled = False
        plain = _run(snap, {"A": 90, "B": 80, "C": 75}, config)
        assert [e.order_id for e in plain.entries] == ["A", "B", "C"]

    def test_max_orders_per_run_and_missing_priority(self) -> None:
        snap = _snapshot([_order("A"), _order("B"), _order("C")])
        result = _run(
            snap,
            priorities=make_priorities({"A": 90, "B": 50}),
            config=SchedulingConfig(max_orders_per_run=1),
        )
        codes = {u.order_id: u.reason_code for u in result.unscheduled}
        assert codes == {"B": "run_limit", "C": "no_priority"}
        assert [e.order_id for e in result.entries] == ["A"]


class TestCalendarsAndMachines:
    def test_entries_respect_shifts_and_downtime(self) -> None:
        down = window(at(hours=2), 3, "maintenance")  # 10:00-13:00 Monday
        machines = [make_machine("CNC-01", calendar_id="CAL", planned_downtime=[down])]
        snap = _snapshot(
            [_order(f"O{i}", ops=[{"cycle_minutes_per_unit": 15.0}]) for i in range(6)], machines=machines
        )
        calendars = build_calendars(snap)
        result = RuleBasedScheduler(FrozenClock(NOW)).schedule(
            snap,
            make_priorities({f"O{i}": 50 for i in range(6)}),
            SchedulingConfig(),
            calendars,
            default_constraint_engine(),
        )
        assert len(result.entries) == 6
        _assert_no_overlap(result)
        _assert_calendar_respected(result, calendars)
        for e in result.entries:
            assert (
                not (e.setup_start < down.end and down.start < e.end)
                or calendars["CNC-01"].working_minutes_between(e.setup_start, e.end)
                == e.setup_minutes + e.run_minutes
            )
        assert result.entries[-1].end > at(days=1)  # spilled into Tuesday

    def test_down_machine_excluded_and_maintenance_machine_delayed(self) -> None:
        machines = [
            make_machine("CNC-01", calendar_id="CAL", status=MachineStatus.DOWN),
            make_machine(
                "CNC-02",
                calendar_id="CAL",
                status=MachineStatus.MAINTENANCE,
                maintenance_windows=[window(at(hours=-1), 3)],
            ),
        ]
        snap = _snapshot([_order("O1")], machines=machines)
        result = _run(snap)
        assert any("CNC-01 excluded: down with no known return" in w for w in result.warnings)
        entry = result.entries[0]
        assert entry.machine_id == "CNC-02" and entry.setup_start == at(hours=2)

    def test_machine_without_calendar_yields_no_calendar(self) -> None:
        snap = _snapshot([_order("O1")], machines=[make_machine("CNC-01", calendar_id="CAL")])
        result = RuleBasedScheduler(FrozenClock(NOW)).schedule(
            snap, make_priorities({"O1": 50}), SchedulingConfig(), {}, default_constraint_engine()
        )
        assert result.unscheduled[0].reason_code == "no_calendar"
        assert any("CNC-01 excluded: no calendar" in w for w in result.warnings)

    def test_beyond_horizon_entries_are_flagged(self) -> None:
        config = SchedulingConfig(horizon_days=1)
        snap = _snapshot(
            [_order(f"O{i}", ops=[{"cycle_minutes_per_unit": 30.0}]) for i in range(4)],
            machines=[make_machine("CNC-01", calendar_id="CAL")],
        )
        result = _run(snap, config=config)
        assert len(result.entries) == 4
        beyond = [e for e in result.entries if e.setup_start >= result.horizon_end]
        assert beyond and any(
            w.startswith(f"{len(beyond)} entries start beyond the horizon end") for w in result.warnings
        )
        assert result.metrics.makespan_hours > 24


class TestFailures:
    def test_missing_cycle_time(self) -> None:
        snap = _snapshot([_order("O1", steps=(CNC, DEB), ops=[{"cycle_minutes_per_unit": None}, {}])])
        result = _run(snap)
        assert result.entries == []
        (item,) = result.unscheduled
        assert item.reason_code == "missing_cycle_time" and item.operation_id == "O1-op1"
        assert "1 later operation(s) of the order not scheduled" in item.reason
        assert result.metrics.unscheduled_orders == 1 and result.metrics.revenue_at_risk == 10_000.0
        assert result.machine_recommendations["O1"].recommended_machine_id is None
        assert result.machine_recommendations["O1"].rejected["CNC-01"] == ["cycle time unknown"]

    def test_no_eligible_machine(self) -> None:
        snap = _snapshot([_order("O1", steps=(ProcessType.HEAT_TREATMENT,))])
        result = _run(snap)
        assert result.unscheduled[0].reason_code == "no_eligible_machine"
        assert "no candidate machines" in result.unscheduled[0].reason

    def test_partial_order_counts_as_unscheduled_but_keeps_entries(self) -> None:
        snap = _snapshot([_order("O1", steps=(CNC, DEB), ops=[{}, {"cycle_minutes_per_unit": None}])])
        result = _run(snap)
        assert len(result.entries) == 1 and result.entries[0].expected_completion is None
        assert result.unscheduled[0].operation_id == "O1-op2"
        assert result.metrics.scheduled_orders == 0 and result.metrics.unscheduled_orders == 1


class TestBlockedOrders:
    def _blocked(self, **extra: Any) -> PlanningSnapshot:
        held = _order("HELD", on_hold=True, hold_reason="credit")
        material = _order("MAT", material_status=MaterialStatus.ON_ORDER, ops=[{"material_id": "AL"}])
        return _snapshot(
            [held, material, _order("OK")],
            materials=[make_material("AL", expected_receipt_date=at(days=1, hours=2))],
            **extra,
        )

    def test_blocked_orders_excluded_by_default(self) -> None:
        result = _run(self._blocked())
        assert [e.order_id for e in result.entries] == ["OK"]
        codes = {u.order_id: (u.reason_code, u.readiness) for u in result.unscheduled}
        assert codes == {
            "HELD": ("blocked", ReadinessState.ON_HOLD),
            "MAT": ("blocked", ReadinessState.WAITING_MATERIAL),
        }
        assert "credit" in next(u.reason for u in result.unscheduled if u.order_id == "HELD")

    def test_blocked_orders_deferred_when_configured(self) -> None:
        config = SchedulingConfig(schedule_blocked_orders=True)
        result = _run(self._blocked(), config=config)
        placed = {e.order_id: e for e in result.entries}
        assert set(placed) == {"OK", "MAT"}
        assert placed["MAT"].setup_start == at(days=1, hours=2)
        assert "deferred until blockers resolve" in placed["MAT"].placement_reason
        (held,) = result.unscheduled
        assert held.order_id == "HELD" and "no known resolution time" in held.reason

    def test_not_schedulable_status_reported(self) -> None:
        snap = _snapshot([_order("QI", order_status=OrderStatus.QUALITY_INSPECTION)])
        result = _run(snap)
        assert result.unscheduled[0].reason_code == "status_not_schedulable"
        assert result.metrics.revenue_at_risk == 0.0


class TestDependencies:
    def test_dependency_scheduled_first_and_release_honoured(self) -> None:
        upstream = _order("UP", ops=[{"cycle_minutes_per_unit": 12.0}])
        downstream = _order("DOWN", depends_on_order_ids={"UP"})
        snap = _snapshot(
            [upstream, downstream],
            machines=[make_machine("CNC-01", calendar_id="CAL"), make_machine("CNC-02", calendar_id="CAL")],
        )
        result = _run(snap, {"DOWN": 95, "UP": 10})
        up, down = result.entries_for_order("UP")[0], result.entries_for_order("DOWN")[0]
        assert down.setup_start >= up.end
        assert down.machine_id != up.machine_id or down.sequence_on_machine > up.sequence_on_machine

    def test_closed_dependency_ignored_and_unscheduled_dependency_blocks(self) -> None:
        done = make_order("DONE", order_status=OrderStatus.COMPLETED, completed_quantity=10.0)
        broken = _order("BROKEN", ops=[{"cycle_minutes_per_unit": None}])
        dep_done = _order("A", depends_on_order_ids={"DONE"})
        dep_broken = _order("B", depends_on_order_ids={"BROKEN"})
        snap = _snapshot([dep_done, dep_broken, broken])
        snap.orders["DONE"] = done
        snap.rebuild_indexes()
        result = _run(snap, {"A": 50, "B": 60, "BROKEN": 70})
        assert [e.order_id for e in result.entries] == ["A"]
        codes = {u.order_id: u.reason_code for u in result.unscheduled}
        assert codes == {"B": "blocked", "BROKEN": "missing_cycle_time"}
        assert "dependency order BROKEN is not scheduled" in next(
            u.reason for u in result.unscheduled if u.order_id == "B"
        )

    def test_cross_order_prerequisite_operation(self) -> None:
        first = _order("FIRST", ops=[{"cycle_minutes_per_unit": 12.0}])
        second = _order("SECOND", ops=[{"prerequisite_operation_id": "FIRST-op1"}])
        snap = _snapshot(
            [first, second],
            machines=[make_machine("CNC-01", calendar_id="CAL"), make_machine("CNC-02", calendar_id="CAL")],
        )
        result = _run(snap, {"SECOND": 90, "FIRST": 10})
        f, s = result.entries_for_order("FIRST")[0], result.entries_for_order("SECOND")[0]
        assert s.setup_start >= f.end


class TestLocks:
    def test_order_lock_pins_machine_and_goes_first(self) -> None:
        snap = _snapshot([_order("A"), _order("B"), _order("L")], locks=[make_lock("L", "CNC-02")])
        result = _run(snap, {"A": 90, "B": 80, "L": 10})
        locked = result.entries_for_order("L")[0]
        assert locked.machine_id == "CNC-02" and locked.sequence_on_machine == 1 and locked.setup_start == NOW
        assert "planner lock L1" in locked.placement_reason
        assert result.machine_recommendations["L"].rejected["CNC-01"]  # locked_machine_assignment violation

    def test_two_order_locks_keep_creation_order(self) -> None:
        locks = [
            make_lock("L2", "CNC-01", lock_id="LK2", created_at=at(hours=-1)),
            make_lock("L1", "CNC-01", lock_id="LK1", created_at=at(hours=-2)),
        ]
        snap = _snapshot(
            [_order("L1"), _order("L2"), _order("A")],
            machines=[make_machine("CNC-01", calendar_id="CAL")],
            locks=locks,
        )
        result = _run(snap, {"A": 99, "L1": 5, "L2": 50})
        assert [e.order_id for e in result.entries] == ["L1", "L2", "A"]

    def test_time_slot_lock_reserves_window(self) -> None:
        slot = ScheduleLock(
            "TS",
            LockType.TIME_SLOT,
            "planner",
            at(hours=-1),
            "trial run",
            machine_id="CNC-01",
            window=window(at(hours=1), 2),
        )
        snap = _snapshot(
            [_order(f"O{i}", ops=[{"cycle_minutes_per_unit": 3.0}]) for i in range(3)],  # 60 min jobs
            machines=[make_machine("CNC-01", calendar_id="CAL")],
            locks=[slot],
        )
        result = _run(snap, {f"O{i}": 90 - i for i in range(3)})
        for e in result.entries:
            assert not (e.setup_start < at(hours=3) and at(hours=1) < e.end), e
        assert result.entries[0].end == at(hours=1) and result.entries[1].setup_start == at(hours=3)
        assert result.entries[2].setup_start == at(hours=4)

    def test_time_slot_lock_with_order_places_it_in_the_slot(self) -> None:
        slot = ScheduleLock(
            "TS",
            LockType.TIME_SLOT,
            "planner",
            at(hours=-1),
            "customer visit",
            order_id="VIP",
            machine_id="CNC-02",
            window=window(at(hours=2), 2),
        )
        snap = _snapshot([_order("VIP"), _order("A")], locks=[slot])
        result = _run(snap, {"VIP": 5, "A": 90})
        vip = result.entries_for_order("VIP")[0]
        assert vip.machine_id == "CNC-02" and vip.setup_start == at(hours=2) and vip.locked
        assert not result.entries_for_order("A")[0].locked

    def test_sequence_lock_fixes_relative_order(self) -> None:
        seq = ScheduleLock(
            "SQ",
            LockType.SEQUENCE,
            "planner",
            at(hours=-1),
            "fixture order",
            machine_id="CNC-01",
            sequence_order_ids=["C", "A", "B"],
        )
        snap = _snapshot(
            [_order("A"), _order("B"), _order("C"), _order("X")],
            machines=[make_machine("CNC-01", calendar_id="CAL")],
            locks=[seq],
        )
        result = _run(snap, {"A": 90, "B": 80, "C": 70, "X": 99})
        order = [e.order_id for e in result.entries]
        # sequence-locked orders are planner decisions: they go first, in the locked order
        assert order == ["C", "A", "B", "X"]

    def test_machine_lock_freezes_machine(self) -> None:
        frozen = ScheduleLock(
            "ML", LockType.MACHINE, "planner", at(hours=-1), "manual control", machine_id="CNC-01"
        )
        snap = _snapshot([_order("A"), _order("B")], locks=[frozen])
        result = _run(snap, {"A": 90, "B": 80})
        assert {e.machine_id for e in result.entries} == {"CNC-02"}
        assert any("CNC-01 excluded: machine locked" in w for w in result.warnings)


class TestFrozenWindow:
    def _previous(self) -> tuple[PlanningSnapshot, ScheduleResult]:
        snap = _snapshot(
            [_order("A"), _order("B"), _order("C"), _order("D")],
            machines=[make_machine("CNC-01", calendar_id="CAL")],
        )
        return snap, _run(snap, {"A": 90, "B": 80, "C": 70, "D": 60})

    def test_entries_inside_lock_window_reproduced_verbatim(self) -> None:
        snap, previous = self._previous()
        config = SchedulingConfig(
            lock_window_minutes=120.0
        )  # A (08:00) and B (09:30) start inside; C (11:00) does not
        # priorities flip: without the frozen window D would go first
        result = _run(snap, {"A": 10, "B": 20, "C": 30, "D": 99}, config, previous=previous.entries)
        assert [e.order_id for e in result.entries] == ["A", "B", "D", "C"]
        for prev, new in zip(previous.entries[:2], result.entries[:2], strict=False):
            assert replace(prev, locked=True) == replace(
                new,
                expected_lateness_hours=prev.expected_lateness_hours,
                expected_completion=prev.expected_completion,
            )
            assert new.locked
        assert result.entries[2].setup_start == result.entries[1].end

    def test_entries_for_closed_orders_dropped_with_warning(self) -> None:
        snap, previous = self._previous()
        snap.orders["A"].order_status = OrderStatus.COMPLETED
        result = _run(
            snap,
            {"B": 80, "C": 70, "D": 60},
            SchedulingConfig(lock_window_minutes=600.0),
            previous=previous.entries,
        )
        assert "A" not in {e.order_id for e in result.entries}
        assert any("Frozen entry for A/A-op1 dropped" in w for w in result.warnings)
        assert result.entries[0].order_id == "B" and result.entries[0].locked

    def test_zero_lock_window_reproduces_nothing(self) -> None:
        snap, previous = self._previous()
        result = _run(
            snap,
            {"A": 10, "B": 20, "C": 30, "D": 99},
            SchedulingConfig(lock_window_minutes=0.0),
            previous=previous.entries,
        )
        assert [e.order_id for e in result.entries] == ["D", "C", "B", "A"]
        assert not any(e.locked for e in result.entries)


class TestBackFill:
    """Gap filling: idle time before a late-released job is used, nothing existing moves."""

    def test_lower_priority_ready_jobs_run_before_a_late_released_high_priority_job(self) -> None:
        # HIGH needs 5.5 h of CNC first, so its deburring step is released at 13:30; the two
        # deburring-only orders are ready now and must not wait behind it on the idle DEB-01.
        high = _order("HIGH", steps=(CNC, DEB), ops=[{"cycle_minutes_per_unit": 30.0}, {}])
        lows = [_order("LOW1", steps=(DEB,)), _order("LOW2", steps=(DEB,))]
        snap = _snapshot(
            [high, *lows],
            machines=[
                make_machine("CNC-01", calendar_id="CAL"),
                make_machine("DEB-01", DEB, "DEB", calendar_id="CAL"),
            ],
        )
        result = _run(snap, {"HIGH": 90, "LOW1": 50, "LOW2": 40})
        deb = {e.order_id: e for e in result.entries_for_machine("DEB-01")}
        assert deb["HIGH"].setup_start == at(hours=5.5) and deb["HIGH"].end == at(hours=7)
        assert deb["LOW1"].setup_start == NOW and deb["LOW1"].end == at(hours=1.5)
        assert deb["LOW2"].setup_start == at(hours=1.5) and deb["LOW2"].end == at(hours=3)
        assert [e.order_id for e in result.entries_for_machine("DEB-01")] == ["LOW1", "LOW2", "HIGH"]
        assert [e.sequence_on_machine for e in result.entries_for_machine("DEB-01")] == [1, 2, 3]
        assert "back-filled into idle time" in deb["LOW1"].placement_reason
        assert "back-filled" not in deb["HIGH"].placement_reason
        assert result.metrics.on_time_pct == 100.0
        _assert_no_overlap(result)
        _assert_calendar_respected(result, build_calendars(snap))

    def test_gap_is_skipped_when_it_would_break_a_same_family_successor(self) -> None:
        # A and B (both family F1) reach DEB-01 late: A at 14:00-15:00, B (released Tuesday 08:30)
        # right behind it with no setup because it follows A. The 90 working minutes between them
        # would hold C (60 min), but C's family F2 would invalidate B's zero setup, so C goes after B.
        a = _order(
            "A",
            steps=(CNC, DEB),
            ops=[{"cycle_minutes_per_unit": 30.0}, {"setup_family": "F1", "cycle_minutes_per_unit": 3.0}],
        )
        b = _order("B", steps=(CNC, DEB), ops=[{"cycle_minutes_per_unit": 48.0}, {"setup_family": "F1"}])
        c = _order("C", steps=(DEB,), ops=[{"setup_family": "F2", "cycle_minutes_per_unit": 3.0}])
        machines = [
            make_machine("CNC-01", calendar_id="CAL"),
            make_machine("CNC-02", calendar_id="CAL"),
            make_machine("DEB-01", DEB, "DEB", calendar_id="CAL", available_from=at(hours=6)),
        ]
        snap = _snapshot([a, b, c], machines=machines)
        result = _run(snap, {"A": 90, "B": 85, "C": 80})
        deb = {e.order_id: e for e in result.entries_for_machine("DEB-01")}
        assert (deb["A"].setup_start, deb["A"].end) == (at(hours=6), at(hours=7))
        assert deb["B"].setup_start == at(days=1, hours=0.5) and deb["B"].setup_minutes == 0.0
        assert deb["C"].setup_start == deb["B"].end and "back-filled" not in deb["C"].placement_reason
        _assert_no_overlap(result)
        # the same job in family F1 keeps B's assumption valid and is back-filled into the gap
        snap.operations["C-op1"].setup_family = "F1"
        result = _run(snap, {"A": 90, "B": 85, "C": 80})
        deb = {e.order_id: e for e in result.entries_for_machine("DEB-01")}
        assert (deb["C"].setup_start, deb["C"].end) == (at(hours=7), at(hours=7.5))  # same family: no setup
        assert deb["B"].setup_start == at(days=1, hours=0.5) and deb["B"].setup_minutes == 0.0
        assert "back-filled" in deb["C"].placement_reason
        _assert_no_overlap(result)

    def test_invariants_with_locks_and_frozen_window_on_a_mixed_plant(self) -> None:
        cnc = [make_machine(f"CNC-0{i}", calendar_id="CAL") for i in (1, 2)]
        deb = [make_machine(f"DEB-0{i}", DEB, "DEB", calendar_id="CAL") for i in (1, 2)]
        ins = [make_machine("INS-01", ProcessType.INSPECTION, "INS", calendar_id="CAL")]
        orders = []
        for i in range(24):
            steps = (CNC, DEB, ProcessType.INSPECTION) if i % 3 else (CNC, ProcessType.INSPECTION)
            ops = [
                {
                    "setup_minutes": 15.0 + 5 * (i % 4),
                    "cycle_minutes_per_unit": [3.0, 9.0, 27.0, 6.0][i % 4],
                    "setup_family": f"F{i % 3}",
                    "material_id": f"M{i % 2}",
                }
                for _ in steps
            ]
            orders.append(_order(f"O{i:02d}", steps=steps, ops=ops, part_family=f"PF{i % 5}"))
        slot = ScheduleLock(
            "TS",
            LockType.TIME_SLOT,
            "planner",
            at(hours=-1),
            "trial",
            machine_id="DEB-01",
            window=window(at(hours=2), 2),
        )
        snap = _snapshot(orders, machines=[*cnc, *deb, *ins], locks=[slot])
        scores = {f"O{i:02d}": float((i * 37) % 100) for i in range(24)}
        calendars = build_calendars(snap)
        config = SchedulingConfig(lock_window_minutes=180.0)
        first = _run(snap, scores, config)
        result = _run(snap, scores, config, previous=first.entries)
        assert _run(snap, scores, config, previous=first.entries).entries == result.entries  # deterministic
        assert len(result.entries) == len(snap.operations) and not result.unscheduled
        assert any("back-filled" in e.placement_reason for e in result.entries)
        _assert_no_overlap(result)
        _assert_calendar_respected(result, calendars)
        for e in result.entries_for_machine("DEB-01"):  # reserved window honoured
            assert not (e.setup_start < at(hours=4) and at(hours=2) < e.end), e
        frozen = [e for e in first.entries if e.setup_start < at(hours=3)]
        assert frozen
        by_op = {e.operation_id: e for e in result.entries}
        for old in frozen:  # frozen-window entries reproduced verbatim and locked
            new = by_op[old.operation_id]
            assert new.locked and (new.machine_id, new.setup_start, new.end) == (
                old.machine_id,
                old.setup_start,
                old.end,
            )
        for order_id in snap.orders:  # operation sequence within an order
            chain = result.entries_for_order(order_id)
            for a, b in zip(chain, chain[1:], strict=False):
                assert b.setup_start >= a.end
        # setup consistency: every entry's recorded setup covers the changeover from the job before it
        for machine in snap.machines.values():
            state = MachineState(
                machine.machine_id,
                NOW,
                machine.current_setup_family,
                machine.current_material_id,
                set(machine.tooling_configuration),
            )
            for e in result.entries_for_machine(machine.machine_id):
                op, order = snap.operations[e.operation_id], snap.orders[e.order_id]
                needed = compute_setup(op, machine, state, config, order=order, snapshot=snap).minutes
                assert needed <= e.setup_minutes + 1e-6, (e.entry_id, needed, e.setup_minutes)
                apply_entry_to_state(state, e, op, order)


class TestRegistry:
    def test_default_registry_and_errors(self) -> None:
        registry = default_registry()
        assert "rule_based" in registry and "rule_based" in registry.names()
        scheduler = registry.create("rule_based", FrozenClock(NOW))
        assert isinstance(scheduler, RuleBasedScheduler)
        with pytest.raises(NotFoundError):
            registry.create("nope", FrozenClock(NOW))
        custom = SchedulerRegistry()
        custom.register("x", lambda c: RuleBasedScheduler(c, batch_lookahead=0))
        assert list(custom) == ["x"]

    def test_clock_drives_now(self) -> None:
        later = NOW + timedelta(days=1)
        snap = _snapshot([_order("O1")])
        result = _run(snap, scheduler=RuleBasedScheduler(FrozenClock(later)))
        assert result.horizon_start == later and result.entries[0].setup_start == later

    def test_frozen_clock_rejects_naive_datetime(self) -> None:
        naive: datetime = NOW.replace(tzinfo=None)
        with pytest.raises(ValueError):
            FrozenClock(naive)
        assert make_operation("O1").order_id == "O1"
