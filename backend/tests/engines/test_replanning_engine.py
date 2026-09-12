"""ReplanningEngine: trigger filter, decision matrix, approval flag, compare view."""

from __future__ import annotations

from datetime import datetime

from app.domain.config import ReplanningConfig, StabilityRules
from app.domain.enums import ReplanTriggerType
from app.domain.results import ScheduleMetrics
from app.engines.replanning import ReplanEvent, ReplanningEngine, hard_events, manual_event
from tests.engines.factories import NOW, at, make_entry, make_quality_schedule

STABILITY = StabilityRules(frozen_window_minutes=30.0, min_improvement_pct=3.0)


def _event(
    type: ReplanTriggerType, entity_id: str = "X", at_: datetime = NOW, **details: object
) -> ReplanEvent:
    return ReplanEvent(
        type,
        "machine" if type in (ReplanTriggerType.MACHINE_DOWN, ReplanTriggerType.MACHINE_UP) else "order",
        entity_id,
        at_,
        f"{type.value} {entity_id}",
        order_id=entity_id
        if type not in (ReplanTriggerType.MACHINE_DOWN, ReplanTriggerType.MACHINE_UP)
        else None,
        machine_id=entity_id if type == ReplanTriggerType.MACHINE_DOWN else None,
        details=details,
    )


def _current(score: float = 70.0):
    return make_quality_schedule(
        [
            make_entry("O0", "CNC-01", at(minutes=10), run_minutes=60),
            make_entry("O1", "CNC-02", at(minutes=10), run_minutes=60),
            make_entry("O2", "CNC-01", at(hours=2), run_minutes=60, sequence_on_machine=2),
            make_entry("O3", "CNC-02", at(hours=2), run_minutes=60, sequence_on_machine=2),
        ],
        quality_score=score,
    )


def _proposed(score: float, *, moves: int = 2, violate_frozen: bool = False):
    entries = [
        make_entry("O0", "CNC-02" if violate_frozen else "CNC-01", at(minutes=10), run_minutes=60),
        make_entry("O1", "CNC-02", at(minutes=10), run_minutes=60),
        make_entry(
            "O2", "CNC-02" if moves >= 1 else "CNC-01", at(hours=2), run_minutes=60, sequence_on_machine=2
        ),
        make_entry(
            "O3", "CNC-01" if moves >= 2 else "CNC-02", at(hours=2), run_minutes=60, sequence_on_machine=2
        ),
    ]
    return make_quality_schedule(entries, quality_score=score)


def _engine(**config: object) -> ReplanningEngine:
    return ReplanningEngine(ReplanningConfig(**config), STABILITY)  # type: ignore[arg-type]


# ------------------------------------------------------------- triggering


def test_should_trigger_follows_trigger_on_list() -> None:
    engine = _engine(trigger_on=["new_order", "machine_down"])
    assert engine.should_trigger([_event(ReplanTriggerType.NEW_ORDER)])
    assert not engine.should_trigger([_event(ReplanTriggerType.MATERIAL_ARRIVED)])
    assert engine.should_trigger(
        [_event(ReplanTriggerType.MATERIAL_ARRIVED), _event(ReplanTriggerType.MACHINE_DOWN)]
    )
    assert engine.should_trigger([manual_event(NOW, "planner asked")])  # MANUAL always triggers
    assert not engine.should_trigger([])
    assert not _engine(enabled=False).should_trigger([_event(ReplanTriggerType.NEW_ORDER)])
    assert engine.triggering_types(
        [_event(ReplanTriggerType.MATERIAL_ARRIVED), _event(ReplanTriggerType.NEW_ORDER)]
    ) == ["new_order"]


# --------------------------------------------------------- decision matrix


def test_no_current_schedule_replans() -> None:
    decision = _engine().evaluate([], None, _proposed(50.0), NOW)
    assert decision.should_replan and decision.requires_approval
    assert decision.changed_entries == 4 and decision.frozen_violations == 0
    assert decision.reason.startswith("no current schedule")
    assert decision.triggers == []


def test_small_improvement_keeps_current_schedule() -> None:
    events = [_event(ReplanTriggerType.MATERIAL_ARRIVED, "AL")]
    decision = _engine().evaluate(events, _current(70.0), _proposed(72.0), NOW)
    assert not decision.should_replan and not decision.requires_approval
    assert decision.improvement_pct == 2.0 and decision.changed_entries == 2
    assert "below the 3 threshold" in decision.reason and decision.triggers == ["material_arrived"]


def test_large_improvement_replans_with_approval_per_config() -> None:
    decision = _engine().evaluate([], _current(70.0), _proposed(80.0), NOW)
    assert decision.should_replan and decision.requires_approval and decision.improvement_pct == 10.0
    assert "quality 70.0 → 80.0 (+10.0 ≥ 3 required)" in decision.reason
    auto = _engine(require_approval=False, significant_change_orders=10).evaluate(
        [], _current(70.0), _proposed(80.0), NOW
    )
    assert auto.should_replan and not auto.requires_approval


def test_identical_proposal_never_replans() -> None:
    decision = _engine().evaluate([], _current(70.0), _proposed(99.0, moves=0), NOW)
    assert not decision.should_replan and decision.changed_entries == 0
    assert "identical" in decision.reason


def test_hard_event_forces_replan_despite_no_improvement() -> None:
    events = [_event(ReplanTriggerType.MACHINE_DOWN, "CNC-01")]
    decision = _engine().evaluate(events, _current(70.0), _proposed(60.0), NOW)
    assert decision.should_replan and decision.improvement_pct == -10.0
    assert "infeasible" in decision.reason and "CNC-01 is down with 2 scheduled order(s)" in decision.reason
    # a machine with nothing scheduled on it is not a hard event
    idle = _engine().evaluate(
        [_event(ReplanTriggerType.MACHINE_DOWN, "CNC-09")], _current(70.0), _proposed(60.0), NOW
    )
    assert not idle.should_replan


def test_hard_event_classification() -> None:
    current = _current()
    events = [
        _event(ReplanTriggerType.NEW_ORDER, "N1", expedited=True),
        _event(ReplanTriggerType.NEW_ORDER, "N2"),
        _event(ReplanTriggerType.NEW_ORDER, "N3", overdue=True, forced_next=True),
        _event(ReplanTriggerType.REWORK, "O2"),
        _event(ReplanTriggerType.QUALITY_FAILURE, "O9"),
        _event(ReplanTriggerType.PRODUCTION_DELAY, "O3", overdue=True),
        _event(ReplanTriggerType.PRODUCTION_DELAY, "O1", overdue=False),
        _event(ReplanTriggerType.MACHINE_DOWN, "CNC-02"),
    ]
    hard = hard_events(events, current, NOW)
    assert [e.entity_id for e, _ in hard] == ["N1", "N3", "O2", "O3", "CNC-02"]
    assert hard[1][1] == "new order N3 is forced_next and overdue"
    assert hard_events(events, None, NOW) == [
        (events[0], "new order N1 is expedited"),
        (events[2], hard[1][1]),
    ]


def test_frozen_violation_and_significant_change_force_approval() -> None:
    engine = _engine(require_approval=False, significant_change_orders=10)
    decision = engine.evaluate([], _current(70.0), _proposed(80.0, violate_frozen=True), NOW)
    assert decision.should_replan and decision.requires_approval and decision.frozen_violations == 1
    assert (
        "1 frozen-window violation(s)" in decision.reason
        and "approval required: frozen-window" in decision.reason
    )
    significant = _engine(require_approval=False, significant_change_orders=2)
    decision2 = significant.evaluate([], _current(70.0), _proposed(80.0), NOW)
    assert decision2.requires_approval and "significant change (2 ≥ 2 orders)" in decision2.reason


def test_max_moves_blocks_soft_replan_but_not_hard() -> None:
    engine = ReplanningEngine(
        ReplanningConfig(), StabilityRules(frozen_window_minutes=30.0, max_moves_per_replan=1)
    )
    soft = engine.evaluate([], _current(70.0), _proposed(90.0), NOW)
    assert not soft.should_replan and "limit 1" in soft.reason
    hard = engine.evaluate(
        [_event(ReplanTriggerType.MACHINE_DOWN, "CNC-01")], _current(70.0), _proposed(90.0), NOW
    )
    assert hard.should_replan and hard.requires_approval


def test_unknown_quality_accepts_changes() -> None:
    current = _current()
    current.quality = None
    decision = _engine().evaluate([], current, _proposed(80.0), NOW)
    assert decision.should_replan and decision.improvement_pct == 0.0
    assert "quality score unavailable" in decision.reason


# ---------------------------------------------------------------- compare


def test_compare_reuses_compare_schedules_and_adds_changes() -> None:
    current, proposed = _current(70.0), _proposed(80.0)
    current.metrics = ScheduleMetrics(on_time_pct=87.0, late_orders=3, scheduled_orders=4)
    proposed.metrics = ScheduleMetrics(on_time_pct=94.0, late_orders=1, scheduled_orders=4)
    out = _engine().compare(current, proposed)
    assert out["on_time_pct"] == {"label": "On-time delivery", "before": 87.0, "after": 94.0, "delta": 7.0}
    assert out["quality_score"]["delta"] == 10.0
    assert out["changes"]["moved_entries"] == 2 and out["changes"]["changed_orders"] == ["O2", "O3"]
    assert out["summary"].startswith("Schedule quality: 70 → 80; On-time delivery: 87% → 94%")
    assert out["summary"].endswith("2 entries change (2 moved, 0 added, 0 removed)")
    assert _engine().describe()["min_improvement_pct"] == 3.0
    report = _engine().stability_report(current, proposed, NOW)
    assert report.moved_entries == 2
