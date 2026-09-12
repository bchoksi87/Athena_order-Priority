# Architecture — Production Priority & Scheduling Engine (PPSE)

This is the *why* companion to `docs/DESIGN_CONTRACT.md` (the *what*). If the two disagree, the
contract wins and this document must be corrected. Spec deliverable STEP 1 (architecture) and
Phase 23.

Starting point: the repository was empty and the ERP/MES is unknown (`docs/AUDIT_REPORT.md` §1–7).
Every architectural choice below is shaped by that fact — the system must be fully functional,
testable and demonstrable without any ERP, and must attach to a real ERP later by adding a
connector, not by touching engines.

---

## 1. Context

```
                         ┌────────────────────────────────────────────────────────────┐
                         │                    PPSE (this system)                      │
 ┌──────────────┐  read  │ ┌──────────────┐   ┌──────────────┐   ┌──────────────────┐ │
 │  ERP / MES   │──────▶ │ │ Integration  │──▶│ PostgreSQL   │──▶│ PlanningSnapshot │ │
 │  (unknown;   │        │ │ connector +  │   │ normalized   │   │ (in-memory,      │ │
 │  mock today) │◀ ─ ─ ─ │ │ normalizer + │   │ model +      │   │ immutable input) │ │
 └──────────────┘ write  │ │ sync + recon │   │ overlays +   │   └────────┬─────────┘ │
   (READ_ONLY now,       │ └──────────────┘   │ results +    │            │           │
    ladder later)        │                    │ audit        │            ▼           │
                         │                    └──────▲───────┘   ┌──────────────────┐ │
 ┌──────────────┐        │                           │           │ Engines          │ │
 │ Planner /    │  HTTPS │ ┌──────────────┐   ┌──────┴───────┐   │ priority         │ │
 │ Manager /    │──────▶ │ │ React        │──▶│ FastAPI      │──▶│ constraints      │ │
 │ Supervisor / │  JWT   │ │ control      │   │ /api/v1      │   │ calendar         │ │
 │ Operator /   │        │ │ tower        │   │ + services   │   │ scheduling       │ │
 │ Executive /  │        │ └──────────────┘   └──────┬───────┘   │ simulation       │ │
 │ Admin        │        │                           │           │ analytics        │ │
 └──────────────┘        │                    ┌──────▼───────┐   │ data quality     │ │
                         │                    │ Workers      │──▶│ replanning       │ │
                         │                    │ sync/replan/ │   └──────────────────┘ │
                         │                    │ alerts       │                        │
                         │                    └──────────────┘                        │
                         └────────────────────────────────────────────────────────────┘
```

External actors: the ERP/MES (source of truth for master and transactional data; target of
writeback later) and six human roles. There are no other systems in scope; SSO and notification
channels (e-mail/chat) are future.

## 2. Components, responsibilities and boundaries

| Component (package) | Responsibility | May depend on | Must not depend on |
|---|---|---|---|
| Integration layer (`app/integration`) | Talk to the ERP (`ERPConnector`), turn raw records into domain objects (`Normalizer` + `field_maps` + `codes`), assess capabilities, reconcile counts, gate writeback (`WritebackGateway`) | `core`, `domain` | `engines`, `db` (sync service in `services` joins them) |
| Normalized model (`app/domain`) | Pure dataclasses, enums, versioned Pydantic config, engine result types, `PlanningSnapshot` | stdlib, pydantic | anything in `app/*` except `core.clock` types |
| Priority engine (`app/engines/priority`) | Score every open order, produce breakdown + explanation, readiness/risk/rank | `domain`, `core.clock`, `engines.constraints` (for readiness) | `db`, `integration`, `services` |
| Constraint engine (`app/engines/constraints`) | Hard eligibility, soft penalties, readiness and blockers per order | `domain` | `db`, `integration` |
| Calendar (`app/engines/calendar`) | Working-time arithmetic per machine | `domain` | everything else |
| Scheduler (`app/engines/scheduling`) | Turn priorities into machine-sequenced `ScheduleResult` with reasons and quality | `domain`, `constraints`, `calendar` | `db`, `integration` |
| Simulation (`app/engines/simulation`) | Apply scenarios to a cloned snapshot, run priority + scheduler twice, diff | all engines | `db` |
| Analytics (`app/engines/analytics`) | KPIs, capacity, bottlenecks, OTD, risk from snapshot + schedule | `domain` | `db` |
| Data quality (`app/engines/data_quality`) | Rule list producing `DataQualityIssue`s with severity policy | `domain` | `db` |
| Replanning (`app/engines/replanning`) | Trigger detection, stability rules, decide whether/what to replan and if approval is required | `domain` | `db` |
| Services (`app/services`) | Use cases: build snapshot from repos, run engines, persist results/versions/audit, enforce state machines | everything | — (top of the backend stack besides API) |
| Persistence (`app/db`) | ORM models, repositories returning domain objects, mappers, snapshot codec, seed | `core`, `domain` | `engines`, `services` |
| API (`app/api`) | Pydantic schemas, routers, auth deps, error mapping | `services`, `core` | `db` directly (only through services/repos injected in `deps.py`) |
| Workers (`app/workers`) | APScheduler jobs: sync, replan, alert evaluation | `services` | engines directly |
| Frontend (`frontend/`) | Control-tower UI over `/api/v1`; no business logic beyond presentation | API types | — |

The one rule that carries the architecture: **`app/engines/**` imports nothing from `app/db/**` or
`app/integration/**`.** Engines see a `PlanningSnapshot` and Pydantic config, and return dataclasses.
Everything else is plumbing that can change without touching the decision logic.

## 3. Data flows

### 3.1 Morning plan generation

1. Worker (or `POST /sync/run`) runs `SyncService.run("incremental")`: `connector.fetch_*(since)` →
   `Normalizer` → validate → upsert into the normalized tables → `reconcile()` → `sync_runs` row.
   Failures raise `IntegrationError`, retried with backoff; the previous data stays intact.
2. `PlanningService.build_snapshot()` reads repositories (orders, operations, machines, materials,
   tooling, calendars, customers, active locks/overrides/expedites, customer rules) into a
   `PlanningSnapshot(as_of=clock.now())`, stores it compressed in `input_snapshots`.
3. `DataQualityEngine.run(snapshot, config)` → issues persisted; blocking issues mark orders
   unschedulable with an explicit reason.
4. `PriorityEngine.evaluate(snapshot, profile)` → `PriorityResult` per order (score, breakdown,
   readiness, risk, rank) persisted in `priority_results`.
5. `ConstraintEngine` + `build_calendars(snapshot)` + `RuleBasedScheduler.schedule(...)` →
   `ScheduleResult` with entries, unscheduled items, machine recommendations, metrics and quality.
6. `ScheduleService` writes `optimization_runs`, a new `schedule_versions` row (DRAFT, version n+1,
   snapshot id, profile/config versions) and `schedule_entries`; analytics engine computes KPIs,
   bottlenecks, capacity; alert engine raises/dedupes alerts.
7. The control tower shows the DRAFT next to the currently PUBLISHED version with the quality
   comparison; the manager approves (→ APPROVED) and publishes (→ PUBLISHED, previous → SUPERSEDED).
   In READ_ONLY mode publishing is internal only and the receipt says so.

### 3.2 Urgent order arrives

1. Incremental sync (or a webhook later) upserts the new order; `ReplanningEngine.detect_triggers`
   sees `NEW_ORDER` (and `CUSTOMER_PRIORITY_CHANGE` if applicable).
2. Optionally a manager calls `POST /orders/{id}/expedite` with reason → `Expedite` overlay + audit row.
3. Replan job builds a fresh snapshot; priority is recomputed (expedite adjustment visible in the
   breakdown); scheduler runs with the **frozen window** and **locks** honoured, so work starting in
   the next `frozen_window_minutes` is untouched and only later slots re-sequence.
4. `ReplanningEngine.evaluate(events, current, new, config)` → `ReplanDecision`: replan only if
   quality improves by ≥ `min_improvement_pct` or a hard trigger demands it; `requires_approval`
   from config. The diff ("12 orders moved, 2 newly late, 5 newly on time") is shown to the planner.
5. Approval → publish as above; version note "Replanned after urgent order X".

### 3.3 What-if simulation

1. `POST /schedule/simulate` with a list of `Scenario`s (discriminated union on `kind`).
2. `SimulationEngine.run(snapshot, scenarios, profile, config)`: `baseline = snapshot`,
   `scenario = snapshot.clone()` mutated by each scenario (machine down → add unplanned downtime;
   add machine → clone a machine into the group; extra shift → extend calendar; material delay →
   shift `expected_receipt_date`; weight change → modified profile; prioritize customer → temporary
   customer rule; urgent orders → synthetic orders injected; outsource → quantities removed;
   due-date change → order dates edited).
3. Priority + scheduler run on both; `diff.compare(baseline, scenario)` → `ScheduleDiff` (orders
   affected/moved/newly late/newly on time, utilisation, overtime, bottlenecks, revenue/margin at
   risk, per-order deltas).
4. Nothing is persisted as a schedule version; the live plan is untouched. The UI renders the
   comparison; the planner may "promote" a scenario by re-running generation with the equivalent
   configuration change (an audited action).

### 3.4 Manager override

1. `POST /orders/{id}/override-priority` (or `/hold`, `/release`, `/force-next`, `/schedule/lock`).
2. Role check (production_manager; planner for hold/release) → validation (reason required, value
   within profile bounds) → `PriorityOverride`/`ScheduleLock` persisted with `created_by`,
   `created_at`, `expires_at` → `audit_log` row with previous and new values.
3. The next priority evaluation applies the override as a `PriorityAdjustment(kind="override")`
   visible in the breakdown; `FORCE_NEXT` sets `forced_next` so the scheduler places it first on
   its recommended machine; locks become hard constraints (`LockedMachineAssignment`) and frozen
   entries.
4. Overrides expire automatically; expiry is a no-op for the audit (the creation row documents it).

### 3.5 Approve & publish

1. `POST /schedule/approve {version}` (production_manager): state machine DRAFT → APPROVED, only
   if no newer DRAFT supersedes it and the referenced snapshot still exists; audit row.
2. `POST /schedule/publish {version}`: APPROVED → PUBLISHED; the previously PUBLISHED version →
   SUPERSEDED; `WritebackGateway.publish(schedule, mode, approved_by)`:
   * READ_ONLY (MVP): returns a receipt with `published=False`, nothing leaves the system.
   * APPROVAL / WRITEBACK / CONTROLLED_AUTO (later): the gateway sends entries to the ERP and
     stores the receipt (external ids, counts, errors); failures keep the version PUBLISHED
     internally but flagged, and raise an alert.
3. Operators see the published version on Machine Schedule; every later replan compares against it.

## 4. Key design decisions

Each decision lists the choice, the rationale and the alternatives that were rejected.

### 4.1 Weighted additive scoring with normalised weights (priority)

*Choice.* Score = Σ w·raw(bonus) − Σ w·raw(penalty) + adjustments, raw ∈ [0,100], weights normalised
to sum 1, result clamped to [0,100].

*Why.* Each term is independently computable and independently explainable ("+25 — due in 18 h"),
which is exactly the spec's Phase 34 requirement. Weights are a UI concept planners understand
(percentages), normalisation keeps the scale stable when a factor is disabled, and a linear model
makes the weight-change preview cheap (rescale, re-rank). Determinism and O(n) cost are trivial.

*Rejected.* A single hand-written formula (not configurable, not explainable); lexicographic or
rule-cascade ranking (opaque tie handling, cannot express trade-offs); learned ranking models
(no training data, not explainable, violates "AI on top of deterministic systems"); multiplicative
scoring (a zero factor annihilates everything; hard to explain).

### 4.2 Rule-based scheduler V1 before CP-SAT

*Choice.* V1 is a deterministic priority-list scheduler with machine choice by earliest expected
completion plus soft cost, opportunistic batching and lock/frozen-window handling. CP-SAT is a
plug-in behind the same `Scheduler` protocol.

*Why.* The spec prescribes it, and it is the right order: V1 yields a *reason per placement*,
runs in seconds on 20k orders, exposes data-quality problems early, and gives a baseline that
any optimiser must beat on the quality score. A CP-SAT model over thousands of operations and
hundreds of machines needs decomposition, time limits and warm starts — it is worth doing on the
bottleneck group once real data shows where the money is.

*Rejected.* Starting with CP-SAT (weeks of modelling on synthetic data, no explanations);
genetic/local-search first (non-deterministic unless seeded, harder to explain than either).

### 4.3 Snapshot-based engines

*Choice.* Engines take an immutable `PlanningSnapshot` and return dataclasses; they never query.

*Why.* Reproducibility (store the snapshot, replay the run — spec Phase 22), simulation by
`clone()` and mutation, unit tests without a database, and performance (all lookups are in-memory
dictionaries with prebuilt indexes). It also enforces the boundary that keeps ERP schema changes
out of decision logic.

*Rejected.* Engines with repository access (untestable, non-reproducible, tempts N+1 queries);
event-sourced state (over-engineering for this scale; snapshots are cheap to compress).

### 4.4 Versioned configuration in the database

*Choice.* `PriorityProfile`, `SchedulingConfig`, `ReplanningConfig`, `AlertConfig`,
`DataQualityConfig` are Pydantic models stored as JSON rows with a monotonic version; every run
records the versions it used.

*Why.* The spec forbids hard-coded rules and demands versions; planners must edit weights in the UI;
audit must answer "which weights produced yesterday's plan". Pydantic validation guarantees the
engine never sees an inconsistent profile (e.g. all weights zero).

*Rejected.* Environment variables/config files (not editable by planners, not versioned per run);
per-row settings tables (no atomic versioning, painful validation).

### 4.5 Read-only writeback first

*Choice.* `WritebackMode.READ_ONLY` is the default; the gateway protocol exists from day one; the
ladder to CONTROLLED_AUTO has explicit gates (`docs/ERP_INTEGRATION.md`).

*Why.* The spec is explicit, and the ERP is unknown — writing to an unknown system is the one
irreversible risk in this project. Having the gateway protocol early means services and UI already
carry approval/publish semantics, so enabling writeback later is a connector task, not a redesign.

### 4.6 PostgreSQL with portable column types

*Choice.* PostgreSQL 16 in dev/prod, but only `String/Integer/Float/Boolean/DateTime(tz)/JSON/Text`
columns, enums stored as strings.

*Why.* PostgreSQL is the spec's preference and the environment has it; portable types let the
unit-test suite run on in-memory SQLite in milliseconds, and keep the door open if the customer's
platform standard is different. JSON (not JSONB) is used for config bodies, breakdowns and
snapshots, which are read whole, not queried by key.

*Rejected.* JSONB/array columns (lock-in, breaks SQLite tests); a document store for snapshots
(second database to operate for no gain).

### 4.7 FastAPI

*Choice.* FastAPI + Uvicorn, Pydantic v2 request/response models, generated OpenAPI.

*Why.* The engines are Python (OR-Tools/NumPy ecosystem, spec Phase 24), so the API should be too;
FastAPI gives typed schemas, dependency injection (`Depends`) that matches the constructor-injection
style of the services, and free API documentation. Async endpoints keep long runs from blocking.

*Rejected.* Django/DRF (ORM and admin unused; heavier); a separate API language (two runtimes,
serialisation boundary through the engines).

### 4.8 Modular monolith, not microservices

*Choice.* One backend image running as API and as worker; strict package boundaries.

*Why.* One team, one database, engines that need the whole snapshot in memory. Boundaries are
enforced by import rules and tests, which gives the modularity the spec asks for without network
hops. The worker is the same image with a different entrypoint, so scaling out (more workers,
CP-SAT on a bigger box) is a deployment change.

### 4.9 Minutes as the duration unit, scores in [0, 100]

*Choice.* Durations `float` minutes inside engines, money `float` base currency, scores 0–100.

*Why.* Minutes match shop-floor thinking and avoid fractional hours in setup arithmetic; a single
0–100 scale makes factors, quality components and the UI comparable; no rounding inside engines
keeps runs deterministic and re-summable.

## 5. Extension points

| Extension | What to add | What not to touch |
|---|---|---|
| New priority factor | A class with `key`, `name`, `kind`, `score(order, ctx)`; register in `engines/priority/registry.py`; add its `FactorKey` literal and default weight in `domain/config.py`; add tests | Engine loop, explanation renderer |
| New hard/soft constraint | A class implementing `HardConstraint.check` or `SoftConstraint.penalty`; add to `default_hard_constraints()` / `default_soft_constraints(config)` or the registry; config parameters in `SchedulingConfig` | Scheduler |
| New scheduler | Implement `Scheduler` (`name`, `version`, `schedule(...)`); register in `engines/scheduling/registry.py`; select via `SchedulingConfig.algorithm`; reuse `quality.compute_quality` | Priority engine, services |
| New ERP connector | Implement `ERPConnector` (fetch_* returning `RawRecord`s, `capabilities()`, `health()`); register a factory in `ConnectorRegistry`; provide field maps and code maps; set `PPSE_ERP_CONNECTOR` | Normaliser core, engines, services |
| New scenario type | A Pydantic model with a new `kind` literal in the `Scenario` union and an `apply(snapshot, ...)` mutation; the engine and diff need no change | Diff, scheduler |
| New data-quality rule | A class implementing `DataQualityRule.run`; add to the engine's rule list; severity in `DEFAULT_SEVERITIES` or config toggle | Other rules |
| New alert type | Enum value + evaluator in the alert service; config threshold in `AlertConfig` | Engines |
| New replanning trigger | Enum value + detector in `replanning/triggers.py`; enable via `ReplanningConfig.trigger_on` | Scheduler |
| Writeback for a real ERP | Implement `WritebackGateway.publish`; use `check_publish_preconditions`; return `WritebackReceipt` | Services, UI |

## 6. Scalability strategy (spec Phase 29)

* **Algorithmic bounds.** Priority: O(orders + operations + machines) with precomputed percentiles
  and machine next-free times in `PriorityContext`. Scheduler V1: O(operations · eligible machines)
  with per-machine sorted timelines; calendars precompute working segments over the horizon.
  Data quality: O(orders + operations + machines) via `DataQualityContext`. No pairwise loops over
  orders anywhere; batching looks only at a bounded window at the head of each machine queue.
* **Horizon and scope limits.** `SchedulingConfig.horizon_days` (default 14) and
  `max_orders_per_run` bound the problem; orders beyond the horizon still get priorities but not
  slots.
* **Database.** Indexes on order status, due date, customer, machine, process, priority score,
  production status and schedule start/end; repositories page results; snapshots stored compressed
  and loaded once per run.
* **Background execution.** Sync, replanning and alert evaluation run in the worker process on a
  schedule; API calls that generate or simulate return a run id and poll, keeping request latency
  bounded.
* **Incremental recalculation.** Incremental sync touches only changed rows; the replanning engine
  computes the affected set (machines/orders touched by a trigger) so a replan can be scoped; the
  frozen window and locks reduce churn.
* **Caching.** Priority results and analytics are persisted per run and served from the database;
  Redis 7 is available for hot dashboard caches and job locks once real volumes justify it.
* **Optimiser scaling.** CP-SAT V2 is applied per machine group with time limits and V1 warm
  starts, never to the whole plant at once.
* **Horizontal scaling.** Stateless API replicas behind the proxy; one worker per job class;
  PostgreSQL vertically first, read replicas for analytics if needed.

## 7. Observability

* **Structured logs** (structlog; JSON in prod). Request id bound by `RequestIdMiddleware` on every
  line and echoed as `X-Request-ID`; services bind `run_id`, `schedule_version`, `user_id`,
  `sync_run_id` where known. No secrets or credentials are logged.
* **Health and metrics.** `GET /health` (DB ping, connector health, background job status);
  `GET /metrics` (request counters by method/path/status, run durations, sync failures, alert
  counts). Prometheus scraping is a formatting change on the same counters.
* **Run records.** Every optimisation run stores start/end, orders considered/scheduled/blocked,
  objective/quality score, algorithm and configuration versions and status — the spec's
  "scheduler execution metrics" — queryable via the Audit Log and System Administration screens.
* **Integration telemetry.** `sync_runs` records per-entity counts, skipped records, reconciliation
  status and error text; repeated failures raise `DATA_QUALITY`/`SCHEDULE_DISRUPTION` alerts.
* **Error tracking.** `AppError` subclasses map to HTTP codes with a stable `code`; unexpected
  exceptions are logged with stack traces and returned as opaque 500s with the request id.
* **Determinism checks.** Tests re-run engines on the same snapshot and compare results, so a
  non-deterministic regression fails CI rather than surfacing as unexplained plan churn.
