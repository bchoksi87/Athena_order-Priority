"""One rule-based scheduling run: the list-scheduling loop (spec Phase 6, V1).

Algorithm (deterministic, O(orders x ops x eligible machines x calendar look-ups)):

1. Readiness of every order is assessed once (bulk ``assessment_map``);
   candidates are selected and ordered by :mod:`candidates`.
2. Machine states start from the ERP snapshot; frozen entries of the previous
   schedule and planner locks are applied first (:mod:`state`, :mod:`locks`).
3. Orders are processed in that global order. Dependencies (``depends_on_order_ids``,
   cross-order ``prerequisite_operation_id``) are processed *before* the
   dependent order, so a high-priority dependent lifts its upstream order.
4. Each pending operation of an order, in sequence, is released at
   ``max(previous operation end, dependency completion, blocker resolution)``,
   ranked over its eligible machines (:mod:`machine_assignment`) and placed on
   the recommended one; the machine state advances (next free, setup family,
   material, tooling, load). Machines are never back-filled: every placement
   goes after the machine's last job, which keeps the loop linear and the
   machine sequence equal to the processing order.
5. After a placement, a bounded *batching lookahead* (:mod:`batching`) may pull
   a later order that shares the machine's new setup forward onto the same
   machine. The lookahead runs from a work-list at the top level, never
   recursively, so long same-setup chains cannot exhaust the stack.

Failures never abort the run: an operation without eligible machine / cycle
time / calendar becomes an :class:`UnscheduledItem` with a reason code and the
rest of that order is skipped (its already placed entries stay; the order
counts as unscheduled).
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime

import structlog

from app.core.clock import ensure_utc
from app.domain.config import SchedulingConfig
from app.domain.models import Machine, Operation, Order, TimeWindow
from app.domain.results import (
    EligibilityResult,
    MachineCandidate,
    MachineRecommendation,
    PriorityResult,
    ScheduleEntry,
    UnscheduledItem,
)
from app.domain.snapshot import PlanningSnapshot
from app.engines.calendar.calendar import MachineCalendar
from app.engines.constraints.base import MachineState
from app.engines.constraints.eligibility import CandidateIndex
from app.engines.constraints.engine import ConstraintEngine
from app.engines.constraints.hard import required_tooling_ids
from app.engines.constraints.readiness import synthesize_operation
from app.engines.scheduling.batching import BatchCandidate, pick_next, shared_dimensions
from app.engines.scheduling.candidates import OrderCandidate
from app.engines.scheduling.locks import LockIndex
from app.engines.scheduling.machine_assignment import (
    CYCLE_TIME_UNKNOWN,
    build_recommendation,
    place_on_calendar,
    rank_machines,
)
from app.engines.scheduling.setup import base_setup_minutes, compute_setup, describe_setup
from app.engines.scheduling.state import apply_entry_to_state

log = structlog.get_logger(__name__)

ENTRY_ID_PREFIX = "ent_"
_QUALITATIVE_PREFIXES = ("No ", "Machine currently", "Lower downstream", "Only eligible")


def entry_id_for(operation_id: str) -> str:
    """Deterministic entry id (same snapshot + config → identical entries)."""
    return f"{ENTRY_ID_PREFIX}{operation_id}"


def batch_key_for(op: Operation, order: Order, config: SchedulingConfig) -> str | None:
    """Group key from the configured batching dimensions (None when no dimension has a value)."""
    parts: list[str] = []
    for dimension in config.batching.dimensions:
        value: str | None = None
        if dimension == "material":
            value = op.material_id or order.required_material_id
        elif dimension == "part_family":
            value = order.part_family
        elif dimension == "customer":
            value = order.customer_id
        elif dimension == "surface_finish":
            value = order.surface_finish
        elif dimension == "technology":
            value = order.technology
        elif dimension == "process":
            value = op.operation_type.value
        elif dimension == "tool":
            tools = required_tooling_ids(op, order)
            value = "+".join(sorted(tools)) if tools else None
        if value:
            parts.append(f"{dimension}={value}")
    return "|".join(parts) if parts else None


@dataclass(slots=True)
class RunContext:
    """Immutable inputs plus the mutable state of one scheduling run."""

    snapshot: PlanningSnapshot
    priorities: Mapping[str, PriorityResult]
    config: SchedulingConfig
    calendars: Mapping[str, MachineCalendar]
    constraints: ConstraintEngine
    now: datetime
    horizon_end: datetime
    states: dict[str, MachineState]
    locks: LockIndex
    frozen_machines: set[str]
    batch_lookahead: int
    entries: list[ScheduleEntry] = field(default_factory=list)
    unscheduled: list[UnscheduledItem] = field(default_factory=list)
    recommendations: dict[str, MachineRecommendation] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    placed: dict[str, ScheduleEntry] = field(default_factory=dict)  # operation_id -> entry
    order_completion: dict[str, datetime] = field(default_factory=dict)  # fully placed orders
    failed_orders: set[str] = field(default_factory=set)
    processed: set[str] = field(default_factory=set)
    beyond_horizon: list[str] = field(default_factory=list)
    batched_entries: int = 0
    _eligibility: dict[str, EligibilityResult] = field(default_factory=dict, repr=False)
    _candidate_index: CandidateIndex | None = field(default=None, repr=False)
    _group_load: dict[str, float] = field(default_factory=dict, repr=False)
    _group_size: dict[str, int] = field(default_factory=dict, repr=False)
    _pending: dict[str, list[Operation]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._candidate_index = CandidateIndex(self.snapshot)
        for machine_id, state in self.states.items():
            group = self.snapshot.machines[machine_id].machine_group
            self._group_load[group] = self._group_load.get(group, 0.0) + state.scheduled_minutes
            self._group_size[group] = self._group_size.get(group, 0) + 1

    # ------------------------------------------------------------ look-ups
    def eligibility(self, op: Operation, order: Order) -> EligibilityResult:
        cached = self._eligibility.get(op.operation_id)
        if cached is None:
            cached = self.constraints.eligible_machines(
                op, self.snapshot, self.now, order=order, index=self._candidate_index
            )
            self._eligibility[op.operation_id] = cached
        return cached

    def group_average_load(self, machine: Machine) -> float | None:
        size = self._group_size.get(machine.machine_group, 0)
        return self._group_load[machine.machine_group] / size if size else None

    def pending_operations(self, order: Order) -> list[Operation]:
        """Pending operations of ``order`` (cached: the snapshot does not change during a run)."""
        cached = self._pending.get(order.order_id)
        if cached is None:
            cached = self.snapshot.pending_operations_for_order(order.order_id)
            if not cached and order.pending_quantity > 0:
                cached = [synthesize_operation(order)]
            self._pending[order.order_id] = cached
        return cached

    def next_unplaced_operation(self, order: Order) -> Operation | None:
        for op in self.pending_operations(order):
            if op.operation_id not in self.placed:
                return op
        return None

    def usable_machines(self, machine_ids: Sequence[str]) -> list[Machine]:
        return [
            self.snapshot.machines[m]
            for m in machine_ids
            if m in self.states and m not in self.frozen_machines and m in self.snapshot.machines
        ]

    # ---------------------------------------------------------- recording
    def unschedule(
        self, order: Order, op: Operation | None, code: str, reason: str, remaining: int = 0
    ) -> None:
        if remaining > 0:
            reason += f"; {remaining} later operation(s) of the order not scheduled"
        priority = self.priorities.get(order.order_id)
        self.unscheduled.append(
            UnscheduledItem(
                order.order_id,
                op.operation_id if op is not None else None,
                code,
                reason,
                priority.readiness if priority is not None else None,
            )
        )
        self.failed_orders.add(order.order_id)

    def record_entry(self, entry: ScheduleEntry, op: Operation, order: Order, machine: Machine) -> None:
        state = self.states[machine.machine_id]
        before = state.scheduled_minutes
        apply_entry_to_state(state, entry, op, order)
        self._group_load[machine.machine_group] += state.scheduled_minutes - before
        self.entries.append(entry)
        self.placed[op.operation_id] = entry
        if entry.setup_start >= self.horizon_end:
            self.beyond_horizon.append(entry.entry_id)


class ListScheduler:
    """Drives the global order loop for one :class:`RunContext`."""

    def __init__(self, ctx: RunContext, queue: Sequence[OrderCandidate]) -> None:
        self.ctx = ctx
        self.queue = list(queue)
        self.by_id: dict[str, OrderCandidate] = {c.order_id: c for c in self.queue}
        self.cursor = 0
        self._index = {c.order_id: i for i, c in enumerate(self.queue)}
        # "next unprocessed slot" pointers (union-find with path compression) so the batching
        # lookahead never re-walks queue slots that were pulled forward earlier
        self._next_slot = list(range(len(self.queue) + 1))

    def _first_unprocessed(self, index: int) -> int:
        root = index
        while self._next_slot[root] != root:
            root = self._next_slot[root]
        while self._next_slot[index] != root:
            self._next_slot[index], index = root, self._next_slot[index]
        return root

    def _mark_processed(self, order_id: str) -> None:
        self.ctx.processed.add(order_id)
        index = self._index.get(order_id)
        if index is not None:
            self._next_slot[index] = index + 1

    # ------------------------------------------------------------ driving
    def run(self) -> None:
        ctx = self.ctx
        for index, candidate in enumerate(self.queue):
            self.cursor = index
            touched = self.process(candidate)
            if ctx.config.batching.enabled and touched:
                self._lookahead_worklist(touched)
        log.info(
            "scheduler.run_complete",
            entries=len(ctx.entries),
            unscheduled=len(ctx.unscheduled),
            batched=ctx.batched_entries,
            beyond_horizon=len(ctx.beyond_horizon),
        )

    def _lookahead_worklist(self, touched: Sequence[str]) -> None:
        work: deque[str] = deque(touched)
        while work:
            machine_id = work.popleft()
            pulled = self._batch_lookahead(machine_id)
            if pulled:
                work.extend(pulled)
                work.append(machine_id)  # state changed: the chain may continue

    def process(
        self, candidate: OrderCandidate, forced_machine: str | None = None, batch_note: str | None = None
    ) -> list[str]:
        """Place ``candidate`` (dependencies first). Returns machine ids that received work."""
        ctx = self.ctx
        if candidate.order_id in ctx.processed:
            return []
        self._mark_processed(candidate.order_id)
        touched: list[str] = []
        for dep_id in sorted(candidate.order.depends_on_order_ids):
            dep = self.by_id.get(dep_id)
            if dep is not None and dep_id not in ctx.processed:
                touched.extend(self.process(dep))
        touched.extend(self._place_order(candidate, forced_machine, batch_note))
        return touched

    # ---------------------------------------------------------- placement
    def _dependency_release(self, candidate: OrderCandidate) -> datetime | None:
        """Release from dependency orders; None when a dependency cannot be honoured."""
        ctx = self.ctx
        order = candidate.order
        release = candidate.release
        for dep_id in sorted(order.depends_on_order_ids):
            dep_order = ctx.snapshot.orders.get(dep_id)
            if dep_order is None or not dep_order.is_open:
                continue
            completion = ctx.order_completion.get(dep_id)
            if completion is None:
                ctx.unschedule(order, None, "blocked", f"dependency order {dep_id} is not scheduled")
                return None
            release = max(release, completion)
        return release

    def _prerequisite_release(self, candidate: OrderCandidate, op: Operation) -> datetime | None:
        """End of a cross-order prerequisite operation; None when it cannot be honoured."""
        ctx = self.ctx
        pre_id = op.prerequisite_operation_id
        if pre_id is None:
            return candidate.release
        entry = ctx.placed.get(pre_id)
        if entry is not None:
            return entry.end
        pre = ctx.snapshot.operations.get(pre_id)
        if pre is None or pre.is_done:
            return candidate.release  # unknown or finished prerequisite: nothing to wait for
        if pre.order_id != candidate.order_id and pre.order_id in self.by_id:
            self.process(self.by_id[pre.order_id])
            entry = ctx.placed.get(pre_id)
            if entry is not None:
                return entry.end
        ctx.unschedule(
            candidate.order,
            op,
            "blocked",
            f"prerequisite operation {pre_id} (order {pre.order_id}) is not scheduled",
        )
        return None

    def _place_order(
        self, candidate: OrderCandidate, forced_machine: str | None, batch_note: str | None
    ) -> list[str]:
        ctx = self.ctx
        order = candidate.order
        release = self._dependency_release(candidate)
        if release is None:
            return []
        ops = ctx.pending_operations(order)
        touched: list[str] = []
        prev_end: datetime | None = None
        first = True
        for index, op in enumerate(ops):
            frozen = ctx.placed.get(op.operation_id)
            if frozen is not None:
                prev_end = frozen.end
                first = False
                continue
            pre_release = self._prerequisite_release(candidate, op)
            if pre_release is None:
                return touched
            earliest = (
                max(release, pre_release, prev_end) if prev_end is not None else max(release, pre_release)
            )
            machine_id = self._place_operation(
                candidate,
                op,
                earliest,
                forced_machine if first else None,
                batch_note if first else None,
                is_last=index == len(ops) - 1,
                remaining=len(ops) - index - 1,
            )
            if machine_id is None:
                return touched
            touched.append(machine_id)
            prev_end = ctx.placed[op.operation_id].end
            first = False
        if prev_end is not None and order.order_id not in ctx.failed_orders:
            ctx.order_completion[order.order_id] = prev_end
        return touched

    def _place_operation(
        self,
        candidate: OrderCandidate,
        op: Operation,
        earliest: datetime,
        forced_machine: str | None,
        batch_note: str | None,
        *,
        is_last: bool,
        remaining: int,
    ) -> str | None:
        ctx = self.ctx
        order = candidate.order
        eligibility = ctx.eligibility(op, order)
        machine_ids = list(eligibility.eligible_machine_ids)
        slot_lock = ctx.locks.slot_locks.get(order.order_id)
        next_op = ctx.next_unplaced_operation(order)
        slot_locked = (
            slot_lock is not None
            and slot_lock.machine_id is not None
            and next_op is not None
            and next_op.operation_id == op.operation_id
        )
        required: str | None = None
        if slot_lock is not None and slot_locked and slot_lock.machine_id is not None:
            required = slot_lock.machine_id
            if slot_lock.window is not None:
                earliest = max(earliest, ensure_utc(slot_lock.window.start))
        elif forced_machine is not None:
            required = forced_machine
        usable = ctx.usable_machines(machine_ids)
        first_op = order.order_id not in ctx.recommendations
        if required is not None and required not in {m.machine_id for m in usable}:
            usable = []
        if not usable:
            code, reason = self._no_machine_reason(eligibility, machine_ids, required, slot_locked)
            ctx.unschedule(order, op, code, reason, remaining)
            if first_op:
                ctx.recommendations[order.order_id] = build_recommendation(op, [], eligibility)
            return None
        reserved: dict[str, list[TimeWindow]] = dict(ctx.locks.reserved)
        if slot_locked and slot_lock is not None and slot_lock.window is not None:
            own = (ensure_utc(slot_lock.window.start), ensure_utc(slot_lock.window.end))
            for machine_id in list(reserved):
                reserved[machine_id] = [w for w in reserved[machine_id] if (w.start, w.end) != own]
        ranked = rank_machines(
            op,
            order,
            usable,
            ctx.states,
            ctx.calendars,
            ctx.constraints,
            ctx.config,
            ctx.now,
            snapshot=ctx.snapshot,
            release=earliest,
            reserved=reserved,
            group_average_load=ctx.group_average_load(usable[0]),
        )
        if first_op:
            ctx.recommendations[order.order_id] = build_recommendation(op, ranked, eligibility)
        if required is not None:
            best = next((c for c in ranked if c.machine_id == required and c.expected_end is not None), None)
        else:
            best = ranked[0] if ranked and ranked[0].expected_end is not None else None
        if best is None:
            where = required or ", ".join(sorted(m.machine_id for m in usable))
            ctx.unschedule(order, op, "missing_cycle_time", f"{CYCLE_TIME_UNKNOWN} on {where}", remaining)
            return None
        machine = ctx.snapshot.machines[best.machine_id]
        entry = self._build_entry(
            candidate, op, machine, best, earliest, reserved, batch_note, is_last, locked=slot_locked
        )
        ctx.record_entry(entry, op, order, machine)
        if batch_note is not None:
            ctx.batched_entries += 1
        return machine.machine_id

    def _no_machine_reason(
        self, eligibility: EligibilityResult, eligible: Sequence[str], required: str | None, slot_locked: bool
    ) -> tuple[str, str]:
        ctx = self.ctx
        if not eligible:
            summary = "; ".join(
                f"{m}: {', '.join(v.message for v in vs)}" for m, vs in sorted(eligibility.rejected.items())
            )
            return "no_eligible_machine", f"no eligible machine ({summary or 'no candidate machines'})"
        if required is not None:
            source = "time-slot lock names" if slot_locked else "forced machine"
            return (
                "no_eligible_machine",
                f"{source} {required}, which is not eligible or not available in this run",
            )
        missing = [m for m in eligible if m not in ctx.calendars]
        if missing:
            return "no_calendar", f"eligible machine(s) {', '.join(missing)} have no working calendar"
        return "no_eligible_machine", f"eligible machine(s) {', '.join(eligible)} are excluded from this run"

    def _build_entry(
        self,
        candidate: OrderCandidate,
        op: Operation,
        machine: Machine,
        best: MachineCandidate,
        earliest: datetime,
        reserved: Mapping[str, Sequence[TimeWindow]],
        batch_note: str | None,
        is_last: bool,
        *,
        locked: bool,
    ) -> ScheduleEntry:
        ctx = self.ctx
        order = candidate.order
        state = ctx.states[machine.machine_id]
        start_floor = max(earliest, state.next_free)
        if machine.available_from is not None:
            start_floor = max(start_floor, ensure_utc(machine.available_from))
        estimate = compute_setup(
            op, machine, state, ctx.config, order=order, snapshot=ctx.snapshot, at=start_floor
        )
        placement = place_on_calendar(
            ctx.calendars[machine.machine_id],
            start_floor,
            estimate.minutes,
            best.run_minutes,
            reserved.get(machine.machine_id, ()),
        )
        priority = candidate.priority
        headline = [best.reasons[0]] if best.reasons else []
        headline += [r for r in best.reasons[1:] if r.startswith(_QUALITATIVE_PREFIXES)]
        rank_text = f"Rank {priority.rank}" if priority.rank is not None else "Unranked"
        reason = f"{rank_text} (score {priority.score:.1f}) → {machine.machine_id}: {'; '.join(headline)}; "
        reason += describe_setup(estimate, ctx.config)
        if candidate.in_progress and op.machine_id == machine.machine_id:
            reason += "; operation in progress"
        elif candidate.locked and candidate.lock_key is not None:
            reason += f"; planner lock {candidate.lock_key[1]}"
        elif priority.forced_next:
            reason += "; forced next by planner"
        if candidate.blocker_note:
            reason += f"; {candidate.blocker_note}"
        if batch_note:
            reason += f"; batched: {batch_note}"
        return ScheduleEntry(
            entry_id=entry_id_for(op.operation_id),
            machine_id=machine.machine_id,
            order_id=order.order_id,
            operation_id=op.operation_id,
            sequence_on_machine=0,
            setup_start=placement.setup_start,
            start=placement.start,
            end=placement.end,
            setup_minutes=estimate.minutes,
            run_minutes=best.run_minutes,
            quantity=op.pending_quantity,
            priority_score=priority.score,
            placement_reason=reason,
            is_last_operation=is_last,
            due_date=order.due_date,
            locked=locked,
            batch_key=batch_key_for(op, order, ctx.config),
            setup_family=op.setup_family,
            material_id=op.material_id or order.required_material_id,
            customer_id=order.customer_id,
        )

    # ----------------------------------------------------------- batching
    def _batch_lookahead(self, machine_id: str) -> list[str]:
        """Pull one same-setup order forward onto ``machine_id`` if the batching rules allow."""
        ctx = self.ctx
        state = ctx.states.get(machine_id)
        machine = ctx.snapshot.machines.get(machine_id)
        if state is None or machine is None or machine_id in ctx.frozen_machines:
            return []
        queue: list[tuple[BatchCandidate, OrderCandidate]] = []
        scanned = 0
        index = self._first_unprocessed(self.cursor + 1)
        while index < len(self.queue) and scanned < ctx.batch_lookahead:
            oc = self.queue[index]
            index = self._first_unprocessed(index + 1)
            scanned += 1
            if oc.release > state.next_free:
                continue
            if any(
                d in self.by_id and d not in ctx.order_completion and (ctx.snapshot.orders[d].is_open)
                for d in oc.order.depends_on_order_ids
                if d in ctx.snapshot.orders
            ):
                continue
            op = ctx.next_unplaced_operation(oc.order)
            if op is None or op.prerequisite_operation_id is not None:
                continue
            if machine_id not in ctx.eligibility(op, oc.order).eligible_machine_ids:
                continue
            run = op.run_minutes_on(machine)
            if run is None:
                continue
            # Base setup is an upper bound on the real changeover; it is exact enough for the
            # due-date protection of bypassed jobs (over-estimating is the safe direction).
            # Only jobs that share the current setup - the ones that can be pulled forward -
            # get the exact estimate through the shared setup code path.
            candidate = BatchCandidate(
                order_id=oc.order_id,
                operation_id=op.operation_id,
                score=oc.priority.score,
                duration_minutes=base_setup_minutes(op, oc.order, ctx.config) + max(0.0, run),
                due_date=oc.order.due_date,
                setup_family=op.setup_family,
                material_id=op.material_id or oc.order.required_material_id,
                part_family=oc.order.part_family,
                customer_id=oc.order.customer_id,
                tooling_ids=frozenset(required_tooling_ids(op, oc.order)),
                forced_next=oc.forced_next,
                locked=oc.locked,
            )
            if shared_dimensions(candidate, state, ctx.config):
                setup = compute_setup(op, machine, state, ctx.config, order=oc.order, snapshot=ctx.snapshot)
                candidate = replace(candidate, duration_minutes=setup.minutes + max(0.0, run))
            queue.append((candidate, oc))
        if len(queue) < 2:
            return []
        decision = pick_next([b for b, _ in queue], state, ctx.config, calendar=ctx.calendars.get(machine_id))
        if decision is None or not decision.pulled_forward:
            return []
        chosen = next(oc for b, oc in queue if b.order_id == decision.chosen.order_id)
        log.debug(
            "scheduler.batch_pull", machine_id=machine_id, order_id=chosen.order_id, reason=decision.reason
        )
        return self.process(chosen, forced_machine=machine_id, batch_note=decision.reason)


__all__ = ["ENTRY_ID_PREFIX", "ListScheduler", "RunContext", "batch_key_for", "entry_id_for"]
