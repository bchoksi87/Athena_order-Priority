# Testing

This document describes the automated test suite of PPSE: the test tiers under `backend/tests/`
and `frontend/src/**/*.test.*`, the markers and fixtures, how each tier runs locally and in CI
(`.github/workflows/ci.yml`), the synthetic dataset used as test data, the property-based and
performance tests with their targets, and the patterns to follow when adding tests for a new
priority factor, constraint, scenario, scheduler or API endpoint. Counts and timings are from
the repository at the time of writing (719 backend tests collected).

**How to read this.** `docs/DESIGN_CONTRACT.md` §4 states the testing conventions;
`docs/PRIORITY_ENGINE.md` and `docs/SCHEDULING_ENGINE.md` cite the tests that pin their
behaviour; `docs/DEPLOYMENT.md` §2 covers the local environment the tests need; spec Phase 30
(testing) and Phase 31 (simulation data).

---

## 1. Test pyramid

| Tier | Location | Collected | Database | Marker | What it covers |
|---|---|---|---|---|---|
| Unit | `backend/tests/unit/` (17 modules) | 159 | SQLite in-memory where needed | `unit` (`pytestmark` in every module) | `core` (settings, db plumbing, security, `create_app` incl. health/metrics/auth endpoints and role guards), `db` (mappers, seed, snapshot codec), `integration` package (capabilities, mock connector, normaliser, reconciliation, snapshot builder, writeback), synthetic generator |
| Engines | `backend/tests/engines/` (33 modules) | 524 | none (in-memory snapshots) | *(none)* | priority (context, engine, explanation, factors, preview), constraints (hard, soft, readiness, engine), calendar, scheduling (setup, assignment, batching, rule-based, metrics/quality, CP-SAT, perf), replanning (triggers, stability, engine), simulation (scenarios, engine), analytics (kpis, capacity, bottleneck, otd, risk, alerts), data quality (rules, engine), the `PlanningPipeline` (`test_pipeline.py`) |
| Integration | `backend/tests/integration/` (5 modules) | 76 | SQLite **and** PostgreSQL | `integration` | repositories for master data, operations/overlays, results/config/schedule versions and `DbSnapshotBuilder` (parametrised over both backends), the `SyncService` (`test_sync_service.py`) and the CLI (`test_cli.py`) |
| Simulation | `backend/tests/simulation/` (`test_scenarios.py`) | 9 | none (medium synthetic plant in memory) | `simulation`, `slow` | The spec Phase 30 scenarios end to end through `PlanningPipeline.simulate`: machine failure, material shortage, urgent order, new order, production delay, rework, capacity increase, plus the full-pipeline timing budget (< 60 s) and the time-of-day robustness check of the scheduler |
| API | `backend/tests/api/` (13 modules) | 172 | SQLite in-memory seeded from the small synthetic plant through the real `SyncService`, priority engine and scheduler | *(none)* | Role matrix over every endpoint × six roles, orders list/detail/explanation/machine options, overrides/expedites/holds/moves/locks with audit rows and mandatory reasons, configuration versions/preview/rollback, customer rules, alerts, audit queries, data quality, users, schedule generate/approve/publish/reject/gantt/compare, simulation, analytics, sync administration, health |
| Frontend | `frontend/src/**/*.test.{ts,tsx}` (7 files) | — | — | — | Vitest + Testing Library: `GanttChart`, `DataTable`, `ExplanationPanel`, `ScoreBar` components, `app/auth`, `api/client`, `lib/formatters` |

Engine tests are the bulk of the pyramid by design: engines are pure functions of a snapshot and
a config, so they are tested without I/O (`docs/ARCHITECTURE.md` §4.3).

---

## 2. Configuration and markers

`backend/pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["unit: fast, no I/O", "integration: requires database",
           "simulation: scenario/stress tests", "slow: long running"]
addopts = "-q"
filterwarnings = ["ignore::DeprecationWarning"]
```

Marker usage as it stands: `unit` on all 15 unit modules (16 `pytestmark` declarations),
`integration` on the 5 integration modules, `slow` on 3 tests (§8), one `skipif`; engine tests carry no marker;
`simulation` is declared but not used. `-m "not slow"` is therefore "everything but the three
long tests", which is what CI runs first.

Other tooling from the same file: ruff (line length 110, rules E/F/I/B/UP/N/W/SIM/RUF; tests relax
B/N/RUF), mypy (`disallow_untyped_defs`, excludes `alembic/` and `tests/`), coverage via
`pytest-cov`, `hypothesis`, `freezegun`, `httpx` (TestClient) in the `dev` extra.

---

## 3. Fixtures

### 3.1 Root `backend/tests/conftest.py` (unit + integration)

| Fixture | Provides |
|---|---|
| `settings` | `Settings(environment="test", database_url=SQLITE_MEMORY_URL, jwt_secret="unit-test-secret", jwt_expire_minutes=60, seed_on_startup=False, _env_file=None)`; resets the settings cache before and after |
| `frozen_clock` | `FrozenClock(2026-09-11T08:00Z)` (`NOW`) |
| `engine` / `session` | Fresh SQLite in-memory engine with `create_all`; a `Session` rolled back at the end |
| `_postgres_engine` (session scope) | Engine on `PPSE_TEST_DATABASE_URL` when set and reachable (`create_all`, then reused); otherwise `None` |
| `db_session` (params `sqlite`, `postgres`) | The SQLite session, or a PostgreSQL session inside a transaction with `join_transaction_mode="create_savepoint"` that is rolled back after each test; the PostgreSQL parameter **skips** when `PPSE_TEST_DATABASE_URL` is unset |
| `seeded_users` | `seed_users` + `seed_default_config` committed; dict `Role → UserRecord` |
| `app` / `app_client` | `create_app(settings, engine=engine, clock=frozen_clock)` with `get_db` overridden to the test session; `TestClient` inside the lifespan |
| `auth_headers(role)` | Bearer header for the seeded user of that role, signed with the test secret at the frozen time |
| `dev_passwords` | username → password of `DEV_USERS` |
| `sample` / `build_sample(now)` | `SampleData`: 2 customers, 3 machines (incl. one in maintenance), 4 orders (one shipped), 5 operations, materials, tooling, a two-shift `Asia/Kolkata` calendar with a holiday and an extra day, locks, overrides, expedites, a customer rule; `sample.snapshot()` builds the `PlanningSnapshot` |
| `load_sample(session, sample)` / `loaded_session` | Persists the sample through the repositories (integration tests) |
| `as_dict(obj)` | `to_jsonable` shortcut for comparisons |

### 3.2 Engine fixtures `backend/tests/engines/conftest.py` + `factories.py`

Deliberately independent of the root conftest. `factories.py` exposes builders with sensible
defaults so a test spells out only the fields it is about: `make_customer`, `make_machine`,
`make_material`, `make_tooling`, `make_operation`, `make_order`, `make_order_with_ops`,
`make_dated_order`, `make_routing`, `make_order_with_routing`, `make_calendar_spec` (08:00–16:00
Mon–Fri UTC by default), `make_24x7_spec`, `make_lock`, `make_override`, `make_expedite`,
`make_customer_rule`, `make_profile(weights)`, `make_context` (a plain `PriorityContext` with
hand-supplied look-ups), `make_priority`/`make_priorities` (results with ranks, no breakdown),
`make_entry`, `make_schedule`, `make_unscheduled`, `make_plant_snapshot(n_orders, machine_ids,
due_hours, …)`, `make_quality_schedule`, `make_snapshot` (rebuilds indexes and invents a customer
for every order that lacks one). `NOW` is Monday 2026-09-07 08:00 UTC — inside the default day
shift. Fixtures: `scheduling_config`, `constraint_engine`, `day_calendar`, `always_calendar`,
`dq_config`, `clean_snapshot` (zero DQ issues), `clock` (`FrozenClock(NOW)`), `scheduler`
(`RuleBasedScheduler(clock)`). Rule for extension: append helpers, never change existing
signatures.

---

## 4. Running the tests

All commands from `backend/` with the virtualenv (`.venv/bin/python -m pytest …`; `pytest` alone
works when the venv is activated).

| Goal | Command |
|---|---|
| Everything except the slow tests (what CI runs first) | `pytest -m "not slow"` |
| Unit tier | `pytest tests/unit` or `pytest -m unit` |
| Engine tier | `pytest tests/engines` |
| One engine area | `pytest tests/engines/test_priority_factors.py -k DueDate` |
| Integration on SQLite only | `pytest tests/integration` (PostgreSQL parameter skips) |
| Integration on SQLite + PostgreSQL | `PPSE_TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@127.0.0.1:5432/ppse_test pytest tests/integration` |
| Slow / performance tests | `pytest -m slow` |
| Coverage | `pytest -m "not slow" --cov=app --cov-report=term-missing` |
| Lint / format / types (as CI) | `ruff format --check app tests synthetic && ruff check app tests synthetic && mypy app` |
| Frontend | `cd frontend && npm test -- --run` (or `npm run test:run`); `npm test` for watch mode |

Local PostgreSQL for the integration tier: create the database once
(`createdb -h 127.0.0.1 -U postgres ppse_test`); the fixture calls `create_all`, so migrations
are not required for tests (they are exercised separately by `alembic upgrade head` in CI).

---

## 5. SQLite vs PostgreSQL

Unit tests use `sqlite+pysqlite:///:memory:` with a `StaticPool` (one shared in-memory database
per engine), `check_same_thread=False` and `PRAGMA foreign_keys=ON` so cascade behaviour matches
PostgreSQL (`app/core/db.py`). `UTCDateTime` re-tags SQLite values as UTC, which is why the domain
contract can require aware datetimes on both backends. Everything portable is asserted on SQLite;
the `db_session` parametrisation runs the same repository tests a second time on PostgreSQL to
catch dialect differences (JSON handling, ordering, savepoints). Skipping rather than failing when
`PPSE_TEST_DATABASE_URL` is unset keeps the suite runnable on a laptop without a database.

---

## 6. CI matrix (`.github/workflows/ci.yml`)

Triggers: pushes to `main` and `claude/**`, all pull requests; one in-progress run per ref.

| Job | Environment | Steps |
|---|---|---|
| **backend** | `ubuntu-latest`, Python 3.11 (pip cache keyed on `pyproject.toml`), `postgres:16-alpine` service on 5432 with `ppse_test`; env `PPSE_ENVIRONMENT=test`, `PPSE_DATABASE_URL` and `PPSE_TEST_DATABASE_URL` → the service, `PPSE_JWT_SECRET=ci-only-secret` | `pip install -e ".[dev,optimization]"` → `ruff format --check app tests synthetic` → `ruff check app tests synthetic` → `mypy app` → `alembic upgrade head` (migration applies on a clean PostgreSQL) → `pytest -q -m "not slow" --cov=app --cov-report=xml` → `pytest -q -m "slow" --timeout=900 \|\| pytest -q -m "slow"` → upload `coverage.xml` (always) |
| **frontend** | `ubuntu-latest`, Node 22 (npm cache) | `npm ci` → `npm run typecheck` → `npm run lint` → `npm test -- --run` → `npm run build` |
| **docker** | needs backend + frontend | `docker/build-push-action` for `backend/` and `frontend/` (`push: false`) |

Because `PPSE_TEST_DATABASE_URL` is set, CI runs the integration tier on both backends. Note on
the slow step: `pytest-timeout` is not in the `dev` extra, so `--timeout=900` makes the first
invocation fail on an unknown option and the `||` fallback runs the slow tests without a timeout.

---

## 7. Synthetic data as test data (spec Phase 31)

`backend/synthetic/` — `SyntheticDataGenerator(seed=42, scale="medium", as_of=None,
dq_defect_ratio=0.03).generate() -> SyntheticDataset` with `to_snapshot()`; the
`MockERPConnector` serves the same dataset as ERP-shaped raw records, and `python -m app.cli sync
--scale … --seed … --dq-defect-ratio …` loads it into the database.

| Scale | Customers | Orders (nominal; duplicates add ≤ 5 %) | Machines | Materials | Tooling | Used by |
|---|---|---|---|---|---|---|
| `small` | 80 | 300 | 12 (11 groups) | ≥ profile | profile | unit tests (`test_synthetic_generator.py` module fixture), local dev |
| `medium` | 800 | 5,000 (≤ 5,250) | 45 | 33 | 40 | dev default in compose; `test_medium_scale_counts` (also asserts 2 machines down and generation < 5 s) |
| `large` | 800 | 20,000 (≤ 21,000) | 120 | — | — | `test_large_scale_performance` (`slow`, < 20 s) |

Determinism: all randomness flows through one `random.Random(seed)`; the reference instant is the
fixed `DEFAULT_AS_OF` = 2026-09-14 03:30 UTC (Monday 09:00 IST) unless `as_of` is given, so the
same `(seed, scale, as_of)` yields byte-identical datasets (`test_same_seed_is_identical` compares
`dataclasses.astuple` fingerprints of orders, operations, machines and customers) and different
seeds differ. Datasets include CNC and additive routings, several machines per group, materials
with shortages, tooling, two calendars, planned/unplanned downtime, setup and cycle times, quality
failures, dependencies and a realistic tier mix (`test_customer_tier_mix`: 2–9 % strategic).

**DQ defect injection** (`synthetic/defects.py`): `dq_defect_ratio` of the open orders is
corrupted in ERP-typical ways, spread evenly over seven kinds — `missing_due_date`,
`missing_cycle_time`, `missing_machine_group`, `negative_quantity`, `impossible_cycle_time`
(5,000 min/unit), `duplicate_order_ref` (a second row `<order_id>-DUP` with cloned operations),
`unknown_material_ref` — one defect per victim, tagged in `order.attributes["injected_defect"]`
so detection recall can be measured; counts are reported in `GenerationStats.dq_defects`.

---

## 8. Property-based tests (Hypothesis)

| Test | Strategy | Property |
|---|---|---|
| `test_priority_factors.py::TestDueDateUrgency::test_property_bounded_and_monotone` (200 examples) | `hours ∈ [−1000, 20000]`, `delta ∈ [0, 5000]` | `urgency_score` stays in [0, 100] and is non-increasing in hours |
| `…::test_property_any_monotone_config_stays_in_range` (100 examples) | random monotone anchor scores | any configuration keeps raw score and points in range |
| `test_calendar.py::test_add_work_minutes_is_monotonic_and_invertible` (150 examples) | `calendar_and_points()`: a sampled spec (UTC day shift, two-shift `Asia/Kolkata` with a holiday, 24×7) × sampled downtime × a start in [−3, +20] days × two minute counts ≤ 5000 | `add_work_minutes` is monotone and consistent with `working_minutes_between` |
| `test_calendar.py` second property (60 examples) | same strategy | round-trip / consistency of the working-time arithmetic |

All use `settings(deadline=None)` so CI machine speed cannot flake them.

---

## 9. Performance tests and targets

| Test | Marker | Data | Asserted bound | Measured here |
|---|---|---|---|---|
| `test_scheduling_perf.py::test_large_plant_under_ten_seconds` | `slow` | 5,000 orders, 12,000 operations (5 routings), 45 machines, 30-day horizon, priorities pre-built | `< 10 s`, every operation scheduled, no overlaps per machine | 9.0 s with back-filling (8.5 s without, same container) |
| `test_data_quality_engine.py::test_twenty_thousand_orders_under_two_seconds` | `slow` | 20,000 orders, 35 machines, 800 customers, 50 materials | `< 2 s` | — |
| `test_synthetic_generator.py::test_large_scale_performance` | `slow` | `large` scale | `< 20 s`, 20,000–21,000 orders, 120 machines | — |
| `test_synthetic_generator.py::test_medium_scale_counts` | — | `medium` | generation `< 5 s` | — |
| `test_priority_engine.py::test_evaluate_scales_to_many_orders` | — | 600 orders, 40 customers, 3 machines | `< 5 s`, ranks 1..600 | — |
| (no test) full `PriorityEngine.evaluate` on the perf snapshot above | — | same as the scheduling perf test | `docs/REQUIREMENTS.md` NFR-02: ≤ 30 s for 20,000 orders | 53.6 s for 5,000 orders — **no test guards this yet and the target is not met at this scale** |

"Measured here" values come from a single run in the development container
(`docs/SCHEDULING_ENGINE.md` §13); treat them as indicative, not as CI assertions.

---

## 10. Frontend tests

`frontend/vite.config.ts` → Vitest with `environment: "jsdom"`, `globals: true`, `css: false`,
`include: ["src/**/*.test.{ts,tsx}"]`, setup `src/test/setup.ts` (registers
`@testing-library/jest-dom/vitest` matchers, `cleanup()` after each test, and a `ResizeObserver`
stub for recharts). Fixtures such as `makePriorityResult` live in `src/test/fixtures`. Tests
render components with Testing Library and assert on `data-testid`s and text — e.g.
`ExplanationPanel.test.tsx` checks that factor lines render as "+points — name — reason" sorted by
magnitude, that adjustments and blockers appear, and that the total equals the score; the UI never
composes its own reasons, so these tests pin the contract with `explanation_lines` in the backend.
Run `npm test -- --run`; CI also runs `typecheck`, `lint` and `build`.

---

## 11. Determinism and invariants worth knowing

Tests that fail if a change introduces non-determinism or breaks a structural invariant:
`test_priority_engine.py::test_ranking_is_deterministic_and_tie_broken_by_due_then_id`,
`test_priority_explanation.py::test_lines_re_sum_to_the_score` and
`test_render_is_pure_function_of_result`, `test_scheduling_rule_based.py::test_deterministic`,
`test_clock_drives_now`, `test_frozen_clock_rejects_naive_datetime`,
`test_scheduling_cpsat.py::test_deterministic`, `test_simulation_engine.py::test_run_is_deterministic`,
`test_scheduling_perf.py` (no overlap on any machine), `test_synthetic_generator.py::test_same_seed_is_identical`,
and `test_db_snapshot_codec.py` (round-trip equality of a full snapshot).

---

## 12. Adding tests

### 12.1 A new priority factor

In `tests/engines/test_priority_factors.py`: a `TestMyFactor` class with `factor = MyFactor()`;
build orders with `make_order(...)`, a context with `make_context(make_snapshot([...]), profile,
**lookups)` (hand-supplied `readiness`, `projected_completion`, `eligible_machines`,
`machine_next_free`, or an `ExtendedPriorityContext(...)` when the factor reads extended fields);
parametrise the curve anchors against the pure scoring function (as `test_curve_anchor_points`
does for `urgency_score`); assert the exact `reason` strings and `details` keys; cover every
missing-data path (`None` inputs, empty population). `test_registry_covers_every_key_and_rejects_unknown`
and `test_every_factor_handles_empty_order` pick the new key up from `FACTOR_KEYS` automatically;
add a Hypothesis property when the factor has a monotone curve. If the context builder changed,
extend `test_priority_context.py`.

### 12.2 A new constraint

Hard: in `tests/engines/test_constraints_hard.py`, instantiate the constraint, build `op` /
`machine` / `order` with the factories and a `ConstraintContext(snapshot, at=NOW, config, order)`;
assert `check()` returns `None` when data is missing (conservative rule), and a `Violation` with
the expected `constraint_key`, message and `details` when it fires; add a case to
`test_constraints_engine.py` showing the machine is rejected in `eligible_machines`. Soft: in
`test_constraints_soft.py` with a `MachineState` in the context; assert the cost arithmetic from
`SchedulingConfig` and that `None` is returned at zero cost. Readiness rules go through
`assess_order` in `test_constraints_readiness.py` with the precedence assertion.

### 12.3 A new scheduler / scheduling rule

Follow `tests/engines/test_scheduling_rule_based.py`: `make_plant_snapshot(...)`,
`make_priorities({...})`, `build_calendars`, `default_constraint_engine`, then assert on
`result.entries_for_machine(...)` / `entries_for_order(...)`: no overlap (`a.end <= b.setup_start`),
operation precedence, `sequence_on_machine` numbering, `placement_reason` content, unscheduled
reason codes, and two identical runs. Register the scheduler and add a `test_registered` case as
in `test_scheduling_cpsat.py`; guard optional dependencies with `pytest.importorskip`.

### 12.4 A new simulation scenario

In `tests/engines/test_simulation_scenarios.py`: parse it from a dict (`parse_scenario({"kind":
…})`), apply with `scenario.apply(snapshot, profile, config)` and assert the original snapshot is
untouched, the clone changed as described, `ScenarioEffect.notes` / `affected_*` are filled and
unknown references raise `ValidationError`; then one end-to-end case in
`test_simulation_engine.py` asserting the `ScheduleDiff` fields.

### 12.5 A new API endpoint

Until a `tests/api/` package exists, follow `tests/unit/test_core_app.py`: use `app_client` and
`auth_headers(Role.PLANNER)`; assert the status code, the response schema, the error envelope
(`{"error", "message", "details"}`), and the role matrix of `docs/DESIGN_CONTRACT.md` §9 for at
least one role below and one at the minimum. If you create `tests/api/`, add an `api` marker to
`pyproject.toml` (unknown markers are not registered automatically) and give the package an
`__init__.py` like the other tiers. Endpoint tests run on the SQLite session with the frozen clock,
so timestamps in responses are exact.

### 12.6 A new repository or table

Add cases to the matching `tests/integration/test_repositories_*.py` module using
`loaded_session` (sample data persisted) or a bare `db_session`; keep them backend-agnostic so
the PostgreSQL parametrisation passes; add the ORM ↔ domain round-trip to
`tests/unit/test_db_mappers.py` and, for anything that goes into a snapshot, to
`tests/unit/test_db_snapshot_codec.py`.
