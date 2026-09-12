"""apply_stability: frozen window verification, move counting, improvement."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.domain.config import StabilityRules
from app.domain.enums import OperationStatus, OrderStatus
from app.engines.replanning import apply_stability
from tests.engines.factories import NOW, at, make_entry, make_plant_snapshot, make_quality_schedule

RULES = StabilityRules(frozen_window_minutes=30.0, min_improvement_pct=3.0)


def _previous() -> list:
    return [
        make_entry("O0", "CNC-01", at(minutes=10), run_minutes=60),  # frozen (starts in 10 min)
        make_entry("O1", "CNC-02", at(minutes=5), run_minutes=60),  # frozen
        make_entry("O2", "CNC-01", at(hours=2), run_minutes=60, sequence_on_machine=2),  # free to move
        make_entry("O3", "CNC-02", at(hours=2), run_minutes=60, sequence_on_machine=2),  # free to move
        make_entry("O4", "CNC-01", at(hours=-3), run_minutes=60),  # finished before now: ignored
    ]


def test_no_previous_schedule_everything_is_added() -> None:
    proposed = make_quality_schedule(_previous()[:2], quality_score=80.0)
    result, report = apply_stability(None, proposed, NOW, RULES)
    assert result is proposed
    assert report.added_entries == 2 and report.changed_entries == 2 and report.frozen_violations == 0
    assert report.improvement_pct == 0.0 and report.quality_known is False
    assert report.changed_order_ids == ["O0", "O1"]


def test_unchanged_schedule_reports_nothing() -> None:
    previous = make_quality_schedule(_previous(), quality_score=70.0)
    proposed = make_quality_schedule(_previous()[:4], quality_score=72.5)
    result, report = apply_stability(previous, proposed, NOW, RULES)
    assert result is proposed
    assert report.frozen_entries == 2 and report.frozen_violations == 0
    assert report.moved_entries == report.added_entries == report.removed_entries == 0
    assert report.unchanged_entries == 4
    assert report.improvement_pct == pytest.approx(2.5) and report.quality_known


def test_moves_outside_the_window_are_counted_not_violations() -> None:
    previous = make_quality_schedule(_previous(), quality_score=70.0)
    proposed = make_quality_schedule(
        [
            *_previous()[:2],
            make_entry("O2", "CNC-02", at(hours=2), run_minutes=60, sequence_on_machine=2),  # machine change
            make_entry("O3", "CNC-02", at(hours=3), run_minutes=60, sequence_on_machine=2),  # moved by 1 h
            make_entry("O5", "CNC-01", at(hours=5), run_minutes=60, sequence_on_machine=3),  # new
        ],
        quality_score=80.0,
    )
    result, report = apply_stability(previous, proposed, NOW, RULES)
    assert result is proposed
    assert (report.moved_entries, report.added_entries, report.removed_entries) == (2, 1, 0)
    assert report.frozen_violations == 0 and report.changed_entries == 3
    kinds = {c.operation_id: (c.kind, c.reason) for c in report.changes}
    assert kinds["O2-op1"] == ("moved", "machine CNC-01 → CNC-02")
    assert kinds["O3-op1"][0] == "moved" and "+60 min" in kinds["O3-op1"][1]
    assert kinds["O5-op1"][0] == "added"
    assert report.to_dict()["changed_orders"] == 3


def test_frozen_entries_are_restored_and_violations_counted() -> None:
    previous = make_quality_schedule(_previous(), quality_score=70.0)
    proposed = make_quality_schedule(
        [
            make_entry("O0", "CNC-02", at(minutes=10), run_minutes=60),  # frozen entry moved machine
            # O1 dropped entirely
            make_entry("O2", "CNC-01", at(hours=2), run_minutes=60),
            make_entry("O3", "CNC-02", at(hours=2), run_minutes=60),
        ],
        quality_score=90.0,
    )
    result, report = apply_stability(previous, proposed, NOW, RULES)
    assert report.frozen_entries == 2 and report.frozen_violations == 2 and report.restored_entries == 2
    assert report.moved_entries == 0
    assert len(report.violations) == 2 and any("dropped" in v for v in report.violations)
    restored = {e.operation_id: e for e in result.entries}
    assert restored["O0-op1"].machine_id == "CNC-01" and restored["O0-op1"].locked
    assert restored["O1-op1"].machine_id == "CNC-02" and restored["O1-op1"].locked
    assert [e.sequence_on_machine for e in result.entries_for_machine("CNC-01")] == [1, 2]
    assert result.warnings and "restored" in result.warnings[0]
    assert proposed.entries[0].machine_id == "CNC-02"  # proposal object untouched
    assert result.quality is proposed.quality

    _, no_restore = apply_stability(previous, proposed, NOW, RULES, restore=False)
    assert no_restore.frozen_violations == 2 and no_restore.restored_entries == 0


def test_small_shift_inside_tolerance_is_not_a_violation() -> None:
    previous = make_quality_schedule(_previous()[:2])
    proposed = make_quality_schedule(
        [make_entry("O0", "CNC-01", at(minutes=10) + timedelta(seconds=20), run_minutes=60), _previous()[1]]
    )
    _, report = apply_stability(previous, proposed, NOW, RULES)
    assert report.frozen_violations == 0 and report.unchanged_entries == 2
    _, strict = apply_stability(previous, proposed, NOW, RULES, tolerance_minutes=0.1)
    assert strict.frozen_violations == 1


def test_finished_or_closed_work_is_not_frozen() -> None:
    snapshot = make_plant_snapshot(4)
    snapshot.orders["O0"].order_status = OrderStatus.COMPLETED
    snapshot.operations["O1-op1"].operation_status = OperationStatus.COMPLETED
    previous = make_quality_schedule(_previous())
    proposed = make_quality_schedule(_previous()[2:4])  # O0 and O1 gone
    _, report = apply_stability(previous, proposed, NOW, RULES, snapshot=snapshot)
    assert report.frozen_entries == 0 and report.frozen_violations == 0 and report.removed_entries == 0
    _, without = apply_stability(previous, proposed, NOW, RULES)
    assert without.frozen_violations == 2


def test_window_size_and_max_moves() -> None:
    previous = make_quality_schedule(_previous(), quality_score=50.0)
    proposed = make_quality_schedule(
        [
            *_previous()[:2],
            make_entry("O2", "CNC-01", at(hours=4), run_minutes=60, sequence_on_machine=2),
            make_entry("O3", "CNC-02", at(hours=4), run_minutes=60, sequence_on_machine=2),
        ],
        quality_score=40.0,
    )
    wide = StabilityRules(frozen_window_minutes=180.0, max_moves_per_replan=1)
    _, report = apply_stability(previous, proposed, NOW, wide)
    assert report.frozen_entries == 4 and report.frozen_violations == 2  # O2/O3 now inside the window
    narrow = StabilityRules(frozen_window_minutes=0.0, max_moves_per_replan=1)
    _, report2 = apply_stability(previous, proposed, NOW, narrow)
    assert report2.frozen_entries == 0 and report2.moved_entries == 2 and report2.max_moves_exceeded
    assert report2.improvement_pct == pytest.approx(-10.0)
