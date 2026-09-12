# Audit Report — Repository and ERP/MES Architecture

**Deliverable:** spec "FIRST TASK" (repository and ERP/MES audit before implementation)
**Repository:** `bchoksi87/Athena_order-Priority`
**Audit date:** 2026-09-11
**Status of this document:** findings are final for the current state of the repository; sections 1–7
must be re-issued once the ERP team grants access (see the discovery procedure in
`docs/ERP_INTEGRATION.md`).

The spec asks for seventeen numbered findings. They follow in that order. Sections 1–6 are short
because there was nothing to discover; section 7 is long because the absence of discoverable data
is the single most important fact this audit establishes.

---

## 1. Current architecture discovered

**Finding: none.** The GitHub repository was completely empty at the start of work — zero commits,
no source tree, no README, no CI configuration, no infrastructure files. There was no existing
application whose architecture could be inspected, and no documentation of the ERP/MES the spec
describes as "internally developed".

| Artefact looked for (spec Phase 1) | Found |
|---|---|
| ERP/MES source code | No |
| API documentation (OpenAPI, WSDL, Postman, wiki) | No |
| Database schema (DDL, ORM models, ER diagrams) | No |
| Configuration files (connection strings, env files) | No |
| Sample data / exports (CSV, JSON, DB dumps) | No |
| Integration documentation (webhooks, ETL jobs, message queues) | No |
| Deployment descriptors (Dockerfiles, compose, k8s, CI) | No |

**Implication.** Every statement about the ERP/MES in this project is an *assumption* until real
access exists. The system has therefore been designed so that the ERP is an opaque external system
behind a single abstraction (`ERPConnector`, `backend/app/integration/connector.py`). Until real
access exists, the application runs end-to-end against a `MockERPConnector` fed by a deterministic
synthetic data generator (`backend/synthetic/`), in **READ-ONLY** mode (`PPSE_WRITEBACK_MODE=read_only`
is the default and the only mode that ships enabled).

The development environment offers Python 3.11, Node 22, PostgreSQL 16, Redis 7 and Chromium; no
Docker daemon is available, so container images are built but not executed here.

## 2. Existing ERP technology stack

**Unknown — not discoverable.** No code, binaries, dependency manifests, HTTP endpoints or vendor
references were present. The spec says the ERP is "internally developed" and "also functions as an
MES"; nothing beyond that sentence is known. Language, framework, runtime, hosting model,
release cadence and ownership are all open questions recorded as U-01…U-04 in `docs/REQUIREMENTS.md`.

**Implication.** The spec's Phase 24 rule ("if the ERP is Python use FastAPI/Django, if .NET use
ASP.NET Core…") cannot be applied on evidence. The stack was chosen on the spec's other criterion
— Python is preferred for the optimisation ecosystem — and on the fact that the integration layer
is language-agnostic (REST or direct DB), so the ERP's language does not constrain ours.

## 3. Existing database technology

**Unknown — not discoverable.** No DDL, connection string, driver dependency or dump was present.
Engine (PostgreSQL / MySQL / SQL Server / Oracle / other), version, schema conventions, presence of
timestamps for incremental sync, and whether read replicas exist are all unknown (U-05…U-08).

**Implication.** The new application has its own PostgreSQL 16 database and never shares a schema
with the ERP. If direct-DB integration is later chosen, the connector reads the ERP through a
read-only account and the ERP's tables are mapped in `app/integration/field_maps.py` — nothing else
in the code base sees them.

## 4. Existing APIs/integration mechanisms

**Unknown — not discoverable.** No REST/SOAP/GraphQL definitions, no webhook or event documentation,
no message broker configuration, no export/ETL jobs and no authentication scheme (API keys, OAuth,
Basic, session cookies, mTLS) were discoverable (U-09…U-13).

**Implication.** The connector protocol supports the three integration styles the spec asks for
(REST, direct DB, events) without committing to any of them: it is a pull-based, `since`-aware
interface (`fetch_*(since)`), which works over REST or SQL and is trivially fed by a webhook
receiver later. Authentication is a connector-construction concern (`ConnectorRegistry.create(name,
clock, **options)`), so credentials for a real ERP are injected by configuration, never coded.

## 5. Relevant production tables/models

**Unknown — not discoverable.** None of the tables the spec lists in Phase 1 (production orders,
customers, parts, BOM, routing, machines/work centres, materials, tooling, production status, job
cards, operation-level data, downtime, maintenance, operators, quality/rejection, rework, scrap,
setup times, cycle times) could be identified, because there was no schema.

**Implication.** The project defines its own **Normalized Manufacturing Data Model** (Phase 3) in
`backend/app/domain/models.py` — `Customer`, `Order` (one row per order line), `Operation`,
`Machine`, `Material`, `Tooling`, `CalendarSpec`/`Shift`, plus planner overlays `ScheduleLock`,
`PriorityOverride`, `Expedite`, `CustomerRule`. It is the *target* of ERP mapping, not a copy of any
ERP table. The mapping from ERP-native keys to this model is a data table (`field_maps.py`) so that
adapting to the real schema is a configuration change.

## 6. Available production data

**None.** No real production data of any kind (historical or current) is available. Volumes stated
in the spec (≈800 customers/month, 5,000–20,000 orders/month, multiple lines per order, CNC and
industrial 3D printing) are taken as design targets, not observations.

**Implication.** Per spec Phase 31 a synthetic dataset stands in. `synthetic.SyntheticDataGenerator`
is deterministic (seeded) and produces three scales: `small` (80 customers, 300 orders — unit tests),
`medium` (800 customers, 5,000 orders — dev default) and `large` (800 customers, 20,000 orders —
stress tests). It generates CNC and additive routings, multiple machines per group, materials with
shortages, tooling, shifts, holidays, planned/unplanned downtime, setup and cycle times, quality
failures, and a configurable ratio of deliberate data-quality defects so that the Data Quality Engine
has something to find. Every conclusion drawn from synthetic data (KPI levels, quality scores,
run times) must be re-validated on real data before the writeback ladder advances.

## 7. Missing data required for optimization

Because nothing was discoverable, **every** field the engines use has status *Unknown — not yet
discoverable against the ERP*. The table below is the complete inventory, grouped by entity, built
from the spec Phase 3 field lists and the domain dataclasses. "Needed for" names the consuming engine
(priority factor keys are those of `PriorityProfile`). "How to obtain" is the recommendation the spec
asks for in the case a field turns out to be missing.

The machine-readable version of the *required/recommended/optional* subset lives in
`app/integration/capabilities.py` (`REQUIRED_FIELDS`) and is rendered by
`assess_capabilities(connector.capabilities())` once a real connector declares what it can supply.

Importance legend: **R** required (engine cannot run correctly without it), **Rec** recommended
(engine degrades gracefully, Data Quality Engine warns), **Opt** optional (feature is skipped).

### 7.1 Customer

| Field | Needed for | Status against ERP | How to obtain (recommendation) |
|---|---|---|---|
| customer_id (R) | All engines: order→customer link | Unknown — not yet discoverable | Customer master primary key; expose verbatim as `external_ref` |
| customer_name (R) | Dashboard, explanations | Unknown — not yet discoverable | Customer master |
| customer_category | `customer_importance` factor | Unknown — not yet discoverable | Customer master classification; else derive from tier |
| customer_tier (Rec) | `customer_importance` (tier_scores) | Unknown — not yet discoverable | ERP tier/segment field; if absent, derive from trailing revenue percentile, maintain in `customer_rules` |
| customer_priority (1–5) | `customer_importance`, `erp_priority` adjustment | Unknown — not yet discoverable | ERP customer priority code; else default 3 |
| strategic_customer_flag | `customer_importance` bonus, delay-penalty multiplier | Unknown — not yet discoverable | ERP flag; else `CustomerRule.tier_override=strategic` maintained by sales |
| annual_revenue | Reporting | Unknown — not yet discoverable | Finance/CRM export |
| customer_revenue (Rec) | `customer_importance` (revenue percentile) | Unknown — not yet discoverable | Sum invoiced sales trailing 12 months from ERP sales ledger |
| customer_profitability (Opt) | `customer_importance` (profitability percentile) | Unknown — not yet discoverable | Finance contribution-margin report; leave None if unreliable |
| customer_service_level | OTD analytics, SLA default | Unknown — not yet discoverable | Contract data; else None |
| sla_hours (Rec) | `sla_risk` factor | Unknown — not yet discoverable | Contract terms; if not in ERP maintain in `customer_rules` via UI |
| escalation_level (Opt) | `customer_importance`, `delay_penalty` | Unknown — not yet discoverable | CRM escalation/complaint field; else manual override |
| historical_on_time_delivery (Opt) | Analytics, risk | Unknown — not yet discoverable | Compute from ERP shipment history (shipped_date vs promised_date) |
| payment_risk | Reporting, future factor | Unknown — not yet discoverable | Finance credit rating; else UNKNOWN |
| preferred_delivery_expectation | Reporting | Unknown — not yet discoverable | Customer master free text |
| account_manager | Dashboard, alerts routing | Unknown — not yet discoverable | CRM owner field |
| active | Filtering | Unknown — not yet discoverable | Customer master status |

### 7.2 Order (one row per order line)

| Field | Needed for | Status against ERP | How to obtain (recommendation) |
|---|---|---|---|
| order_id (R) | Everything | Unknown — not yet discoverable | Compose `order_no + line_no` (`codes.compose_order_id`) if ERP keys header and line separately |
| order_line_id | Traceability | Unknown — not yet discoverable | Line number from order-line table |
| external_order_ref | Writeback correlation | Unknown — not yet discoverable | ERP order number verbatim |
| customer_id (R) | Customer factors | Unknown — not yet discoverable | Order header FK |
| part_id (R) | Routing, batching (part family) | Unknown — not yet discoverable | Order line item FK |
| part_name / part_family | Batching affinity, UI | Unknown — not yet discoverable | Part master; family may need to be introduced (group by drawing/technology) |
| order_date, received_date | Aging/fairness adjustment | Unknown — not yet discoverable | Order header timestamps |
| requested_delivery_date (R) | `due_date_urgency`, lateness, alerts | Unknown — not yet discoverable | Order line; if only header-level date exists, apply to all lines and flag |
| promised_delivery_date (Rec) | Effective due date (promised > requested) | Unknown — not yet discoverable | Order acknowledgement date |
| revised_delivery_date (Rec) | Effective due date (highest precedence) | Unknown — not yet discoverable | Change-order history; else None |
| quantity (R) | Run-time computation, capacity | Unknown — not yet discoverable | Order line quantity in production units |
| completed_quantity (Rec) | Pending quantity, progress | Unknown — not yet discoverable | Production reporting / job card confirmations |
| cancelled_quantity | Pending quantity | Unknown — not yet discoverable | Order-line change history |
| order_status (R) | Open/closed filtering, schedulable set | Unknown — not yet discoverable | ERP status code; map codes in `codes.ORDER_STATUS_CODES` |
| erp_priority (priority / current_priority) | `erp_priority_points` adjustment | Unknown — not yet discoverable | ERP priority code 1–5; else None |
| production_status (raw) | Data quality, UI | Unknown — not yet discoverable | Keep raw ERP text verbatim |
| material_status (Rec) | `production_readiness`, readiness state | Unknown — not yet discoverable | Derive from material allocation/reservation in ERP; else compute from Material stock |
| quality_status (Rec) | QUALITY_HOLD readiness, rework alerts | Unknown — not yet discoverable | QC module inspection result |
| shipping_status | Closed-order detection | Unknown — not yet discoverable | Dispatch module |
| order_value (Rec) | `order_value` factor, revenue at risk | Unknown — not yet discoverable | Line net value from sales order |
| estimated_cost | Margin | Unknown — not yet discoverable | Costing module (standard cost × qty) |
| estimated_margin (Rec) | `margin` factor, margin at risk | Unknown — not yet discoverable | order_value − estimated_cost; only if costing is reliable |
| actual_margin | Analytics | Unknown — not yet discoverable | Post-completion costing |
| process_type (R) | Machine eligibility, capacity by process | Unknown — not yet discoverable | Routing header technology (CNC/AM/...) |
| manufacturing_route | Routing validation | Unknown — not yet discoverable | Ordered list of routing operation types |
| machine_group | Eligibility fallback when operations lack group | Unknown — not yet discoverable | Routing/work-centre group |
| required_machine_id | Hard pin to a machine | Unknown — not yet discoverable | Routing "fixed work centre" field; else None |
| required_material_id | Material readiness (order-level fallback) | Unknown — not yet discoverable | BOM main material for the part |
| tooling_requirement | Tooling readiness | Unknown — not yet discoverable | Routing tool list / fixture list |
| estimated_setup_minutes | Setup fallback when operation lacks it | Unknown — not yet discoverable | Routing standard setup |
| estimated_cycle_minutes_per_unit | Cycle fallback | Unknown — not yet discoverable | Routing standard time |
| estimated_total_production_minutes | Capacity, remaining work | Unknown — not yet discoverable | Sum of routing times × qty; compute if absent |
| customer_priority / technical_priority / commercial_priority | Adjustments, reporting | Unknown — not yet discoverable | ERP priority sub-codes if they exist; else None |
| lateness_penalty_per_day (Opt) | `delay_penalty` factor | Unknown — not yet discoverable | Contract clause; else default ratio of order value from profile |
| sla_hours | `sla_risk` (order-level SLA) | Unknown — not yet discoverable | Order-level SLA if quoted; else customer SLA |
| special_instructions | UI | Unknown — not yet discoverable | Order notes |
| drawing_approved (Opt) | WAITING_APPROVAL readiness | Unknown — not yet discoverable | Engineering release flag; else assume approved and flag assumption |
| on_hold / hold_reason (Opt) | ON_HOLD readiness | Unknown — not yet discoverable | ERP hold flag; PPSE also maintains its own holds |
| depends_on_order_ids (Opt) | `downstream_impact` factor, dependency scheduling | Unknown — not yet discoverable | Assembly BOM links between orders; else None (feature disabled) |
| surface_finish / technology | Batching dimensions | Unknown — not yet discoverable | Part/routing attributes |

### 7.3 Operation (routing step)

| Field | Needed for | Status against ERP | How to obtain (recommendation) |
|---|---|---|---|
| operation_id (R) | Everything | Unknown — not yet discoverable | Routing-step key (order_id + sequence if no key) |
| order_id (R) | Linking | Unknown — not yet discoverable | FK to order line |
| sequence (R) | Operation dependencies | Unknown — not yet discoverable | Routing step number |
| operation_type (R) | Machine eligibility (process) | Unknown — not yet discoverable | Routing operation/work-centre type; map to `ProcessType` |
| machine_group (R) | Eligibility | Unknown — not yet discoverable | Work-centre group of the step |
| machine_id | ERP-preferred machine (soft constraint) / pin | Unknown — not yet discoverable | Routing assigned work centre; else None |
| eligible_machine_ids | Explicit eligibility | Unknown — not yet discoverable | Alternative work-centre list; else derived from group + process |
| setup_minutes (Rec) | Setup estimation, sequencing | Unknown — not yet discoverable | Routing setup standard; else `SetupRules.default_setup_minutes` (flagged) |
| cycle_minutes_per_unit (R) | Run time, completion, lateness | Unknown — not yet discoverable | Routing run standard; if absent derive from historical actuals (actual_end − actual_start)/qty |
| machine_cycle_minutes (Opt) | Machine-specific cycle times | Unknown — not yet discoverable | Alternative-work-centre times; else single value |
| quantity / completed_quantity | Pending work | Unknown — not yet discoverable | Job card confirmations |
| operation_status (Rec) | Progress, next-operation detection | Unknown — not yet discoverable | Job card status; map in `codes.OPERATION_STATUS_CODES` |
| prerequisite_operation_id | Dependencies beyond linear sequence | Unknown — not yet discoverable | Routing predecessor; else derive from sequence |
| material_id / material_quantity_per_unit (Rec) | Material readiness per step | Unknown — not yet discoverable | BOM component assigned to step |
| tooling_ids (Opt) | Tooling constraints | Unknown — not yet discoverable | Routing tool list |
| operator_requirement | Future operator constraint | Unknown — not yet discoverable | Skill/qualification code |
| quality_requirement | Inspection steps | Unknown — not yet discoverable | Inspection plan |
| setup_family (Opt) | Setup changeover, batching | Unknown — not yet discoverable | Fixture/program family; introduce if absent |
| estimated_start / estimated_end | Comparison with ERP plan | Unknown — not yet discoverable | ERP planned dates |
| actual_start / actual_end | Behind-schedule alerts, cycle-time learning | Unknown — not yet discoverable | Job card time bookings |

### 7.4 Machine / work centre

| Field | Needed for | Status against ERP | How to obtain (recommendation) |
|---|---|---|---|
| machine_id (R) | Everything | Unknown — not yet discoverable | Work-centre key |
| machine_name, machine_type | UI | Unknown — not yet discoverable | Work-centre master |
| process_type (R) | Eligibility | Unknown — not yet discoverable | Work-centre technology |
| machine_group (R) | Eligibility, capacity | Unknown — not yet discoverable | Work-centre group |
| location | UI | Unknown — not yet discoverable | Plant/cell |
| status (Rec) | MACHINE_UNAVAILABLE readiness | Unknown — not yet discoverable | Live MES status; else AVAILABLE |
| availability / available_from | Earliest start | Unknown — not yet discoverable | MES current job end |
| capacity_hours_per_day, working_hours, shifts (Rec) | Calendar | Unknown — not yet discoverable | Shift plan; else plant default calendar maintained in PPSE |
| calendar_id (Rec) | Calendar resolution | Unknown — not yet discoverable | Shift-model assignment |
| efficiency (Opt) | Run-time multiplier | Unknown — not yet discoverable | OEE/performance factor; else 1.0 |
| utilization | Analytics | Unknown — not yet discoverable | MES trailing utilization |
| maintenance_windows (Rec) | Hard constraint, calendar | Unknown — not yet discoverable | CMMS/maintenance plan |
| planned_downtime / unplanned_downtime | Calendar | Unknown — not yet discoverable | MES downtime log |
| compatible_materials (Opt) | Material compatibility constraint | Unknown — not yet discoverable | Work-centre capability list; else all |
| compatible_processes | Multi-process machines | Unknown — not yet discoverable | Capability list |
| max_part_size_mm (compatible_part_sizes) | Part-size constraint | Unknown — not yet discoverable | Machine envelope spec |
| tooling_configuration | Tooling constraint, setup | Unknown — not yet discoverable | Current mounted tools (MES) |
| setup_requirements | Setup rules | Unknown — not yet discoverable | Machine notes |
| current_material_id / current_setup_family (Opt) | Setup changeover at horizon start | Unknown — not yet discoverable | MES current job attributes |
| preferred_rank | Preferred-machine soft constraint | Unknown — not yet discoverable | Planner-maintained in PPSE if absent |

### 7.5 Material

| Field | Needed for | Status against ERP | How to obtain (recommendation) |
|---|---|---|---|
| material_id (R), material_name | Readiness | Unknown — not yet discoverable | Material master |
| material_type, grade, supplier | UI, batching | Unknown — not yet discoverable | Material master |
| unit | Quantity semantics | Unknown — not yet discoverable | Material master UoM |
| available_quantity (R) | WAITING_MATERIAL readiness | Unknown — not yet discoverable | Inventory on-hand by storage location |
| reserved_quantity (Rec) | Free stock | Unknown — not yet discoverable | Allocations/reservations table |
| incoming_quantity (Opt), expected_receipt_date (Rec) | Blocker resolution time, material_delay scenario | Unknown — not yet discoverable | Open purchase orders |
| minimum_stock | Shortage alerts | Unknown — not yet discoverable | Material master |
| compatible_machine_ids | Compatibility constraint | Unknown — not yet discoverable | Capability matrix; else all |

### 7.6 Tooling

| Field | Needed for | Status against ERP | How to obtain (recommendation) |
|---|---|---|---|
| tooling_id (Rec), tooling_name | WAITING_TOOLING readiness | Unknown — not yet discoverable | Tool master; if no tool module exists, feature is disabled |
| available, available_from (Rec) | Readiness | Unknown — not yet discoverable | Tool crib status |
| compatible_machine_ids (Opt) | Tooling compatibility constraint | Unknown — not yet discoverable | Tool/machine matrix |
| setup_minutes | Setup estimation | Unknown — not yet discoverable | Tool master |
| expected_life, current_usage, maintenance_status | Usability | Unknown — not yet discoverable | Tool management module |

### 7.7 Calendar / shifts / holidays

| Field | Needed for | Status against ERP | How to obtain (recommendation) |
|---|---|---|---|
| calendar_id (Rec), name, timezone | Calendar | Unknown — not yet discoverable | Shift-model master; else PPSE default 24×7 (flagged) |
| shifts (name, start, end, weekdays) (Rec) | Working time | Unknown — not yet discoverable | Shift plan; else maintained in PPSE Scheduling Configuration |
| holidays (Opt) | Working time | Unknown — not yet discoverable | Plant holiday calendar |
| overtime_windows, extra_working_days | Overtime rules, extra_shift scenario | Unknown — not yet discoverable | Maintained in PPSE |

### 7.8 Production status feed (near-real-time)

| Field | Needed for | Status against ERP | How to obtain (recommendation) |
|---|---|---|---|
| operation_id, order_id | Linking | Unknown — not yet discoverable | Job card key |
| operation_status, completed_quantity | Progress, replanning triggers | Unknown — not yet discoverable | Job card confirmations |
| actual_start / actual_end | Behind-schedule detection | Unknown — not yet discoverable | Time bookings |
| reported_at (updated_at) | Incremental sync watermark | Unknown — not yet discoverable | Row modification timestamp — **critical**: without it only full sync is possible |

### 7.9 Fields the spec lists that are deliberately not modelled yet

Operators (Phase 6 input, Phase 12 bottleneck), scrap and rejection quantities, BOM explosion,
job cards as separate entities, and energy cost per machine (present only as a config map). They are
tracked as future requirements F-xx; the domain model's `attributes: dict` on every entity carries
such data verbatim until first-class fields are warranted.

## 8. Proposed system architecture

Modular monolith (one backend process, strictly layered) plus a separate frontend, exactly as
`docs/DESIGN_CONTRACT.md` fixes it and as `docs/ARCHITECTURE.md` explains it:

```
ERP/MES (unknown) --read-only--> Integration layer (connector, normalizer, sync, reconciliation)
        --> PostgreSQL (normalized model + overlays + results + audit)
        --> PlanningSnapshot (in-memory, immutable input to every engine)
        --> Priority engine -> Constraint engine -> Calendar -> Scheduler (V1 rule-based, V2 CP-SAT plug-in)
        --> Simulation / Analytics / Data quality / Replanning engines
        --> Application services (use cases, audit, versioning)
        --> FastAPI /api/v1 (JWT, RBAC) --> React control-tower UI
        --> Background workers (sync, replan, alerts)
        --> Writeback gateway (READ_ONLY now; APPROVAL -> WRITEBACK -> CONTROLLED_AUTO later)
```

Boundaries enforced by rule: engines (`app/engines/**`) import nothing from `app/db/**` or
`app/integration/**`; they take a `PlanningSnapshot` and versioned Pydantic config and return
dataclasses from `app/domain/results.py`. This is what makes every run reproducible, simulatable
and unit-testable without a database.

## 9. Proposed database architecture

Own PostgreSQL 16 database (`ppse`), schema managed by Alembic, SQLAlchemy 2 typed models with
portable column types only (String/Integer/Float/Boolean/DateTime(tz)/JSON/Text) so that unit tests
run on in-memory SQLite. Table families (all with `created_at`/`updated_at`):

| Family | Tables | Notes |
|---|---|---|
| Normalized ERP mirror | customers, orders, operations, machines, machine_downtime, calendar_specs, materials, tooling | Upserted by sync; `external_*` refs kept verbatim |
| Planner overlays | customer_rules, schedule_locks, priority_overrides, expedites | Never overwritten by sync |
| Versioned configuration | system_configs, priority_profiles, scheduling_configs | JSON body + monotonic version |
| Engine outputs | priority_results, optimization_runs, schedule_versions, schedule_entries, data_quality_issues, alerts | Every run traceable to profile/config/snapshot versions |
| Audit and reproducibility | audit_log, input_snapshots (compressed JSON), sync_runs | Answers "why was this scheduled at 14:30 yesterday?" |
| Security | users | bcrypt hashes, role |

Required indexes: order status, due date, customer_id, machine_id, process_type, priority score,
production status, schedule start/end. Analytics initially read from the operational tables through
the analytics engine; materialised views are a later optimisation once real volumes are known.
Full detail is in `docs/DATA_MODEL.md`.

## 10. Priority engine architecture

Configurable weighted additive scoring (`app/engines/priority`). Each factor is an independent class
implementing `PriorityFactor` (`key`, `name`, `kind ∈ {bonus, penalty}`, `score(order, ctx) ->
FactorScore`), registered by canonical key: `due_date_urgency, sla_risk, customer_importance,
order_value, margin, delay_penalty, production_readiness, machine_availability, setup_efficiency,
batching_affinity, downstream_impact`. Weights come from a versioned `PriorityProfile` and are
normalised so enabled bonus weights sum to 1.0; raw factor scores are in [0, 100].

```
base  = Σ w_i · raw_i (bonus)  −  Σ w_i · raw_i (penalty)
score = clamp(base + aging + fairness + expedite + override + customer_rule + erp_priority, 0, 100)
```

A `PriorityContext` precomputes snapshot-wide statistics (percentiles, machine next-free times,
readiness/blockers, projected completion) so each factor is O(1) per order and the whole evaluation
is O(orders + operations + machines). The explanation ("+25 — Due in 18 hours …") is rendered from
the same `FactorScore`/`PriorityAdjustment` list that produced the number — never written separately
(spec Phase 34). Output: `PriorityResult` with score, breakdown, readiness, blockers, risk level and
rank; persisted in `priority_results`. Detail: `docs/PRIORITY_ENGINE.md`.

## 11. Scheduling engine architecture

Three cooperating components:

* **Constraint engine** (`app/engines/constraints`): hard constraints return `Violation`s (process
  capability, explicit eligibility, machine group, material and tooling compatibility, machine
  operable, part size, quantity restriction, locked machine assignment); soft constraints return
  `Penalty` costs in minute-equivalents (preferred machine, setup changeover, utilisation balance,
  energy cost, customer sequence, batch preference). It also derives each order's `ReadinessState`
  and blockers.
* **Calendar** (`app/engines/calendar`): `MachineCalendar` turns shifts, holidays, overtime windows
  and downtime into working-time arithmetic (`add_work_minutes`, `working_minutes_between`).
* **Scheduler** (`app/engines/scheduling`): `Scheduler` protocol (`name`, `version`, `schedule(...)
  -> ScheduleResult`). **V1** is the rule-based list scheduler the spec prescribes: drop blocked
  orders → sort by priority → for each next operation compute eligible machines → pick the machine
  minimising expected completion + soft cost → respect frozen/locked entries and operation
  dependencies → estimate setup from family/material → opportunistic batching within configured
  delay and priority-gap limits → compute expected completion and lateness → placement reason per
  entry → schedule quality score. **V2** (`cpsat.py`, optional OR-Tools import) plugs into the same
  registry and is judged against V1 by the same quality score on the same snapshots.

Outputs match the spec Phase 6 list exactly (`ScheduleEntry`: machine, order, operation, setup
start, start, end, sequence, quantity, expected completion, expected lateness, priority score,
placement reason). Detail: `docs/SCHEDULING_ENGINE.md`.

## 12. Recommended technology stack

| Layer | Choice | Reason (given the audit result) |
|---|---|---|
| Backend | Python 3.11, FastAPI, Uvicorn | Optimisation ecosystem (OR-Tools, NumPy); OpenAPI for free; async I/O for sync jobs |
| Data | PostgreSQL 16, SQLAlchemy 2, Alembic | Spec preference; portable types keep SQLite for unit tests |
| Validation/config | Pydantic v2, pydantic-settings | Versioned business config as validated JSON |
| Optimisation | Pure-Python rule-based V1; OR-Tools CP-SAT V2 (optional extra) | Deterministic first; CP-SAT only where it demonstrably beats V1 |
| Auth | PyJWT HS256 + bcrypt, six roles | Simple, auditable; swap to OIDC when the enterprise IdP is known |
| Jobs | APScheduler in-process (dev) / worker container (prod) | No broker dependency until volumes require one; Redis 7 available for caching later |
| Logging | structlog (JSON in prod) | Request/run/user context bound on every line |
| Frontend | React 18 + TypeScript + Vite, TanStack Query, Recharts, custom SVG Gantt | Spec preference; no heavy component framework for a dense control-tower look |
| Packaging | Dockerfiles, docker-compose, GitHub Actions | Spec deployment requirements; built but not run in this environment (no Docker daemon) |
| Quality | ruff, mypy (strict on domain/engines), pytest, hypothesis, freezegun | Determinism and typing enforced in CI |

## 13. Security architecture

* **Authentication:** `POST /auth/login` exchanges username/password (bcrypt, 72-byte limit enforced)
  for a signed HS256 JWT (`sub, username, role, type=access, iat, exp`); expiry configurable
  (`PPSE_JWT_EXPIRE_MINUTES`). Production refuses to run silently with the dev secret (warning at
  startup; deployment docs require `PPSE_JWT_SECRET`).
* **Authorisation:** role ranking `executive(0) < operator < supervisor < planner <
  production_manager < admin(5)`; write endpoints require a minimum role, read endpoints also admit the
  read-only executive. Endpoint→role matrix is fixed in `DESIGN_CONTRACT.md §9`.
* **Audit:** every override, lock, expedite, hold, approval, publish and configuration change writes
  `audit_log(user_id, timestamp, entity_type, entity_id, action, previous_value, new_value, reason)`.
* **ERP credentials:** injected via environment/secret store into the connector factory; the ERP
  account must be read-only until the writeback ladder advances; never logged.
* **Transport and secrets:** TLS terminated at the reverse proxy; secrets via environment or the
  platform's secret manager; `.env.example` documents every variable and contains no real secret.
* **Input validation:** Pydantic request models; `AppError` hierarchy mapped to HTTP codes without
  leaking stack traces; request IDs on every response for support without exposing internals.
* **Not yet covered (roadmap):** SSO/OIDC, per-customer data scoping for external users, rate
  limiting, field-level encryption of commercial data (order value/margin) at rest.

## 14. Deployment architecture

Containers: `db` (postgres:16-alpine), `migrate` (one-shot Alembic), `seed` (users, config,
synthetic sync), `backend` (uvicorn, non-root, healthcheck on `/api/v1/health`), `worker` (same image,
`PPSE_BACKGROUND_JOBS_ENABLED=true`, runs sync/replan/alert jobs), `frontend` (nginx serving the Vite
build and proxying `/api`). `docker-compose.yml` wires them for development; production uses the
same images behind a TLS reverse proxy with managed PostgreSQL and daily base backups + WAL
archiving. CI (`.github/workflows/ci.yml`) runs ruff, mypy, Alembic upgrade, unit/engine/integration
tests against a PostgreSQL service, slow simulation tests, and the frontend typecheck/lint/build.
Detail and runbooks: `docs/DEPLOYMENT.md`.

Constraint in this environment: no Docker daemon, so images are validated by CI, not locally.

## 15. Development roadmap

Mapping of the spec's Phase 40 steps to milestones. "Now" means present in this repository at the
time of writing (some modules are being completed in the same iteration; see the per-module docs).

| Spec step | Milestone | Scope | Delivered now | Later |
|---|---|---|---|---|
| STEP 1 Repository audit | M0 | This report, `ARCHITECTURE.md`, `DESIGN_CONTRACT.md` | Yes | Re-issue §1–7 after ERP discovery |
| STEP 2 Requirements | M0 | `REQUIREMENTS.md` (FR/NFR/A/U/D/F) | Yes | Update U-xx/D-xx as answers arrive |
| STEP 3 Data model | M1 | Domain dataclasses, enums, config models; ORM, repositories, mappers, Alembic `0001_initial_schema`, snapshot codec; `DATA_MODEL.md` | Yes | Materialised analytics views when volumes are known |
| STEP 4 ERP connector (read-only) | M1 | `ERPConnector` protocol, `MockERPConnector`, synthetic generator (3 scales), normalizer + field maps + code maps + parsers, capability assessment, reconciliation, `sync_runs` table/repository, `ReadOnlyWritebackGateway` | Yes (`SyncService` orchestration: this iteration) | `RestERPConnector` / `SqlERPConnector` once ERP access exists; webhook receiver |
| STEP 5 Priority engine V1 | M2 | 11 factors, adjustments (aging, fairness, expedite, override, customer rule, ERP priority), explanation, tests incl. property tests | This iteration | Percentile caching across runs; learned parameters (F) |
| STEP 6 Scheduler V1 | M2 | Constraint engine (hard/soft/readiness), calendar, rule-based scheduler, machine assignment ranking, setup estimation, batching, quality score, data-quality engine | Constraints/calendar/DQ: yes; scheduler: this iteration | CP-SAT V2 behind the same interface |
| STEP 7 Dashboard | M3 | Services + API v1 routers; React control tower (16 screens from Phase 33) | This iteration (auth/health API now) | Materialised KPI views, websocket push |
| STEP 8 Simulation | M3 | Scenario types, simulation engine, diff, `POST /schedule/simulate`, What-If screen, weight-change preview | This iteration | Monte-Carlo on cycle-time uncertainty (F) |
| STEP 9 Human override | M3 | Expedite/hold/release/override/force-next/locks with reason + audit | This iteration | Move-order drag-and-drop on Gantt |
| STEP 10 Audit | M3 | `audit_log`, `optimization_runs`, `schedule_versions`, `input_snapshots`, Audit Log screen | This iteration | Retention policy, export |
| Continuous replanning (Phase 11) | M4 | Trigger detection, stability rules, approval gate, worker job | This iteration (engine) | Event-driven triggers from ERP webhooks |
| STEP 11 ERP writeback | M5 | `WritebackGateway` protocol + READ_ONLY/mock gateways now | Protocol only | APPROVAL → WRITEBACK → CONTROLLED_AUTO after validation gates (`ERP_INTEGRATION.md`) |
| Future AI (Phase 38/39) | M6 | — | No | Cycle-time and late-order prediction, NL interface over the API |

Gate between M4 and M5: at least four weeks of parallel running against real ERP data in READ_ONLY
mode with planners confirming the recommended sequence (acceptance criteria in §17 and in
`ERP_INTEGRATION.md`).

## 16. Risks and assumptions

| # | Risk / assumption | Impact | Mitigation |
|---|---|---|---|
| R1 | ERP has no modification timestamps → no incremental sync | Full sync only; latency and load | Connector supports full mode; ask ERP team for change-tracking or a CDC/export job |
| R2 | ERP lacks cycle/setup times (spec's own example) | Scheduler cannot compute durations | DQ engine blocks/flags; derive from job-card actuals; default setup from config, flagged |
| R3 | ERP order status vocabulary does not map cleanly to the 15 spec statuses | Wrong open/closed classification | Code maps are data (`codes.py`); reconciliation report compares counts every sync |
| R4 | Synthetic data misrepresents real distributions | Weights and thresholds tuned to fiction | All business numbers are versioned config; re-tune on real data before APPROVAL mode |
| R5 | Real volumes (20k orders/month, hundreds of machines) exceed V1 run-time targets | Slow replanning | Engines are O(n log n); horizon limit, `max_orders_per_run`, worker process; CP-SAT only on the bottleneck subset |
| R6 | Planners distrust or ignore recommendations | No adoption | Explainability on every number; override + audit; quality score comparison old vs new |
| R7 | Writeback corrupts ERP state | Production disruption | Ladder with gates; READ_ONLY default; idempotent publish with receipts; ERP account permissions |
| R8 | Unknown ERP auth (mTLS, SSO) delays connector | Timeline | Connector factory takes arbitrary options; REST and SQL variants documented step-by-step |
| R9 | Dependencies between orders (assemblies) not present in ERP | `downstream_impact` unusable | Factor weight defaults to 0; enabled when data exists |
| R10 | No Docker daemon in dev environment | Images untested locally | CI builds and runs them |
| A1 | One production order line = one schedulable unit with its own routing | Model shape | Confirm with ERP team (D-02) |
| A2 | All timestamps can be converted to UTC; plant timezone known | Calendar correctness | `CalendarSpec.timezone`; confirm plant TZ |
| A3 | Base currency INR | Money display | `SystemConfig.currency` |
| A4 | Material is consumed by the first operation that references it | Readiness | Per-operation `material_id` supported when ERP has it |
| A5 | Machines within a group are alternatives unless eligibility says otherwise | Eligibility | Explicit `eligible_machine_ids` overrides |

## 17. MVP definition

**Scope.** A read-only decision-support system that every morning answers "what should we produce
next, why, on which machine, and what happens if I change it", running against either the
synthetic mock ERP or a real read-only ERP connector.

**Included:**

1. Integration: `ERPConnector` abstraction, `MockERPConnector` + synthetic data (or a real
   read-only connector if access exists), full and incremental sync, normalisation, reconciliation,
   sync-run history, capability report (Required vs Available).
2. Data quality engine with dashboard ("N orders cannot be scheduled because …").
3. Priority engine V1: all eleven factors, aging/fairness/expedite/override/customer-rule
   adjustments, per-order explanation generated from the computation.
4. Constraint engine (hard + soft + readiness), machine calendars, rule-based scheduler V1 with
   machine recommendation and reasons, schedule quality score, versioned schedules
   (DRAFT → APPROVED → PUBLISHED → SUPERSEDED) with optimisation-run records and input snapshots.
5. What-if simulation with the nine scenario kinds and a baseline-vs-scenario diff.
6. Human control: expedite, hold/release, override priority, force next, locks (order/machine/
   sequence/time slot) — all requiring a reason and written to the audit log.
7. Control-tower UI: the sixteen screens of Phase 33, dense industrial look, light and dark.
8. Alerts (Phase 20), analytics (KPIs, capacity, bottlenecks, OTD), continuous replanning with
   stability rules and approval gate.
9. Security: JWT auth, six roles, audit log; observability: structured logs, request IDs,
   `/health`, `/metrics`, per-run metrics.
10. Writeback: **READ_ONLY only**. `POST /schedule/publish` records the publication internally and
    returns a receipt stating nothing was sent to the ERP.

**Excluded from MVP:** CP-SAT/metaheuristic optimisers, APPROVAL/WRITEBACK/CONTROLLED_AUTO modes,
operator constraints, AI predictions, natural-language interface, SSO.

**Acceptance criteria:**

| # | Criterion | Verification |
|---|---|---|
| AC1 | `docker compose up` (or the documented local equivalent) yields a running system with seeded users and the medium synthetic dataset (800 customers, 5,000 orders) synced | `GET /health` OK; `GET /sync/runs` shows a successful full run |
| AC2 | `POST /schedule/generate` on the medium dataset completes in ≤ 60 s and on the large dataset (20,000 orders) in ≤ 5 min, producing a versioned DRAFT with quality score | Simulation test suite, `optimization_runs` row |
| AC3 | Priority evaluation of 20,000 open orders completes in ≤ 30 s and is bit-for-bit deterministic across two runs on the same snapshot and profile | Unit/simulation tests |
| AC4 | Every order shows a breakdown whose factor points and adjustments sum to the displayed score (±0.01) and the explanation text is produced from that breakdown | Property test + UI check |
| AC5 | No schedule entry violates a hard constraint, overlaps another entry on the same machine, starts before its prerequisite operation ends, or falls outside working time | Schedule validator run on every result in tests |
| AC6 | Locks and frozen window are honoured: a regenerate after locking the next 4 hours changes no entry inside the window | Integration test |
| AC7 | Every override/expedite/hold/lock/approval writes an audit row with user, timestamp, previous, new, reason; `GET /audit` returns it | Integration test |
| AC8 | Simulation of "machine down 8 h" returns orders affected, late before/after, utilisation, overtime, bottlenecks, revenue and margin at risk, without altering the current schedule | Integration test |
| AC9 | Data quality dashboard lists blocking issues by code with counts matching the injected defects of the synthetic dataset | Unit + integration test |
| AC10 | Weight-change preview reports how many orders enter/leave the top-N ("moves 27 orders into the top 50") | Unit test |
| AC11 | Writeback mode is READ_ONLY by default; `POST /schedule/publish` never contacts the ERP and the receipt says so | Unit test on `ReadOnlyWritebackGateway` |
| AC12 | RBAC: every endpoint in the contract matrix rejects lower roles with 403 and accepts the minimum role | API test matrix |
| AC13 | CI green: ruff format/lint, mypy, migrations, unit/engine/integration/simulation tests, frontend typecheck/lint/build | GitHub Actions |
