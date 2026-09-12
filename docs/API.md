# API reference

Generated from the FastAPI application (`backend/scripts/generate_api_docs.py`). All endpoints are prefixed with `/api/v1`, exchange JSON, and require a bearer JWT obtained from `POST /auth/login` unless marked public. Errors use the envelope `{"error": <code>, "message": <text>, "details": {...}}`. Role precedence: executive < operator < supervisor < planner < production_manager < admin; `X+` means role X or higher, and read endpoints additionally admit the read-only executive role. The interactive OpenAPI UI is served at `/docs`.

## alerts

| Method | Path | Summary | Access |
|---|---|---|---|
| `GET` | `/api/v1/alerts` | Active alerts | supervisor+ or executive (read) |
| `GET` | `/api/v1/alerts/summary` | Active alert counts by severity | supervisor+ or executive (read) |
| `GET` | `/api/v1/alerts/{alert_id}` | One alert | supervisor+ or executive (read) |
| `POST` | `/api/v1/alerts/{alert_id}/acknowledge` | Acknowledge an alert | supervisor+ |

## analytics

| Method | Path | Summary | Access |
|---|---|---|---|
| `GET` | `/api/v1/analytics/bottlenecks` | Bottlenecks (spec Phase 12) | executive+ or executive (read) |
| `GET` | `/api/v1/analytics/capacity` | Required vs available hours (spec Phase 13) | executive+ or executive (read) |
| `GET` | `/api/v1/analytics/kpis` | Executive KPIs (spec Phase 8) | executive+ or executive (read) |
| `GET` | `/api/v1/analytics/on-time-delivery` | Historical and projected OTD | executive+ or executive (read) |
| `GET` | `/api/v1/analytics/schedule-quality` | Quality of the active plan vs the newest draft (spec Phase 36) | executive+ or executive (read) |

## audit

| Method | Path | Summary | Access |
|---|---|---|---|
| `GET` | `/api/v1/audit` | Query the audit log | production_manager+ |

## auth

| Method | Path | Summary | Access |
|---|---|---|---|
| `POST` | `/api/v1/auth/login` | Exchange credentials for a JWT | public |
| `GET` | `/api/v1/auth/me` | The authenticated principal | any authenticated user |

## customers

| Method | Path | Summary | Access |
|---|---|---|---|
| `GET` | `/api/v1/customers` | Customers with tier and rules | planner+ or executive (read) |
| `GET` | `/api/v1/customers/{customer_id}` | Customer with its rule | planner+ or executive (read) |
| `DELETE` | `/api/v1/customers/{customer_id}/rules` | Delete the rule | production_manager+ |
| `GET` | `/api/v1/customers/{customer_id}/rules` | Customer rule | planner+ or executive (read) |
| `PUT` | `/api/v1/customers/{customer_id}/rules` | Create or replace the rule | production_manager+ |

## data-quality

| Method | Path | Summary | Access |
|---|---|---|---|
| `GET` | `/api/v1/data-quality` | Data quality dashboard | planner+ or executive (read) |
| `GET` | `/api/v1/data-quality/issues` | Issues of the latest run | planner+ or executive (read) |
| `POST` | `/api/v1/data-quality/run` | Run the data quality engine now | planner+ |

## health

| Method | Path | Summary | Access |
|---|---|---|---|
| `GET` | `/api/v1/health` | Liveness/readiness probe | public |
| `GET` | `/api/v1/metrics` | Simple JSON counters | public |

## locks

| Method | Path | Summary | Access |
|---|---|---|---|
| `POST` | `/api/v1/schedule/lock` | Create a lock | production_manager+ |
| `GET` | `/api/v1/schedule/locks` | Active locks | operator+ or executive (read) |
| `POST` | `/api/v1/schedule/unlock` | Release a lock | production_manager+ |

## machines

| Method | Path | Summary | Access |
|---|---|---|---|
| `GET` | `/api/v1/machines` | Machines with current load | operator+ or executive (read) |
| `GET` | `/api/v1/machines/{machine_id}` | Machine detail | operator+ or executive (read) |
| `GET` | `/api/v1/machines/{machine_id}/schedule` | Machine schedule | operator+ or executive (read) |

## orders

| Method | Path | Summary | Access |
|---|---|---|---|
| `GET` | `/api/v1/expedites` | Active expedites | operator+ or executive (read) |
| `DELETE` | `/api/v1/expedites/{expedite_id}` | Cancel an expedite | production_manager+ |
| `GET` | `/api/v1/orders` | Priority queue | operator+ or executive (read) |
| `GET` | `/api/v1/orders/{order_id}` | Order detail view | operator+ or executive (read) |
| `POST` | `/api/v1/orders/{order_id}/expedite` | Expedite an order for a limited time | production_manager+ |
| `GET` | `/api/v1/orders/{order_id}/explanation` | Why is this order prioritised? | operator+ or executive (read) |
| `POST` | `/api/v1/orders/{order_id}/force-next` | Force the order to be produced next | production_manager+ |
| `POST` | `/api/v1/orders/{order_id}/hold` | Put an order on hold | planner+ |
| `POST` | `/api/v1/orders/{order_id}/lock-machine` | Lock the machine assignment of an order | production_manager+ |
| `GET` | `/api/v1/orders/{order_id}/machines` | Eligible machines for the order's next operation | operator+ or executive (read) |
| `POST` | `/api/v1/orders/{order_id}/move` | Move the order to a machine (optionally at a time) | production_manager+ |
| `POST` | `/api/v1/orders/{order_id}/override-priority` | Increase, decrease or set the priority score | production_manager+ |
| `GET` | `/api/v1/orders/{order_id}/overrides` | Active overrides of an order | operator+ or executive (read) |
| `POST` | `/api/v1/orders/{order_id}/release` | Release a planner hold | planner+ |
| `GET` | `/api/v1/overrides` | Active overrides | operator+ or executive (read) |
| `DELETE` | `/api/v1/overrides/{override_id}` | Cancel an override | production_manager+ |

## priority-configuration

| Method | Path | Summary | Access |
|---|---|---|---|
| `GET` | `/api/v1/priority/configuration` | Active priority profile | planner+ or executive (read) |
| `PUT` | `/api/v1/priority/configuration` | Save a new priority profile version | admin+ |
| `POST` | `/api/v1/priority/configuration/preview` | Simulate a weight change | planner+ |
| `GET` | `/api/v1/priority/configuration/versions` | Configuration versions | planner+ or executive (read) |
| `GET` | `/api/v1/priority/configuration/versions/{version}` | One configuration version | planner+ or executive (read) |
| `POST` | `/api/v1/priority/configuration/versions/{version}/activate` | Roll back to a version | admin+ |

## schedule

| Method | Path | Summary | Access |
|---|---|---|---|
| `GET` | `/api/v1/schedule` | Active plan with a page of its entries | operator+ or executive (read) |
| `POST` | `/api/v1/schedule/approve` | Approve a draft (DRAFT → APPROVED) | production_manager+ |
| `GET` | `/api/v1/schedule/compare` | Compare two versions | operator+ or executive (read) |
| `GET` | `/api/v1/schedule/gantt` | Gantt board: rows per machine | operator+ or executive (read) |
| `POST` | `/api/v1/schedule/generate` | Generate a new DRAFT schedule version | planner+ |
| `POST` | `/api/v1/schedule/publish` | Publish an approved version (APPROVED → PUBLISHED) | production_manager+ |
| `POST` | `/api/v1/schedule/reject` | Reject a draft or approved version | production_manager+ |
| `POST` | `/api/v1/schedule/replan` | Evaluate continuous replanning now | planner+ |
| `GET` | `/api/v1/schedule/runs/{run_id}` | Optimization run details | operator+ or executive (read) |
| `POST` | `/api/v1/schedule/simulate` | What-if simulation (nothing is persisted) | planner+ |
| `GET` | `/api/v1/schedule/versions` | Schedule versions | operator+ or executive (read) |
| `GET` | `/api/v1/schedule/versions/{version}` | One version | operator+ or executive (read) |
| `GET` | `/api/v1/schedule/versions/{version}/entries` | Entries of one version | operator+ or executive (read) |
| `GET` | `/api/v1/schedule/{date}` | Day view (YYYY-MM-DD) grouped by machine | operator+ or executive (read) |

## scheduling-configuration

| Method | Path | Summary | Access |
|---|---|---|---|
| `GET` | `/api/v1/scheduling/configuration` | Active scheduling configuration | planner+ or executive (read) |
| `PUT` | `/api/v1/scheduling/configuration` | Save a new configuration version | admin+ |
| `GET` | `/api/v1/scheduling/configuration/versions` | Configuration versions | planner+ or executive (read) |
| `GET` | `/api/v1/scheduling/configuration/versions/{version}` | One configuration version | planner+ or executive (read) |
| `POST` | `/api/v1/scheduling/configuration/versions/{version}/activate` | Roll back to a version | admin+ |

## simulation

| Method | Path | Summary | Access |
|---|---|---|---|
| `GET` | `/api/v1/simulation/scenario-types` | JSON schema of every what-if scenario kind | operator+ or executive (read) |

## sync

| Method | Path | Summary | Access |
|---|---|---|---|
| `GET` | `/api/v1/sync/capabilities` | Required / Available / Missing | admin+ |
| `POST` | `/api/v1/sync/run` | Run an ERP synchronisation now | admin+ |
| `GET` | `/api/v1/sync/runs` | Sync run history | admin+ |
| `GET` | `/api/v1/sync/runs/{run_id}` | One sync run | admin+ |
| `GET` | `/api/v1/sync/status` | Last run, watermark and connector health | admin+ |

## users

| Method | Path | Summary | Access |
|---|---|---|---|
| `GET` | `/api/v1/users` | List users | admin+ |
| `POST` | `/api/v1/users` | Create a user | admin+ |
| `GET` | `/api/v1/users/{user_id}` | One user | admin+ |
| `PATCH` | `/api/v1/users/{user_id}` | Activate or deactivate a user | admin+ |
| `POST` | `/api/v1/users/{user_id}/reset-password` | Reset a user's password | admin+ |

## Conventions

* Paginated list endpoints accept `page` and `page_size` and return `{items, total, page, page_size, pages, has_more}`.
* Every mutating endpoint that changes priorities, locks, expedites, configuration or plan status requires a `reason` and writes an audit row (`GET /audit`).
* Timestamps are ISO-8601 UTC; durations are minutes unless the field name says hours.
* `POST /schedule/generate` creates a DRAFT version; `approve` and `publish` move it through the state machine (draft → approved → published → superseded). In READ_ONLY writeback mode publishing activates the plan inside PPSE and records a skipped writeback receipt.

