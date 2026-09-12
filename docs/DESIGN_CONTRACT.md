# Design Contract — Production Priority & Scheduling Engine (PPSE)

This document is the binding engineering contract for everyone (human or agent) working on this
repository. It fixes the technology stack, repository layout, domain model, engine interfaces,
persistence model, API surface and coding conventions so that independently developed modules
compose without rework. `docs/ARCHITECTURE.md` explains the *why*; this file states the *what*.

If code and this document disagree, fix the code (or open a discussion and update both).

---

## 1. Audit result that shaped this contract

The repository `bchoksi87/Athena_order-Priority` was empty at project start (no commits, no ERP
source, no schema, no API documentation, no sample data). The ERP/MES is therefore treated as an
**unknown external system** behind an abstraction (`ERPConnector`). Every field the engines need is
recorded in `docs/ERP_INTEGRATION.md` as *Required / Available / Missing* against the ERP, and the
`MockERPConnector` (backed by the synthetic data generator) stands in until real ERP access exists.

Nothing in `app/engines/**` may import from `app/db/**` or `app/integration/**`. Engines operate on
the in-memory **Normalized Manufacturing Data Model** (`app/domain/**`) only.

---

## 2. Technology stack

| Layer | Choice | Notes |
|---|---|---|
| Backend language | Python 3.11 | Preferred by spec for optimization ecosystem |
| Web framework | FastAPI 0.141 + Uvicorn | OpenAPI generated automatically |
| ORM / migrations | SQLAlchemy 2.0 (typed `Mapped[]`) + Alembic | Portable column types only (see §7) |
| Database | PostgreSQL 16 (dev/prod), SQLite in-memory (unit tests) | Local cluster available on port 5432, `postgres/postgres`, DBs `ppse`, `ppse_test` |
| Validation / config | Pydantic v2 + pydantic-settings | All engine configuration is Pydantic |
| Auth | PyJWT (HS256) + bcrypt, role-based | Roles in §9 |
| Logging | structlog (JSON in prod, console in dev) | |
| Background jobs | APScheduler 3.x inside the API process (dev) / dedicated worker container (prod) | |
| Optimization | Pure-Python rule-based scheduler (V1); OR-Tools CP-SAT plug-in (V2, optional import) | |
| Frontend | React 18 + TypeScript + Vite, React Router, TanStack Query, Recharts, custom SVG Gantt | No component framework; hand-written design tokens |
| Packaging | `backend/Dockerfile`, `frontend/Dockerfile` (nginx), `docker-compose.yml`, GitHub Actions CI | |
| Tooling | ruff (lint+format), mypy (strict on `app/domain`, `app/engines`), pytest, hypothesis, freezegun | |

Backend virtualenv lives at `backend/.venv` (git-ignored). Run everything from `backend/` with
`.venv/bin/python -m ...`.

---

## 3. Repository layout

```
/
├── README.md
├── docs/                         # all long-form documentation (see §12)
├── docker-compose.yml
├── .github/workflows/ci.yml
├── backend/
│   ├── pyproject.toml            # deps, ruff, mypy, pytest config
│   ├── alembic.ini, alembic/     # migrations
│   ├── app/
│   │   ├── main.py               # create_app() only; no logic
│   │   ├── core/                 # config.py, logging.py, security.py, db.py, errors.py, clock.py, ids.py
│   │   ├── domain/               # Normalized Manufacturing Data Model (pure Python, no I/O)
│   │   │   ├── enums.py, models.py, snapshot.py, results.py, config.py
│   │   ├── db/
│   │   │   ├── models/           # SQLAlchemy ORM; one module per aggregate
│   │   │   ├── repositories/     # typed repositories; only place that issues queries
│   │   │   ├── mappers.py        # ORM <-> domain conversion
│   │   │   └── seed.py           # dev users/config seeding
│   │   ├── integration/          # ERP connector protocol, mock connector, normalizer, sync, reconciliation, writeback gateway
│   │   ├── engines/
│   │   │   ├── priority/         # base.py (protocols), engine.py, explanation.py, factors/<one file per factor>.py, registry.py
│   │   │   ├── constraints/      # base.py, hard.py, soft.py, engine.py, readiness.py
│   │   │   ├── calendar/         # calendar.py (MachineCalendar), builder.py
│   │   │   ├── scheduling/       # base.py, rule_based.py, machine_assignment.py, setup.py, batching.py, quality.py, registry.py, cpsat.py (optional)
│   │   │   ├── simulation/       # scenarios.py, engine.py, diff.py
│   │   │   ├── analytics/        # kpis.py, capacity.py, bottleneck.py, otd.py, risk.py
│   │   │   ├── data_quality/     # rules.py, engine.py
│   │   │   └── replanning/       # triggers.py, stability.py, engine.py
│   │   ├── services/             # application services (use cases): orchestrate engines + repos + audit
│   │   ├── api/
│   │   │   ├── deps.py, router.py
│   │   │   ├── schemas/          # Pydantic request/response models
│   │   │   └── v1/               # one router module per resource
│   │   └── workers/              # background jobs (sync, replan, alerts)
│   ├── synthetic/                # synthetic data generator (deterministic, seeded)
│   └── tests/{unit,engines,integration,api,simulation}/
└── frontend/                     # Vite + React + TS (see §11)
```

Rules: no file over ~600 lines; no module-level mutable state; no business constants outside
`app/domain/config.py` defaults or the DB-stored configuration.

---

## 4. Conventions

* **Time**: every `datetime` is timezone-aware UTC. Durations in **minutes** (`float`) inside engines.
  Never call `datetime.now()` directly; inject `Clock` (`app/core/clock.py`: `SystemClock`, `FrozenClock`).
* **IDs**: strings. ERP identifiers are kept verbatim as `external_*` where relevant; internal IDs are
  ULID-like strings from `app/core/ids.py:new_id(prefix)`.
* **Money**: `float` in base currency units (INR by default, currency code in config). Never round inside engines.
* **Scores**: all scores are `float` in `[0, 100]`; higher = more urgent / better.
* **Errors**: raise subclasses of `app.core.errors.AppError` (`NotFoundError`, `ValidationError`,
  `ConflictError`, `AuthorizationError`, `IntegrationError`); the API layer maps them to HTTP.
* **Logging**: `structlog.get_logger(__name__)`; bind `run_id`, `schedule_version`, `user_id` where known.
* **DI**: engines and services receive collaborators through constructors. FastAPI `Depends` wires them in `app/api/deps.py`.
* **Determinism**: engines must be deterministic for a given snapshot + config (stable sort keys, no randomness).
* **Explainability**: every score/placement carries a machine-readable breakdown and a human-readable reason produced by the same code path that computed the number.
* **Typing**: full type hints; `from __future__ import annotations`; Protocols for interfaces.
* **Tests**: pytest; unit tests use in-memory objects or SQLite; integration tests use PostgreSQL when
  `PPSE_TEST_DATABASE_URL` is set (they skip otherwise). Markers: `unit`, `integration`, `simulation`, `slow`.

---

## 5. Domain model (`app/domain`)

Authoritative definitions are in code (`enums.py`, `models.py`, `snapshot.py`, `results.py`, `config.py`).
Summary:

* `Customer`, `Order` (one row per order **line**), `Operation`, `Machine`, `Material`, `Tooling`,
  `Shift`, `TimeWindow`, `CalendarSpec`, `ScheduleLock`, `PriorityOverride`, `Expedite`, `CustomerRule`.
* `PlanningSnapshot` bundles everything the engines need at one instant (`as_of`). It is the only
  input engines accept and it is trivially cloneable for simulation (`snapshot.clone()`).
* `results.py` holds engine outputs: `FactorScore`, `PriorityAdjustment`, `PriorityResult`,
  `Blocker`, `Violation`, `Penalty`, `EligibilityResult`, `MachineRecommendation`, `ScheduleEntry`,
  `UnscheduledItem`, `ScheduleMetrics`, `ScheduleQuality`, `ScheduleResult`, `DataQualityIssue`,
  `Alert`, `SimulationResult`, `ScheduleDiff`.
* `config.py` holds versioned configuration models: `PriorityProfile` (factor weights + thresholds +
  aging/fairness/expedite rules), `SchedulingConfig` (horizon, locking, stability, setup, batching,
  objectives, overtime), `ReplanningConfig`, `AlertConfig`, `DataQualityConfig`.

Readiness states (spec Phase 3): `READY, WAITING_MATERIAL, WAITING_TOOLING, WAITING_APPROVAL,
WAITING_PREVIOUS_OPERATION, MACHINE_UNAVAILABLE, QUALITY_HOLD, ON_HOLD, OTHER_CONSTRAINT`.

---

## 6. Engine interfaces

### 6.1 Priority engine (`app/engines/priority`)
```python
class PriorityFactor(Protocol):
    key: str                      # canonical key, see list below
    name: str
    kind: Literal["bonus", "penalty"]
    def score(self, order: Order, ctx: PriorityContext) -> FactorScore: ...

class PriorityEngine:
    def __init__(self, factors: Sequence[PriorityFactor], clock: Clock): ...
    def evaluate(self, snapshot: PlanningSnapshot, profile: PriorityProfile,
                 customer_rules: Mapping[str, CustomerRule] | None = None) -> dict[str, PriorityResult]: ...
    def evaluate_order(self, order: Order, ctx: PriorityContext) -> PriorityResult: ...
```
Canonical factor keys (weights configurable per `PriorityProfile`):
`due_date_urgency, sla_risk, customer_importance, order_value, margin, delay_penalty,
production_readiness, machine_availability, setup_efficiency, batching_affinity, downstream_impact`.
Final score = Σ(weight_i × raw_i) for bonus factors − Σ(weight_i × raw_i) for penalty factors,
then + adjustments (aging, fairness, expedite, override, customer rule), clamped to [0, 100].
Weights are normalised so that all enabled positive weights sum to 1.0 (`PriorityProfile.weight_map()`).
The shipped factors are all bonus-kind: a large setup lowers the setup-efficiency bonus instead of
adding a separate penalty line; penalty-kind factors remain supported. Explanation text is rendered from the
`FactorScore`/`PriorityAdjustment` lists, never written independently.

### 6.2 Constraint engine (`app/engines/constraints`)
```python
class HardConstraint(Protocol):
    key: str
    def check(self, op: Operation, machine: Machine, ctx: ConstraintContext) -> Violation | None: ...
class SoftConstraint(Protocol):
    key: str
    def penalty(self, op: Operation, machine: Machine, ctx: ConstraintContext) -> Penalty | None: ...
class ConstraintEngine:
    def eligible_machines(self, op: Operation, snapshot: PlanningSnapshot, at: datetime) -> EligibilityResult: ...
    def soft_penalties(self, op: Operation, machine: Machine, ctx: ConstraintContext) -> list[Penalty]: ...
    def order_blockers(self, order: Order, snapshot: PlanningSnapshot, at: datetime) -> list[Blocker]: ...
    def readiness(self, order: Order, snapshot: PlanningSnapshot, at: datetime) -> ReadinessState: ...
```

### 6.3 Calendar (`app/engines/calendar`)
```python
class MachineCalendar:
    def is_working(self, t: datetime) -> bool
    def next_working_time(self, t: datetime) -> datetime
    def add_work_minutes(self, start: datetime, minutes: float) -> datetime
    def working_minutes_between(self, a: datetime, b: datetime) -> float
    def working_windows(self, a: datetime, b: datetime) -> list[TimeWindow]
def build_calendar(machine: Machine, spec: CalendarSpec, extra_downtime: Iterable[TimeWindow] = ()) -> MachineCalendar
```

### 6.4 Scheduling engine (`app/engines/scheduling`)
```python
class Scheduler(Protocol):
    name: str
    version: str
    def schedule(self, snapshot: PlanningSnapshot, priorities: Mapping[str, PriorityResult],
                 config: SchedulingConfig, calendars: Mapping[str, MachineCalendar],
                 constraints: ConstraintEngine) -> ScheduleResult: ...
```
`ScheduleResult.entries` is machine-sequenced; each entry carries `placement_reason` and
`priority_score`. `machine_recommendations[order_id]` lists eligible machines with ranked reasons.
`quality` is computed by `quality.compute_quality(result, config)`. V1 = `RuleBasedScheduler`
(`name="rule_based"`, `version="1.0.0"`). Additional schedulers register in `registry.py`.

### 6.5 Simulation (`app/engines/simulation`)
`Scenario` union (Pydantic, discriminated on `kind`): `machine_down`, `urgent_orders`,
`add_machine`, `extra_shift`/`working_day`, `outsource`, `material_delay`, `prioritize_customer`,
`weight_change`, `due_date_change`. `SimulationEngine.run(snapshot, scenarios, profile, config) ->
SimulationResult` (baseline vs scenario `ScheduleResult`s + `ScheduleDiff`).

### 6.6 Analytics, data quality, replanning
`analytics.kpis.compute_executive_kpis`, `analytics.capacity.compute_capacity`, `analytics.bottleneck.find_bottlenecks`,
`analytics.otd.on_time_delivery`; `data_quality.engine.DataQualityEngine.run(snapshot, config) -> list[DataQualityIssue]`;
`replanning.engine.ReplanningEngine.evaluate(events, current_schedule, new_schedule, config) -> ReplanDecision`.

---

## 7. Persistence (`app/db`)

Portable SQLAlchemy types only: `String`, `Integer`, `Float`, `Boolean`, `DateTime(timezone=True)`,
`JSON` (not JSONB), `Text`. Enum columns are stored as `String` with the enum `.value`. Indexes
required on: order status, due date, customer_id, machine_id, process_type, priority score,
production status, schedule start/end.

Tables (all with `created_at`, `updated_at`): `customers`, `customer_rules`, `orders`, `operations`,
`machines`, `machine_downtime`, `calendar_specs`, `materials`, `tooling`, `priority_profiles`
(versioned config JSON), `scheduling_configs` (versioned), `priority_results`, `optimization_runs`,
`schedule_versions`, `schedule_entries`, `schedule_locks`, `priority_overrides`, `expedites`,
`alerts`, `audit_log`, `data_quality_issues`, `sync_runs`, `input_snapshots`, `users`.

Repositories expose domain objects, not ORM rows, to services (`db/mappers.py`).

---

## 8. Integration (`app/integration`)

```python
class ERPConnector(Protocol):        # READ-ONLY
    def fetch_customers(self, since: datetime | None) -> list[RawRecord]
    def fetch_orders(self, since)    # incl. operations
    def fetch_machines(self, since)
    def fetch_materials(self, since)
    def fetch_tooling(self, since)
    def fetch_production_status(self, since)
    def capabilities(self) -> ConnectorCapabilities   # which fields the ERP can supply
class WritebackGateway(Protocol):    # modes: READ_ONLY (default) | APPROVAL | WRITEBACK | CONTROLLED_AUTO
    def publish(self, schedule: ScheduleResult, mode: WritebackMode) -> WritebackReceipt
```
`SyncService.run(mode="full"|"incremental")` = fetch → normalise → validate → upsert → reconcile →
record `sync_runs`. `MockERPConnector` serves synthetic data and is the default connector.

---

## 9. API (`app/api/v1`) and roles

Prefix `/api/v1`. Bearer JWT. Roles: `admin, production_manager, planner, supervisor, operator, executive`.

| Endpoint | Roles (minimum) |
|---|---|
| `POST /auth/login`, `GET /auth/me` | any |
| `GET /orders`, `GET /orders/{id}`, `GET /orders/{id}/explanation`, `GET /orders/{id}/machines` | operator+ |
| `POST /orders/{id}/expedite`, `/hold`, `/release`, `/override-priority`, `/force-next` | production_manager (planner may hold/release) |
| `GET /machines`, `GET /machines/{id}`, `GET /machines/{id}/schedule` | operator+ |
| `GET /schedule`, `GET /schedule/{date}`, `GET /schedule/versions`, `GET /schedule/versions/{v}` | operator+ |
| `POST /schedule/generate`, `POST /schedule/simulate` | planner+ |
| `POST /schedule/approve`, `POST /schedule/publish`, `POST /schedule/lock`, `POST /schedule/unlock` | production_manager |
| `GET/PUT /priority/configuration`, `GET /priority/configuration/versions`, `POST /priority/configuration/preview` | GET planner+, PUT admin |
| `GET/PUT /scheduling/configuration` | GET planner+, PUT admin |
| `GET/PUT /customers/{id}/rules` | GET planner+, PUT production_manager |
| `GET /analytics/kpis`, `/capacity`, `/bottlenecks`, `/on-time-delivery`, `/schedule-quality` | executive+ (read) |
| `GET /alerts`, `POST /alerts/{id}/acknowledge` | supervisor+ |
| `GET /audit` | production_manager+ (admin full) |
| `GET /data-quality`, `POST /data-quality/run` | planner+ |
| `POST /sync/run`, `GET /sync/runs` | admin |
| `GET /health`, `GET /metrics` | public |

Role ordering for "+": executive < operator < supervisor < planner < production_manager < admin
(executive is read-only but may read every dashboard endpoint).

---

## 10. Auditability and versioning

Every schedule generation stores an `optimization_runs` row (run id, start/end, orders considered /
scheduled / blocked, objective + quality score, algorithm + version, profile version, config
version, status) and a `schedule_versions` row (monotonic `version_number`, status
`DRAFT → APPROVED → PUBLISHED → SUPERSEDED`, generated by/at, input snapshot id). Input snapshots
are stored as compressed JSON in `input_snapshots`. Every override/lock/expedite/approval writes an
`audit_log` row: `user_id, timestamp, entity_type, entity_id, action, previous_value, new_value, reason`.

---

## 11. Frontend

`frontend/src`: `api/` (typed client per resource, generated types in `api/types.ts`), `app/` (router,
providers, auth), `components/` (DataTable, KpiCard, StatusPill, GanttChart, Timeline, FilterBar,
ExplanationPanel, RiskBadge, ScoreBar), `pages/` (one folder per screen from spec Phase 33),
`styles/tokens.css` (dense industrial control-tower look, light + dark), `lib/` (formatters, time).
Pages: ExecutiveDashboard, ControlTower, PriorityQueue, MachineSchedule, GanttSchedule, OrderDetail,
MachineDetail, BottleneckAnalysis, CapacityPlanning, WhatIfSimulation, PriorityConfiguration,
SchedulingConfiguration, Alerts, DataQuality, AuditLog, SystemAdministration.

---

## 12. Documentation set (`docs/`)

`AUDIT_REPORT.md` (first-task analysis), `ARCHITECTURE.md`, `REQUIREMENTS.md`, `DATA_MODEL.md`,
`API.md`, `PRIORITY_ENGINE.md`, `SCHEDULING_ENGINE.md`, `ERP_INTEGRATION.md`, `DEPLOYMENT.md`,
`TESTING.md`, plus this contract. `README.md` at the root links to all of them.
