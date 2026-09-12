# Requirements — Production Priority & Scheduling Engine (PPSE)

Spec deliverable STEP 2. Every requirement is traceable to a spec phase (P-nn) and, where it is
already realised, to the module that implements it. Identifiers are stable: never renumber, only
append or mark *withdrawn*.

Priority: **M** must (MVP), **S** should (post-MVP, same release train), **C** could (later).

---

## 1. Functional requirements

### 1.1 Integration (P1, P2, P26, STEP 4)

| ID | Requirement | Pri | Realised by |
|---|---|---|---|
| FR-01 | The system reads ERP/MES data through a single `ERPConnector` abstraction; no engine or service depends on ERP schema or transport. | M | `app/integration/connector.py`; contract §1, §8 |
| FR-02 | Connectors expose customers, orders (with lines), operations, machines, materials, tooling, calendars and production status, each with an optional `since` watermark. | M | `ERPConnector.fetch_*` |
| FR-03 | A connector declares which normalised fields it can supply (`capabilities()`), and the system produces a Required / Available / Missing report with engine impact and recommendation. | M | `app/integration/capabilities.py` |
| FR-04 | Sync supports full and incremental modes, runs on a schedule and on demand (`POST /sync/run`), and records each run (`sync_runs`: mode, counts, duration, status, errors). | M | `SyncService`, worker |
| FR-05 | Raw ERP records are normalised into the domain model through declarative field maps and status-code maps; unmappable records are skipped with a logged issue, never silently defaulted for required fields. | M | `normalizer.py`, `field_maps.py`, `codes.py` |
| FR-06 | After each sync the system reconciles connector counts against stored counts per entity and flags discrepancies above configurable thresholds. | M | `reconciliation.py` |
| FR-07 | Integration failures are retried with backoff; persistent failure is surfaced as an alert and a failed `sync_runs` row; the last good snapshot remains usable. | M | `SyncService`, `IntegrationError` |
| FR-08 | A `MockERPConnector` serves a deterministic synthetic dataset (small/medium/large) so the whole system runs without ERP access. | M | `mock_connector.py`, `synthetic/` |
| FR-09 | Writeback is governed by a mode ladder READ_ONLY → APPROVAL → WRITEBACK → CONTROLLED_AUTO; READ_ONLY is the default and the only mode enabled in MVP. | M | `writeback.py`, `Settings.writeback_mode` |
| FR-10 | Webhook/event-driven ingestion can be added without changing engines or services (connector may push records into the same normalise → upsert path). | S | `SyncService` design |

### 1.2 Normalised data model (P3, STEP 3)

| ID | Requirement | Pri | Realised by |
|---|---|---|---|
| FR-11 | The normalised model contains Customer, Order (one row per order line), Operation, Machine, Material, Tooling, Calendar/Shift with at least the spec Phase 3 fields; optional fields are explicit `None`. | M | `app/domain/models.py` |
| FR-12 | Orders support multi-operation routings of arbitrary length and process mix (e.g. CNC → deburr → inspect → surface → assembly → pack; AM → support removal → finishing → inspect). | M | `Operation.sequence`, `prerequisite_operation_id` |
| FR-13 | Machines support identical/alternative/preferred machines, machine-specific cycle times, capabilities, downtime and maintenance. | M | `Machine`, `Operation.machine_cycle_minutes` |
| FR-14 | The 15 production statuses of Phase 3 and the 9 readiness states are first-class enums. | M | `app/domain/enums.py` |
| FR-15 | A `PlanningSnapshot` bundles all engine inputs at one instant and can be cloned for simulation and serialised for audit. | M | `snapshot.py`, `db/snapshot_codec.py` |

### 1.3 Priority engine (P4, P14–P17, P34, STEP 5)

| ID | Requirement | Pri | Realised by |
|---|---|---|---|
| FR-20 | Priority is a configurable weighted sum of independently computed factors; no single hard-coded formula. | M | `PriorityEngine`, `PriorityProfile` |
| FR-21 | Factors implemented: due-date urgency (days until due, projected completion, projected lateness), SLA risk, customer importance (tier, strategic flag, revenue, profitability, escalation), order value, margin, delay penalty, production readiness, machine availability (incl. single-capable-machine bonus), setup efficiency, batching affinity, downstream impact. | M | `engines/priority/factors/*` |
| FR-22 | Weights, thresholds and per-factor parameters are editable via API/UI and stored as versioned `PriorityProfile`s. | M | `GET/PUT /priority/configuration` |
| FR-23 | Adjustments applied after the weighted base: aging, fairness/starvation boost, expedite, manual override, customer rule, ERP priority code; final score clamped to [0, 100]. | M | `PriorityAdjustment` |
| FR-24 | Fairness: no order waits beyond a configurable threshold without a boost; no customer occupies more than a configurable share of the top-N. | M | `FairnessConfig` |
| FR-25 | Every score carries a machine-readable breakdown and a human-readable explanation generated from the same breakdown ("+25 — Due in 18 hours"). | M | `FactorScore`, `explanation.py` |
| FR-26 | Changing weights shows a preview: how many orders enter/leave the top-N and the rank deltas, without saving. | M | `POST /priority/configuration/preview` |
| FR-27 | Customer-specific rules (SLA hours, tier override, boost points) are configurable per customer. | M | `CustomerRule`, `/customers/{id}/rules` |
| FR-28 | Expedite raises priority by configurable points for a configurable, bounded duration with mandatory reason and user; normal scoring resumes on expiry. | M | `Expedite`, `ExpediteConfig` |
| FR-29 | Material/tooling/approval/previous-operation/machine/quality-hold blockers are reflected in readiness and can cap blocked orders' scores. | M | `ConstraintEngine.readiness`, `blocked_order_cap` |

### 1.4 Constraints, calendars and scheduling (P5, P6, P10, P18, P19, P35, P36, STEP 6)

| ID | Requirement | Pri | Realised by |
|---|---|---|---|
| FR-30 | Hard constraints (violations): process capability, explicit eligibility, machine group, material compatibility, tooling compatibility, machine operable/maintenance, part size, quantity restriction, locked machine assignment, operation sequence. | M | `engines/constraints/hard.py` |
| FR-31 | Soft constraints (penalties in minute-equivalents): preferred machine, setup changeover, utilisation balancing, energy cost, customer sequence preference, batch preference. | M | `engines/constraints/soft.py` |
| FR-32 | Machine calendars from shifts, weekdays, holidays, overtime windows, extra working days, maintenance and downtime; scheduler arithmetic only counts working time. | M | `engines/calendar` |
| FR-33 | Scheduler V1 (rule-based): remove blocked orders, sort by priority, assign eligible machines, sequence per machine, respect dependencies and locks, estimate setups, batch opportunistically, compute projected completion and lateness, produce placement reason per entry. | M | `RuleBasedScheduler` |
| FR-34 | For each order the system lists eligible machines, ranks them (capability, availability, workload, setup, expected completion, efficiency, utilisation, downstream impact) and states why the recommended one wins. | M | `MachineRecommendation` |
| FR-35 | Setup grouping and batching never delay a job beyond `max_delay_hours` or pull forward a job more than `min_priority_gap` points below the head. | M | `BatchingRules` |
| FR-36 | Every schedule has a quality score (0–100) with components (on-time %, lateness, utilisation, setup efficiency, at-risk) and can be compared old vs new. | M | `quality.compute_quality`, `ScheduleDiff` |
| FR-37 | Locks: order, machine assignment, sequence and time-slot locks; a manager can lock the next N hours and the engine only optimises after the locked period. | M | `ScheduleLock`, `lock_window_minutes` |
| FR-38 | Additional schedulers (CP-SAT, metaheuristics) register under the same `Scheduler` protocol and are selected by configuration. | S | `scheduling/registry.py`, `cpsat.py` |
| FR-39 | Objective weights (on-time delivery, tardiness, setup, utilisation, margin, WIP) are configurable and drive the quality score and V2 objectives. | M | `ObjectiveWeights` |

### 1.5 Simulation (P7, STEP 8)

| ID | Requirement | Pri | Realised by |
|---|---|---|---|
| FR-40 | What-if scenarios run on a cloned snapshot and never alter the live plan: machine_down, urgent_orders, add_machine, extra_shift/working_day, outsource, material_delay, prioritize_customer, weight_change, due_date_change. | M | `engines/simulation` |
| FR-41 | Simulation output shows orders affected, delivery impact per order, machine and capacity utilisation, late orders before/after, additional overtime, bottlenecks, revenue and margin at risk. | M | `ScheduleDiff` |
| FR-42 | Scenarios can be combined in one run and the result is persisted for later reference (not as a schedule version). | S | `SimulationResult` |

### 1.6 Human override and audit (P9, P22, P37, STEP 9, STEP 10)

| ID | Requirement | Pri | Realised by |
|---|---|---|---|
| FR-50 | Managers can increase/decrease/set priority, force next, hold/release, expedite, move an order, lock machine assignment, lock schedule. | M | `PriorityOverride`, `/orders/{id}/*`, `/schedule/lock` |
| FR-51 | Every override requires user, timestamp, previous value, new value and reason, and is written to `audit_log`. | M | `AuditService` |
| FR-52 | Every schedule generation stores an `optimization_runs` row (run id, start/end, orders considered/scheduled/blocked, objective and quality score, algorithm + version, profile + config versions, status). | M | `optimization_runs` |
| FR-53 | Schedules are versioned (monotonic number, DRAFT → APPROVED → PUBLISHED → SUPERSEDED / REJECTED, generated by/at, input snapshot id, note such as "Replanned after CNC-07 downtime"). | M | `schedule_versions` |
| FR-54 | Input snapshots are stored (compressed) so a run can be reproduced and "why was this order scheduled at 14:30 yesterday?" can be answered from stored data. | M | `input_snapshots` |
| FR-55 | Approve and publish are separate actions with distinct roles; publish honours the writeback mode. | M | `/schedule/approve`, `/schedule/publish` |

### 1.7 Continuous replanning and alerts (P11, P20)

| ID | Requirement | Pri | Realised by |
|---|---|---|---|
| FR-60 | The system detects triggers: new order, order completed, machine down/up, material arrived, quality failure, rework, production delay, customer priority change, config change, manual, scheduled. | M | `ReplanTriggerType`, `replanning/triggers.py` |
| FR-61 | On significant change it recalculates priorities, identifies the affected schedule, re-optimises, compares old vs new, notifies planners and requires approval when configured. | M | `ReplanningEngine`, worker |
| FR-62 | Stability rules: entries starting within the frozen window are not moved unless improvement exceeds a configurable threshold; optional cap on moves per replan. | M | `StabilityRules` |
| FR-63 | Alerts for likely-late, overdue, machine downtime, material shortage, tool shortage, capacity overload, bottleneck, SLA breach risk, production behind schedule, schedule disruption, starvation, data quality — each with severity, time, order/machine, reason and recommended action; acknowledgeable. | M | `Alert`, `/alerts` |

### 1.8 Analytics and dashboards (P8, P12, P13, P32, P33, STEP 7)

| ID | Requirement | Pri | Realised by |
|---|---|---|---|
| FR-70 | Executive KPIs: open orders, pending quantity, due today/tomorrow/this week, overdue, at risk, OTD % and expected OTD %, machine and capacity utilisation, revenue and margin at risk, blocked by material/tooling/machine, waiting for approval. | M | `ExecutiveKpis`, `/analytics/kpis` |
| FR-71 | Priority queue table (rank, order, customer, due, qty, process, machine, priority, risk, status) with sort/filter by customer, due date, machine, process, priority, risk, status, material, revenue, margin. | M | PriorityQueue page |
| FR-72 | Machine-centric Gantt board with day/week/machine-group/process views showing setup, jobs and breaks. | M | GanttChart, MachineSchedule pages |
| FR-73 | Order detail with the full "why is this order prioritised?" breakdown, machine recommendation, dependencies, expected completion/lateness, risk and history. | M | OrderDetail page, `/orders/{id}/explanation` |
| FR-74 | Bottleneck analysis by machine group, machine, process, material, tooling: utilisation, orders waiting, capacity shortfall hours, revenue at risk, recommendation. | M | `find_bottlenecks` |
| FR-75 | Capacity planning: required vs available hours by day/week and by machine, machine group, process, department with gap. | M | `compute_capacity` |
| FR-76 | Data quality dashboard: counts of unschedulable orders by cause, drill-down to entities, recommendation per issue. | M | `DataQualityEngine`, `/data-quality` |
| FR-77 | Screens: Executive Dashboard, Control Tower, Priority Queue, Machine Schedule, Gantt, Order Detail, Machine Detail, Bottleneck Analysis, Capacity Planning, What-If, Priority Configuration, Scheduling Configuration, Alerts, Data Quality, Audit Log, System Administration. | M | `frontend/src/pages` |

### 1.9 Data quality (P21)

| ID | Requirement | Pri | Realised by |
|---|---|---|---|
| FR-80 | Detect missing due dates, invalid dates, missing cycle/setup times, missing machine assignment, missing material, negative quantities, duplicate orders, incorrect status, impossible production times, missing customer, conflicting machine capability, invalid routing, missing operations, unknown references. | M | `engines/data_quality/rules*.py` |
| FR-81 | Severity (blocking / warning / info) is policy-driven and configurable for the toggled codes; blocking issues exclude the order from scheduling with an explicit reason. | M | `SeverityPolicy`, `DataQualityConfig` |

### 1.10 Security and administration (P27, P28)

| ID | Requirement | Pri | Realised by |
|---|---|---|---|
| FR-90 | JWT bearer authentication; bcrypt password storage; token expiry configurable. | M | `core/security.py`, `/auth/*` |
| FR-91 | Six roles (admin, production_manager, planner, supervisor, operator, executive) with the endpoint matrix of contract §9; executive is read-only on all dashboards. | M | `ROLE_RANK`, `api/deps.py` |
| FR-92 | System administration screen: users, roles, connector status, sync runs, writeback mode (display), configuration versions. | M | SystemAdministration page |

## 2. Non-functional requirements

| ID | Requirement | Target / rule | Source |
|---|---|---|---|
| NFR-01 | Scale: thousands of orders, hundreds of thousands of order lines, hundreds of machines, thousands of operations per run. | Engines O(n log n) in orders/operations; no O(n²) loops over orders; horizon and `max_orders_per_run` limits | P29 |
| NFR-02 | Priority evaluation throughput | ≤ 30 s for 20,000 open orders on one core | P29, AC3 |
| NFR-03 | Schedule generation (V1) | ≤ 60 s medium dataset (5,000 orders); ≤ 5 min large (20,000) | P29, AC2 |
| NFR-04 | Interactive API latency | p95 ≤ 500 ms for list/detail endpoints on the medium dataset; generation/simulation are asynchronous or long-poll with run ids | P29 |
| NFR-05 | Replanning cadence | Configurable interval (default 30 min) and trigger-driven; incremental recalculation where possible | P11, P29 |
| NFR-06 | Determinism | Same snapshot + config ⇒ identical results (stable sort keys, injected clock, no randomness) | contract §4 |
| NFR-07 | Explainability | Every score, placement, recommendation and alert carries a breakdown and reason produced by the computing code path | P34 |
| NFR-08 | Configurability | Priority weights, customer tiers, SLA rules, due-date thresholds, aging, machine preferences, setup penalties, batch rules, overtime, shifts, holidays, calendars, expedite rules, lock period, replanning threshold, fairness rules are configuration, versioned in DB, editable via UI; no business constant in engine code | spec "Configuration over hard coding" |
| NFR-09 | Auditability | All overrides/approvals/config changes logged with user, timestamp, previous, new, reason; every run reproducible from stored snapshot + versions | P22 |
| NFR-10 | Security | RBAC on every endpoint; secrets from environment/secret store; TLS at the proxy; no credentials or PII in logs; production refuses default JWT secret silently (warns) | P27 |
| NFR-11 | Observability | Structured JSON logs with request id, run id, schedule version, user id; `/health`, `/metrics`; per-run metrics (duration, orders considered/scheduled/blocked, objective); integration failure counters | spec "Observability" |
| NFR-12 | Availability and recovery | Stateless API/worker containers; PostgreSQL daily base backup + WAL; last good snapshot survives ERP outage | spec "Deployment" |
| NFR-13 | Portability | Portable SQL types; unit tests on SQLite, integration on PostgreSQL; containerised deployment | P24 |
| NFR-14 | Code quality | Full typing (mypy strict on domain/engines), ruff clean, files ≤ ~600 lines, no module-level mutable state, DI via constructors | spec "Development quality" |
| NFR-15 | Testing | Unit (priority, due date, SLA, customer scoring, eligibility, material, constraints, setup, generation), integration (ERP→DB, DB→priority, priority→scheduler, scheduler→writeback), simulation (machine failure, shortage, urgent order, delay, rework, capacity increase) | P30 |
| NFR-16 | Time handling | All datetimes timezone-aware UTC; durations in minutes; plant timezone in calendars | contract §4 |
| NFR-17 | UI | Dense industrial control-tower design, light + dark, keyboard-friendly tables, exception highlighting; not a generic admin template | P32 |
| NFR-18 | Safety | Writeback never enabled by default; every mode transition is an explicit, audited configuration change with documented gates | P26 |

## 3. Assumptions

| ID | Assumption | Consequence if false |
|---|---|---|
| A-01 | One ERP order *line* is the schedulable unit and owns its routing. | Remodel `Order` granularity; normaliser composes ids differently |
| A-02 | The ERP can be read without side effects (a read-only account or read endpoints exist). | Need an export job instead of live reads |
| A-03 | Machine working time can be expressed as recurring shifts + exceptions. | Calendar model needs per-day explicit windows (supported via `overtime_windows`) |
| A-04 | Cycle time scales linearly with quantity per operation. | Add lot-size/parallel-cavity model to `Operation.attributes` |
| A-05 | Setup depends on (machine, setup family, material) transitions only. | Extend `SetupRules` with a changeover matrix |
| A-06 | Machines within a group are interchangeable unless eligibility data says otherwise. | Require explicit `eligible_machine_ids` from routing |
| A-07 | Base currency INR; money is comparable across orders without FX. | Add currency conversion in the normaliser |
| A-08 | The plant operates in a single timezone. | Per-calendar timezone already supported; UI shows plant local time |
| A-09 | Synthetic data volumes (800 customers, 5k–20k orders/month) are representative of real load. | Re-baseline performance targets |
| A-10 | Planners will keep overlays (locks, overrides, customer rules) in PPSE, not in the ERP. | Sync must also import ERP-side overrides |

## 4. Unknowns (ERP/MES)

| ID | Unknown | Blocking for |
|---|---|---|
| U-01 | ERP language, framework and hosting | Stack alignment (P24), connector variant |
| U-02 | ERP release/change process and who owns integration | Change coordination |
| U-03 | Whether the ERP exposes an MES event stream (job start/finish) | Event-driven replanning |
| U-04 | ERP environment topology (prod/test instances, network reachability) | Deployment, test connector |
| U-05 | Database engine and version | SQL connector dialect |
| U-06 | Presence of row modification timestamps / change tracking | Incremental sync (else full only) |
| U-07 | Table and column names for the entities in AUDIT_REPORT §7 | Field maps |
| U-08 | Read replica availability and load limits | Sync interval |
| U-09 | Existence and style of APIs (REST/SOAP/GraphQL/none) | REST connector |
| U-10 | Authentication mechanism (API key, OAuth2, Basic, session, mTLS, SSO) | Connector options, secret handling |
| U-11 | Webhook/event capability | FR-10 |
| U-12 | Rate limits and pagination conventions | Retry policy |
| U-13 | Existing export/ETL jobs that could be reused | Interim integration |
| U-14 | Status code vocabularies for orders, operations, machines, materials | `codes.py` maps |
| U-15 | Availability and reliability of cycle time, setup time, margin, penalty, SLA, dependency data | Factor enablement, DQ severities |
| U-16 | Whether order-to-order dependencies (assemblies) exist in the ERP | `downstream_impact` |
| U-17 | Whether tooling is managed in the ERP at all | Tooling constraints |
| U-18 | Shift model, holidays and plant timezone | Calendars |
| U-19 | Which ERP fields may eventually be written (sequence, planned dates, assigned machine) and their semantics | Writeback design (STEP 11) |
| U-20 | Corporate identity provider for SSO | Auth roadmap |

## 5. ERP dependencies

| ID | Dependency (what we need from the ERP team) | Needed by milestone |
|---|---|---|
| D-01 | Read-only credentials to a non-production ERP instance (DB or API) and network access | M1 (real connector) |
| D-02 | Schema/API documentation or a guided walkthrough covering the entities in AUDIT_REPORT §7 | M1 |
| D-03 | Sample exports (≥ 1 month of orders with operations, machines, materials, customers) | M1 |
| D-04 | Confirmation of status-code vocabularies and their business meaning | M1 |
| D-05 | Modification timestamps or a change feed on orders, operations, production status | M1 (incremental sync) |
| D-06 | Shift plans, holiday calendar, maintenance plan source | M2 |
| D-07 | Standard cycle and setup times per routing step, or job-card actuals to derive them | M2 |
| D-08 | Commercial data access policy (order value, margin, customer revenue) | M2 |
| D-09 | A named ERP integration owner for change coordination and incident handling | M1 |
| D-10 | For writeback: target fields, transaction semantics, idempotency key, test instance, rollback procedure | M5 |
| D-11 | For events: webhook or queue endpoint specification | M4 (optional) |

## 6. Future requirements

| ID | Requirement | Spec |
|---|---|---|
| F-01 | Optimisation V2: CP-SAT model for bottleneck groups (minimise weighted tardiness + setup), selected per run and benchmarked against V1 by quality score. | P6 V2, P19 |
| F-02 | Metaheuristics (local search, simulated annealing, genetic) as additional `Scheduler` plug-ins for large horizons. | P6 V2 |
| F-03 | Operator/skill constraints and operator bottlenecks. | P5, P12 |
| F-04 | Scrap/rejection and rework-loop modelling in routings. | P1, P3 |
| F-05 | Cycle-time prediction from job-card actuals (regression) feeding `machine_cycle_minutes`. | P38 |
| F-06 | Late-order probability model replacing threshold-based risk levels. | P38 |
| F-07 | Machine failure prediction to pre-empt downtime scenarios. | P38 |
| F-08 | Demand and material demand forecasting for capacity planning horizon. | P38 |
| F-09 | Customer priority prediction / suggestion of tier changes. | P38 |
| F-10 | Intelligent exception summaries on the control tower ("17 orders at high risk, ₹42.6 lakh exposed, bottleneck CNC 5-axis"). | P38 |
| F-11 | Natural-language interface that translates questions into existing API calls and answers only from system data. | P39 |
| F-12 | APPROVAL writeback mode: approved schedule sent to ERP as recommendation fields. | P26 |
| F-13 | WRITEBACK mode: approved schedule updates ERP sequence/planned dates/assigned machine with receipts and reconciliation. | P26 |
| F-14 | CONTROLLED_AUTO: automatic publish under configured rules (quality ≥ threshold, changes ≤ N, business hours), with kill switch. | P26 |
| F-15 | Event-driven ingestion from ERP webhooks/queues with sub-minute replanning triggers. | P2, P11 |
| F-16 | Monte-Carlo simulation over cycle-time and arrival uncertainty. | P7 |
| F-17 | Materialised analytics views and Redis caching for dashboards at full scale. | P29 |
| F-18 | SSO/OIDC, per-customer external portal views. | P27 |
| F-19 | Drag-and-drop move-order on the Gantt with immediate constraint validation. | P9 |
| F-20 | Multi-plant / multi-site scheduling with transfer lead times. | — |
