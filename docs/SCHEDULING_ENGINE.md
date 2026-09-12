# Scheduling Engine

This document explains how PPSE turns priorities into an executable machine schedule: the
working-time calendars (`app/engines/calendar`), the hard/soft constraints and readiness rules
(`app/engines/constraints`), the rule-based V1 scheduler and its helpers (`app/engines/scheduling`),
the optional CP-SAT re-sequencer, continuous replanning (`app/engines/replanning`) and what-if
simulation (`app/engines/simulation`). It describes what the code does today, the numbers measured
on the performance test data, and where the implementation stops short of the specification.

**How to read this.** `docs/DESIGN_CONTRACT.md` §6.2–§6.6 fix the interfaces; `docs/ARCHITECTURE.md`
§4.2 records why V1 is rule-based; `docs/PRIORITY_ENGINE.md` describes the `PriorityResult`s this
engine consumes; `docs/DATA_MODEL.md` describes `Operation`, `Machine`, `CalendarSpec`, locks and
the persisted `schedule_entries`; `docs/TESTING.md` lists the tests cited here
(`backend/tests/engines/test_calendar.py`, `test_constraints_*.py`, `test_scheduling_*.py`,
`test_replanning_*.py`, `test_simulation_*.py`). Spec phases: 5 (constraints), 6 (scheduling), 7
(simulation), 10 (locking), 11 (replanning), 18 (utilisation), 19 (objectives), 35 (machine
assignment), 36 (quality score).

---

## 1. Inputs and outputs (contract §6.4)

```python
class Scheduler(Protocol):
    name: str
    version: str
    def schedule(self, snapshot, priorities, config, calendars, constraints) -> ScheduleResult: ...
```

`RuleBasedScheduler` (`name="rule_based"`, `version="1.0.0"`) and `CpSatScheduler` (`"cpsat"`,
`"0.1.0"`) both add a keyword-only `previous_entries: Sequence[ScheduleEntry] | None` used for
the frozen window (§6). Instances are created through `SchedulerRegistry` (`registry.py`); the
scheduler never reads the wall clock (`Clock` injected; `now = clock.now()`).

### 1.1 Inputs (spec Phase 6 list → where they come from)

| Spec input | Source in the code |
|---|---|
| Orders, operations | `snapshot.orders`, `snapshot.operations` (pending operations per order via `snapshot.pending_operations_for_order`; an order without routing gets a synthetic order-level operation, `readiness.synthesize_operation`) |
| Machines | `snapshot.machines` (status, `available_from`, current setup family / material / mounted tooling, `preferred_rank`, `efficiency`) |
| Machine calendars, working shifts, downtime | `calendars: Mapping[machine_id, MachineCalendar]` built by `build_calendars(snapshot)` from `CalendarSpec`s plus `Machine.all_downtime` (§3) |
| Materials, tooling | Through the constraint engine's readiness assessment and hard constraints (§4) |
| Operators | Not modelled: `Operation.operator_requirement` is stored but no constraint reads it (`docs/REQUIREMENTS.md` F-03) |
| Due dates | `Order.due_date` (revised > promised > requested) |
| Priorities | `priorities: Mapping[order_id, PriorityResult]` (score, rank, `forced_next`, readiness) |
| Setup times | `Operation.setup_minutes` → `Order.estimated_setup_minutes` → `SchedulingConfig.setup.default_setup_minutes` (30), modified by setup family / material factors and unmounted tooling (§4.3) |
| Cycle times | `Operation.cycle_minutes_per_unit`, per-machine override `machine_cycle_minutes[machine_id]`, divided by `Machine.efficiency` (`Operation.run_minutes_on`) |
| Constraints | `ConstraintEngine` (hard, soft, readiness) |
| Configuration | `SchedulingConfig` (versioned; defaults below) |

`SchedulingConfig` defaults (`app/domain/config.py`):

| Field | Default | Used by |
|---|---|---|
| `algorithm` | `"rule_based"` | Registry lookup by the (not yet implemented) service layer; the schedulers do not read it |
| `horizon_days` | 14 | `horizon_end`; entries starting beyond it are flagged in `warnings`; utilisation is measured inside it |
| `lock_window_minutes` | 240 | Frozen window: previous entries starting within it are reproduced (§6) |
| `stability` | `frozen_window_minutes=30`, `min_improvement_pct=3`, `max_moves_per_replan=None` | Replanning decision (§11) |
| `setup` | `same_family_setup_factor=0`, `same_material_setup_factor=0.5`, `default_setup_minutes=30`, `setup_penalty_cost_per_minute=1` | Setup estimate, `setup_changeover` penalty, batch bonus |
| `batching` | `enabled=True`, `dimensions=["material","part_family"]`, `max_delay_hours=4`, `min_priority_gap=15` | Batching lookahead (§7), `batch_preference` penalty, `batch_key` |
| `machine_preference` | `preferred_machine_cost=0`, `non_preferred_machine_cost_minutes=30`, `utilization_balance_cost_per_pct=0.5`, `energy_cost_per_hour={}` | Soft constraints |
| `schedule_blocked_orders` | False | Defer blocked orders to their blocker's `resolves_at` instead of dropping them |
| `at_risk_slack_hours` | 8 | "At risk" metric and the lateness quality component |
| `max_orders_per_run` | None | Candidate cap (`run_limit` unscheduled items) |
| `quality_weights` | otd 40 / lateness 20 / utilization 15 / setup_efficiency 15 / at_risk 10 | Quality score (§9) |
| `objectives` (`ObjectiveWeights`) | 40/20/10/15/10/5 | **Not read by any engine yet** (reserved for V2 multi-objective, §14) |
| `overtime` (`OvertimeRules`) | `allow_overtime=False` | **Not read by any engine yet**; overtime only enters through calendar `overtime_windows` (scenarios) |

### 1.2 Outputs

`ScheduleResult(algorithm, algorithm_version, profile_id, profile_version, config_version,
generated_at, horizon_start, horizon_end, entries, unscheduled, metrics, quality,
machine_recommendations, warnings, run_id)`. `run_id` is left `None` by the engines (a service
assigns it). Entries are sorted by `(machine_id, setup_start)` and numbered
`sequence_on_machine` 1..n per machine (`rule_based.finalise_entries`).

`ScheduleEntry` against the spec's output list:

| Spec output | `ScheduleEntry` field |
|---|---|
| Machine / Order / Operation | `machine_id`, `order_id`, `operation_id`; `entry_id = "ent_" + operation_id` (deterministic) |
| Start / End | `setup_start`, `start` (run start, after setup), `end` — all UTC, placed on working time only |
| Sequence | `sequence_on_machine` |
| Setup | `setup_minutes` (+ `setup_family`, `material_id`, `batch_key`) |
| Production quantity | `quantity` (the operation's pending quantity) |
| Expected completion / lateness | `expected_completion` (end of the order's last entry), `due_date`, `expected_lateness_hours` (signed; `None` when the order is only partially placed or has no due date); `is_last_operation` |
| Priority score | `priority_score` |
| Reason for placement | `placement_reason` (§8.3), plus `locked` and `customer_id` |

`UnscheduledItem(order_id, operation_id, reason_code, reason, readiness)` reason codes:

| `reason_code` | Produced when |
|---|---|
| `status_not_schedulable` | Open order whose status is not in `SCHEDULABLE_ORDER_STATUSES` (e.g. QUALITY_INSPECTION, ON_HOLD status) |
| `no_priority` | No `PriorityResult` for the order |
| `blocked` | Readiness blockers other than the structural states, and either `schedule_blocked_orders=False` or a blocker without `resolves_at`; also a dependency/prerequisite that could not be scheduled |
| `run_limit` | Beyond `max_orders_per_run` |
| `no_eligible_machine` | No machine passes the hard constraints, or the forced/slot-locked machine is not usable in this run |
| `no_calendar` | Eligible machines exist but none has a calendar |
| `missing_cycle_time` | Run time unknown on every usable machine |
| `data_quality` | Added by `app/engines/pipeline.py::SchedulerAdapter`, not by the scheduler itself: orders with a *blocking* data-quality issue are withheld from the scheduler and reported with their issue messages (§2.1) |

`machine_recommendations[order_id]` holds the Phase 35 `MachineRecommendation` for the order's
first placed (or failed) operation; `metrics` and `quality` are computed last (§9).

---

## 2. Engine boundaries

`app/engines/**` imports nothing from `app/db` or `app/integration`. The scheduler receives a
`PlanningSnapshot`, priority results, calendars and a constraint engine and returns dataclasses;
the same call is used for live planning, simulation and tests. All datetimes are timezone-aware
UTC (`ensure_utc`), durations are minutes.

### 2.1 The planning pipeline (`app/engines/pipeline.py`)

`PlanningPipeline(clock, factors=None, scheduler_name="rule_based", registry=None,
data_quality_engine=None).run(snapshot, system_config, previous_entries=None) -> PipelineResult`
is the pure-engine flow the services, API and CLI are meant to call — still without persistence:

```
data_quality → calendars → constraints → priority_context → priority → schedule
→ quality → kpis → capacity (weekly, per machine group) → bottlenecks → alerts
```

Each stage is timed (`PipelineResult.timings`, seconds, plus `total`) and logged as
`pipeline.stage` with the `run_id` bound. The scheduler is wrapped in a `SchedulerAdapter` that
forwards `previous_entries` when the wrapped scheduler accepts it (warning otherwise) and applies
the **data-quality gate**: orders with a blocking `DataQualityIssue` keep their priority result
(they stay in the queue with their blockers) but are withheld from the scheduler and come back as
`UnscheduledItem(reason_code="data_quality")`, so the schedule accounts for every open order.
`PipelineResult` carries the snapshot, DQ report, calendars, priorities, schedule, KPIs,
capacity, bottlenecks, alerts, the profile/config ids and versions, the scheduler name/version
and `dq_excluded_order_ids`; `summary()` yields the numbers an `optimization_runs` row needs.
`PlanningPipeline.simulate(...)` builds a `SimulationEngine` from the same components (§12) and
`explain_order(result, order_id)` assembles the Phase 34/35 view — priority explanation, placed
entries with their reasons, unscheduled items and data-quality messages — from the objects it
returns, never from independent text.

---

## 3. Calendar model (`app/engines/calendar`)

### 3.1 Data

`CalendarSpec(calendar_id, name, timezone="UTC", shifts, holidays, overtime_windows,
extra_working_days)`:

* `Shift(name, start: time, end: time, weekdays=(0..4))` — times are **local to
  `spec.timezone`**; `end <= start` means the shift crosses midnight and ends on the next local
  date; DST is handled by `zoneinfo`, so a 22:00–06:00 shift is 7 real hours on the spring-forward
  night and 9 on the fall-back night.
* `holidays` — local dates with no shifts; `extra_working_days` — local dates on which every shift
  runs even if it is a holiday or a non-working weekday (explicit extra day wins over holiday).
* `overtime_windows` — absolute UTC `TimeWindow`s added on top of the shifts.
* Machine downtime (`maintenance_windows`, `planned_downtime`, `unplanned_downtime`, all UTC) and
  the "not before `Machine.available_from`" window are subtracted.

### 3.2 Construction and fallbacks (`builder.py`)

`build_calendars(snapshot, extra_downtime=None)` returns one `MachineCalendar` per machine.
`resolve_calendar_spec`: `machine.calendar_id` → `snapshot.default_calendar_id` → a 24×7
fallback spec (`__24x7__`) with a warning (`calendar.fallback_24x7`). One `ShiftPattern` per spec is
shared by every machine using it. `extra_downtime` is how the machine-down scenario adds windows.

### 3.3 Algorithm (`calendar.py`)

Working time is materialised lazily one **UTC day** at a time and cached: the shifts of the local
dates that can overlap the UTC day (`[U−2, U+1]`, enough for offsets −12..+14 and shifts up to
24 h) are converted to UTC segments, clipped, merged with overtime windows, then downtime and the
pre-`available_from` window are subtracted (`merge_segments`, `subtract_segments`). Public API:

| Method | Semantics |
|---|---|
| `is_working(t)` | `t` inside a working segment |
| `next_working_time(t)` | earliest working instant ≥ `t` |
| `add_work_minutes(start, minutes)` | instant at which `minutes` of working time after `start` are done; 0 minutes → `next_working_time`; work ending exactly on a shift boundary ends *at* the boundary |
| `working_minutes_between(a, b)` | working minutes in `[a, b)` |
| `working_windows(a, b)`, `downtime_windows(a, b)`, `available_hours(a, b)`, `describe()` | reporting helpers |

A calendar with no working time within `MAX_SEARCH_DAYS = 60` consecutive days raises
`ConfigurationError` instead of looping (the priority context and `place_on_calendar` callers
catch it where a fallback is possible). Two Hypothesis properties assert that `add_work_minutes`
is monotone and consistent with `working_minutes_between` on multi-timezone, multi-shift specs
with downtime (`test_calendar.py`).

---

## 4. Constraint engine (`app/engines/constraints`)

`ConstraintEngine(hard, soft, config)` (`default_constraint_engine(config)` builds the shipped
set). It holds no snapshot state; every call receives the snapshot and the instant `at`.

### 4.1 Candidate narrowing (`eligibility.py`)

Before hard constraints run, `candidate_machines(op, order, snapshot)` picks the plausible set, in
precedence: `op.eligible_machine_ids` (explicit ERP list) → `order.required_machine_id` →
`op.machine_group` or `order.machine_group` → every machine supporting `op.operation_type`.
The order-level fields (`required_machine_id`, `machine_group`, and `tooling_requirement`) describe
the order's *primary* process and are applied only to operations whose `operation_type` equals
`order.process_type` (`hard.is_primary_operation`; every operation counts as primary when the
order's process type is `OTHER`), so the deburring step of a CNC order is never pinned to the CNC
machine. Candidates are sorted by `(preferred_rank, machine_id)`; `evaluate_eligibility` returns
`EligibilityResult(eligible_machine_ids, rejected: machine_id → [Violation])` — every constraint
is evaluated so a rejected machine carries all of its reasons.

### 4.2 Hard constraints (`hard.py`)

Each returns a `Violation(constraint_key, message, details)` or `None`. Rules fire only when both
sides of a comparison are known — incomplete master data widens eligibility (the Data Quality
Engine reports it) instead of silently blocking.

| Key | Checks | Data read |
|---|---|---|
| `process_capability` | `machine.supports_process(op.operation_type)` (own process or `compatible_processes`) | machine, operation |
| `explicit_eligibility` | machine in `op.eligible_machine_ids` when set; equals `order.required_machine_id` when set (primary operations only); equals `op.machine_id` while the operation is IN_PROGRESS (otherwise `op.machine_id` is a soft preference) | operation, order |
| `machine_group` | machine group equals `op.machine_group` (else, for primary operations, `order.machine_group`) unless an explicit list exists | operation, order, machine |
| `material_compatibility` | `machine.compatible_materials` (when non-empty) must contain the material; `material.compatible_machine_ids` (when non-empty) must contain the machine | operation/order material, machine, material |
| `tooling_compatibility` | every required tool with a known `compatible_machine_ids` must list the machine | `op.tooling_ids`, else `order.tooling_requirement` for primary operations only (`required_tooling_ids`), tooling |
| `machine_operable` | status AVAILABLE/RUNNING passes; DOWN, OFFLINE or MAINTENANCE passes **only** when one of the machine's downtime windows ends after `at` (the calendar removes the window and the scheduler starts work at the return); otherwise rejected with "… with no scheduled return" | machine status, downtime |
| `part_size` | sorted part dimensions (`order.attributes["part_size_mm"]` or `part_length/width/height_mm`) fit the sorted `machine.max_part_size_mm` | order attributes, machine |
| `quantity_restriction` | `machine.attributes["min_batch_qty"|"max_batch_qty"]` bound the pending quantity | machine attributes |
| `locked_machine_assignment` | an active ORDER/MACHINE lock naming a machine, or an active `LOCK_MACHINE_ASSIGNMENT` override, pins the order (locks beat overrides; latest wins); the pin applies only to operations the pinned machine can perform — other steps of the route stay free | snapshot locks/overrides |

### 4.3 Soft constraints and the setup estimate (`soft.py`)

Soft constraints return `Penalty(constraint_key, cost, message, details)` with `cost` in
**minute-equivalents** (negative = bonus) so the scheduler can trade preference against time.
They receive a `ConstraintContext(snapshot, at, config, order, machine_state,
group_average_load_minutes)`; `MachineState` is the scheduler's mutable view of a machine
(`next_free`, current family/material, mounted tooling, `scheduled_minutes`, last customer/part
family).

| Key | Cost (defaults) | Config |
|---|---|---|
| `preferred_machine` | 30 min when the machine is not `op.machine_id` (0 for the preferred machine; omitted when `op.machine_id` is unset or the cost is 0) | `machine_preference.non_preferred_machine_cost_minutes`, `preferred_machine_cost` |
| `setup_changeover` | `estimate_setup(...).minutes × setup_penalty_cost_per_minute` (1/min) | `setup.*` |
| `utilization_balance` | `0.5 × (% the machine's planned minutes sit above its group average)`; only above average | `machine_preference.utilization_balance_cost_per_pct` |
| `energy_cost` | `energy_cost_per_hour[machine_id] × run hours` (empty map by default → never fires) | `machine_preference.energy_cost_per_hour` |
| `customer_sequence` | −`bonus` when the machine's last job was the same customer | bonus derived, see below |
| `batch_preference` | −`bonus` per configured dimension shared with the machine's last job: `material`, `part_family`, `tool` (all required tooling mounted); `customer` is covered by `customer_sequence`; other dimensions have no machine-state counterpart | `batching.enabled`, `batching.dimensions` |

`batch_bonus_minutes(config) = default_setup_minutes × same_material_setup_factor ×
setup_penalty_cost_per_minute` (30 × 0.5 × 1 = 15 min) — there is no dedicated bonus field, so the
bonus is "the changeover a same-material follow-on saves".

`estimate_setup(op, order, machine, state, config, ctx)` is the **single** setup formula used by
the scheduler, the `setup_changeover` penalty, the batching rules and the priority factor
`setup_efficiency`:

```
base    = op.setup_minutes | order.estimated_setup_minutes | config.setup.default_setup_minutes
factor  = same_family_setup_factor   (op.setup_family == machine's current family)      # 0.0
        | same_material_setup_factor (material == machine's current material)          # 0.5
        | 1.0                          (changeover, or machine state unknown)
minutes = base × factor + Σ tooling.setup_minutes for required tools not mounted (needs ctx)
```

`SetupEstimate(minutes, base_minutes, factor, basis, tooling_minutes, reason, base_source)`;
`scheduling/setup.py::compute_setup` wraps it and returns 0 minutes for an operation already IN
PROGRESS on that machine; `describe_setup` renders "60 min setup: changeover from family 'F1'/
material 'AL' (no ERP setup time; default 30 min used) [30 min x 0.5]".

### 4.4 Readiness (`readiness.py`)

`assess_order(order, snapshot, at, config)` returns `ReadinessAssessment(state, blockers, notes,
next_operation_id, eligible_machine_ids)`; `ConstraintEngine.assessment_map` does it for every
order with one shared `ReadinessIndex`. Checks, in order (each yields a `Blocker(state, message,
resolves_at, details)`):

1. closed status or nothing pending → OTHER_CONSTRAINT (short-circuits);
2. `order.on_hold`, status ON_HOLD, or an active HOLD_ORDER override not superseded by a later
   RELEASE_HOLD → ON_HOLD (`resolves_at` = override expiry);
3. `quality_status` HOLD/FAILED → QUALITY_HOLD;
4. `drawing_approved=False` → WAITING_APPROVAL;
5. material: `material_status` UNAVAILABLE/ON_ORDER → WAITING_MATERIAL (`resolves_at` = material's
   `expected_receipt_date`); AVAILABLE → ok; otherwise free quantity vs `pending × per-unit`, with
   incoming stock giving a `resolves_at`; unknown material / per-unit → a *note*, not a blocker;
6. tooling not usable (`available=False` or life exhausted) or `available_from > at` →
   WAITING_TOOLING;
7. `depends_on_order_ids` still open (cancelled dependency → OTHER_CONSTRAINT) and an unfinished
   `prerequisite_operation_id` → WAITING_PREVIOUS_OPERATION (`resolves_at` from estimated ends);
8. no eligible machine at all (candidate rejections summarised, e.g. `machine_operable (2)`), or
   every eligible machine unavailable at `at` (down with a known return, or before
   `available_from`) → MACHINE_UNAVAILABLE; in the second case `resolves_at` = the earliest
   eligible machine's return.

The single state is the highest-precedence blocker in `READINESS_PRECEDENCE`:
`ON_HOLD > QUALITY_HOLD > WAITING_APPROVAL > WAITING_MATERIAL > WAITING_TOOLING >
MACHINE_UNAVAILABLE > WAITING_PREVIOUS_OPERATION > OTHER_CONSTRAINT > READY`. Missing data never
blocks; only positive evidence does.

---

## 5. Rule-based scheduler V1, step by step (spec Phase 6 "VERSION 1")

`RuleBasedScheduler.schedule` (`rule_based.py`) wires the modules; the loop is
`run.py::ListScheduler`.

| Spec step | Implementation |
|---|---|
| 1. Remove blocked orders | `candidates.select_candidates`: readiness of every order from `assessment_map`; orders with *real* blockers become `UnscheduledItem("blocked")`. Two states are handled structurally, not as blockers: WAITING_PREVIOUS_OPERATION (dependencies are placed first and their completion is the release) and MACHINE_UNAVAILABLE (calendars/machine states model downtime). With `schedule_blocked_orders=True` and every blocker carrying `resolves_at`, the order is kept with `release = max(resolves_at)` and a `blocker_note` in its reason. Non-schedulable statuses, missing priorities and `max_orders_per_run` overflow are also removed here. Inside `PlanningPipeline` (§2.1) orders with a *blocking* data-quality issue are withheld before this step and reported as `data_quality`. |
| 2. Calculate priority score | Done beforehand by the priority engine; the scheduler only reads `score`, `rank`, `forced_next`. |
| 3. Sort eligible orders | Global processing order by bucket: (0) orders with an operation IN_PROGRESS on a machine, (1) order-locked orders in lock creation order, (2) `forced_next`, (3) everything else by `(-score, due_date, order_id)`. SEQUENCE locks then re-assign their orders to the positions they occupy, in the locked order (`apply_sequence_locks`). |
| 4. Assign orders to compatible machines | Per pending operation, in sequence: `eligibility` (cached per operation) → usable machines (initialised state, not frozen) → `rank_machines` (§8) → recommended machine, unless a forced/slot-locked machine is required. |
| 5. Optimise machine sequence | The machine sequence *is* the processing order: machines are never back-filled; each placement goes after the machine's last job (`state.next_free`). Opportunistic batching (§7) may pull a same-setup order forward right after a placement. |
| 6. Respect operation dependencies | Release of an operation = `max(order release, dependency completions, cross-order prerequisite end, previous operation end)`. Dependencies (`depends_on_order_ids`) are processed *before* the dependent (`ListScheduler.process` recurses), so a high-priority dependent lifts its upstream order; an unschedulable dependency makes the dependent `blocked`. |
| 7. Calculate projected completion | `place_on_calendar(calendar, earliest, setup, run, reserved)`: `setup_start = next_working_time(earliest)`, `start = add_work_minutes(setup_start, setup)`, `end = add_work_minutes(start, run)`, retried past any reserved lock window. `finalise_entries` sets `expected_completion` = end of the order's last entry. |
| 8. Detect lateness | `expected_lateness_hours = end − due_date` (signed); `metrics.late_orders`, `orders_at_risk`, `revenue_at_risk` (§9). |
| 9. Recalculate priority | Not done inside the scheduler; a replan re-runs the priority engine on a fresh snapshot (§11, `docs/ARCHITECTURE.md` §3.2). |
| 10. Produce schedule | `ScheduleResult` with entries, unscheduled items, recommendations, warnings (excluded machines, dropped frozen entries, beyond-horizon entries, batched count), metrics and quality. |

Machine states (`state.init_machine_states`): one `MachineState` per machine with a calendar;
`next_free = max(now, available_from)`; inoperable machines start at the end of their known
downtime and are **excluded** (warning) when no return is known; planner-frozen machines keep their
old entries but take no new work. Failures never abort a run: an operation without machine / cycle
time / calendar becomes an `UnscheduledItem`, the rest of that order is skipped (already placed
entries stay, the order counts as unscheduled, `expected_completion` is cleared).

---

## 6. Locks and the frozen window (spec Phases 9, 10)

`locks.build_lock_index(snapshot, now)` normalises `snapshot.active_locks(now)` (creation order,
expired windows dropped):

| `LockType` / shape | Effect |
|---|---|
| `TIME_SLOT` or `MACHINE` **with a window** and a machine | The window is *reserved* on that machine: `place_on_calendar` skips it. If the lock also names an order, that order's next operation is placed at the window start on that machine (`locked=True`) and the order is processed in the locked bucket. |
| `ORDER` (or `MACHINE` naming an order) with a machine | The order is *pinned* to the machine (hard constraint `locked_machine_assignment`) and processed first, in lock creation order. |
| `ORDER` without a machine | Processed first; its previous entries are kept verbatim when `previous_entries` are supplied. |
| `SEQUENCE` (`sequence_order_ids`, ≥ 2 ids) | Listed orders keep their relative order in the global queue. |
| `MACHINE` naming neither order nor window | The machine is *frozen*: previous entries are reproduced, nothing new is placed on it (`Machine … excluded: machine locked by planner`). |

**Frozen window ("lock the next 4 hours").** `state.reproduce_frozen_entries(previous_entries,
…)` copies every entry of the previous schedule whose `setup_start < now +
config.lock_window_minutes` (240), plus entries of order-locked orders and frozen machines, as
`locked=True` entries that become the head of each machine queue. Finished entries, entries of
closed orders, of machines not in this run, or contradicting a newer pin are dropped with a
warning. Note the two different windows: the **scheduler** freezes with
`SchedulingConfig.lock_window_minutes` (240); the **replanning** stability check verifies
`StabilityRules.frozen_window_minutes` (30). Tests: `test_order_lock_pins_machine_and_goes_first`,
`test_time_slot_lock_reserves_window`, `test_sequence_lock_fixes_relative_order`,
`test_machine_lock_freezes_machine`, `test_entries_inside_lock_window_reproduced_verbatim`.

---

## 7. Batching rules and guard rails (spec Phase 4 §10, Phase 6)

After every placement the scheduler runs a bounded **lookahead** on the machine that received work
(`run.py::_batch_lookahead`): it scans at most `batch_lookahead` (25) not-yet-processed queue slots
(a union-find skips slots pulled forward earlier), builds a `BatchCandidate` for each order whose
next unplaced operation is eligible on that machine, is released by the machine's `next_free`, has
no unfinished dependencies/prerequisites and a known run time, and asks `batching.pick_next`.

`pick_next(queue, machine_state, config, calendar)` returns the head unless a later candidate
satisfies **all** of:

* it shares the machine's current setup — the setup family, or one of the configured
  `batching.dimensions` with a machine-state counterpart (`material`, `part_family`, `tool`,
  `customer`);
* the head is neither `forced_next` nor locked, and no forced/locked item is bypassed (the scan
  stops at the first);
* `head.score − candidate.score ≤ min_priority_gap` (15; the scan stops at the first candidate
  outside the gap since the queue is priority-ordered);
* pulling it forward delays the head by at most `max_delay_hours` (4) of its setup + run;
* every bypassed job still meets its due date after the delay, checked with the machine calendar
  (`_due_dates_hold`), not only the head.

The pulled order is then processed with `forced_machine=machine_id` and a `batch_note` that ends
up in `placement_reason` ("batched: pulled forward ahead of 2 job(s): shares material with the
current setup on CNC-01; delays O-7 by 1.5 h (limit 4 h), score gap 9.0 (limit 15)"); the run's
`warnings` count pulled entries. Bypassed jobs' delay uses the *base* setup as a conservative upper
bound. Tests: `test_scheduling_batching.py`, `test_batching_pulls_same_material_job_forward`.

---

## 8. Machine assignment (spec Phase 35)

### 8.1 Ranking (`machine_assignment.rank_machines`)

For every eligible machine, with the same code the placement uses:

```
earliest    = max(now, release, state.next_free, machine.available_from)
setup       = compute_setup(op, machine, state, config, ctx)          # family/material/tooling aware
run         = op.run_minutes_on(machine)                               # None → rejected "cycle time unknown"
setup_start, end = place_span(calendar, earliest, setup + run, reserved windows)
soft_cost   = Σ constraint_engine.soft_penalties(op, machine, ctx).cost
total_cost  = minutes(now → end) + soft_cost
sort key    = (total_cost, machine.preferred_rank, machine_id)
```

Feasible candidates get `rank` 1..n and `recommended = (rank == 1)`; machines with no state
(excluded from the run), no calendar or unknown cycle time are appended as rejected candidates
with `expected_end=None`. `build_recommendation` assembles the `MachineRecommendation(order_id,
operation_id, eligible, recommended_machine_id, rejected: machine → reasons)` including hard
constraint rejections.

Spec Phase 35 criteria and how each is covered: capability (hard constraints), availability
(`next_free`, calendar, reserved windows), current workload (`next_free` and the
`utilization_balance` penalty on `scheduled_minutes`), setup requirements (`compute_setup`,
`setup_changeover`), expected completion (`end`), efficiency (`Machine.efficiency` in
`run_minutes_on`), utilisation (`utilization_balance`), downstream impact — **approximated by the
lowest planned load** ("Lower downstream impact (lowest planned load: 0.7 h)"); no successor-chain
analysis is performed.

### 8.2 Reasons

`_explain` renders every candidate's `reasons` from the numbers just computed: for the winner
"Expected completion 2.3 hours earlier than next best (CNC-01)" or the tie explanation ("… tied
with CNC-02; preferred by machine rank 0 vs 1" / "lower preference cost 60 vs 90"), "No setup change
needed (same setup family 'F1')", "No additional tooling setup", "Machine currently available",
"Lower downstream impact (…)"; for the others "Expected completion 1.0 hours later than CNC-02" or
"Same expected completion … but higher cost"; then for all: expected start/end, the setup
description, "Run 2.0 h" and every soft-penalty message.

### 8.3 Placement reason

`ScheduleEntry.placement_reason` = `"Rank 2 (score 56.1) → CNC-01: <winner headline reasons>;
<setup description>[; operation in progress | planner lock <id> | forced next by planner][;
deferred until blockers resolve at …][; batched: …]"`.

---

## 9. Metrics and the quality score (spec Phases 18, 36)

### 9.1 `compute_metrics(result, snapshot, calendars, config)` (`metrics.py`)

* an order is *scheduled* when it has entries and no `UnscheduledItem` (partially placed orders
  count as unscheduled);
* completion = end of the order's last entry; `lateness = completion − due`; late when > 0; on
  time otherwise; *at risk* when on time with slack `< at_risk_slack_hours` (8); orders without a
  due date are neither late nor at risk (`on_time_pct` is over dated orders; 100 % when there are
  scheduled but no dated orders);
* `avg_lateness_hours` = mean tardiness of the *late* orders; `total_tardiness_hours` = Σ positive
  lateness; `max_lateness_hours`;
* `total_run_hours`, `total_setup_hours`, `setup_count` (entries with setup > 0),
  `makespan_hours` (horizon start → last end);
* utilisation per machine = working minutes occupied by entries inside the horizon ÷ working
  minutes the calendar offers inside the horizon; `overall_utilization_pct` is the pooled ratio;
* `revenue_scheduled`; `revenue_at_risk` / `margin_at_risk` = value/margin of late orders plus
  unscheduled orders except `status_not_schedulable` ones (`OUT_OF_SCOPE_CODES`);
* `wip_orders_avg` = Σ per-order span (first setup start → last end, clipped to the horizon) ÷
  horizon length.

### 9.2 `compute_quality(result, config)` (`quality.py`)

Components, each 0..100:

```
on_time_delivery = on_time_pct
lateness         = 100 / (1 + avg_lateness_hours / at_risk_slack_hours)   # one slack of lateness halves it
utilization      = overall_utilization_pct
setup_efficiency = 100 × run / (run + setup)                               # 100 when no work
at_risk          = 100 × (1 − orders_at_risk / scheduled_orders)          # 100 when nothing scheduled
score            = Σ component × normalised(config.quality_weights)       # unknown keys ignored
```

`ScheduleQuality(score, components, weights, summary)`; summary = "On-time 94% · Utilization 87%
· Setup efficiency 81% · Avg lateness 2.4 h · At risk 14". `compare_schedules(a, b)` produces the
spec's before → after pairs ("On-time delivery: 87% → 94%; Average lateness: 8.2h → 2.4h; …") for
on-time %, average lateness, utilisation, setup hours, late orders, orders at risk, revenue at
risk, scheduled orders and the quality score.

Note that utilisation is measured over the whole `horizon_days` window: a two-day workload in a
14-day horizon reports a low utilisation and therefore a low utilisation component (see the worked
example, §15).

---

## 10. CP-SAT V2 re-sequencer (`cpsat.py`)

Optional: the module imports `ortools.sat.python.cp_model`; `default_registry()` registers
`"cpsat"` only when the import succeeds (`cpsat_available()`), so a deployment without the
`optimization` extra keeps working with `rule_based`. Constructor parameters: `time_limit_seconds`
(10), `top_k_machines` (3), `max_jobs_per_machine` (60), `num_workers` (1), `random_seed` (0).

Scope, deliberately bounded:

1. run the rule-based scheduler (warm start and fallback);
2. pick the `top_k_machines` with the most planned minutes;
3. on each, independently, re-sequence the *movable* jobs (locked entries and reserved lock
   windows are fixed intervals) to minimise
   `Σ 100 × (priority_score + 1) × tardiness + 100 × setup_penalty_cost_per_minute × Σ setup`,
   in working-minute coordinates (`working_minutes_between` from an anchor, so non-working time is
   invisible). Model: one interval per job with `start ≥ release` (end of its predecessor on
   another machine) and `end ≤ current end` when a successor is already placed on another machine
   (other machines are never touched); `AddNoOverlap`; `AddCircuit` over the jobs gives the
   sequence and the sequence-dependent setup (priced with `compute_setup`); same-order jobs on the
   same machine keep their operation order; hints from the rule-based starts;
4. rebuild the machine's entries through `place_on_calendar`/`compute_setup`; keep the new
   sequence only when quality does not drop; otherwise keep the rule-based entries for that machine.

Falls back to the rule-based result (with a `cpsat: …` warning) when: fewer than two movable jobs
or more than `max_jobs_per_machine` on the machine; no FEASIBLE/OPTIMAL solution within the time
limit; the solution leaves the sequence unchanged; the rebuild would violate a deadline or place a
predecessor after its successor; or the schedule quality would fall. `test_scheduling_cpsat.py`
shows a low-score but very urgent order being moved ahead on the deburring machine, reducing late
orders without violating precedence or overlap, and that two runs are identical.

---

## 11. Continuous replanning (spec Phase 11, `app/engines/replanning`)

### 11.1 Triggers (`triggers.py`)

`detect_events(previous_snapshot, current_snapshot, alerts=AlertConfig())` compares two snapshots
in O(orders + operations + machines + materials + customers) and emits `ReplanEvent(type,
entity_type, entity_id, occurred_at, message, order_id, machine_id, details)`:

| Observation | `ReplanTriggerType` |
|---|---|
| Order appears (open) | `NEW_ORDER` (details: `overdue`, `expedited`, `forced_next`) |
| Order closes, is cancelled, or disappears from the feed | `ORDER_COMPLETED` |
| `quality_status` → FAILED/HOLD | `QUALITY_FAILURE` |
| Status or quality → REWORK; operation status → REWORK | `REWORK` |
| Operation `estimated_end` drifts later, actual start later than estimate, or still running past its planned end, by more than `AlertConfig.behind_schedule_minutes` (60) | `PRODUCTION_DELAY` (details `overdue` when the projected end passes the due date) |
| Due date, `erp_priority`/`customer_priority`/`commercial_priority`/`technical_priority` change; customer tier/priority/strategic flag/escalation change; customer rule added/changed/removed; new active expedite | `CUSTOMER_PRIORITY_CHANGE` (`details["change"]` says which) |
| `material_status` → AVAILABLE; material `available_quantity` increases | `MATERIAL_ARRIVED` |
| Machine operable → not operable; new future downtime window; machine removed from feed | `MACHINE_DOWN` |
| Machine not operable → operable; future downtime cancelled; new machine | `MACHINE_UP` |
| New active override or lock | `MANUAL` |
| Config change, scheduled run | built by services with `manual_event(type=CONFIG_CHANGE | SCHEDULED)` |

### 11.2 Stability (`stability.py`)

`apply_stability(previous, proposed, now, StabilityRules, snapshot, restore=True)` returns the
(possibly repaired) proposal and a `StabilityReport`:

* **frozen window** — every previous entry still pending that starts within
  `frozen_window_minutes` (30) of `now` must keep machine and start (± 1 minute tolerance); a
  frozen entry that is missing, moved or re-assigned counts as a `frozen_violation`, and with
  `restore=True` the previous placement is put back as a locked entry with a warning (restoring
  can overlap a proposed entry on the same machine — that is exactly what the violation count
  exposes and why approval is then required);
* **moves** — `moved` (machine or start changed), `added`, `removed`, `unchanged`;
  `max_moves_exceeded` when `moved > max_moves_per_replan`;
* **improvement** — `improvement_pct = quality_after − quality_before` (percentage points on the
  0..100 quality score), `quality_known` when both exist.

### 11.3 Decision (`engine.py::ReplanningEngine`)

`should_trigger(events)`: replanning enabled and any event type is in `ReplanningConfig.trigger_on`
(`MANUAL` and `SCHEDULED` always count). `evaluate(events, current, proposed, now)` →
`ReplanDecision(should_replan, reason, improvement_pct, changed_entries, frozen_violations,
requires_approval, triggers)`:

1. no current schedule → replan;
2. a **hard event** makes the current plan infeasible → replan regardless of improvement: a
   machine down with orders still scheduled on it, a new order that is forced-next / expedited /
   already overdue, quality failure or rework on a scheduled order, a delay pushing a scheduled
   order past its due date (`hard_events`);
3. identical proposal → no; `max_moves_per_replan` exceeded → no; quality unknown → yes;
   `improvement_pct ≥ min_improvement_pct` (3) → yes; otherwise no ("Do not change an order
   scheduled within the next 30 minutes unless the improvement exceeds a configurable threshold").

`requires_approval = should_replan and (config.require_approval (default True) or
frozen_violations > 0 or max_moves_exceeded or changed orders ≥ significant_change_orders (5))`.
`compare(current, proposed)` returns `compare_schedules` plus the change list for the planner's
old-vs-new view. Nothing runs this loop automatically yet: `app/workers` is empty and
`python -m app.cli worker` is a stub that exits with "background worker not yet implemented"
(`docs/DEPLOYMENT.md` §1).

---

## 12. What-if simulation (spec Phase 7, `app/engines/simulation`)

`SimulationEngine(priority_engine, scheduler, constraint_engine_factory=default_constraint_engine,
calendar_builder=build_calendars, clock, currency="INR", bottleneck_fn=None).run(snapshot,
scenarios, profile, config, previous_entries=None) -> SimulationResult`:

1. **baseline** = `plan(snapshot)`: constraint engine and calendars built, priority context built
   with the *same* calendars/constraint engine, priorities evaluated, scheduler run;
2. **scenario** = every scenario applied in order on **one clone** (`snapshot.clone()`) via
   `apply_scenarios`; profile and config flow through (`weight_change` returns a new profile);
   then `plan(clone)` — the same code path, so a difference is caused by the scenario alone;
3. **diff** = `diff_schedules(...)`. The input snapshot is never mutated and nothing is persisted
   as a schedule version. `PlanningPipeline.simulate` (§2.1) builds this engine from the
   pipeline's own priority engine and scheduler, wrapped in the data-quality gate, so a what-if
   baseline is exactly what `run` would plan.

Scenarios are Pydantic models discriminated on `kind` (`scenarios.py::Scenario`); unknown
references raise `ValidationError` rather than silently doing nothing:

| `kind` | Effect on the clone |
|---|---|
| `machine_down` | `unplanned_downtime` window (`start` default now; `end` or `duration_hours`); status left as reported |
| `add_machine` | deep copy of an existing machine as a fresh idle one (own id, calendar, `available_from`); tool and material releases keyed on the source machine (`compatible_machine_ids`) are extended to the clone |
| `extra_working_day` | date added to `extra_working_days` of one or `"all"` calendars |
| `extra_shift` | local start–end on a date → absolute overtime window on one/all calendars |
| `urgent_orders` | inline orders (route steps) or clones of existing orders with a new due date, injected as NEW at `as_of`, expedited by default with profile defaults |
| `outsource` | pending quantity booked as `cancelled_quantity` on named orders, or the least urgent orders queued for a machine group up to `quantity` pieces (operations shrink accordingly) |
| `material_delay` | later `expected_receipt_date`; orders using the material set ON_ORDER (allocated stock kept unless `affects_allocated_stock`) |
| `material_arrival` | stock received now (orders → AVAILABLE) or expected at a date |
| `prioritize_customer` | a `CustomerRule` (boost points / tier override / SLA) written into `snapshot.customer_rules` |
| `weight_change` | partial weight overrides by factor key and/or a full replacement `PriorityProfile` |
| `due_date_change` | `revised_delivery_date` set |
| `hold_orders` | `on_hold=True` with reason |
| `expedite_orders` | active `Expedite` from `as_of` (profile defaults when points/hours omitted) |

Every scenario leaves a trail in `attributes["simulation_notes"]` of the entities it touched and
reports `notes`, `affected_order_ids`, `affected_machine_ids` in `SimulationResult.scenarios`.

`ScheduleDiff` (`diff.py`), all derived from the two schedules, their metrics and the two
snapshots: `orders_affected` (completion moved > 1 min, machine changed, late flag flipped, or
scheduled in only one plan), `orders_moved_machine`, `orders_resequenced` (first operation's
sequence changed on the same machine), `orders_newly_late`, `orders_newly_on_time`, late orders /
on-time % / average lateness / utilisation / setup hours / revenue and margin at risk before and
after, `additional_overtime_hours` (hours of scheduled work outside the regular shifts — inside
overtime windows or on extra working days — after minus before), `bottlenecks_before/after`
(`bottleneck_fn` when injected, else the top-3 machine groups by mean utilisation), per-order
`OrderDelta`s sorted by |delta|, and `summary` ("17 orders affected; late orders 12 → 15; on-time
91.3% → 88.0%; utilisation 76% → 87%; revenue at risk ₹4.2 L → ₹6.1 L; additional overtime +8.0 h;
bottlenecks CNC (92%) → AM (95%)"). `money.format_money` renders INR in lakh/crore.

---

## 13. Complexity and measured performance

Design bounds (`docs/ARCHITECTURE.md` §6): readiness index built once; eligibility cached per
operation; per-machine state advanced in O(1); no back-filling and no pairwise order loops;
batching lookahead bounded by 25 slots; calendars cache working segments per UTC day. Overall
V1 cost is O(operations × eligible machines × calendar look-ups).

Measured on the perf test snapshot (`tests/engines/test_scheduling_perf.py::_big_snapshot`: 5,000
orders, 12,000 operations over 5 routings, 45 machines in 4 groups, 800 customers, one
08:00–16:00 weekday calendar, 30-day horizon) in this development container:

| Step | Measured | Test bound |
|---|---|---|
| `RuleBasedScheduler.schedule` (priorities pre-built) | **6.3 s**, 12,000 entries, 0 unscheduled | `< 10 s` (`test_large_plant_under_ten_seconds`, `slow`) |
| `PriorityEngine.evaluate` with the full context on the same snapshot | **53.6 s** | engine test bound is 600 orders `< 5 s` (`test_evaluate_scales_to_many_orders`); `docs/REQUIREMENTS.md` NFR-02 asks ≤ 30 s for 20,000 orders — **not met at 5,000 orders in this measurement** |
| `DataQualityEngine.run` | — | 20,000 orders `< 2 s` (`test_twenty_thousand_orders_under_two_seconds`, `slow`) |
| Synthetic generator, `large` scale | — | `< 20 s` (`test_large_scale_performance`, `slow`) |

The priority-context build (readiness for every order, per-operation candidate enumeration,
projections, batching index) dominates the end-to-end time at this scale and is the first target
for optimisation; the scheduler itself is inside its budget. `PlanningPipeline` records the
per-stage seconds in `PipelineResult.timings` and logs `pipeline.stage`, so this split is visible
on every real run.

---

## 14. Known limitations and roadmap

Limitations of the code as it stands:

* Objectives (`ObjectiveWeights`) and overtime rules (`OvertimeRules`) are configuration only;
  no engine reads them. The quality score uses `quality_weights`; CP-SAT minimises weighted
  tardiness plus setup.
* No back-filling: a short job never fills a gap before an earlier-placed long job on the same
  machine; sequencing quality comes from the priority order and the bounded batching lookahead.
* "Downstream impact" in machine ranking is the lowest planned load, not a chain analysis.
* Operators, fixtures and energy pricing are not constraints (energy exists as a soft cost with an
  empty default map); operation sequence within an order is strictly serial; lot splitting and
  parallel machines for one operation are not supported.
* CP-SAT re-sequences per machine with fixed machine assignment; it never moves a job to another
  machine and works on at most `top_k_machines`.
* Scheduler utilisation and the quality component are horizon-relative (see §9.2).
* `PlanningPipeline` (§2.1) orchestrates the engines without persistence, and application
  services / API routers exist for queries, overrides, locks, expedites, configuration, customer
  rules, alerts, audit, data quality and users — but no service or endpoint yet runs the pipeline
  to generate, approve or publish a schedule *version* (`/schedule/generate`, `/schedule/simulate`,
  `/schedule/approve`, `/schedule/publish`, `/schedule/versions`, `/analytics/*` and `/sync/*` of
  contract §9 are missing), and the background worker is a CLI stub.

Roadmap (spec Phase 6 "VERSION 2", Phase 19; `docs/REQUIREMENTS.md` F-01/F-02): extend the CP-SAT
model to machine choice within a group with `ObjectiveWeights` as the objective (on-time delivery,
total tardiness, setup time, utilisation, margin, WIP) and V1 as warm start; add local-search /
simulated-annealing schedulers behind the same `Scheduler` protocol; benchmark every algorithm by
the quality score against V1 on the synthetic and, later, real datasets; operator/skill and
fixture constraints; scrap/rework loops.

---

## 15. Worked example

Run with `backend/.venv/bin/python` at a frozen clock of Monday 2026-09-07 08:00 UTC: four orders
(`O-A` due in 10 h, `O-B` due in 30 h, `O-C` due in 26 h, `O-H` due in 12 h but on hold), two CNC
machines (CNC-01 currently set up for material `AL` / family `F1`, CNC-02 with unknown state and
`preferred_rank=1`) and one deburring machine, all on an 08:00–16:00 UTC weekday calendar; CNC
operations 60 min setup + 12 min/unit × 10, deburring 10 min + 3 min/unit; `O-A` and `O-B` have a
deburring second operation; default `PriorityProfile()` and `SchedulingConfig()`.

```python
config = SchedulingConfig()
calendars = build_calendars(snapshot)
constraints = default_constraint_engine(config)
priorities = PriorityEngine(default_factors(), FrozenClock(NOW)).evaluate(snapshot, PriorityProfile())
result = RuleBasedScheduler(FrozenClock(NOW)).schedule(snapshot, priorities, config, calendars, constraints)
```

Real output:

```
priorities: {'O-A': (55.6, 3, 'ready'), 'O-B': (56.1, 2, 'ready'), 'O-C': (50.7, 4, 'ready'), 'O-H': (60.3, 1, 'on_hold')}

machine  seq  order  op      setup_start       start             end               setup  run   lateness_h
CNC-01   1    O-B    O-B-10  Mon 08:00         Mon 09:00         Mon 11:00         60     120   -26.33
CNC-02   1    O-A    O-A-10  Mon 08:00         Mon 09:00         Mon 11:00         60     120   -5.67
CNC-02   2    O-C    O-C-10  Mon 11:00         Mon 11:00         Mon 13:00         0      120   -21.0
DEB-01   1    O-B    O-B-20  Mon 11:00         Mon 11:10         Mon 11:40         10     30    -26.33
DEB-01   2    O-A    O-A-20  Mon 11:40         Mon 11:50         Mon 12:20         10     30    -5.67

ent_O-B-10: Rank 2 (score 56.1) → CNC-01: Earliest expected completion (tied with CNC-02; preferred by machine rank 0 vs 1); Machine currently available; 60 min setup: changeover from family 'F1'/material 'AL'
ent_O-A-10: Rank 3 (score 55.6) → CNC-02: Expected completion 3.0 hours earlier than next best (CNC-01); Machine currently available; Lower downstream impact (lowest planned load: 0.0 h); 60 min setup: machine state unknown, full setup assumed
ent_O-C-10: Rank 4 (score 50.7) → CNC-02: Expected completion 1.0 hours earlier than next best (CNC-01); No setup change needed (same setup family 'F1'); 0 min setup: same setup family 'F1' [60 min x 0]
ent_O-B-20: Rank 2 (score 56.1) → DEB-01: Only eligible machine; Lower downstream impact (lowest planned load: 0.0 h); 10 min setup: machine state unknown, full setup assumed
ent_O-A-20: Rank 3 (score 55.6) → DEB-01: Only eligible machine; Lower downstream impact (lowest planned load: 0.7 h); 10 min setup: changeover from family None/material 'ST'

unscheduled: [('O-H', 'blocked', 'blocked (on_hold): order on hold: credit check')]
warnings: []

recommendation for O-B / O-B-10: recommended=CNC-01
  rank 1 CNC-01 end=Mon 11:00 setup=60 run=120 soft_cost=60
      - Earliest expected completion (tied with CNC-02; preferred by machine rank 0 vs 1)
      - Machine currently available
      - Expected start 2026-09-07T08:00:00+00:00, end 2026-09-07T11:00:00+00:00 (3.0 h from now)
      - 60 min setup: changeover from family 'F1'/material 'AL'
      - Run 2.0 h
      - 60 min setup on CNC-01: changeover from family 'F1'/material 'AL'
  rank 2 CNC-02 end=Mon 11:00 setup=60 run=120 soft_cost=60
      - Same expected completion and cost as CNC-01; lower machine preference rank (1 vs 0)
      - Machine currently available
      - Expected start 2026-09-07T08:00:00+00:00, end 2026-09-07T11:00:00+00:00 (3.0 h from now)
      - 60 min setup: machine state unknown, full setup assumed
      - Run 2.0 h
      - 60 min setup on CNC-02: machine state unknown, full setup assumed

metrics: scheduled=3 unscheduled=1 on_time=3 late=0 at_risk=1 on_time_pct=100.0 avg_lateness_h=0.00 total_setup_h=2.33 total_run_h=7.00 util_pct=3.89 revenue_at_risk=90,000
machine utilization: {'CNC-01': 3.75, 'CNC-02': 6.25, 'DEB-01': 1.67}
quality: score=78.5 components={'on_time_delivery': 100.0, 'lateness': 100.0, 'utilization': 3.9, 'setup_efficiency': 75.0, 'at_risk': 66.7}
quality weights: {'on_time_delivery': 0.4, 'lateness': 0.2, 'utilization': 0.15, 'setup_efficiency': 0.15, 'at_risk': 0.1}
summary: On-time 100% · Utilization 4% · Setup efficiency 75% · Avg lateness 0.0 h · At risk 1
```

Reading it: `O-H` has the highest priority (blocked orders are not capped by default) but is
removed in step 1 as `blocked`, and its 90,000 shows up as revenue at risk. `O-B` (rank 2) is
placed first; both CNC machines finish at 11:00 and the tie goes to the preferred rank. `O-A`
(rank 3) then finds CNC-02 three hours earlier than the now-busy CNC-01. `O-C` shares family `F1`
with what CNC-02 just ran, so it needs no setup and starts at 11:00. The deburring operations are
released by their CNC predecessors' ends. `O-A`'s slack is 5.67 h (< 8 h), hence one order at
risk and the 66.7 at-risk component; the 3.9 % utilisation is relative to the 14-day horizon.

---

## 16. Extending

* **New scheduler:** implement `Scheduler` (`name`, `version`, `schedule(...)`, optionally the
  `previous_entries` keyword so simulation can use it), reuse `finalise_entries`,
  `compute_metrics` and `compute_quality`, and register a factory in
  `registry.default_registry` (or on a `SchedulerRegistry` instance). `CpSatScheduler` is the
  template for wrapping V1.
* **New hard constraint:** a class with `key` and `check(op, machine, ctx) -> Violation | None`
  appended to `hard.default_hard_constraints()`; keep it silent when data is missing; add cases to
  `test_constraints_hard.py`. **New soft constraint:** `key` and `penalty(op, machine, ctx) ->
  Penalty | None` with the cost in minute-equivalents from `SchedulingConfig`, added to
  `soft.default_soft_constraints(config)`; it is picked up by `rank_machines` automatically.
* **New scenario:** a `ScenarioBase` subclass with a `kind: Literal[...]`, `_mutate(snapshot,
  effect)` and `describe()`; add it to the `Scenario` union and `SCENARIO_KINDS`; the engine and
  diff need no change (`test_simulation_scenarios.py`).
* **New replan trigger:** a detector in `triggers.py` producing an existing `ReplanTriggerType`
  (the enum is frozen), plus its entry in `ReplanningConfig.trigger_on` and, if it invalidates the
  current plan, a rule in `engine.hard_events`.
