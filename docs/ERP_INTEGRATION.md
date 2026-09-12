# ERP/MES Integration

Spec Phases 1, 2, 26 and STEP 4/11. The ERP/MES is an unknown external system
(`docs/AUDIT_REPORT.md` §1–7). This document fixes how PPSE talks to it, what it needs from it,
how it copes when data is missing, and how writeback is introduced safely.

---

## 1. Connector abstraction

```
ERP/MES ──▶ ERPConnector (fetch_* → RawRecord) ──▶ Normalizer (field_maps, codes)
        ──▶ validate ──▶ upsert (repositories) ──▶ reconcile ──▶ sync_runs
```

`app/integration/connector.py`:

```python
class ERPConnector(Protocol):                      # READ-ONLY by construction
    name: str
    def fetch_customers(self, since: datetime | None = None) -> list[RawRecord]: ...
    def fetch_orders(self, since=None) -> list[RawRecord]: ...        # one record per order LINE
    def fetch_operations(self, since=None) -> list[RawRecord]: ...
    def fetch_machines(self, since=None) -> list[RawRecord]: ...
    def fetch_materials(self, since=None) -> list[RawRecord]: ...
    def fetch_tooling(self, since=None) -> list[RawRecord]: ...
    def fetch_calendars(self, since=None) -> list[RawRecord]: ...
    def fetch_production_status(self, since=None) -> list[RawRecord]: ...
    def capabilities(self) -> ConnectorCapabilities: ...   # which normalised fields it can fill
    def health(self) -> ConnectorHealth: ...
```

* `RawRecord(entity, external_id, payload: dict, updated_at, source)` is the ERP row exactly as
  exposed, with ERP-native keys. Nothing outside `app/integration` ever sees a raw record.
* `ENTITY_NAMES` fixes the fetch order (masters first: customer, material, machine, tooling,
  calendar, order, operation, production_status) so references resolve on upsert.
* Connectors are created by `ConnectorRegistry.create(name, clock, **options)`; options (URLs,
  credentials, schema names) come from settings/secrets, never from code. `PPSE_ERP_CONNECTOR`
  selects the connector (`mock` is the default).
* `MockERPConnector` wraps a `SyntheticDataset` and emits records that *look* like an ERP export
  (terse status codes, ISO strings, flags as "Y"/"N") so the normaliser is exercised for real.
  `set_failure()` lets tests simulate outages.

The code extends the contract's minimum with `fetch_operations`, `fetch_calendars` and `health()`;
these are additive and documented here as part of the protocol.

## 2. Sync modes

| Mode | Trigger | Behaviour | When to use |
|---|---|---|---|
| **Full** | `POST /sync/run {"mode":"full"}`, first run, nightly job | `since=None`; every entity fetched; upsert all; open orders absent from the ERP but present locally are closed, never deleted (`order_status = cancelled`, `attributes.missing_from_erp_since = <run started_at>`, counted as `orders_closed_missing` in the run summary and logged as `sync.orders_closed_missing`; `SyncOptions.prune_missing_orders` deletes instead; an empty order fetch closes nothing); reconciliation compares totals | Initial load, recovery after failed incrementals, ERPs without change tracking |
| **Incremental** | Worker every `PPSE_SYNC_INTERVAL_MINUTES` (default 15) | `since = started_at of the most recent completed run` (using the start rather than the end of that run is the built-in overlap); only changed rows; upsert; reconciliation on fetched-vs-upserted counts | Normal operation; requires reliable `updated_at` in the ERP (U-06) |
| **Webhook-ready** | ERP pushes an event (future, D-11) | The receiver converts the event payload into `RawRecord`s and feeds the same normalise → upsert path; then raises a replanning trigger | Near-real-time replanning without polling |

Watermarks (as implemented in `app/integration/sync_service.py`): each `sync_runs` row stores its
`started_at`; the next incremental run uses the `started_at` of the most recent *completed* run as
`since`, so records changed while that run was fetching are picked up again rather than lost. Failed
runs never advance the watermark; without any completed run an incremental request degrades to a
full fetch (recorded in the run details). Records are de-duplicated by `(entity, external_id)`.

`SyncService.run(mode)` = fetch (`snapshot_builder.fetch_all`) → normalise → validate → upsert →
`reconcile` → record. It is idempotent: re-running with the same data changes nothing but the
`sync_runs` history.

## 3. Normalisation approach

* **Declarative field maps** (`field_maps.py`): per entity an ordered list of `FieldMap(raw_key,
  domain_attr, parser, required, fallback)`. Adapting to a real ERP means editing these tables or
  passing alternative maps to `Normalizer(field_maps=...)`.
* **Code vocabularies** (`codes.py`): ERP status codes → domain enums (`ORDER_STATUS_CODES`,
  `OPERATION_STATUS_CODES`, `MACHINE_STATUS_CODES`, …), matched case-insensitively. Unknown codes
  produce a `NormalizationIssue` and the record keeps the enum default plus the raw text in
  `production_status`/`attributes`.
* **Identity**: ERP keys are kept verbatim (`external_ref`, `external_order_ref`); order lines are
  keyed `order_no + line_no` (`codes.compose_order_id`) when the ERP separates header and line.
* **Parsers** (`parsers.py`): ISO-8601 dates/datetimes (naive values are assumed UTC — a connector
  for an ERP that stores plant-local naive timestamps must attach the plant offset before handing
  over the record), numbers with thousands separators, percentages, Y/N-style flags, delimited
  lists/sets, time windows, shifts, part sizes, and enum parsers built from the code vocabularies.
* **Required vs optional**: a record missing a *required* field (e.g. order without `quantity`) is
  skipped and logged; missing optional fields stay `None` so the Data Quality Engine reports them
  and engines degrade gracefully instead of guessing.
* **Production status feed**: normalised into `ProductionStatusUpdate` objects that are applied to
  the matching `Operation` (status, completed quantity, actual start/end) rather than creating
  new entities.
* **Result**: `NormalizationResult(records, issues)`; counts and issue codes are stored on the
  `sync_runs` row and are visible in System Administration.

## 4. Required-vs-Available field matrix

The full field inventory with "Needed for" and "How to obtain" columns is in
`docs/AUDIT_REPORT.md` §7 (eight entity tables). **Every field is currently Unknown — not yet
discoverable**, because no ERP artefact existed. The machine-readable subset is `REQUIRED_FIELDS`
in `app/integration/capabilities.py`; `assess_capabilities(connector.capabilities())` produces the
report (Available / Missing per field, importance, engine impact, recommendation, coverage %,
`can_schedule`) and `CapabilityReport.to_markdown()` renders it for this section once a real
connector exists.

Summary of the *required* set (engine cannot run without them):

| Entity | Required fields | If missing |
|---|---|---|
| customer | customer_id, customer_name | Orders cannot be attributed; customer factors disabled |
| order | order_id, customer_id, part_id, quantity, requested_delivery_date, order_status | Order skipped (id/qty) or blocked by DQ (due date) |
| operation | operation_id, order_id, sequence, operation_type, machine_group, cycle_minutes_per_unit | Cannot compute run time or eligibility → order blocked by DQ |
| machine | machine_id, machine_group, process_type | No eligibility → nothing schedulable |
| material | material_id, available_quantity | Material readiness unknown → treated as UNKNOWN, warned |

### 4.1 Discovery procedure (what to ask the ERP team)

Run this in the first week of ERP access; record answers against U-xx in `docs/REQUIREMENTS.md`.

1. **Access**: read-only DB account or API credentials to a non-production instance; network path;
   contact owner (D-01, D-09).
2. **Technology**: language/framework, database engine and version, hosting, release cadence (U-01,
   U-05).
3. **Interfaces**: list of REST/SOAP/GraphQL endpoints or views intended for integration; existing
   exports/ETL jobs; webhook/queue capability; rate limits; pagination (U-09…U-13).
4. **Tables/APIs to look for** (typical names in home-grown ERPs — verify, do not assume):
   * Orders: `sales_order`, `so_header`, `so_line`, `production_order`, `work_order`, `job`,
     `job_card` — need header/line keys, customer FK, part FK, quantities, dates, status, value.
   * Routing/operations: `routing`, `route_step`, `wo_operation`, `job_op` — sequence, work
     centre, setup/run standards, status, actuals.
   * Machines: `work_center`, `machine`, `resource`, `equipment` — group, technology, status,
     capacity, shift model.
   * Downtime/maintenance: `machine_downtime`, `maintenance_plan`, `pm_schedule`.
   * Materials/inventory: `item`, `material`, `stock`, `inventory_balance`, `reservation`,
     `purchase_order` (incoming).
   * Tooling/fixtures: `tool`, `fixture`, `tool_crib`.
   * Customers: `customer`, `account`, `customer_contract` (SLA, tier).
   * Calendars: `shift`, `shift_pattern`, `holiday`, `plant_calendar`.
   * Quality: `inspection`, `ncr`, `rework`, `scrap`.
   * Change tracking: `updated_at`/`modified_on` columns, audit tables, triggers, CDC.
5. **Semantics**: status code vocabularies and lifecycle (U-14); what "promised" vs "requested"
   date mean; whether a line can be partially shipped; quantity units.
6. **Data reliability**: which of cycle time, setup time, margin, penalty, SLA, dependencies are
   maintained and trusted (U-15…U-17).
7. **Calendars**: shift model, holidays, plant timezone (U-18).
8. **Writeback candidates** (for later): which fields the ERP would accept (sequence number,
   planned start/end, assigned work centre, priority code), transaction semantics, idempotency,
   test instance, rollback (U-19, D-10).
9. **Samples**: one month of exports for every entity above (D-03).

Output of discovery: filled `ConnectorCapabilities`, updated `field_maps.py`/`codes.py`, refreshed
AUDIT_REPORT §7 status column, and a decision REST vs direct-DB connector (§8).

## 5. Reconciliation

`reconcile(connector_counts, stored_counts, thresholds)` after every sync compares, per entity, the
number of records the connector reported with the number now stored (open/active), and classifies
each `EntityDelta` as `ok` / `warning` / `mismatch` against `ReconciliationThresholds`
(defaults: `warning_pct=1.0`, `mismatch_pct=5.0`, `absolute_tolerance=2` — integration tuning, not a
business rule). The `ReconciliationReport` (worst status + summary) is stored on the `sync_runs`
row; `mismatch` raises a `DATA_QUALITY` alert and, for orders, blocks automatic replanning until a
full sync succeeds.
Additional checks on real ERPs: sum of open quantities, count of orders due this week, and
spot-check of N random orders field-by-field (planned as a nightly job).

## 6. Error handling and retry policy

| Failure | Handling |
|---|---|
| Connector unreachable / timeout / 5xx | `RetryPolicy` in `app/integration/retry.py`: 3 attempts, exponential backoff (base 0.5 s, factor 2, capped at 30 s; sleep is injectable for tests) around each connector fetch; then `IntegrationError`, the run is marked FAILED with the error recorded, partial upserts are rolled back, previous data stays, and the next scheduled run retries |
| Authentication failure (401/403) | No retry; FAILED with code `auth`; alert with recommended action "rotate credentials"; connector health reports degraded |
| Partial entity failure (e.g. tooling endpoint down) | Today the whole run fails atomically (no half-written dataset). A PARTIAL status that keeps the other entities is planned once a real connector exists |
| Malformed record | Skipped with `NormalizationIssue`; run continues; issue counts stored |
| Reference to unknown master (order → missing customer) | Upserted with the reference kept; DQ `UNKNOWN_REFERENCE` warning; resolved on next master sync |
| Reconciliation MISMATCH | Run SUCCEEDED with warning; alert; auto-replan paused for orders until a full sync is OK |
| Watermark gap (worker down for a long time) | Planned: escalate to a full sync when the gap exceeds `2 × interval`; today the incremental run simply fetches everything changed since the last completed run |
| ERP rate limit (429) | Planned for the REST connector: honour `Retry-After`; halve the page size for the run |

All failures are logged with `sync_run_id`, entity, attempt and (sanitised) error text; never with
credentials or full payloads at INFO level.

## 7. Writeback modes ladder

`WritebackMode` (`app/domain/enums.py`) and `WritebackGateway.publish(schedule, mode, approved_by)
-> WritebackReceipt` (`app/integration/writeback.py`). `check_publish_preconditions` enforces the
mode rules shared by every gateway. The mode is deployment configuration (`PPSE_WRITEBACK_MODE`),
changed only by an admin and recorded in the audit log.

| Mode | What happens on publish | Gate to enter this mode |
|---|---|---|
| **READ_ONLY** (default, MVP) | Version becomes PUBLISHED internally; receipt `published=False`, "nothing sent to ERP". Operators follow the PPSE screens. | — |
| **APPROVAL** | Requires `approved_by`; the approved schedule is sent to the ERP as *recommendation* fields (e.g. suggested sequence, suggested machine) that the ERP displays but does not enforce. | ≥ 4 weeks READ_ONLY parallel run on real data; planners accept ≥ 80 % of top-50 recommendations without override (measured from audit log); DQ blocking issues < 2 % of open orders; reconciliation OK for 30 consecutive days; ERP test instance validated with the real gateway; rollback procedure documented and rehearsed |
| **WRITEBACK** | The approved schedule updates the ERP's executable fields (sequence, planned start/end, assigned work centre); receipts store ERP ids; a post-write read-back verifies the state. | ≥ 4 weeks APPROVAL mode with no receipt errors; measured OTD not worse than pre-PPSE baseline; ERP owner sign-off on field semantics and idempotency; alerting on write failures live; kill switch (`READ_ONLY`) tested |
| **CONTROLLED_AUTO** | Replans that pass the configured `AutoPublishRule` (a callable `ScheduleResult -> (ok, reason)` assembled from configuration: quality ≥ threshold, changed entries ≤ N, no newly late strategic orders, inside business hours, no `mismatch` reconciliation) are published without a human click; everything else waits for approval. `check_publish_preconditions` refuses this mode when no rule is configured. | ≥ 8 weeks WRITEBACK with human approval; replan-decision history shows the rules would have approved ≥ 95 % of what humans approved and 0 % of what they rejected; on-call ownership defined; automatic fallback to WRITEBACK on any receipt error |

Rules that never change: publish is idempotent (same version → same ERP state), every publish
writes an audit row and a receipt, a failed writeback never rolls back the internal version
silently, and stepping *down* the ladder is always allowed immediately.

## 8. Implementing a real connector

Both variants implement the same protocol and reuse the normaliser; choose by what the ERP offers
(U-09). Estimated effort after discovery: 1–2 weeks including tests.

### 8.1 Common steps

1. Complete discovery (§4.1); fill `ConnectorCapabilities` honestly — a field the ERP has but that
   is unreliable is *Missing*.
2. Create `app/integration/<name>_connector.py` with a class implementing `ERPConnector`; constructor
   takes `clock` and connection options; no module-level state.
3. Add field maps for the ERP's column/attribute names (new tables in `field_maps.py` or a module
   `field_maps_<name>.py` passed to `Normalizer`); extend `codes.py` maps with the ERP vocabulary.
4. Register a factory in `ConnectorRegistry.default()` (`registry.register("<name>", factory)`);
   add settings fields for its options (`PPSE_ERP_<NAME>_URL`, credentials via secrets).
5. Implement `health()` (cheap call: `SELECT 1` or `GET /ping`) and `capabilities()`.
6. Tests: unit tests with recorded fixtures (sanitised real exports) for every entity; a contract
   test that runs `assess_capabilities` and asserts `can_schedule`; an integration test behind
   `PPSE_ERP_TEST_URL` that fetches one page from the test instance.
7. Run a full sync into a scratch database; review the reconciliation report and DQ dashboard with
   the ERP owner; fix maps; repeat until blocking issues are understood.
8. Switch `PPSE_ERP_CONNECTOR` in the target environment; keep `mock` for CI.

### 8.2 REST variant

* HTTP client: `httpx.Client` with timeouts (connect 5 s, read 30 s), connection pooling, retry
  wrapper implementing §6; auth per U-10 (API key header, OAuth2 client-credentials with token
  cache, or Basic) injected as options.
* Pagination: follow the ERP's convention (page/size, cursor, or `Link` headers); stream pages into
  `RawRecord`s; stop when a page is empty; respect `since` through the API's modified-since filter
  if present, otherwise filter client-side and prefer full sync.
* Map each endpoint to one entity; if the ERP nests operations inside orders, split them in the
  connector (orders → `order` records, embedded steps → `operation` records with `order_id`).
* `updated_at` from the payload's modification field; if none, use the fetch time and declare
  incremental sync unsupported (`capabilities`).
* Do not transform values in the connector beyond splitting/flattening; the normaliser owns parsing.

### 8.3 Direct-DB variant

* SQLAlchemy `Engine` to the ERP database with a **read-only** account (verify with an attempted
  write in a test transaction that must fail); `pool_pre_ping`; statement timeout set on the
  connection.
* One SQL statement per entity, selecting only the needed columns, with `WHERE modified_on >= :since`
  when change tracking exists; order by primary key and fetch in server-side cursors/batches of
  5,000 rows to bound memory.
* Never join across the ERP schema in ways that encode business rules; keep queries in a
  `queries_<name>.py` module as constants so the ERP owner can review them.
* Composite keys (order header + line) are composed into `external_id` in the query.
* Schedule syncs off-peak for the ERP if it has no replica; keep the incremental window small.
* Treat schema changes as breaking: the contract test asserts the expected columns exist at startup
  and the connector reports `health()` degraded otherwise.

### 8.4 Webhook receiver (optional, later)

An API route (admin-scoped, HMAC-verified) accepts the ERP's event payload, converts it to
`RawRecord`s of the right entity, runs the normalise → upsert path for those records, and raises the
matching `ReplanTriggerType`. Polling remains as the safety net.
