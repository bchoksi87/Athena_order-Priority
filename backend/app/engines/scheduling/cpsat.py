"""CP-SAT re-sequencing on top of the rule-based schedule (spec Phase 6 "VERSION 2").

Optional: importing this module requires OR-Tools. :func:`registry.default_registry`
only registers ``cpsat`` when the import succeeds.

Scope (deliberately bounded, correctness first):

1. run the rule-based scheduler (warm start and fallback);
2. pick the ``top_k_machines`` with the most planned minutes;
3. on each of them, independently, re-sequence the jobs to minimise
   ``Σ (priority + 1) x tardiness + setup_cost x Σ setup`` with a time limit;
4. rebuild the machine's entries through the same calendar / setup code as
   the rule-based scheduler, verify every cross-machine precedence still
   holds, and keep the new sequence only when the schedule quality does not
   drop; otherwise the rule-based entries for that machine are kept.

Model (per machine, in *working-minute* coordinates so non-working time is
invisible, exactly as ``MachineCalendar.add_work_minutes`` sees it):

* one interval per movable job with ``start >= release`` (end of its
  predecessor operation on another machine) and ``end <= deadline`` (its
  current end, when a successor operation is already placed on another
  machine — other machines are never touched, so a job may move earlier but
  not later than the plan its successor relies on);
* locked entries and reserved lock windows are fixed intervals;
* an ``AddCircuit`` over the movable jobs gives the sequence; the setup of a
  job is the setup implied by the arc that reaches it (sequence-dependent
  setup, priced with :func:`~app.engines.scheduling.setup.compute_setup`);
* same-order jobs on the same machine keep their operation sequence.

Determinism: one worker and a fixed seed; with a time limit a proof may not
finish, but the search itself is reproducible.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime

import structlog
from ortools.sat.python import cp_model

from app.core.clock import Clock, ensure_utc
from app.domain.config import SchedulingConfig
from app.domain.models import Machine, Operation, Order, TimeWindow
from app.domain.results import PriorityResult, ScheduleEntry, ScheduleResult
from app.domain.snapshot import PlanningSnapshot
from app.engines.calendar.calendar import MachineCalendar
from app.engines.constraints.base import MachineState
from app.engines.constraints.engine import ConstraintEngine
from app.engines.constraints.hard import required_tooling_ids
from app.engines.scheduling.locks import build_lock_index
from app.engines.scheduling.machine_assignment import place_on_calendar
from app.engines.scheduling.metrics import compute_metrics
from app.engines.scheduling.quality import compute_quality
from app.engines.scheduling.rule_based import RuleBasedScheduler, finalise_entries
from app.engines.scheduling.setup import compute_setup, describe_setup
from app.engines.scheduling.state import apply_entry_to_state

log = structlog.get_logger(__name__)

_SCALE = 100  # objective integer scale for fractional setup costs


@dataclass(slots=True)
class _Job:
    entry: ScheduleEntry
    op: Operation
    order: Order
    release_w: int  # working minutes since anchor
    deadline_w: int | None  # None = free
    due_w: int | None  # only when this is the order's last operation
    same_machine_pred: str | None  # entry id of the predecessor operation on this machine


class CpSatScheduler:
    name = "cpsat"
    version = "0.1.0"

    def __init__(
        self,
        clock: Clock,
        *,
        base: RuleBasedScheduler | None = None,
        time_limit_seconds: float = 10.0,
        top_k_machines: int = 3,
        max_jobs_per_machine: int = 60,
        num_workers: int = 1,
        random_seed: int = 0,
    ) -> None:
        self.clock = clock
        self.base = base if base is not None else RuleBasedScheduler(clock)
        self.time_limit_seconds = max(0.1, time_limit_seconds)
        self.top_k_machines = max(0, top_k_machines)
        self.max_jobs_per_machine = max(2, max_jobs_per_machine)
        self.num_workers = max(1, num_workers)
        self.random_seed = random_seed

    # -------------------------------------------------------------- driver
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
        base = self.base.schedule(
            snapshot, priorities, config, calendars, constraints, previous_entries=previous_entries
        )
        now = ensure_utc(self.clock.now())
        reserved = build_lock_index(snapshot, now).reserved
        load: dict[str, float] = defaultdict(float)
        for e in base.entries:
            load[e.machine_id] += e.setup_minutes + e.run_minutes
        targets = sorted(load, key=lambda m: (-load[m], m))[: self.top_k_machines]
        improved: list[ScheduleEntry] = list(base.entries)
        notes: list[str] = []
        for machine_id in targets:
            machine = snapshot.machines.get(machine_id)
            calendar = calendars.get(machine_id)
            if machine is None or calendar is None:
                continue
            outcome = self._resequence(
                machine, calendar, base, improved, snapshot, config, now, reserved.get(machine_id, ())
            )
            if outcome is None:
                continue
            new_entries, note = outcome
            trial = [e for e in improved if e.machine_id != machine_id] + new_entries
            candidate = self._assemble(base, trial, snapshot, calendars, config)
            if (
                candidate.quality is not None
                and base.quality is not None
                and (candidate.quality.score + 1e-9 < base.quality.score)
            ):
                notes.append(f"{machine_id}: CP-SAT sequence rejected (quality would drop)")
                continue
            improved = trial
            notes.append(note)
        result = self._assemble(base, improved, snapshot, calendars, config)
        result.algorithm = self.name
        result.algorithm_version = self.version
        result.warnings = list(base.warnings) + [f"cpsat: {n}" for n in notes]
        if not notes:
            result.warnings.append("cpsat: no machine re-sequenced; rule-based schedule returned")
        return result

    def _assemble(
        self,
        base: ScheduleResult,
        entries: list[ScheduleEntry],
        snapshot: PlanningSnapshot,
        calendars: Mapping[str, MachineCalendar],
        config: SchedulingConfig,
    ) -> ScheduleResult:
        result = replace(base, entries=[replace(e) for e in entries], warnings=list(base.warnings))
        failed = {u.order_id for u in result.unscheduled}
        finalise_entries(result, snapshot, failed)
        result.metrics = compute_metrics(result, snapshot, calendars, config)
        result.quality = compute_quality(result, config)
        return result

    # -------------------------------------------------------------- model
    def _jobs(
        self,
        machine: Machine,
        calendar: MachineCalendar,
        entries: list[ScheduleEntry],
        all_entries: Sequence[ScheduleEntry],
        snapshot: PlanningSnapshot,
        anchor: datetime,
    ) -> tuple[list[_Job], list[ScheduleEntry]]:
        """Movable jobs and fixed (locked) entries for ``machine``."""
        by_op = {e.operation_id: e for e in all_entries}

        def w(t: datetime) -> int:
            return round(calendar.working_minutes_between(anchor, t))

        jobs: list[_Job] = []
        fixed: list[ScheduleEntry] = []
        for entry in entries:
            op = snapshot.operations.get(entry.operation_id)
            order = snapshot.orders.get(entry.order_id)
            if entry.locked or op is None or order is None:
                fixed.append(entry)
                continue
            pending = snapshot.pending_operations_for_order(entry.order_id)
            idx = next((i for i, o in enumerate(pending) if o.operation_id == op.operation_id), -1)
            pred = pending[idx - 1] if idx > 0 else None
            succ = pending[idx + 1] if 0 <= idx < len(pending) - 1 else None
            release_w, same_pred = 0, None
            if pred is not None:
                pred_entry = by_op.get(pred.operation_id)
                if pred_entry is not None:
                    if pred_entry.machine_id == machine.machine_id and not pred_entry.locked:
                        same_pred = pred_entry.entry_id
                    else:
                        release_w = w(pred_entry.end)
            deadline_w: int | None = None
            if succ is not None:
                succ_entry = by_op.get(succ.operation_id)
                if succ_entry is not None and succ_entry.machine_id != machine.machine_id:
                    deadline_w = w(entry.end)
            due_w = w(order.due_date) if entry.is_last_operation and order.due_date is not None else None
            jobs.append(_Job(entry, op, order, release_w, deadline_w, due_w, same_pred))
        return jobs, fixed

    def _setup_after(
        self,
        job: _Job,
        prev: _Job | None,
        machine: Machine,
        initial: MachineState,
        snapshot: PlanningSnapshot,
        config: SchedulingConfig,
    ) -> float:
        state = MachineState(
            machine_id=machine.machine_id,
            next_free=initial.next_free,
            current_setup_family=initial.current_setup_family,
            current_material_id=initial.current_material_id,
            mounted_tooling=set(initial.mounted_tooling),
        )
        if prev is not None:
            state.current_setup_family = prev.op.setup_family
            state.current_material_id = prev.op.material_id or prev.order.required_material_id
            state.mounted_tooling |= required_tooling_ids(prev.op, prev.order)
        return compute_setup(job.op, machine, state, config, order=job.order, snapshot=snapshot).minutes

    def _resequence(
        self,
        machine: Machine,
        calendar: MachineCalendar,
        base: ScheduleResult,
        current: Sequence[ScheduleEntry],
        snapshot: PlanningSnapshot,
        config: SchedulingConfig,
        now: datetime,
        reserved: Sequence[TimeWindow],
    ) -> tuple[list[ScheduleEntry], str] | None:
        entries = sorted(
            (e for e in current if e.machine_id == machine.machine_id),
            key=lambda e: (e.setup_start, e.entry_id),
        )
        if len(entries) < 2 or len(entries) > self.max_jobs_per_machine:
            return None
        anchor = min([now, *(e.setup_start for e in entries)])
        jobs, fixed = self._jobs(machine, calendar, entries, current, snapshot, anchor)
        if len(jobs) < 2:
            return None
        initial = MachineState(
            machine_id=machine.machine_id,
            next_free=max(now, ensure_utc(machine.available_from)) if machine.available_from else now,
            current_setup_family=machine.current_setup_family,
            current_material_id=machine.current_material_id,
            mounted_tooling=set(machine.tooling_configuration),
        )
        for entry in fixed:  # locked work already on the machine shapes the initial state
            apply_entry_to_state(
                initial,
                entry,
                snapshot.operations.get(entry.operation_id),
                snapshot.orders.get(entry.order_id),
            )

        def w(t: datetime) -> int:
            return round(calendar.working_minutes_between(anchor, t))

        horizon_w = (
            max(w(base.horizon_end), max(w(e.end) for e in entries))
            + sum(round(e.setup_minutes + e.run_minutes) for e in entries)
            + 1
        )
        model = cp_model.CpModel()
        n = len(jobs)
        starts = [model.new_int_var(j.release_w, horizon_w, f"s{i}") for i, j in enumerate(jobs)]
        setups = [model.new_int_var(0, horizon_w, f"su{i}") for i in range(n)]
        ends = [model.new_int_var(0, horizon_w, f"e{i}") for i in range(n)]
        intervals = []
        for i, job in enumerate(jobs):
            run = round(job.entry.run_minutes)
            model.add(ends[i] == starts[i] + setups[i] + run)
            intervals.append(model.new_interval_var(starts[i], setups[i] + run, ends[i], f"iv{i}"))
            if job.deadline_w is not None:
                model.add(ends[i] <= job.deadline_w)
        for entry in fixed:
            s, e = w(entry.setup_start), w(entry.end)
            if e > s:
                intervals.append(model.new_fixed_size_interval_var(s, e - s, f"fx{entry.entry_id}"))
        for k, window in enumerate(reserved):
            s, e = w(window.start), w(window.end)
            if e > s:
                intervals.append(model.new_fixed_size_interval_var(s, e - s, f"rs{k}"))
        model.add_no_overlap(intervals)

        # sequence via circuit over nodes 0 (dummy start) and 1..n
        arcs: list[tuple[int, int, cp_model.IntVar]] = []
        setup_terms: list[list[tuple[int, cp_model.IntVar]]] = [[] for _ in range(n)]
        for i, job in enumerate(jobs):
            first = model.new_bool_var(f"first{i}")
            arcs.append((0, i + 1, first))
            setup_terms[i].append(
                (round(self._setup_after(job, None, machine, initial, snapshot, config)), first)
            )
            arcs.append((i + 1, 0, model.new_bool_var(f"last{i}")))
            for k, other in enumerate(jobs):
                if k == i:
                    continue
                lit = model.new_bool_var(f"arc{i}_{k}")
                arcs.append((i + 1, k + 1, lit))
                model.add(starts[k] >= ends[i]).only_enforce_if(lit)
                setup_terms[k].append(
                    (round(self._setup_after(other, job, machine, initial, snapshot, config)), lit)
                )
        model.add_circuit(arcs)
        for i in range(n):
            model.add(setups[i] == sum(m * lit for m, lit in setup_terms[i]))
        index_by_entry = {job.entry.entry_id: i for i, job in enumerate(jobs)}
        for i, job in enumerate(jobs):
            if job.same_machine_pred is not None and job.same_machine_pred in index_by_entry:
                model.add(starts[i] >= ends[index_by_entry[job.same_machine_pred]])
        objective = []
        setup_cost = round(_SCALE * config.setup.setup_penalty_cost_per_minute)
        for i, job in enumerate(jobs):
            if job.due_w is not None:
                tard = model.new_int_var(0, horizon_w, f"t{i}")
                model.add(tard >= ends[i] - job.due_w)
                objective.append(_SCALE * (round(job.entry.priority_score) + 1) * tard)
            objective.append(setup_cost * setups[i])
            model.add_hint(starts[i], w(job.entry.setup_start))
        model.minimize(sum(objective))

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = self.time_limit_seconds
        solver.parameters.num_search_workers = self.num_workers
        solver.parameters.random_seed = self.random_seed
        status = solver.solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            log.info("cpsat.no_solution", machine_id=machine.machine_id, status=solver.status_name(status))
            return None
        order = sorted(range(n), key=lambda i: (solver.value(starts[i]), jobs[i].entry.entry_id))
        changed = sum(1 for i, k in enumerate(order) if jobs[k].entry.entry_id != jobs[i].entry.entry_id)
        if changed == 0:
            log.debug("cpsat.sequence_unchanged", machine_id=machine.machine_id)
            return None
        rebuilt = self._rebuild(machine, calendar, jobs, order, fixed, initial, snapshot, config, reserved)
        if rebuilt is None:
            return None
        note = (
            f"{machine.machine_id}: {changed} of {n} jobs re-sequenced "
            f"(objective {solver.objective_value / _SCALE:.0f}, {solver.status_name(status)}, "
            f"{solver.wall_time:.2f}s)"
        )
        return rebuilt, note

    def _rebuild(
        self,
        machine: Machine,
        calendar: MachineCalendar,
        jobs: list[_Job],
        order: list[int],
        fixed: list[ScheduleEntry],
        initial: MachineState,
        snapshot: PlanningSnapshot,
        config: SchedulingConfig,
        reserved: Sequence[TimeWindow],
    ) -> list[ScheduleEntry] | None:
        """Re-place jobs in ``order`` through the calendar; None when a deadline would be violated."""
        state = MachineState(
            machine_id=machine.machine_id,
            next_free=initial.next_free,
            current_setup_family=machine.current_setup_family,
            current_material_id=machine.current_material_id,
            mounted_tooling=set(machine.tooling_configuration),
        )
        windows = sorted(
            [*reserved, *(TimeWindow(e.setup_start, e.end, e.entry_id) for e in fixed)],
            key=lambda x: (x.start, x.end),
        )
        by_entry = {job.entry.entry_id: job for job in jobs}
        placed_end: dict[str, datetime] = {}
        out: list[ScheduleEntry] = []
        for position, k in enumerate(order, start=1):
            job = jobs[k]
            earliest = max(state.next_free, calendar.add_work_minutes(initial.next_free, job.release_w))
            if job.same_machine_pred is not None and job.same_machine_pred in placed_end:
                earliest = max(earliest, placed_end[job.same_machine_pred])
            elif job.same_machine_pred is not None and job.same_machine_pred in by_entry:
                return None  # predecessor sequenced after its successor: infeasible
            estimate = compute_setup(
                job.op, machine, state, config, order=job.order, snapshot=snapshot, at=earliest
            )
            placement = place_on_calendar(
                calendar, earliest, estimate.minutes, job.entry.run_minutes, windows
            )
            if job.deadline_w is not None and placement.end > job.entry.end:
                return None
            previous_position = job.entry.sequence_on_machine
            reason = job.entry.placement_reason
            if previous_position != position or estimate.minutes != job.entry.setup_minutes:
                reason += (
                    f"; CP-SAT re-sequenced on {machine.machine_id} "
                    f"(position {previous_position} → {position}); {describe_setup(estimate, config)}"
                )
            entry = replace(
                job.entry,
                setup_start=placement.setup_start,
                start=placement.start,
                end=placement.end,
                setup_minutes=estimate.minutes,
                placement_reason=reason,
            )
            apply_entry_to_state(state, entry, job.op, job.order)
            placed_end[entry.entry_id] = entry.end
            out.append(entry)
        return out + [replace(e) for e in fixed]


__all__ = ["CpSatScheduler"]
