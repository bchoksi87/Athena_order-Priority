"""Machine ranking for one operation (spec Phase 35).

For every eligible machine the ranking computes, with the *same* code paths the
scheduler uses to place the job:

* slot            = :func:`find_slot`: the earliest *gap* of the machine's
  :class:`~app.engines.scheduling.timeline.MachineTimeline` the job fits into
  (release, calendar working time, reserved lock windows and the timeline's
  successor rule permitting), else the tail
  ``max(state.next_free, machine.available_from, now, release)``; without a
  timeline (or with ``tail_only``) only the tail is considered;
* setup minutes   = :func:`~app.engines.scheduling.setup.compute_setup` (family /
  material factors, unmounted tooling) against the state the job actually
  follows - the slot before the gap, or the tail state;
* run minutes     = ``op.run_minutes_on(machine)`` (machine-specific cycle time
  and efficiency); ``None`` rejects the machine with "cycle time unknown";
* expected end    = calendar arithmetic (non-working time skipped);
* soft cost       = sum of the constraint engine's soft penalties (minute-equivalents);
* total cost      = minutes from ``now`` to the expected end + soft cost.

Candidates are sorted by ``(total cost, preferred_rank, machine_id)`` so ties
are broken deterministically. The first candidate is *recommended* and its
reasons are phrased as in the spec ("Expected completion 2.3 hours earlier
than next best", "No additional tooling setup", "Machine currently
available", "Lower downstream impact"). Reasons are rendered from the numbers
just computed, never written independently.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.core.clock import ensure_utc
from app.domain.config import SchedulingConfig
from app.domain.models import Machine, Operation, Order, TimeWindow
from app.domain.results import EligibilityResult, MachineCandidate, MachineRecommendation
from app.domain.snapshot import PlanningSnapshot
from app.engines.calendar.calendar import MachineCalendar
from app.engines.constraints.base import ConstraintContext, MachineState
from app.engines.constraints.engine import ConstraintEngine
from app.engines.constraints.hard import required_tooling_ids
from app.engines.constraints.soft import SetupEstimate
from app.engines.scheduling.setup import compute_setup, describe_setup
from app.engines.scheduling.timeline import MachineTimeline, successor_setup_holds

CYCLE_TIME_UNKNOWN = "cycle time unknown"
_WORK_EPS = 1e-6  # working-minute tolerance (matches the calendar's own epsilon)
NO_CALENDAR = "no working calendar for this machine"
NOT_INITIALISED = "machine excluded from this run (not operable and no known return)"


@dataclass(slots=True, frozen=True)
class Placement:
    """Where a job lands on a calendar: setup, run and end instants (UTC)."""

    setup_start: datetime
    start: datetime
    end: datetime


def place_on_calendar(
    calendar: MachineCalendar,
    earliest: datetime,
    setup_minutes: float,
    run_minutes: float,
    reserved: Sequence[TimeWindow] = (),
) -> Placement:
    """Earliest placement at or after ``earliest`` avoiding non-working time and ``reserved`` windows.

    ``reserved`` must be sorted by start. Each retry moves past the conflicting
    window's end, so the loop is bounded by the number of windows.
    """
    t = ensure_utc(earliest)
    for _ in range(len(reserved) + 1):
        setup_start = calendar.next_working_time(t)
        start = calendar.add_work_minutes(setup_start, setup_minutes) if setup_minutes > 0 else setup_start
        end = calendar.add_work_minutes(start, run_minutes) if run_minutes > 0 else start
        conflict = next((w for w in reserved if w.start < end and setup_start < w.end), None)
        if conflict is None:
            return Placement(setup_start, start, end)
        t = conflict.end
    return Placement(setup_start, start, end)  # pragma: no cover - defensive


def place_span(
    calendar: MachineCalendar,
    earliest: datetime,
    minutes: float,
    reserved: Sequence[TimeWindow] = (),
) -> tuple[datetime, datetime]:
    """``(setup_start, end)`` of a job of ``minutes`` working minutes: the cheap form of
    :func:`place_on_calendar` used while *ranking* (one calendar walk instead of two).

    Working time is additive on a calendar, so the end equals the one produced by
    placing setup and run separately; the intermediate run start is only computed for
    the machine that is finally chosen.
    """
    t = ensure_utc(earliest)
    setup_start = end = t
    for _ in range(len(reserved) + 1):
        setup_start = calendar.next_working_time(t)
        end = calendar.add_work_minutes(setup_start, minutes) if minutes > 0 else setup_start
        conflict = next((w for w in reserved if w.start < end and setup_start < w.end), None)
        if conflict is None:
            break
        t = conflict.end
    return setup_start, end


@dataclass(slots=True)
class SlotPlacement:
    """Where :func:`find_slot` put a job: instants, setup estimate and the state it follows."""

    setup_start: datetime
    end: datetime
    estimate: SetupEstimate
    state: MachineState  # predecessor view the setup was computed against
    in_gap: bool  # False = appended after the machine's last job
    gap_end: datetime | None = None  # start of the following slot when ``in_gap``


def find_slot(
    op: Operation,
    order: Order | None,
    machine: Machine,
    state: MachineState,
    calendar: MachineCalendar,
    config: SchedulingConfig,
    floor: datetime,
    run_minutes: float,
    reserved: Sequence[TimeWindow] = (),
    *,
    timeline: MachineTimeline | None = None,
    snapshot: PlanningSnapshot | None = None,
    tail_only: bool = False,
) -> SlotPlacement:
    """Earliest feasible slot for ``op`` on ``machine`` at or after ``floor``.

    Gaps of ``timeline`` are probed in time order: the timeline's successor
    rule is checked first (O(1)), then the setup against the slot before the
    gap; the job fits when ``setup + run`` working minutes are available from
    ``max(floor, gap start)`` to the gap end (the next slot's setup start) -
    decided from the gap's recorded working minutes when the job can start at
    the gap start and there are no reserved windows, else by a calendar walk.
    The calendar is walked once for the slot finally taken. Without a fitting
    gap the job goes after the tail.
    """
    floor = ensure_utc(floor)
    if timeline is not None and not tail_only:
        family = op.setup_family
        material = op.material_id or (order.required_material_id if order is not None else None)
        for gap in timeline.gaps_from(floor):
            if gap.work_minutes < run_minutes or not successor_setup_holds(gap, family, material, config):
                continue
            candidate = max(floor, gap.start)
            pred = timeline.state_before(gap, state)
            estimate = compute_setup(op, machine, pred, config, order=order, snapshot=snapshot, at=candidate)
            needed = estimate.minutes + run_minutes
            if gap.work_minutes + _WORK_EPS < needed:
                continue
            setup_start, end = place_span(calendar, candidate, needed, reserved)
            if (candidate > gap.start or reserved) and end > gap.end:
                continue
            return SlotPlacement(setup_start, end, estimate, pred, True, gap.end)
    candidate = max(floor, state.next_free)
    estimate = compute_setup(op, machine, state, config, order=order, snapshot=snapshot, at=candidate)
    setup_start, end = place_span(calendar, candidate, estimate.minutes + run_minutes, reserved)
    return SlotPlacement(setup_start, end, estimate, state, False)


def _hours(delta: timedelta) -> float:
    return delta.total_seconds() / 3600.0


@dataclass(slots=True)
class _Scored:
    candidate: MachineCandidate
    total_cost: float
    preferred_rank: int
    setup_reason: str
    tooling_minutes: float
    same_family: bool
    available_now: bool
    load_minutes: float
    penalties: list[str]


def _rejected_candidate(machine_id: str, reason: str) -> MachineCandidate:
    return MachineCandidate(machine_id, 0, None, None, 0.0, 0.0, 0.0, [reason])


def rank_machines(
    op: Operation,
    order: Order | None,
    eligible: Sequence[Machine],
    states: Mapping[str, MachineState],
    calendars: Mapping[str, MachineCalendar],
    constraints: ConstraintEngine,
    config: SchedulingConfig,
    now: datetime,
    *,
    snapshot: PlanningSnapshot | None = None,
    release: datetime | None = None,
    reserved: Mapping[str, Sequence[TimeWindow]] | None = None,
    group_average_load: float | None = None,
    timelines: Mapping[str, MachineTimeline] | None = None,
    tail_only: bool = False,
    slot_out: dict[str, SlotPlacement] | None = None,
) -> list[MachineCandidate]:
    """Rank ``eligible`` machines for ``op``; feasible ones first (rank 1..n), rejected ones after.

    Rejected candidates (unknown cycle time, no calendar, no machine state) have
    ``expected_start``/``expected_end`` ``None`` and ``rank`` 0. ``release`` is the
    earliest instant the operation may start (previous operation, dependency,
    blocker resolution); ``reserved`` holds locked windows per machine.
    ``timelines`` enables gap filling (:func:`find_slot`); ``tail_only`` forces
    tail placement (batching pulls a job behind the machine's *current* setup);
    ``slot_out`` receives the :class:`SlotPlacement` of every feasible machine so
    the caller can place the winner without recomputing it.
    """
    now = ensure_utc(now)
    if snapshot is None:
        snapshot = PlanningSnapshot(as_of=now, orders={order.order_id: order} if order else {})
    floor = max(now, ensure_utc(release)) if release is not None else now
    scored: list[_Scored] = []
    rejected: list[MachineCandidate] = []
    for machine in eligible:
        state = states.get(machine.machine_id)
        calendar = calendars.get(machine.machine_id)
        if state is None:
            rejected.append(_rejected_candidate(machine.machine_id, NOT_INITIALISED))
            continue
        if calendar is None:
            rejected.append(_rejected_candidate(machine.machine_id, NO_CALENDAR))
            continue
        run = op.run_minutes_on(machine)
        if run is None:
            rejected.append(_rejected_candidate(machine.machine_id, CYCLE_TIME_UNKNOWN))
            continue
        run = max(0.0, run)
        earliest = floor
        if machine.available_from is not None:
            earliest = max(earliest, ensure_utc(machine.available_from))
        slot = find_slot(
            op,
            order,
            machine,
            state,
            calendar,
            config,
            earliest,
            run,
            (reserved or {}).get(machine.machine_id, ()),
            timeline=timelines.get(machine.machine_id) if timelines is not None else None,
            snapshot=snapshot,
            tail_only=tail_only,
        )
        if slot_out is not None:
            slot_out[machine.machine_id] = slot
        estimate, setup_start, end = slot.estimate, slot.setup_start, slot.end
        ctx = ConstraintContext(
            snapshot=snapshot,
            at=setup_start,
            config=config,
            order=order,
            machine_state=slot.state,
            group_average_load_minutes=group_average_load,
        )
        penalties = constraints.soft_penalties(op, machine, ctx)
        soft_cost = sum(p.cost for p in penalties)
        total = (end - now).total_seconds() / 60.0 + soft_cost
        candidate = MachineCandidate(
            machine_id=machine.machine_id,
            rank=0,
            expected_start=setup_start,
            expected_end=end,
            setup_minutes=estimate.minutes,
            run_minutes=run,
            soft_cost=soft_cost,
            reasons=[],
        )
        scored.append(
            _Scored(
                candidate=candidate,
                total_cost=total,
                preferred_rank=machine.preferred_rank,
                setup_reason=describe_setup(estimate, config),
                tooling_minutes=estimate.tooling_minutes,
                same_family=estimate.basis == "same_family",
                available_now=setup_start <= now,
                load_minutes=state.scheduled_minutes,
                penalties=[p.message for p in penalties],
            )
        )
    scored.sort(key=lambda s: (s.total_cost, s.preferred_rank, s.candidate.machine_id))
    _explain(scored, op, order, now)
    ranked = [s.candidate for s in scored]
    for i, candidate in enumerate(ranked, start=1):
        candidate.rank = i
        candidate.recommended = i == 1
    rejected.sort(key=lambda c: c.machine_id)
    return ranked + rejected


def _explain(scored: list[_Scored], op: Operation, order: Order | None, now: datetime) -> None:
    """Fill ``reasons`` of every scored candidate from the computed numbers."""
    if not scored:
        return
    best = scored[0]
    min_load = min(s.load_minutes for s in scored)
    lowest_load_unique = sum(1 for s in scored if s.load_minutes == min_load) == 1
    needs_tooling = bool(required_tooling_ids(op, order))
    best_end = best.candidate.expected_end
    for idx, s in enumerate(scored):
        c = s.candidate
        assert c.expected_end is not None and best_end is not None
        reasons: list[str] = []
        if idx == 0:
            if len(scored) > 1:
                runner_up = scored[1]
                assert runner_up.candidate.expected_end is not None
                gain = _hours(runner_up.candidate.expected_end - c.expected_end)
                if gain > 0:
                    reasons.append(
                        f"Expected completion {gain:.1f} hours earlier than next best "
                        f"({runner_up.candidate.machine_id})"
                    )
                elif s.total_cost < runner_up.total_cost:
                    reasons.append(
                        f"Earliest expected completion (tied with {runner_up.candidate.machine_id}; "
                        f"lower preference cost {s.total_cost:.0f} vs {runner_up.total_cost:.0f})"
                    )
                else:
                    reasons.append(
                        f"Earliest expected completion (tied with {runner_up.candidate.machine_id}; "
                        f"preferred by machine rank {s.preferred_rank} vs {runner_up.preferred_rank})"
                    )
            else:
                reasons.append("Only eligible machine")
            if s.same_family and c.setup_minutes == 0:
                reasons.append(f"No setup change needed (same setup family {op.setup_family!r})")
            if needs_tooling and s.tooling_minutes == 0:
                reasons.append("No additional tooling setup")
            if s.available_now:
                reasons.append("Machine currently available")
            if lowest_load_unique and s.load_minutes == min_load:
                reasons.append(f"Lower downstream impact (lowest planned load: {s.load_minutes / 60:.1f} h)")
        else:
            later = _hours(c.expected_end - best_end)
            if later > 0:
                reasons.append(
                    f"Expected completion {later:.1f} hours later than {best.candidate.machine_id}"
                )
            elif s.total_cost > best.total_cost:
                reasons.append(
                    f"Same expected completion as {best.candidate.machine_id} but higher cost "
                    f"({s.total_cost:.0f} vs {best.total_cost:.0f})"
                )
            else:
                reasons.append(
                    f"Same expected completion and cost as {best.candidate.machine_id}; "
                    f"lower machine preference rank ({s.preferred_rank} vs {best.preferred_rank})"
                )
            if s.available_now:
                reasons.append("Machine currently available")
        reasons.append(
            f"Expected start {c.expected_start.isoformat() if c.expected_start else '?'}, "
            f"end {c.expected_end.isoformat()} ({_hours(c.expected_end - now):.1f} h from now)"
        )
        reasons.append(s.setup_reason)
        reasons.append(f"Run {c.run_minutes / 60:.1f} h")
        reasons.extend(s.penalties)
        c.reasons = reasons


def build_recommendation(
    op: Operation,
    candidates: Sequence[MachineCandidate],
    eligibility: EligibilityResult | None = None,
) -> MachineRecommendation:
    """Spec Phase 35 payload: eligible (feasible) candidates, the recommended one and every rejection."""
    feasible = [c for c in candidates if c.expected_end is not None]
    rejected: dict[str, list[str]] = {}
    if eligibility is not None:
        for machine_id in sorted(eligibility.rejected):
            rejected[machine_id] = [v.message for v in eligibility.rejected[machine_id]]
    for c in candidates:
        if c.expected_end is None:
            rejected.setdefault(c.machine_id, []).extend(c.reasons)
    return MachineRecommendation(
        order_id=op.order_id,
        operation_id=op.operation_id,
        eligible=feasible,
        recommended_machine_id=feasible[0].machine_id if feasible else None,
        rejected=rejected,
    )


__all__ = [
    "CYCLE_TIME_UNKNOWN",
    "NOT_INITIALISED",
    "NO_CALENDAR",
    "Placement",
    "SlotPlacement",
    "build_recommendation",
    "find_slot",
    "place_on_calendar",
    "place_span",
    "rank_machines",
]
