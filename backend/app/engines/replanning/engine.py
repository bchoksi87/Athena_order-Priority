"""ReplanningEngine (DESIGN_CONTRACT §6.6, spec Phase 11).

Decision flow for a proposed schedule:

1. ``should_trigger(events)`` — is any event type in ``ReplanningConfig.trigger_on``
   (``MANUAL`` and ``SCHEDULED`` always count while replanning is enabled)?
2. ``evaluate(events, current, proposed, now)``:

   * no current schedule → replan (nothing to keep stable);
   * :func:`~app.engines.replanning.stability.apply_stability` verifies the
     frozen window and measures moves and improvement;
   * a **hard event** makes the current plan infeasible → replan regardless
     of the improvement threshold: a machine that went down with work still
     scheduled on it, a new order that is forced-next / expedited / already
     overdue, quality failure or rework on a scheduled order, a production
     delay that pushes a scheduled order past its due date;
   * otherwise replan only when the proposal changes something *and* the
     quality improvement reaches ``StabilityRules.min_improvement_pct``
     (percentage points on the 0..100 quality score), and does not exceed
     ``max_moves_per_replan``;
   * ``requires_approval`` follows ``ReplanningConfig.require_approval``; it
     is also forced when the frozen window was violated, the move cap was
     exceeded, or at least ``significant_change_orders`` orders changed.

3. ``compare(current, proposed)`` — the planner's old-vs-new view, reusing
   :func:`app.engines.scheduling.quality.compare_schedules` plus the change
   counts.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

import structlog

from app.core.clock import ensure_utc
from app.domain.config import ReplanningConfig, StabilityRules
from app.domain.enums import ReplanTriggerType
from app.domain.results import ReplanDecision, ScheduleResult
from app.domain.snapshot import PlanningSnapshot
from app.engines.replanning.stability import StabilityReport, apply_stability
from app.engines.replanning.triggers import ReplanEvent
from app.engines.scheduling.quality import compare_schedules

log = structlog.get_logger(__name__)

#: Trigger types that fire whenever replanning is enabled, whatever ``trigger_on`` says.
ALWAYS_TRIGGER: frozenset[ReplanTriggerType] = frozenset(
    {ReplanTriggerType.MANUAL, ReplanTriggerType.SCHEDULED}
)
_MAX_LISTED = 5


def _scheduled_orders(schedule: ScheduleResult | None, now: datetime) -> dict[str, set[str]]:
    """order_id → machines with entries of that order still ahead of ``now``."""
    out: dict[str, set[str]] = defaultdict(set)
    if schedule is None:
        return out
    for entry in schedule.entries:
        if ensure_utc(entry.end) > now:
            out[entry.order_id].add(entry.machine_id)
    return out


def hard_events(
    events: Iterable[ReplanEvent], current: ScheduleResult | None, now: datetime
) -> list[tuple[ReplanEvent, str]]:
    """Events that make ``current`` infeasible, each with the reason it counts as hard."""
    now = ensure_utc(now)
    by_order = _scheduled_orders(current, now)
    machines_with_work: dict[str, int] = defaultdict(int)
    for machines in by_order.values():
        for machine_id in machines:
            machines_with_work[machine_id] += 1
    hard: list[tuple[ReplanEvent, str]] = []
    for event in events:
        d = event.details
        if event.type == ReplanTriggerType.MACHINE_DOWN:
            machine_id = event.machine_id or event.entity_id
            n = machines_with_work.get(machine_id, 0)
            if n > 0:
                hard.append((event, f"{machine_id} is down with {n} scheduled order(s) on it"))
        elif event.type == ReplanTriggerType.NEW_ORDER:
            flags = [k for k in ("forced_next", "expedited", "overdue") if d.get(k)]
            if flags:
                hard.append((event, f"new order {event.entity_id} is {' and '.join(flags)}"))
        elif event.type in (ReplanTriggerType.QUALITY_FAILURE, ReplanTriggerType.REWORK):
            order_id = event.order_id or event.entity_id
            if order_id in by_order:
                hard.append((event, f"{event.type.value.replace('_', ' ')} on scheduled order {order_id}"))
        elif event.type == ReplanTriggerType.PRODUCTION_DELAY:
            order_id = event.order_id or event.entity_id
            if d.get("overdue") and order_id in by_order:
                hard.append((event, f"delay pushes scheduled order {order_id} past its due date"))
    return hard


class ReplanningEngine:
    def __init__(self, config: ReplanningConfig, stability: StabilityRules) -> None:
        self.config = config
        self.stability = stability

    # ------------------------------------------------------------- trigger
    def should_trigger(self, events: Iterable[ReplanEvent]) -> bool:
        """True when replanning is enabled and any event type is configured to trigger it."""
        if not self.config.enabled:
            return False
        wanted = {t.lower() for t in self.config.trigger_on}
        return any(e.type in ALWAYS_TRIGGER or e.type.value in wanted for e in events)

    def triggering_types(self, events: Iterable[ReplanEvent]) -> list[str]:
        wanted = {t.lower() for t in self.config.trigger_on}
        return sorted({e.type.value for e in events if e.type in ALWAYS_TRIGGER or e.type.value in wanted})

    # ------------------------------------------------------------ evaluate
    def evaluate(
        self,
        events: Sequence[ReplanEvent],
        current_schedule: ScheduleResult | None,
        proposed_schedule: ScheduleResult,
        now: datetime,
        *,
        snapshot: PlanningSnapshot | None = None,
    ) -> ReplanDecision:
        """Decide whether ``proposed_schedule`` should replace ``current_schedule``."""
        now = ensure_utc(now)
        _, report = apply_stability(
            current_schedule, proposed_schedule, now, self.stability, snapshot=snapshot
        )
        triggers = sorted({e.type.value for e in events})
        hard = hard_events(events, current_schedule, now)
        changed_orders = len(report.changed_order_ids)
        significant = changed_orders >= self.config.significant_change_orders > 0

        reasons: list[str] = []
        if current_schedule is None:
            should = True
            reasons.append("no current schedule")
        elif hard:
            should = True
            listed = "; ".join(r for _, r in hard[:_MAX_LISTED])
            more = f" (+{len(hard) - _MAX_LISTED} more)" if len(hard) > _MAX_LISTED else ""
            reasons.append(f"current schedule infeasible: {listed}{more}")
        elif report.changed_entries == 0:
            should = False
            reasons.append("proposed schedule is identical to the current one")
        elif report.max_moves_exceeded:
            should = False
            reasons.append(
                f"would move {report.moved_entries} entries (limit {self.stability.max_moves_per_replan})"
            )
        elif not report.quality_known:
            should = True
            reasons.append("quality score unavailable; accepting proposal on changes alone")
        elif report.improvement_pct >= self.stability.min_improvement_pct:
            should = True
            reasons.append(
                f"quality {report.quality_before:.1f} → {report.quality_after:.1f} "
                f"(+{report.improvement_pct:.1f} ≥ {self.stability.min_improvement_pct:g} required)"
            )
        else:
            should = False
            reasons.append(
                f"improvement {report.improvement_pct:+.1f} below the {self.stability.min_improvement_pct:g} "
                "threshold; keeping the current schedule"
            )
        if should:
            reasons.append(
                f"{report.changed_entries} entries change ({report.moved_entries} moved, "
                f"{report.added_entries} added, {report.removed_entries} removed; {changed_orders} orders)"
            )
        if report.frozen_violations:
            reasons.append(f"{report.frozen_violations} frozen-window violation(s)")
        if triggers:
            reasons.append("triggers: " + ", ".join(triggers))

        requires_approval = should and (
            self.config.require_approval
            or report.frozen_violations > 0
            or report.max_moves_exceeded
            or significant
        )
        if should and requires_approval and not self.config.require_approval:
            why = (
                "frozen-window violations"
                if report.frozen_violations
                else "move limit exceeded"
                if report.max_moves_exceeded
                else f"significant change ({changed_orders} ≥ {self.config.significant_change_orders} orders)"
            )
            reasons.append(f"approval required: {why}")
        decision = ReplanDecision(
            should_replan=should,
            reason="; ".join(reasons),
            improvement_pct=report.improvement_pct,
            changed_entries=report.changed_entries,
            frozen_violations=report.frozen_violations,
            requires_approval=requires_approval,
            triggers=triggers,
        )
        log.info(
            "replanning.decision",
            should_replan=should,
            requires_approval=requires_approval,
            improvement_pct=round(report.improvement_pct, 2),
            changed_entries=report.changed_entries,
            frozen_violations=report.frozen_violations,
            hard_events=len(hard),
            triggers=triggers,
        )
        return decision

    def stability_report(
        self,
        current: ScheduleResult | None,
        proposed: ScheduleResult,
        now: datetime,
        *,
        snapshot: PlanningSnapshot | None = None,
    ) -> StabilityReport:
        return apply_stability(current, proposed, ensure_utc(now), self.stability, snapshot=snapshot)[1]

    # ------------------------------------------------------------- compare
    def compare(self, current: ScheduleResult, proposed: ScheduleResult) -> dict[str, Any]:
        """Old-vs-new view: metric pairs (``compare_schedules``) plus what moved."""
        out = compare_schedules(current, proposed)
        _, report = apply_stability(
            current, proposed, ensure_utc(proposed.horizon_start), self.stability, restore=False
        )
        out["changes"] = {
            "moved_entries": report.moved_entries,
            "added_entries": report.added_entries,
            "removed_entries": report.removed_entries,
            "unchanged_entries": report.unchanged_entries,
            "changed_orders": report.changed_order_ids,
            "frozen_violations": report.frozen_violations,
            "entries": [c.to_dict() for c in report.changes],
        }
        out["summary"] = (
            f"{out['summary']}; {report.changed_entries} entries change "
            f"({report.moved_entries} moved, {report.added_entries} added, {report.removed_entries} removed)"
        )
        return out

    def describe(self) -> dict[str, Any]:
        return {
            "enabled": self.config.enabled,
            "trigger_on": list(self.config.trigger_on),
            "require_approval": self.config.require_approval,
            "significant_change_orders": self.config.significant_change_orders,
            "frozen_window_minutes": self.stability.frozen_window_minutes,
            "min_improvement_pct": self.stability.min_improvement_pct,
            "max_moves_per_replan": self.stability.max_moves_per_replan,
        }


__all__ = ["ALWAYS_TRIGGER", "ReplanningEngine", "hard_events"]
