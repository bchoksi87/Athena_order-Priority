"""Schedule stability rules (spec Phase 11: "do not constantly reshuffle production").

:func:`apply_stability` reconciles a *proposed* schedule with the *previous*
(current) one under :class:`~app.domain.config.StabilityRules`:

* **frozen window** — every previous entry that starts within
  ``frozen_window_minutes`` of ``now`` (or is already running) must keep its
  machine and start time. The scheduler normally reproduces such entries
  through its ``previous_entries`` mechanism; here we *verify*: a frozen
  entry that is missing, moved or re-assigned in the proposal counts as a
  violation and — unless ``restore=False`` — the previous placement is put
  back (marked ``locked``) and a warning is added to the result. When
  ``snapshot`` is given, entries whose operation is finished or whose order is
  closed are ignored (nothing left to protect), and entries whose machine can
  no longer run them (unknown, or DOWN / OFFLINE / MAINTENANCE with no
  scheduled return — the rule the scheduler applies when it excludes a
  machine) are compared like any other entry but never *frozen*: a hard
  event such as a machine failure must not count as a violation of the plan
  it just invalidated.
* **moves** — entries present in both schedules that changed machine or start
  by more than ``tolerance_minutes``; ``added`` / ``removed`` cover the rest.
  ``max_moves_per_replan`` is reported (``max_moves_exceeded``), the decision
  engine turns it into "do not replan" / "requires approval".
* **improvement** — ``improvement_pct`` is the quality-score difference on the
  0..100 scale (``after - before``), the number ``min_improvement_pct`` is
  compared against; ``quality_known`` says whether both scores existed.

Nothing here rebuilds a schedule: restoring a frozen entry can, in
principle, overlap a proposed entry on the same machine. That is exactly the
situation the violation count exposes, and why the decision engine requires
approval when it is non-zero.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

import structlog

from app.core.clock import ensure_utc
from app.domain.config import StabilityRules
from app.domain.results import ScheduleEntry, ScheduleResult
from app.domain.snapshot import PlanningSnapshot
from app.engines.constraints.hard import maintenance_end_after

log = structlog.get_logger(__name__)

#: Start-time tolerance below which an entry is "unchanged" (seconds rounding, not a rule).
DEFAULT_TOLERANCE_MINUTES = 1.0
_MAX_LISTED = 10


@dataclass(slots=True)
class EntryChange:
    operation_id: str
    order_id: str
    kind: str  # "moved" | "added" | "removed" | "frozen_violation"
    previous_machine_id: str | None
    proposed_machine_id: str | None
    previous_start: datetime | None
    proposed_start: datetime | None
    shift_minutes: float | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "order_id": self.order_id,
            "kind": self.kind,
            "previous_machine_id": self.previous_machine_id,
            "proposed_machine_id": self.proposed_machine_id,
            "previous_start": self.previous_start.isoformat() if self.previous_start else None,
            "proposed_start": self.proposed_start.isoformat() if self.proposed_start else None,
            "shift_minutes": self.shift_minutes,
            "reason": self.reason,
        }


@dataclass(slots=True)
class StabilityReport:
    frozen_window_minutes: float
    frozen_entries: int = 0
    frozen_violations: int = 0
    restored_entries: int = 0
    moved_entries: int = 0
    added_entries: int = 0
    removed_entries: int = 0
    unchanged_entries: int = 0
    quality_before: float | None = None
    quality_after: float | None = None
    improvement_pct: float = 0.0
    quality_known: bool = False
    max_moves: int | None = None
    max_moves_exceeded: bool = False
    changes: list[EntryChange] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)

    @property
    def changed_entries(self) -> int:
        return self.moved_entries + self.added_entries + self.removed_entries

    @property
    def changed_order_ids(self) -> list[str]:
        return sorted({c.order_id for c in self.changes})

    def to_dict(self) -> dict[str, Any]:
        return {
            "frozen_window_minutes": self.frozen_window_minutes,
            "frozen_entries": self.frozen_entries,
            "frozen_violations": self.frozen_violations,
            "restored_entries": self.restored_entries,
            "moved_entries": self.moved_entries,
            "added_entries": self.added_entries,
            "removed_entries": self.removed_entries,
            "unchanged_entries": self.unchanged_entries,
            "changed_entries": self.changed_entries,
            "changed_orders": len(self.changed_order_ids),
            "quality_before": self.quality_before,
            "quality_after": self.quality_after,
            "improvement_pct": self.improvement_pct,
            "quality_known": self.quality_known,
            "max_moves": self.max_moves,
            "max_moves_exceeded": self.max_moves_exceeded,
            "violations": list(self.violations),
            "changes": [c.to_dict() for c in self.changes],
        }


def _quality(result: ScheduleResult | None) -> float | None:
    return result.quality.score if result is not None and result.quality is not None else None


def _still_relevant(entry: ScheduleEntry, snapshot: PlanningSnapshot | None) -> bool:
    """False when the entry's work is finished (nothing left to keep in place)."""
    if snapshot is None:
        return True
    order = snapshot.orders.get(entry.order_id)
    if order is None or not order.is_open:
        return False
    op = snapshot.operations.get(entry.operation_id)
    return op is None or not op.is_done


def _machine_can_run(entry: ScheduleEntry, snapshot: PlanningSnapshot | None, now: datetime) -> bool:
    """False when the entry's machine is unknown or inoperable with no scheduled return."""
    if snapshot is None:
        return True
    machine = snapshot.machines.get(entry.machine_id)
    if machine is None:
        return False
    return machine.status.is_operable or maintenance_end_after(machine, now) is not None


def _renumber(entries: list[ScheduleEntry]) -> list[ScheduleEntry]:
    by_machine: dict[str, list[ScheduleEntry]] = defaultdict(list)
    for entry in entries:
        by_machine[entry.machine_id].append(entry)
    ordered: list[ScheduleEntry] = []
    for machine_id in sorted(by_machine):
        seq = sorted(by_machine[machine_id], key=lambda e: (e.setup_start, e.start, e.operation_id))
        for i, entry in enumerate(seq, start=1):
            entry.sequence_on_machine = i
        ordered.extend(seq)
    return ordered


def apply_stability(
    previous: ScheduleResult | None,
    proposed: ScheduleResult,
    now: datetime,
    rules: StabilityRules,
    *,
    snapshot: PlanningSnapshot | None = None,
    restore: bool = True,
    tolerance_minutes: float = DEFAULT_TOLERANCE_MINUTES,
) -> tuple[ScheduleResult, StabilityReport]:
    """Verify the frozen window, count moves and compute the improvement (see module docstring)."""
    now = ensure_utc(now)
    report = StabilityReport(
        frozen_window_minutes=rules.frozen_window_minutes,
        quality_before=_quality(previous),
        quality_after=_quality(proposed),
        max_moves=rules.max_moves_per_replan,
    )
    if report.quality_before is not None and report.quality_after is not None:
        report.improvement_pct = report.quality_after - report.quality_before
        report.quality_known = True
    if previous is None:
        report.added_entries = len(proposed.entries)
        report.changes = [
            EntryChange(
                e.operation_id,
                e.order_id,
                "added",
                None,
                e.machine_id,
                None,
                e.setup_start,
                None,
                "no previous schedule",
            )
            for e in sorted(proposed.entries, key=lambda e: (e.order_id, e.operation_id))
        ]
        return proposed, report

    window_end = now + timedelta(minutes=max(0.0, rules.frozen_window_minutes))
    tolerance = timedelta(minutes=max(0.0, tolerance_minutes))
    proposed_by_op: dict[str, ScheduleEntry] = {e.operation_id: e for e in proposed.entries}
    previous_by_op: dict[str, ScheduleEntry] = {}
    for entry in previous.entries:
        if ensure_utc(entry.end) <= now or not _still_relevant(entry, snapshot):
            continue  # finished or no longer pending: nothing to protect
        previous_by_op.setdefault(entry.operation_id, entry)

    restored: dict[str, ScheduleEntry] = {}
    for op_id in sorted(previous_by_op):
        old = previous_by_op[op_id]
        new = proposed_by_op.get(op_id)
        frozen = ensure_utc(old.setup_start) < window_end and _machine_can_run(old, snapshot, now)
        if frozen:
            report.frozen_entries += 1
        if new is None:
            reason = "dropped from the proposed schedule"
            kind = "frozen_violation" if frozen else "removed"
            report.changes.append(
                EntryChange(
                    op_id, old.order_id, kind, old.machine_id, None, old.setup_start, None, None, reason
                )
            )
            if frozen:
                report.frozen_violations += 1
                report.violations.append(f"{old.order_id}/{op_id}: {reason}")
                if restore:
                    restored[op_id] = replace(old, locked=True)
            else:
                report.removed_entries += 1
            continue
        shift = (ensure_utc(new.setup_start) - ensure_utc(old.setup_start)).total_seconds() / 60.0
        machine_changed = new.machine_id != old.machine_id
        moved = machine_changed or abs(ensure_utc(new.setup_start) - ensure_utc(old.setup_start)) > tolerance
        if not moved:
            report.unchanged_entries += 1
            continue
        reason = (
            f"machine {old.machine_id} → {new.machine_id}"
            if machine_changed
            else f"start shifted {shift:+.0f} min"
        )
        if frozen:
            report.frozen_violations += 1
            report.violations.append(f"{old.order_id}/{op_id}: {reason} inside the frozen window")
            report.changes.append(
                EntryChange(
                    op_id,
                    old.order_id,
                    "frozen_violation",
                    old.machine_id,
                    new.machine_id,
                    old.setup_start,
                    new.setup_start,
                    shift,
                    reason,
                )
            )
            if restore:
                restored[op_id] = replace(old, locked=True)
            continue
        report.moved_entries += 1
        report.changes.append(
            EntryChange(
                op_id,
                old.order_id,
                "moved",
                old.machine_id,
                new.machine_id,
                old.setup_start,
                new.setup_start,
                shift,
                reason,
            )
        )
    for op_id in sorted(set(proposed_by_op) - set(previous_by_op)):
        new = proposed_by_op[op_id]
        report.added_entries += 1
        report.changes.append(
            EntryChange(
                op_id,
                new.order_id,
                "added",
                None,
                new.machine_id,
                None,
                new.setup_start,
                None,
                "new in proposed schedule",
            )
        )
    report.changes.sort(key=lambda c: (c.order_id, c.operation_id, c.kind))
    if rules.max_moves_per_replan is not None and report.moved_entries > rules.max_moves_per_replan:
        report.max_moves_exceeded = True

    result = proposed
    if restored:
        result = replace(
            proposed,
            entries=_renumber(
                [restored.get(e.operation_id, e) for e in proposed.entries]
                + [r for op_id, r in sorted(restored.items()) if op_id not in proposed_by_op]
            ),
            warnings=[*proposed.warnings],
        )
        listed = ", ".join(report.violations[:_MAX_LISTED])
        more = (
            f" (+{len(report.violations) - _MAX_LISTED} more)" if len(report.violations) > _MAX_LISTED else ""
        )
        result.warnings.append(
            f"{len(restored)} frozen-window entr{'y' if len(restored) == 1 else 'ies'} restored from the "
            f"previous schedule: {listed}{more}"
        )
        report.restored_entries = len(restored)
        log.warning(
            "replanning.frozen_violations",
            violations=report.frozen_violations,
            restored=len(restored),
            window_minutes=rules.frozen_window_minutes,
        )
    return result, report


__all__ = ["DEFAULT_TOLERANCE_MINUTES", "EntryChange", "StabilityReport", "apply_stability"]
