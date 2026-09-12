"""Batching: pull a same-setup job forward when the rules allow (spec Phase 6/18).

The rule-based scheduler is a list scheduler: it normally takes the head of a
priority-ordered queue. :func:`pick_next` lets it deviate *slightly* to save a
changeover, under rules that all come from ``config.batching``:

* the candidate must share the machine's current setup — the setup family
  (which by definition means "no changeover") or one of the configured
  ``dimensions`` (material, part_family, tool, customer; the other dimensions
  have no machine-state counterpart and are ignored);
* its priority score must be within ``min_priority_gap`` of the head's;
* pulling it forward must delay the head by at most ``max_delay_hours`` of
  machine working time (its setup + run);
* it must never bypass a ``forced_next`` or locked item, and never bypass an
  order whose due date would then be missed (checked with the machine calendar
  for every bypassed item, not only the head).

The queue is scanned in order and the scan stops at the first item outside
the score gap, so the cost is bounded by the gap, not by the queue length.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.domain.config import SchedulingConfig
from app.engines.calendar.calendar import MachineCalendar
from app.engines.constraints.base import MachineState


@dataclass(slots=True, frozen=True)
class BatchCandidate:
    """What the batching rule needs to know about one queued job for a machine."""

    order_id: str
    operation_id: str
    score: float
    duration_minutes: float  # setup + run on this machine (working minutes)
    due_date: datetime | None = None
    setup_family: str | None = None
    material_id: str | None = None
    part_family: str | None = None
    customer_id: str | None = None
    tooling_ids: frozenset[str] = field(default_factory=frozenset)
    forced_next: bool = False
    locked: bool = False


@dataclass(slots=True, frozen=True)
class BatchDecision:
    chosen: BatchCandidate
    reason: str
    pulled_forward: bool = False
    matched_dimensions: tuple[str, ...] = ()
    bypassed: int = 0


def shared_dimensions(candidate: BatchCandidate, state: MachineState, config: SchedulingConfig) -> list[str]:
    """Batching dimensions ``candidate`` shares with the machine's current state (setup family first)."""
    matched: list[str] = []
    if candidate.setup_family is not None and candidate.setup_family == state.current_setup_family:
        matched.append("setup_family")
    for dimension in config.batching.dimensions:
        if dimension == "material":
            if candidate.material_id is not None and candidate.material_id == state.current_material_id:
                matched.append("material")
        elif dimension == "part_family":
            if candidate.part_family is not None and candidate.part_family == state.last_part_family:
                matched.append("part_family")
        elif dimension == "tool":
            if candidate.tooling_ids and candidate.tooling_ids <= state.mounted_tooling:
                matched.append("tool")
        elif (
            dimension == "customer"
            and candidate.customer_id is not None
            and candidate.customer_id == state.last_customer_id
        ):
            matched.append("customer")
    return matched


def _finish(state: MachineState, calendar: MachineCalendar | None, minutes: float) -> datetime:
    if calendar is None:
        return state.next_free + timedelta(minutes=minutes)
    return calendar.add_work_minutes(state.next_free, minutes)


def _due_dates_hold(
    bypassed: Sequence[BatchCandidate],
    extra_minutes: float,
    state: MachineState,
    calendar: MachineCalendar | None,
) -> str | None:
    """None when every bypassed job still meets its due date after ``extra_minutes`` of delay."""
    cumulative = 0.0
    for job in bypassed:
        cumulative += job.duration_minutes
        if job.due_date is None:
            continue
        if _finish(state, calendar, cumulative + extra_minutes) > job.due_date:
            return job.order_id
    return None


def pick_next(
    queue: Sequence[BatchCandidate],
    machine_state: MachineState,
    config: SchedulingConfig,
    *,
    calendar: MachineCalendar | None = None,
) -> BatchDecision | None:
    """Choose the next job for ``machine_state`` from a priority-ordered ``queue``.

    Returns the head unless a later job shares the current setup and every
    batching rule allows pulling it forward. ``None`` when the queue is empty.
    """
    if not queue:
        return None
    head = queue[0]
    rules = config.batching
    if not rules.enabled:
        return BatchDecision(head, "batching disabled; head of queue")
    if head.forced_next or head.locked:
        return BatchDecision(head, "head is forced next / locked; batching skipped")
    head_matches = shared_dimensions(head, machine_state, config)
    if head_matches:
        return BatchDecision(
            head,
            f"head already shares {', '.join(head_matches)} with the current setup",
            False,
            tuple(head_matches),
        )
    for index in range(1, len(queue)):
        candidate = queue[index]
        if candidate.forced_next or candidate.locked:
            break  # never reorder around a forced/locked item
        gap = head.score - candidate.score
        if gap > rules.min_priority_gap:
            break  # queue is priority-ordered: nothing further is close enough
        matched = shared_dimensions(candidate, machine_state, config)
        if not matched:
            continue
        delay_hours = candidate.duration_minutes / 60.0
        if delay_hours > rules.max_delay_hours:
            continue
        violated = _due_dates_hold(queue[:index], candidate.duration_minutes, machine_state, calendar)
        if violated is not None:
            continue
        reason = (
            f"pulled forward ahead of {index} job(s): shares {', '.join(matched)} with the current setup "
            f"on {machine_state.machine_id}; delays {head.order_id} by {delay_hours:.1f} h "
            f"(limit {rules.max_delay_hours:g} h), score gap {gap:.1f} (limit {rules.min_priority_gap:g})"
        )
        return BatchDecision(candidate, reason, True, tuple(matched), index)
    return BatchDecision(head, "no batch candidate within the priority gap / delay limits; head of queue")


__all__ = ["BatchCandidate", "BatchDecision", "pick_next", "shared_dimensions"]
