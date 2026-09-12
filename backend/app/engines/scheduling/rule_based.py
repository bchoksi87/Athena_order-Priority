"""Rule-based scheduler V1 (DESIGN_CONTRACT §6.4, spec Phase 6 "VERSION 1").

``RuleBasedScheduler.schedule`` turns priorities into an executable schedule:

1. remove blocked orders (or defer them to their blocker's resolution),
2. take the priority order (human decisions — in-progress work, locks,
   forced-next — first),
3. assign every pending operation to the machine with the earliest expected
   completion after soft-constraint costs (spec Phase 35), back-filling idle
   gaps in each machine's timeline before appending after its last job,
4. keep same-setup jobs together when the batching rules allow,
5. respect operation sequence, order dependencies, calendars, downtime and
   locked windows,
6. compute projected completion, lateness, metrics and the quality score.

The heavy lifting lives in :mod:`run` (the loop), :mod:`candidates`,
:mod:`state`, :mod:`timeline`, :mod:`locks`, :mod:`machine_assignment`,
:mod:`batching`, :mod:`metrics` and :mod:`quality`; this module wires them together and
finalises the result (machine sequence numbers, last-operation flags,
expected completion and lateness, warnings).

``now`` comes from the injected :class:`Clock` — never from the wall clock —
so a frozen clock makes the whole run reproducible; entry ids are derived
from operation ids for the same reason.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import timedelta

import structlog

from app.core.clock import Clock, ensure_utc
from app.domain.config import SchedulingConfig
from app.domain.results import PriorityResult, ScheduleEntry, ScheduleResult
from app.domain.snapshot import PlanningSnapshot
from app.engines.calendar.calendar import MachineCalendar
from app.engines.constraints.engine import ConstraintEngine
from app.engines.scheduling.candidates import select_candidates
from app.engines.scheduling.locks import build_lock_index
from app.engines.scheduling.metrics import compute_metrics, order_lateness_hours
from app.engines.scheduling.quality import compute_quality
from app.engines.scheduling.run import ListScheduler, RunContext
from app.engines.scheduling.state import init_machine_states, reproduce_frozen_entries

log = structlog.get_logger(__name__)

#: Default bound on how many queued orders the batching lookahead inspects per
#: placement. An algorithm parameter (keeps the run linear), not a business rule.
DEFAULT_BATCH_LOOKAHEAD = 25
_MAX_LISTED_WARNINGS = 10


class RuleBasedScheduler:
    name = "rule_based"
    version = "1.0.0"

    def __init__(self, clock: Clock, *, batch_lookahead: int = DEFAULT_BATCH_LOOKAHEAD) -> None:
        self.clock = clock
        self.batch_lookahead = max(0, batch_lookahead)

    def schedule(
        self,
        snapshot: PlanningSnapshot,
        priorities: Mapping[str, PriorityResult],
        config: SchedulingConfig,
        calendars: Mapping[str, MachineCalendar],
        constraints: ConstraintEngine,
        *,
        previous_entries: Sequence[ScheduleEntry] | None = None,
    ) -> ScheduleResult:
        """Build a schedule for ``snapshot``; ``previous_entries`` feeds the frozen lock window."""
        now = ensure_utc(self.clock.now())
        horizon_end = now + timedelta(days=config.horizon_days)
        log.info(
            "scheduler.start",
            algorithm=self.name,
            version=self.version,
            orders=len(snapshot.orders),
            machines=len(snapshot.machines),
            horizon_days=config.horizon_days,
            previous_entries=len(previous_entries or ()),
        )
        locks = build_lock_index(snapshot, now)
        pool = init_machine_states(snapshot, calendars, now, locks)
        frozen = reproduce_frozen_entries(
            previous_entries, snapshot, pool.states, config, now, locks, timelines=pool.timelines
        )
        assessments = constraints.assessment_map(snapshot, now)
        selection = select_candidates(snapshot, priorities, config, assessments, now, locks)

        ctx = RunContext(
            snapshot=snapshot,
            priorities=priorities,
            config=config,
            calendars=calendars,
            constraints=constraints,
            now=now,
            horizon_end=horizon_end,
            states=pool.states,
            locks=locks,
            frozen_machines=set(locks.frozen_machines),
            batch_lookahead=self.batch_lookahead,
            timelines=pool.timelines,
        )
        ctx.warnings.extend(pool.warnings)
        ctx.warnings.extend(frozen.warnings)
        ctx.unscheduled.extend(selection.unscheduled)
        for entry in frozen.entries:
            ctx.entries.append(entry)
            ctx.placed[entry.operation_id] = entry
            if entry.setup_start >= horizon_end:
                ctx.beyond_horizon.append(entry.entry_id)
        ListScheduler(ctx, selection.candidates).run()

        result = ScheduleResult(
            algorithm=self.name,
            algorithm_version=self.version,
            profile_id=_profile_id(priorities),
            profile_version=_profile_version(priorities),
            config_version=config.version,
            generated_at=now,
            horizon_start=now,
            horizon_end=horizon_end,
            entries=ctx.entries,
            unscheduled=sorted(ctx.unscheduled, key=lambda u: (u.order_id, u.operation_id or "")),
            machine_recommendations=ctx.recommendations,
            warnings=list(ctx.warnings),
        )
        finalise_entries(result, snapshot, ctx.failed_orders)
        if ctx.beyond_horizon:
            listed = ", ".join(ctx.beyond_horizon[:_MAX_LISTED_WARNINGS])
            more = (
                f" (+{len(ctx.beyond_horizon) - _MAX_LISTED_WARNINGS} more)"
                if len(ctx.beyond_horizon) > _MAX_LISTED_WARNINGS
                else ""
            )
            result.warnings.append(
                f"{len(ctx.beyond_horizon)} entries start beyond the horizon end "
                f"{horizon_end.isoformat()} (capacity shortfall): {listed}{more}"
            )
        if ctx.batched_entries:
            result.warnings.append(f"{ctx.batched_entries} entries pulled forward by batching rules")
        result.metrics = compute_metrics(result, snapshot, calendars, config)
        result.quality = compute_quality(result, config)
        log.info(
            "scheduler.done",
            entries=len(result.entries),
            unscheduled=len(result.unscheduled),
            on_time_pct=round(result.metrics.on_time_pct, 1),
            quality=round(result.quality.score, 1),
        )
        return result


def finalise_entries(result: ScheduleResult, snapshot: PlanningSnapshot, failed_orders: set[str]) -> None:
    """Machine sequence numbers, order completion, lateness; entries sorted by (machine, time)."""
    by_machine: dict[str, list[ScheduleEntry]] = defaultdict(list)
    for entry in result.entries:
        by_machine[entry.machine_id].append(entry)
    ordered: list[ScheduleEntry] = []
    for machine_id in sorted(by_machine):
        entries = sorted(by_machine[machine_id], key=lambda e: (e.setup_start, e.start, e.operation_id))
        for seq, entry in enumerate(entries, start=1):
            entry.sequence_on_machine = seq
        ordered.extend(entries)
    result.entries = ordered
    completion = result.order_completion()
    for entry in result.entries:
        order = snapshot.orders.get(entry.order_id)
        if entry.due_date is None and order is not None:
            entry.due_date = order.due_date
        if entry.order_id in failed_orders:
            entry.expected_completion = None
            entry.expected_lateness_hours = None
            continue
        done = completion.get(entry.order_id)
        entry.expected_completion = done
        entry.expected_lateness_hours = (
            order_lateness_hours(done, entry.due_date) if done is not None else None
        )


def _profile_id(priorities: Mapping[str, PriorityResult]) -> str:
    for key in sorted(priorities):
        return priorities[key].profile_id
    return "unknown"


def _profile_version(priorities: Mapping[str, PriorityResult]) -> int:
    for key in sorted(priorities):
        return priorities[key].profile_version
    return 0


__all__ = ["DEFAULT_BATCH_LOOKAHEAD", "RuleBasedScheduler", "finalise_entries"]
