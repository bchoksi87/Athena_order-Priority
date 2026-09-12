# Deployment

This document describes how to run PPSE locally, how the container topology in
`docker-compose.yml` is wired, which environment variables exist (`backend/app/core/config.py`),
how the database is migrated, backed up and restored, what the application logs and exposes for
monitoring, how to upgrade, and how the ERP writeback mode is rolled out. It is written against
the repository as it is; parts of the runtime that are planned but not implemented are marked as
such rather than described as working.

**How to read this.** `docs/ARCHITECTURE.md` §2 (components) and §7 (observability) give the
design; `docs/DESIGN_CONTRACT.md` §2 fixes the stack; `docs/ERP_INTEGRATION.md` §7 defines the
writeback ladder and its gates; `docs/TESTING.md` covers CI; `docs/DATA_MODEL.md` §8 lists the
tables a backup must cover. Spec sections: DEPLOYMENT, OBSERVABILITY, Phase 26, Phase 27.

---

## 1. Components and runtime shape

| Process | Image / command | Purpose | Status |
|---|---|---|---|
| PostgreSQL 16 | `postgres:16-alpine` (compose) or an external cluster | The only state store (`docs/DATA_MODEL.md`) | available |
| `migrate` (job) | backend image, `python -m app.cli migrate` | Alembic `upgrade head` | implemented |
| `seed` (job) | backend image, `python -m app.cli seed && python -m app.cli sync --mode full` | Dev users + default config; synthetic ERP load through the mock connector | implemented (see §6.5 caveat on users) |
| `backend` (API) | backend image, `uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000 --proxy-headers` | FastAPI on `/api/v1` | implemented: `/health`, `/metrics`, `/auth/*`, `/orders` (queue, detail, explanation, machine options, expedite/hold/release/override-priority/force-next/move/lock-machine, overrides and expedites), `/machines` (list, detail, schedule), `/schedule/lock`, `/schedule/unlock`, `/schedule/locks`, `/priority/configuration` (+ versions, activate, preview), `/scheduling/configuration` (+ versions, activate), `/customers` (+ rules), `/alerts` (+ summary, acknowledge), `/audit`, `/data-quality` (+ issues, run), `/users`, backed by `app/services/*`. **Missing** from contract §9: `/schedule/generate`, `/schedule/simulate`, `/schedule/approve`, `/schedule/publish`, `/schedule/versions`, `/analytics/*`, `/sync/*` — nothing yet runs `app/engines/pipeline.py::PlanningPipeline` to produce a schedule version |
| `worker` | backend image, `python -m app.cli worker` | Background sync / replanning / alert evaluation (APScheduler 3 `BackgroundScheduler`; intervals from `PPSE_SYNC_INTERVAL_MINUTES`, `PPSE_REPLAN_INTERVAL_MINUTES`, alerts every 5 min; `max_instances=1`, coalescing, graceful SIGTERM shutdown). Run one job and exit with `--once sync|replan|alerts` (used for smoke tests and cron-style deployments). |
| `frontend` | nginx 1.27 serving the Vite build; proxies `/api/` to `BACKEND_URL` | React control tower | implemented (pages exist; they depend on backend endpoints that are not there yet) |

The backend image is a single artefact; API and worker are the same image with different
commands (`docs/ARCHITECTURE.md` §4.8).

---

## 2. Local development

### 2.1 Backend

Prerequisites: Python 3.11, PostgreSQL 16 reachable (the development environment used for this
repository has a local cluster on `127.0.0.1:5432`, user/password `postgres`/`postgres`, databases
`ppse` and `ppse_test` — `docs/DESIGN_CONTRACT.md` §2). OR-Tools is optional (`optimization` extra;
`cpsat` is registered only when it imports).

```bash
cd backend
python3.11 -m venv .venv
.venv/bin/pip install -e ".[dev,optimization]"        # ortools is optional but the compose image ships it
cp ../.env.example ../.env                              # or export PPSE_* variables
.venv/bin/python -m app.cli migrate                     # alembic upgrade head
.venv/bin/python -m app.cli seed                        # dev users (admin/admin123 …) + SystemConfig v1
.venv/bin/python -m app.cli sync --mode full --scale small   # synthetic ERP data via the mock connector
.venv/bin/python -m app.cli snapshot-stats              # sanity check: counts of the planning snapshot
.venv/bin/uvicorn app.main:create_app --factory --reload --port 8000
```

`Settings` reads `.env` from the current working directory (`env_file=".env"`), so either run
from a directory containing `.env` or export the variables. OpenAPI docs are served at
`http://127.0.0.1:8000/api/v1/docs`. In `PPSE_ENVIRONMENT=dev` the app also seeds users and the
default configuration on start-up (`seed_on_startup=True`; failures are logged and ignored so an
unmigrated database does not block start-up).

CLI reference (`python -m app.cli --help`; global options `--database-url`, `--log-level`,
`--json`; exit codes 0 ok, 1 application error, 2 usage):

| Command | Options | What it does |
|---|---|---|
| `migrate` | `--revision` (default `head`) | Runs Alembic `upgrade` with the resolved database URL |
| `seed` | — | `seed_users` (the six `DEV_USERS`) and `seed_default_config` (idempotent) |
| `sync` | `--mode full|incremental`, `--connector`, `--scale small|medium|large`, `--seed`, `--dq-defect-ratio`, `--prune`, `--retry-attempts` | `SyncService.run(mode)` with the selected connector (`PPSE_ERP_CONNECTOR`, default `mock`) |
| `snapshot-stats` | `--as-of`, `--include-closed` | Builds the `PlanningSnapshot` from the database and prints its summary |
| `create-user` | `--username`, `--password`, `--role`, `--display-name`, `--email` | Creates a local account |
| `worker` | `--once sync|replan|alerts` | Runs the APScheduler loop (blocking) or a single job; exit 1 when the job fails |

### 2.2 Frontend

```bash
cd frontend
npm ci                      # Node >= 22.12; .npmrc pins exact versions
npm run dev                 # Vite on http://localhost:5173, /api proxied to http://127.0.0.1:8000
```

`vite.config.ts` proxies every `/api` call to `VITE_DEV_PROXY_TARGET` (default
`http://127.0.0.1:8000`) so the browser never needs CORS in development; `VITE_API_BASE_URL`
(default `/api/v1`) and `VITE_APP_ENV` are the only build-time variables
(`frontend/.env.example`).

---

## 3. Environment variables

All backend settings are `PPSE_<FIELD>` (pydantic-settings, case-insensitive, `.env` supported,
unknown variables ignored). Business rules are never environment variables — they are versioned
configuration in the database (`docs/DATA_MODEL.md` §8.3).

| Variable | Default | Meaning |
|---|---|---|
| `PPSE_ENVIRONMENT` | `dev` | `dev` \| `test` \| `prod`; `prod` only changes the JWT-secret warning and disables start-up seeding (`is_dev`) |
| `PPSE_DATABASE_URL` | `postgresql+psycopg://postgres:postgres@127.0.0.1:5432/ppse` | SQLAlchemy URL; `pool_pre_ping` for PostgreSQL |
| `PPSE_TEST_DATABASE_URL` | unset | Used instead of `database_url` when `environment=test`; also enables the PostgreSQL variant of the integration tests |
| `PPSE_DATABASE_ECHO` | `false` | SQL echo |
| `PPSE_JWT_SECRET` | `dev-only-insecure-secret-change-me` | HS256 signing secret. **Must be set in production**; the app only *warns* (`insecure_jwt_secret`) when the default is used with `environment=prod` |
| `PPSE_JWT_ALGORITHM` | `HS256` | |
| `PPSE_JWT_EXPIRE_MINUTES` | `480` | Token lifetime (≥ 1) |
| `PPSE_LOG_LEVEL` | `INFO` | Upper-cased |
| `PPSE_LOG_JSON` | `false` | `true` → structlog JSON renderer (the backend image sets `true`) |
| `PPSE_CORS_ORIGINS` | `["http://localhost:5173","http://127.0.0.1:5173"]` | JSON list or comma-separated |
| `PPSE_ERP_CONNECTOR` | `mock` | Connector name in `ConnectorRegistry` |
| `PPSE_SYNTHETIC_SEED` | `42` | Mock dataset seed |
| `PPSE_SYNTHETIC_SCALE` | `small` | Mock dataset scale (`.env.example` and compose set `medium`) |
| `PPSE_WRITEBACK_MODE` | `read_only` | `read_only` \| `approval` \| `writeback` \| `controlled_auto` (§10) |
| `PPSE_BACKGROUND_JOBS_ENABLED` | `false` | Reserved for the worker; nothing consumes it yet |
| `PPSE_SYNC_INTERVAL_MINUTES` | `15` | Reserved for the worker (≥ 1) |
| `PPSE_REPLAN_INTERVAL_MINUTES` | `30` | Reserved for the worker (≥ 1) |
| `PPSE_SEED_ON_STARTUP` | `true` | Dev-only start-up seeding (only acts when `environment=dev`) |
| `PPSE_CURRENCY` | `INR` | Display currency |
| `PPSE_API_PREFIX` | `/api/v1` | Must start with `/` |
| `PPSE_APP_NAME`, `PPSE_APP_VERSION` | "Production Priority & Scheduling Engine", `0.1.0` | Reported by `/health` and OpenAPI |

Compose-only variables (`.env.example`): `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`,
`POSTGRES_PORT` (5432), `BACKEND_PORT` (8000), `FRONTEND_PORT` (8080). Frontend build-time:
`VITE_API_BASE_URL`, `VITE_APP_ENV`; runtime: `BACKEND_URL` (nginx proxy target).

---

## 4. docker-compose topology and start-up order

```
db (postgres:16-alpine, healthcheck pg_isready, volume ppse-pgdata, port ${POSTGRES_PORT})
 └─ migrate  (depends_on db: service_healthy)            python -m app.cli migrate
     ├─ seed     (depends_on migrate: completed_successfully)  seed && sync --mode full
     ├─ backend  (depends_on migrate: completed_successfully)  uvicorn …, port ${BACKEND_PORT}:8000,
     │            healthcheck GET /api/v1/health, PPSE_BACKGROUND_JOBS_ENABLED=false
     ├─ worker   (depends_on migrate: completed_successfully)  python -m app.cli worker   (PPSE_BACKGROUND_JOBS_ENABLED=true)
     └─ frontend (depends_on backend: service_healthy)   nginx, port ${FRONTEND_PORT}:80, BACKEND_URL=http://backend:8000
```

All backend services share the `x-backend-env` anchor (`PPSE_DATABASE_URL` pointing at `db`,
`PPSE_JWT_SECRET`, `PPSE_LOG_JSON=true`, connector/scale/seed, writeback mode, CORS origins).
`seed` and `backend` both wait only for `migrate`, so the API may come up while the synthetic
sync is still loading — `/health` reports the database as reachable, not the data as loaded.

```bash
cp .env.example .env            # set PPSE_JWT_SECRET and POSTGRES_PASSWORD
docker compose up -d --build
docker compose logs -f seed     # wait for "sync … completed"
open http://localhost:8080      # dev accounts: admin/admin123, manager/manager123, planner/planner123, …
```

No Docker daemon was available in the environment that produced this repository; the images are
built in CI (`.github/workflows/ci.yml`, `docker` job) but the compose stack has not been run
end-to-end here.

---

## 5. Images

* **Backend** (`backend/Dockerfile`): multi-stage `python:3.11-slim`; wheels for `.[optimization]`
  built in the first stage, installed in the runtime stage; app, `synthetic/`, `alembic.ini` and
  `alembic/` copied so the CLI and migrations run from the image; non-root user `ppse`;
  `PPSE_ENVIRONMENT=prod`, `PPSE_LOG_JSON=true` baked in; `EXPOSE 8000`; `HEALTHCHECK` every 30 s
  on `/api/v1/health`; default command is uvicorn with `--proxy-headers`.
* **Frontend** (`frontend/Dockerfile`): `node:22-alpine` build (`npm ci`, `npm run build` with
  `VITE_API_BASE_URL=/api/v1` baked in) → `nginx:1.27-alpine` with
  `nginx/default.conf.template` rendered by the official entrypoint (`${BACKEND_URL}`): gzip,
  `/healthz` → 200, `/api/` proxied with `X-Forwarded-*` headers and a 300 s read timeout for long
  runs, immutable caching for `/assets/`, SPA fallback to `index.html`; `HEALTHCHECK` on `/healthz`.

---

## 6. Production guidance

### 6.1 Secrets

Provide `PPSE_JWT_SECRET` (random, ≥ 32 bytes) and the database credentials through the
orchestrator's secret store (Docker/Compose secrets, Kubernetes Secrets, a vault-backed env
injector) — never in the image or a committed `.env`. The application refuses nothing: with the
default secret in `prod` it only logs `insecure_jwt_secret`, so enforce the check in deployment
(e.g. a start-up script that fails when `PPSE_JWT_SECRET` is unset). Logs redact database
credentials (`cli._redact`) and never include tokens or passwords; keep `PPSE_DATABASE_ECHO`
off in production.

### 6.2 TLS termination

Both containers listen on plain HTTP (backend 8000, frontend 80). Terminate TLS at a reverse
proxy or ingress in front of the frontend container (which already proxies `/api/`), or in front
of both. The backend runs uvicorn with `--proxy-headers`, and nginx forwards `X-Forwarded-For` /
`X-Forwarded-Proto`, so redirects and request logging see the external scheme. Restrict
`PPSE_CORS_ORIGINS` to the public origin(s) of the UI.

### 6.3 Scaling API vs worker

The API is stateless (settings, clock, engine and session factory live on `app.state`; JWTs are
self-contained; the in-process `MetricsRegistry` is per replica) and can run several replicas
behind the proxy. Schedule generation and simulation are CPU-bound (`docs/SCHEDULING_ENGINE.md`
§13), so size API pods for the largest expected run or, once the worker exists, move generation
into it and have the API return run ids (`docs/ARCHITECTURE.md` §6). The design is one worker
process per job class (APScheduler in-process); do not run two workers with the same jobs against
one database until job locking exists. Today: run one API replica per CPU you want to dedicate to
runs and scale `worker` to 0.

### 6.4 Health checks

* Backend: `GET /api/v1/health` → `200 {"status":"ok","database":"ok",…}` or `503` with
  `"degraded"` / `"unavailable"` when `SELECT 1` fails. It checks the database only (the
  connector and background-job status mentioned in `docs/ARCHITECTURE.md` §7 are not included).
* Frontend: `GET /healthz` → `200 ok`.
* PostgreSQL: `pg_isready`.

Use the backend probe for both liveness and readiness; add a readiness gate on "migrations at
head" if the orchestrator can run the `migrate` job separately (§7).

### 6.5 Users and seeding in production

`python -m app.cli seed` always creates the six development accounts with well-known passwords
(`admin/admin123`, `manager/manager123`, `planner/planner123`, `supervisor/supervisor123`,
`operator/operator123`, `executive/executive123`) regardless of `PPSE_ENVIRONMENT` — the
`seed_users` docstring mentions an `allow` guard that does not exist. In production **do not run
`seed`**; run only `seed_default_config` semantics by creating real accounts with
`python -m app.cli create-user --role admin …` and let the first API start (or a small script)
store `SystemConfig()` v1 — or run `seed` once and immediately disable/re-password the dev accounts.
Start-up seeding is skipped automatically outside `dev`.

### 6.6 Resources

The priority context and scheduler hold the whole snapshot in memory (5,000 orders / 12,000
operations comfortably fit in a few hundred MB); CP-SAT (`num_workers=1`, 10 s limit per
machine) adds bounded CPU. PostgreSQL sizing is dominated by `input_snapshots` (compressed JSON per
run) and `schedule_entries` (one row per operation per version) — see §8 for retention.

---

## 7. Database migration strategy

* **Tool:** Alembic (`backend/alembic.ini`, `backend/alembic/env.py`, versions in
  `backend/alembic/versions/`). `env.py` imports `app.db.models` so `Base.metadata` is complete,
  resolves the URL as `-x db_url=…` > `PPSE_DATABASE_URL` (via `Settings.effective_database_url`),
  sets `compare_type=True`, and `render_as_batch` for SQLite.
* **Current state:** one revision, `0001` "Initial schema", hand-reviewed after autogenerate
  against PostgreSQL 16; it creates all 25 tables and indexes and has a working `downgrade()`.
* **Applying:** `python -m app.cli migrate` (programmatic `alembic upgrade head`; `--revision` to
  target another revision) or `alembic upgrade head` from `backend/`; against a different database
  `alembic -x db_url=postgresql+psycopg://… upgrade head`. CI runs `alembic upgrade head` against
  a fresh PostgreSQL service on every push.
* **Policy (guidance):** migrations are forward-only in shared environments — never edit a
  revision that has been applied anywhere; fix forward with a new revision. Downgrades exist for
  local development and disaster recovery only; a production rollback restores a backup (§8).
  Run `migrate` as a separate job before rolling the API (the compose `migrate` service is exactly
  that), so replicas never start against an older schema; keep migrations backward compatible
  with the previous application version (add columns nullable, backfill, then constrain).
* **Review checklist for autogenerate:** only portable types (`String`, `Integer`, `Float`,
  `Boolean`, `DateTime(timezone=True)`, `JSON`, `Text`, `LargeBinary`); enums as `String`, no
  `JSONB`/arrays/PostgreSQL enums; no foreign keys to ERP-owned references (`docs/DATA_MODEL.md`
  §7.1); indexes named as in the ORM; explicit `server_default` for new non-null columns; a
  `downgrade()` that reverses the change; run the unit tests (SQLite `create_all`) and the
  PostgreSQL integration tests before committing.
* Tests use `Base.metadata.create_all` on SQLite; production never does.

---

## 8. Backup and restore (PostgreSQL)

There is one database; everything PPSE knows — synced masters, planner overlays, configuration
versions, results, snapshots and the audit log — lives in it (`docs/DATA_MODEL.md` §8). No backup
tooling is shipped with the repository; the following is the recommended operating procedure.

* **Logical backups (baseline):** `pg_dump -Fc -d ppse -f ppse-$(date -u +%FT%H%M).dump` at least
  daily (custom format compresses `input_snapshots.payload`, which is already gzip, poorly — expect
  the dump to be close to the table size). Restore with `pg_restore -d ppse --clean --if-exists`.
  With compose: `docker compose exec db pg_dump -U $POSTGRES_USER -Fc $POSTGRES_DB > backup.dump`.
* **Point-in-time recovery (recommended for production):** enable WAL archiving on the cluster
  (`archive_mode=on`, `archive_command`, or a managed service's PITR) plus periodic base backups
  (`pg_basebackup` or the provider's snapshots). The audit log and schedule versions are
  append-heavy; PITR lets you recover to just before an operator error. The
  `docs/REQUIREMENTS.md` NFR-12 target is "daily base backup + WAL".
* **What consistency means here:** a schedule version is `schedule_versions` + its
  `schedule_entries` + the `optimization_runs` row + the `input_snapshots` row it references;
  `pg_dump` is transactionally consistent, so a single dump preserves that. Do not back up tables
  selectively.
* **Retention:** keep every configuration version and the audit log indefinitely (auditability);
  `input_snapshots` and `priority_results` can be pruned after the retention period your audit
  policy requires (a run's snapshot is what makes "why was this scheduled at 14:30 yesterday?"
  answerable — `docs/ARCHITECTURE.md` §4.3), using the `as_of` / `computed_at` indexes.
* **Volume:** with compose the data lives in the named volume `ppse-pgdata`; back up the database,
  not the volume, unless the cluster is stopped.
* **Test restores** into a scratch database on a schedule (`PPSE_DATABASE_URL` pointed at it,
  `python -m app.cli snapshot-stats` as a smoke test).
* **Re-sync is not a substitute:** ERP masters can be re-synced (`sync --mode full`), but overlays,
  approvals, versions and the audit trail cannot be regenerated.

---

## 9. Logging and monitoring

### 9.1 Logging (`app/core/logging.py`)

structlog with `merge_contextvars`, log level, logger name, ISO UTC timestamp, stack/exception
rendering; JSON renderer when `PPSE_LOG_JSON=true` (the image default), coloured console
otherwise. `RequestIdMiddleware` binds `request_id` (from an incoming `X-Request-ID` or a new
`req_…` id), `method` and `path` to every line of a request and echoes `X-Request-ID` in the
response; `get_current_user` adds `user_id` and `role`. Engines log structured events
(`priority.evaluated`, `scheduler.start`/`scheduler.done`, `replanning.decision`,
`simulation.done`, `calendar.fallback_24x7`, `cpsat.no_solution`, …) with counts and durations
where available. `uvicorn.access`, `sqlalchemy.engine`, `httpx` are raised to WARNING. Ship stderr
to your log pipeline; no file logging is configured.

### 9.2 Monitoring endpoints

* `GET /api/v1/health` (public): status, database, version, environment, time; 503 when the
  database is unreachable.
* `GET /api/v1/metrics` (public): **JSON counters, not Prometheus text** —
  `requests_total`, `errors_total` (5xx), `by_status` (`2xx`…), `by_path` (`"GET /api/v1/health"`),
  `counters` (free-form, via `MetricsRegistry.increment`). Counters are per process and reset on
  restart; scrape them with a JSON exporter or add a Prometheus renderer (a formatting change on
  the same registry).

### 9.3 What to alert on

| Signal | Source today | Threshold suggestion |
|---|---|---|
| Backend health 503 / container unhealthy | `/health`, Docker `HEALTHCHECK` | any, for > 1 min |
| 5xx rate | `/metrics` `errors_total` delta, or log lines `unhandled_error` / `app_error` with code ≥ 500 | > 1 % of requests |
| Sync failures | `sync_runs.status = 'failed'` (SQL; no endpoint yet), log `cli.failed` | any failed run; no `completed` run within 2 × `PPSE_SYNC_INTERVAL_MINUTES` |
| Reconciliation mismatch | `sync_runs.details` (reconciliation status) | `mismatch` |
| Run failures / duration | `optimization_runs.status`, `started_at`/`finished_at` (once services write them); `scheduler.done` log | failed runs; duration above the budget in `docs/SCHEDULING_ENGINE.md` §13 |
| Data-quality blocking share | `GET /api/v1/data-quality` (summary of the latest run), `data_quality_issues` | > 2 % of open orders (`docs/ERP_INTEGRATION.md` §7 gate) |
| Active critical alerts | `GET /api/v1/alerts/summary` (counts by severity), `alerts` table | any `critical` |
| Insecure configuration | log `insecure_jwt_secret` at start-up | any |
| Disk / WAL | PostgreSQL monitoring | standard |

---

## 10. Upgrade procedure

1. Build and tag both images from the release commit (CI does `docker build` for both; push them
   to your registry in your release job).
2. Take a backup (§8) — mandatory when the release contains a migration.
3. Run the migration job with the new backend image (`python -m app.cli migrate`); it is
   idempotent and safe to re-run. Verify `alembic current` (or the log line `migrated … to head`).
4. Roll the API replicas to the new image; wait for `/health` 200 on each.
5. Roll the frontend image (static assets; cache-busting is by hashed file names).
6. Roll the worker when it exists.
7. Smoke test: `/health`, login, the CLI `snapshot-stats`, one schedule generation once the
   endpoint exists.
8. Rollback = redeploy the previous images. Because migrations are forward-only, a rollback after
   a schema change requires either a backward-compatible migration (§7) or a restore of the
   pre-upgrade backup; plan releases so that step 3 never makes the previous image unusable.

Configuration versions in the database are independent of releases; a release never rewrites
them, and `ConfigRepository.activate_version` can roll configuration back separately.

---

## 11. Writeback-mode rollout (spec Phase 26)

`PPSE_WRITEBACK_MODE` selects the `WritebackMode`; the default and the only mode with a shipped
gateway is `read_only`: `ReadOnlyWritebackGateway.publish()` logs `writeback.skipped_read_only`
and returns a `WritebackReceipt(status="skipped_read_only", message="writeback gateway is
read-only; schedule was not sent to the ERP")` whatever mode is requested.
`check_publish_preconditions` enforces the shared rules — `approval` requires an approving user,
`controlled_auto` requires a configured auto-publish rule — and no gateway for `approval`,
`writeback` or `controlled_auto` exists yet (`docs/REQUIREMENTS.md` F-12…F-14).

Rollout order, with the gates from `docs/ERP_INTEGRATION.md` §7 (parallel-run weeks, override
acceptance rate, DQ blocking share, reconciliation streak, rehearsed rollback):

1. **`read_only` first, for weeks.** Operators follow PPSE screens; measure agreement with what
   planners actually do; nothing leaves the system. This is the only mode to deploy with today.
2. **`approval`**: after a real ERP gateway exists and is validated on an ERP test instance —
   approved schedules are sent as recommendation fields; every publish writes an audit row and a
   receipt.
3. **`writeback`**: executable fields (sequence, planned dates, work centre) are written with
   read-back verification; keep the kill switch (`PPSE_WRITEBACK_MODE=read_only`) tested.
4. **`controlled_auto`**: only with configured rules and an on-call owner; automatic fallback to
   `writeback` on receipt errors.

Changing the mode is a deployment configuration change made by an admin and recorded in the
audit log; stepping *down* the ladder is always allowed immediately.

---

## 12. Not implemented yet (summary)

* Writeback gateways other than read-only: `APPROVAL`, `WRITEBACK` and `CONTROLLED_AUTO` route
  through `MockWritebackGateway` until a real ERP gateway exists (`docs/ERP_INTEGRATION.md` §7).
  Never enable them against a production ERP before the validation gates described there.
* `/metrics` is JSON (request counters, engine counters); a Prometheus exposition format is not
  provided yet — scrape the JSON or add an exporter.
* `POST /sync/run` executes synchronously in the API process (≈25 s for the medium synthetic
  plant); large real-ERP loads should be triggered through the worker instead.
* Backup scripts are described in §7 but not shipped; wire `pg_dump` into your platform's job
  scheduler.
* The order list endpoint pages in Python after loading the open book (≈2 s for 4,000 open lines);
  SQL-level paging is the planned optimisation for the "large" plant size.
