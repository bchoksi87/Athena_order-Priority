# Data Model

This document describes the Normalized Manufacturing Data Model (`backend/app/domain/`) that every
engine reads, the `PlanningSnapshot` that bundles it, the engine result types, the versioned
configuration models, and the PostgreSQL schema (`backend/app/db/models/`, migration
`backend/alembic/versions/0001_initial_schema.py`) that stores all of it. It maps every field of
spec Phase 3 to where it lives and states which fields the ERP supplies, which are derived, and
which planners own.

**How to read this.** `docs/DESIGN_CONTRACT.md` §5 and §7 are the binding summary;
`docs/ERP_INTEGRATION.md` explains how ERP records become these objects; `docs/PRIORITY_ENGINE.md`
and `docs/SCHEDULING_ENGINE.md` explain how the fields are used; `docs/AUDIT_REPORT.md` §7 lists
which ERP fields are still unknown. Spec deliverable STEP 3.

---

## 1. Conventions

* **Domain objects** are `@dataclass(slots=True)` classes in `app/domain/models.py`; enums are
  `StrEnum`s in `app/domain/enums.py`; engine outputs are dataclasses in `app/domain/results.py`;
  configuration is Pydantic (`app/domain/config.py`). Nothing in `app/domain` performs I/O.
* **Identifiers** are strings. ERP identifiers are kept verbatim (`external_ref`,
  `external_order_ref`); internal ids are ULID-like `prefix_…` strings from `app/core/ids.py`.
* **Time**: every `datetime` is timezone-aware UTC (the ORM type `UTCDateTime` rejects naive values
  and re-tags SQLite values as UTC); durations are minutes (`float`); calendar shift times are
  local `time`s in the calendar's `timezone`.
* **Money**: `float` in base currency (`SystemConfig.currency`, default INR); never rounded in
  engines.
* **Optional ERP data is `None`**, never a sentinel; engines degrade gracefully and the Data
  Quality Engine reports the gap.
* **Source column legend** in the tables below: `ERP` — synced from the ERP through the
  normaliser; `derived` — computed by PPSE; `planner` — created in PPSE by users (overlays,
  configuration); `either` — ERP when available, otherwise planner/config.

---

## 2. Enumerations (`app/domain/enums.py`)

| Enum | Values | Notes |
|---|---|---|
| `ProcessType` | cnc_machining, additive_3d_printing, support_removal, deburring, finishing, heat_treatment, surface_treatment, inspection, assembly, packing, other | Routings of arbitrary length |
| `OrderStatus` (spec PRODUCTION STATUS, 15) | new, released, planned, scheduled, material_waiting, tooling_waiting, in_production, partially_completed, quality_inspection, rework, completed, packed, shipped, on_hold, cancelled | `is_open` = not in {completed, packed, shipped, cancelled}; `is_schedulable` = {new, released, planned, scheduled, material_waiting, tooling_waiting, in_production, partially_completed, rework} |
| `OperationStatus` | pending, ready, scheduled, in_progress, completed, on_hold, rework, cancelled | `is_done` = completed or cancelled |
| `ReadinessState` (9) | ready, waiting_material, waiting_tooling, waiting_approval, waiting_previous_operation, machine_unavailable, quality_hold, on_hold, other_constraint | Spec lists 8; `on_hold` is separated from `other_constraint` |
| `MachineStatus` | available, running, down, maintenance, offline | `is_operable` = available or running |
| `MaterialStatus` | available, partial, unavailable, on_order, unknown | Order-level, ERP supplied |
| `QualityStatus` | none, pending, passed, failed, rework, hold | |
| `ShippingStatus` | not_shipped, partial, shipped | |
| `CustomerTier` | strategic, key, standard, low | |
| `PaymentRisk` | low, medium, high, unknown | |
| `RiskLevel` | low, medium, high, critical | Derived per order |
| `ScheduleStatus` | draft, approved, published, superseded, rejected | Schedule version state machine |
| `LockType` | order, machine, sequence, time_slot | |
| `OverrideType` | increase_priority, decrease_priority, set_priority, force_next, hold_order, release_hold, move_order, lock_machine_assignment | |
| `AlertSeverity` / `AlertType` | info, warning, high, critical / 12 alert types | |
| `Role` | admin, production_manager, planner, supervisor, operator, executive | `ROLE_RANK` ordering |
| `WritebackMode` | read_only, approval, writeback, controlled_auto | |
| `DataQualitySeverity` / `DataQualityCode` | blocking, warning, info / 15 codes | |
| `SyncMode` | full, incremental | |
| `ReplanTriggerType` | new_order, order_completed, machine_down, machine_up, material_arrived, quality_failure, rework, production_delay, customer_priority_change, manual, config_change, scheduled | |

Enums are stored in the database as their string `.value` (`String(32)` columns).

---

## 3. Entities (`app/domain/models.py`)

### 3.1 Customer

| Field | Type | Meaning | Source |
|---|---|---|---|
| `customer_id` | str | Primary key | ERP |
| `customer_name` | str | | ERP |
| `customer_category` | str = "standard" | Free ERP category | ERP |
| `customer_tier` | CustomerTier = STANDARD | Tier used by customer scoring | either (ERP or planner via `CustomerRule.tier_override`) |
| `customer_priority` | int = 3 | ERP priority code 1 (highest)..5 | ERP |
| `strategic_customer_flag` | bool | | ERP |
| `annual_revenue`, `customer_revenue` | float? | Company revenue; trailing-12-month revenue with us | ERP |
| `customer_profitability` | float? (0..1) | Contribution-margin ratio | ERP |
| `customer_service_level` | float? (0..1) | Target OTD | ERP |
| `sla_hours` | float? | Contractual turnaround | either |
| `escalation_level` | int = 0 | 0 none .. 3 executive | ERP |
| `historical_on_time_delivery` | float? | | ERP |
| `payment_risk` | PaymentRisk = UNKNOWN | | ERP |
| `preferred_delivery_expectation` | str? | | ERP |
| `account_manager` | str? | | ERP |
| `active` | bool = True | | ERP |
| `external_ref` | str? | ERP key verbatim | ERP |
| `attributes` | dict | Unmapped ERP attributes | ERP |

### 3.2 Order (one row per order **line**)

| Field | Type | Meaning | Source |
|---|---|---|---|
| `order_id` | str | Unique per line (`order_no + line_no` composed by the normaliser) | ERP |
| `customer_id`, `part_id` | str | References (no FK in the DB, §8) | ERP |
| `order_line_id`, `external_order_ref` | str? | ERP line id / header reference | ERP |
| `part_name`, `part_family` | str? | Family drives batching | ERP |
| `order_date`, `received_date` | datetime? | SLA and aging clocks start at `received_date` else `order_date` | ERP |
| `requested_delivery_date`, `promised_delivery_date`, `revised_delivery_date` | datetime? | `due_date` property = revised > promised > requested | ERP (revised also via `due_date_change` scenario) |
| `quantity`, `completed_quantity`, `cancelled_quantity` | float | `pending_quantity` property = max(0, qty − completed − cancelled) | ERP |
| `order_status` | OrderStatus = NEW | | ERP |
| `erp_priority` | int? | ERP priority code → `erp_priority_points` adjustment | ERP |
| `production_status` | str? | Raw ERP production status text | ERP |
| `material_status`, `quality_status`, `shipping_status` | enums | | ERP |
| `order_value`, `estimated_cost`, `estimated_margin`, `actual_margin` | float? | | ERP |
| `process_type` | ProcessType = OTHER | Primary process | ERP |
| `manufacturing_route` | list[ProcessType] | Route summary (operations are authoritative) | ERP |
| `machine_group`, `required_machine_id`, `required_material_id` | str? | Order-level fallbacks when operations lack them | ERP |
| `tooling_requirement` | set[str] | Order-level tooling | ERP |
| `estimated_setup_minutes`, `estimated_cycle_minutes_per_unit`, `estimated_total_production_minutes` | float? | Order-level time estimates (fallback for missing operation data) | ERP |
| `customer_priority`, `technical_priority`, `commercial_priority` | int? | ERP priority codes (changes trigger replanning) | ERP |
| `lateness_penalty_per_day` | float? | Contractual penalty | ERP |
| `sla_hours` | float? | Order-level SLA (highest precedence) | ERP |
| `special_instructions` | str? | | ERP |
| `drawing_approved` | bool = True | False → WAITING_APPROVAL | ERP |
| `on_hold`, `hold_reason` | bool, str? | ERP hold (planner holds are overrides) | ERP |
| `depends_on_order_ids` | set[str] | Orders that must finish first | ERP |
| `surface_finish`, `technology` | str? | Batching dimensions | ERP |
| `attributes` | dict | e.g. `part_size_mm`, `part_length_mm`…, `simulation_notes`, `outsourced_quantity`, `injected_defect` | ERP / derived |

Properties: `pending_quantity`, `due_date`, `is_open` (status open and pending > 0),
`hours_until_due(now)`.

### 3.3 Operation

| Field | Type | Meaning | Source |
|---|---|---|---|
| `operation_id`, `order_id`, `sequence` | str, str, int | Ordered routing step | ERP |
| `operation_type` | ProcessType | | ERP |
| `machine_group` | str? | | ERP |
| `machine_id` | str? | Assigned (binding while IN_PROGRESS) or ERP-preferred machine (soft preference otherwise) | ERP |
| `eligible_machine_ids` | set[str] | Explicit list; empty = derive from group/process | ERP |
| `setup_minutes`, `cycle_minutes_per_unit` | float? | | ERP |
| `machine_cycle_minutes` | dict[machine_id, float] | Machine-specific cycle time override | ERP |
| `quantity`, `completed_quantity` | float | `pending_quantity` property | ERP |
| `operation_status` | OperationStatus = PENDING | `is_done` property | ERP (production status feed) |
| `prerequisite_operation_id` | str? | May reference another order's operation | ERP |
| `material_id`, `material_quantity_per_unit` | str?, float? | Material requirement | ERP |
| `tooling_ids` | set[str] | | ERP |
| `operator_requirement`, `quality_requirement` | str? | Stored, not yet used by constraints | ERP |
| `setup_family` | str? | Same family ⇒ no changeover | ERP |
| `estimated_start`, `estimated_end`, `actual_start`, `actual_end` | datetime? | ERP plan/actuals (delay detection, dependency `resolves_at`) | ERP |
| `attributes` | dict | | ERP |

Methods: `cycle_minutes_on(machine_id)`, `run_minutes_on(machine)` = cycle × pending ÷
`machine.efficiency`.

### 3.4 Machine

| Field | Type | Meaning | Source |
|---|---|---|---|
| `machine_id`, `machine_name`, `machine_type` | str | | ERP |
| `process_type`, `machine_group`, `location` | | Primary process, interchangeability group | ERP |
| `status` | MachineStatus = AVAILABLE | | ERP |
| `calendar_id` | str? | → `CalendarSpec`; falls back to the snapshot default | either |
| `efficiency` | float = 1.0 | Divides cycle time (0.8 = slower) | ERP |
| `utilization` | float? | Trailing utilisation from ERP (reporting) | ERP |
| `capacity_hours_per_day` | float? | Reporting | ERP |
| `maintenance_windows`, `planned_downtime`, `unplanned_downtime` | list[TimeWindow] | `all_downtime` property; subtracted from the calendar | ERP (unplanned also via `machine_down` scenario) |
| `compatible_materials` | set[str] | Empty = no restriction | ERP |
| `compatible_processes` | set[ProcessType] | Additional processes (`supports_process`) | ERP |
| `max_part_size_mm` | (float, float, float)? | Envelope | ERP |
| `tooling_configuration` | set[str] | Tooling currently mounted | ERP |
| `setup_requirements` | dict | Free-form | ERP |
| `current_material_id`, `current_setup_family` | str? | Current state for changeover estimates | ERP |
| `available_from` | datetime? | Earliest instant new work may start | ERP |
| `preferred_rank` | int = 0 | Lower = preferred within the group (tie-breaker) | either |
| `attributes` | dict | e.g. `min_batch_qty`, `max_batch_qty` | ERP |

### 3.5 Material and Tooling

`Material(material_id, material_name, material_type, grade, supplier, unit="kg",
available_quantity, reserved_quantity, incoming_quantity, expected_receipt_date, minimum_stock,
compatible_machine_ids, attributes)` — all ERP; `free_quantity` = max(0, available − reserved).

`Tooling(tooling_id, tooling_name, available=True, available_from, compatible_machine_ids,
setup_minutes, expected_life, current_usage, maintenance_status="ok", attributes)` — all ERP;
`is_usable` = available and (usage < life when both known).

### 3.6 Calendar primitives

`TimeWindow(start, end, reason)` — frozen half-open UTC interval (`minutes`, `overlaps`,
`contains`, `intersect`). `Shift(name, start: time, end: time, weekdays=(0..4))` — recurring local
shift; `crosses_midnight` when `end <= start`. `CalendarSpec(calendar_id, name, timezone="UTC",
shifts, holidays: list[date], overtime_windows: list[TimeWindow], extra_working_days: list[date])`
— source: ERP (`fetch_calendars`) or planner; the snapshot's `default_calendar_id` is the
`calendar_specs.is_default` row.

### 3.7 Planner overlays

| Object | Fields | Semantics |
|---|---|---|
| `ScheduleLock` | `lock_id, lock_type, created_by, created_at, reason, order_id?, machine_id?, window?, sequence_order_ids, active` | See `docs/SCHEDULING_ENGINE.md` §6; `active_locks(now)` drops inactive and expired-window locks |
| `PriorityOverride` | `override_id, order_id, override_type, created_by, created_at, reason, value?, target_machine_id?, expires_at?, active` | `value` = delta points or absolute score by type; `target_machine_id` for LOCK_MACHINE_ASSIGNMENT; `is_active_at(now)` |
| `Expedite` | `expedite_id, order_id, created_by, created_at, reason, boost_points, starts_at, expires_at, active` | Active when `starts_at ≤ now < expires_at`; the strongest active one per order applies |
| `CustomerRule` | `customer_id, sla_hours?, tier_override?, priority_boost_points=0, notes?, active` | One rule per customer |

All overlays are `planner` source (simulation scenarios create transient ones on a cloned snapshot).

---

## 4. PlanningSnapshot (`app/domain/snapshot.py`)

The only input type every engine accepts:

```python
@dataclass(slots=True)
class PlanningSnapshot:
    as_of: datetime
    customers: dict[str, Customer]; orders: dict[str, Order]; operations: dict[str, Operation]
    machines: dict[str, Machine]; materials: dict[str, Material]; tooling: dict[str, Tooling]
    calendars: dict[str, CalendarSpec]; default_calendar_id: str | None
    locks: list[ScheduleLock]; overrides: list[PriorityOverride]; expedites: list[Expedite]
    customer_rules: dict[str, CustomerRule]
    snapshot_id: str | None; source: str = "unknown"      # "db" | "synthetic" | "test"
```

Private indexes (`_ops_by_order`, `_machines_by_group`, `_dependents`) are built by
`rebuild_indexes()` (or lazily) and excluded from serialisation. Query helpers:
`operations_for_order`, `pending_operations_for_order`, `next_operation_for_order`,
`machines_in_group`, `machines_for_process`, `dependents_of`, `open_orders()` (sorted by id),
`calendar_for_machine`, `active_expedites(now)` (strongest per order), `active_overrides(now)`,
`active_locks(now)`, `summary()`. `clone()` deep-copies for simulation; engines never mutate the
snapshot they receive. Builders: `app/db/snapshot_builder.py::DbSnapshotBuilder` (open orders and
their operations, all masters, active overlays and rules, constant number of queries) and
`synthetic.SyntheticDataset.to_snapshot()`.

---

## 5. Engine results (`app/domain/results.py`)

| Type | Produced by | Persisted in |
|---|---|---|
| `FactorScore(key, name, kind, raw_score, weight, points, reason, details)` | priority factors | `priority_results.factors` (JSON) |
| `PriorityAdjustment(kind, points, reason, source_id)` | priority adjustments / ranking | `priority_results.adjustments` |
| `PriorityResult(order_id, score, base_score, factors, adjustments, readiness, blocked, blocking_reasons, risk_level, explanation, profile_id, profile_version, computed_at, hours_until_due, projected_completion, projected_lateness_hours, forced_next, rank)` | `PriorityEngine` | `priority_results` |
| `Violation`, `Penalty`, `Blocker(state, message, resolves_at, details)`, `EligibilityResult` | constraint engine | not persisted (rendered into reasons) |
| `MachineCandidate`, `MachineRecommendation` | machine assignment | not persisted yet |
| `ScheduleEntry`, `UnscheduledItem`, `ScheduleMetrics`, `ScheduleQuality`, `ScheduleResult` | scheduler | `schedule_entries`, `schedule_versions.{unscheduled, metrics, quality, warnings}`, `optimization_runs` |
| `OrderDelta`, `ScheduleDiff`, `SimulationResult` | simulation | not persisted yet (`docs/REQUIREMENTS.md` FR-42 is *should*) |
| `ReplanDecision` | replanning | not persisted yet |
| `CapacityRow`, `Bottleneck`, `ExecutiveKpis` | analytics | not persisted (computed per request) |
| `DataQualityIssue`, `Alert` | data quality, alert evaluation | `data_quality_issues`, `alerts` |

---

## 6. Configuration models (`app/domain/config.py`)

`SystemConfig` aggregates `priority_profile: PriorityProfile`, `scheduling: SchedulingConfig`,
`replanning: ReplanningConfig`, `alerts: AlertConfig`, `data_quality: DataQualityConfig`,
`currency`. Every model is `extra="forbid"`; defaults are the shipped
`PriorityProfile-A` / `SchedulingConfig-A` (documented in `docs/PRIORITY_ENGINE.md` §1–§4 and
`docs/SCHEDULING_ENGINE.md` §1.1). Source: planner (admin) via the configuration tables (§9).

---

## 7. Persistence conventions (`app/db`)

* Portable SQLAlchemy types only: `String`, `Integer`, `Float`, `Boolean`, `DateTime(timezone=True)`
  (wrapped as `UTCDateTime`), `JSON` (not JSONB), `Text`, `LargeBinary` (snapshots). Enums are
  `String(32)`; ids `String(64)`; names `String(255)`.
* Every table has `created_at`, `updated_at` (`TimestampMixin`, server default `now()`).
* Sets become sorted JSON lists, tuples lists, `TimeWindow`/`Shift` small JSON objects, dates ISO
  strings (`app/db/mappers.py`, `snapshot_codec.encode_shift/encode_time_window`).
* Master tables carry `synced_at` (last ERP sync touch; `None` for locally created rows).
* Repositories (`app/db/repositories/`) return domain objects or the record dataclasses in
  `app/db/records.py` (`Page`, `UserRecord`, `ConfigVersionInfo`, `OptimizationRunRecord`,
  `ScheduleVersionInfo`, `AlertRecord`, `AuditEntry`, `DataQualityIssueRecord`,
  `DataQualitySummary`, `SyncRunRecord`, `SnapshotInfo`).

### 7.1 Deliberate non-FK ERP references

References to ERP-owned entities are plain indexed columns, **not** foreign keys, so that a sync
can upsert orders whose customer, machine or material has not arrived yet (or was deleted in the
ERP) without failing; the Data Quality Engine reports `UNKNOWN_REFERENCE`. Concretely:
`orders.customer_id`, `orders.part_id`, `orders.required_machine_id`, `orders.required_material_id`,
`orders.depends_on_order_ids`, `operations.machine_id`, `operations.material_id`,
`operations.prerequisite_operation_id`, `machines.calendar_id`, and every `order_id` /
`machine_id` / `customer_id` on result, overlay and alert tables. Real foreign keys exist only
where PPSE owns both sides: `customer_rules → customers`, `operations → orders`,
`machine_downtime → machines` (all CASCADE), `optimization_runs → input_snapshots`,
`schedule_versions → optimization_runs, input_snapshots` (SET NULL), `schedule_entries →
schedule_versions` (CASCADE).

---

## 8. Tables

Notation: `PK` primary key, `FK` foreign key, `NN` not null, `ix` single-column index, `JSON`
portable JSON column. `ts` = `created_at`, `updated_at` (present on every table, omitted below).

### 8.1 Master data (ERP-synced)

**`customers`** ↔ `Customer`. `customer_id` PK; `customer_name` NN; `customer_category` NN;
`customer_tier` NN ix; `customer_priority` int NN; `strategic_customer_flag` bool NN;
`annual_revenue`, `customer_revenue`, `customer_profitability`, `customer_service_level`,
`sla_hours` float; `escalation_level` int NN; `historical_on_time_delivery` float; `payment_risk`
NN; `preferred_delivery_expectation`, `account_manager` str; `active` bool NN ix; `external_ref`
str(128); `attributes` JSON NN; `synced_at`.

**`customer_rules`** ↔ `CustomerRule`. `customer_id` PK, FK `customers` CASCADE; `sla_hours`;
`tier_override`; `priority_boost_points` NN; `notes` text; `active` NN; `updated_by`;
`last_changed_at`.

**`orders`** ↔ `Order`. `order_id` PK; `customer_id` NN ix; `part_id` NN ix; `order_line_id`;
`external_order_ref` ix; `part_name`; `part_family` ix; `order_date`, `received_date`,
`requested_delivery_date`, `promised_delivery_date`, `revised_delivery_date`; **`due_date`** ix
(denormalised effective due date, written by the mapper); `quantity`, `completed_quantity`,
`cancelled_quantity` NN; `order_status` NN ix; `erp_priority`; `production_status` ix;
`material_status`, `quality_status`, `shipping_status` NN; `order_value`, `estimated_cost`,
`estimated_margin`, `actual_margin`; `process_type` NN ix; `manufacturing_route` JSON;
`machine_group` ix; `required_machine_id` ix; `required_material_id` ix; `tooling_requirement`
JSON; `estimated_setup_minutes`, `estimated_cycle_minutes_per_unit`,
`estimated_total_production_minutes`; `customer_priority`, `technical_priority`,
`commercial_priority` int; `lateness_penalty_per_day`, `sla_hours`; `special_instructions` text;
`drawing_approved` NN; `on_hold` NN; `hold_reason` text; `depends_on_order_ids` JSON;
`surface_finish`, `technology`; `attributes` JSON NN; `synced_at`. Composite indexes
`ix_orders_customer_status (customer_id, order_status)`, `ix_orders_status_due (order_status,
due_date)`.

**`operations`** ↔ `Operation`. `operation_id` PK; `order_id` NN FK `orders` CASCADE ix;
`sequence` int NN; `operation_type` NN ix; `machine_group` ix; `machine_id` ix;
`eligible_machine_ids` JSON; `setup_minutes`, `cycle_minutes_per_unit`; `machine_cycle_minutes`
JSON; `quantity`, `completed_quantity` NN; `operation_status` NN ix; `prerequisite_operation_id`;
`material_id` ix; `material_quantity_per_unit`; `tooling_ids` JSON; `operator_requirement`,
`quality_requirement`, `setup_family`; `estimated_start`, `estimated_end`, `actual_start`,
`actual_end`; `attributes`. Index `ix_operations_order_sequence (order_id, sequence)`.

**`machines`** ↔ `Machine` (without downtime). `machine_id` PK; `machine_name`, `machine_type` NN;
`process_type` NN ix; `machine_group` NN ix; `location`; `status` NN ix; `calendar_id`;
`efficiency` NN; `utilization`; `capacity_hours_per_day`; `compatible_materials`,
`compatible_processes` JSON; `max_part_size_mm` JSON (nullable 3-list); `tooling_configuration`,
`setup_requirements` JSON; `current_material_id`; `current_setup_family`; `available_from`;
`preferred_rank` int NN; `attributes`; `synced_at`. Index `ix_machines_group_rank (machine_group,
preferred_rank)`.

**`machine_downtime`** ↔ the three `Machine` downtime lists. `downtime_id` PK; `machine_id` NN FK
`machines` CASCADE ix; `kind` NN ∈ {maintenance, planned, unplanned}; `start` NN; `end` NN ix;
`reason` text NN. Index `ix_machine_downtime_machine_start (machine_id, start)`.

**`calendar_specs`** ↔ `CalendarSpec`. `calendar_id` PK; `name` NN; `timezone` NN; `shifts` JSON
(list of `{name, start, end, weekdays, …}`); `holidays` JSON (ISO dates); `overtime_windows` JSON
(list of `{start, end, reason}`); `extra_working_days` JSON; `is_default` bool NN ix (the
snapshot's `default_calendar_id`).

**`materials`** ↔ `Material`. `material_id` PK; `material_name` NN; `material_type` NN; `grade`;
`supplier`; `unit` NN; `available_quantity`, `reserved_quantity`, `incoming_quantity` NN;
`expected_receipt_date`; `minimum_stock` NN; `compatible_machine_ids` JSON; `attributes`;
`synced_at`.

**`tooling`** ↔ `Tooling`. `tooling_id` PK; `tooling_name` NN; `available` NN; `available_from`;
`compatible_machine_ids` JSON; `setup_minutes` NN; `expected_life`; `current_usage`;
`maintenance_status` NN; `attributes`; `synced_at`.

### 8.2 Planner overlays

**`schedule_locks`** ↔ `ScheduleLock`. `lock_id` PK; `lock_type` NN ix; `order_id` ix;
`machine_id` ix; `window_start`, `window_end`; `window_reason` text NN; `sequence_order_ids` JSON;
`reason` text NN; `created_by` NN; `lock_created_at` NN; `active` NN ix; `released_by`;
`released_at`.

**`priority_overrides`** ↔ `PriorityOverride`. `override_id` PK; `order_id` NN ix;
`override_type` NN ix; `value`; `target_machine_id`; `reason` NN; `created_by` NN;
`override_created_at` NN; `expires_at`; `active` NN ix; `released_by`; `released_at`. Index
`ix_priority_overrides_order_active`.

**`expedites`** ↔ `Expedite`. `expedite_id` PK; `order_id` NN ix; `boost_points` NN; `starts_at`
NN; `expires_at` NN ix; `reason` NN; `created_by` NN; `expedite_created_at` NN; `active` NN ix;
`released_by`; `released_at`. Index `ix_expedites_order_active`.

(`customer_rules` above is also planner-owned.)

### 8.3 Configuration versioning

**`system_configs`** ↔ `SystemConfig`. `config_id` PK; `version` int NN **unique**; `is_active` NN
ix; `payload` JSON NN (the whole `SystemConfig`); `created_by`; `reason` text.

**`priority_profiles`** ↔ `PriorityProfile`. `row_id` PK; `profile_id` NN ix; `version` NN;
`name` NN; `is_active` ix; `payload` JSON; `system_config_version` ix; `created_by`; unique
`(profile_id, version)`.

**`scheduling_configs`** ↔ `SchedulingConfig`. Same shape with `config_id`; unique `(config_id,
version)`.

`ConfigRepository.save_new_version(config, created_by, reason, activate=True)` allocates the next
monotonic version, aligns the embedded `priority_profile.version` and `scheduling.version` to it,
writes the three rows, and (when activating) clears `is_active` on every older row.
`activate_version(version)` makes an older version active again without copying it (rollback).
Results reference `(profile_id, profile_version)` and `config_version`, so "which weights produced
yesterday's plan" is answered by a lookup on these tables. (`system_configs` is an addition to the
contract §7 table list; the migration docstring records it.)

### 8.4 Results and audit

**`priority_results`** ↔ `PriorityResult`. `result_id` PK; `run_id` NN ix; `order_id` NN ix;
`score` NN ix; `base_score` NN; `factors` JSON; `adjustments` JSON; `readiness` NN ix; `blocked`
NN; `blocking_reasons` JSON; `risk_level` NN ix; `explanation` text NN; `profile_id` NN;
`profile_version` NN; `computed_at` NN ix; `hours_until_due`; `projected_completion`;
`projected_lateness_hours`; `forced_next` NN; `rank`. Unique `(run_id, order_id)`; indexes
`ix_priority_results_run_rank (run_id, rank)`, `ix_priority_results_order_computed (order_id,
computed_at)`.

**`optimization_runs`** ↔ `OptimizationRunRecord` (spec "for each optimization run store …").
`run_id` PK; `kind` NN ix (priority | schedule | simulation …); `status` NN ix; `started_at` NN
ix; `finished_at`; `orders_considered`, `orders_scheduled`, `orders_blocked` int NN;
`objective_score`, `quality_score`; `algorithm`, `algorithm_version` NN; `profile_id`,
`profile_version`, `config_version`; `input_snapshot_id` FK `input_snapshots` SET NULL;
`triggered_by`; `trigger_reason`, `error_message` text; `metrics` JSON; `warnings` JSON. Index
`ix_optimization_runs_kind_started`.

**`schedule_versions`** ↔ `ScheduleVersionInfo` + `ScheduleResult` header (spec Phase 37).
`schedule_version_id` PK; `version_number` NN **unique** (monotonic); `status` NN ix
(`ScheduleStatus`); `label`; `run_id` FK `optimization_runs` SET NULL ix; `input_snapshot_id` FK
`input_snapshots` SET NULL; `algorithm`, `algorithm_version`, `profile_id` NN; `profile_version`,
`config_version` NN; `generated_by`; `generated_at` NN ix; `horizon_start`, `horizon_end` NN;
`approved_by`, `approved_at`, `published_by`, `published_at`, `superseded_at`; `entry_count` NN;
`metrics` JSON; `quality` JSON (nullable); `unscheduled` JSON; `warnings` JSON; `notes` text.

**`schedule_entries`** ↔ `ScheduleEntry`. `row_id` PK; `schedule_version_id` NN FK
`schedule_versions` CASCADE ix; `entry_id` NN; `machine_id` NN ix; `order_id` NN ix;
`operation_id` NN; `sequence_on_machine` NN; `setup_start` NN; `start` NN ix; `end` NN ix;
`setup_minutes`, `run_minutes`, `quantity`, `priority_score` NN; `placement_reason` text NN;
`is_last_operation` NN; `expected_completion`; `due_date`; `expected_lateness_hours`; `locked` NN;
`batch_key`; `setup_family`; `material_id`; `customer_id`. Unique `(schedule_version_id,
entry_id)`; indexes `ix_schedule_entries_version_machine_start (schedule_version_id, machine_id,
start)`, `ix_schedule_entries_version_order (schedule_version_id, order_id)`.

**`input_snapshots`** ↔ `PlanningSnapshot` (compressed). `snapshot_id` PK; `as_of` NN ix; `source`
NN; `codec` NN (`gzip+json/v1`); `payload` LargeBinary NN; `size_bytes` NN; `sha256` NN;
`summary` JSON (entity counts); `created_by`.

**`audit_log`** ↔ `AuditEntry` (spec Phase 9/22). `audit_id` PK; `user_id` NN ix; `timestamp` NN
ix; `entity_type` NN ix; `entity_id` NN; `action` NN ix; `previous_value` JSON; `new_value` JSON;
`reason` text; `request_id`; `details` JSON. Indexes `ix_audit_log_entity (entity_type,
entity_id)`, `ix_audit_log_user_time (user_id, timestamp)`.

**`alerts`** ↔ `AlertRecord`. `alert_id` PK; `dedupe_key` NN **unique**; `alert_type` NN ix;
`severity` NN ix; `title` NN; `reason`, `recommended_action` text NN; `raised_at` NN ix;
`last_seen_at` NN; `occurrences` NN; `order_id` ix; `machine_id` ix; `entity_ref`; `details`
JSON; `active` NN ix; `acknowledged_by`, `acknowledged_at`, `resolved_at`. Indexes
`ix_alerts_active_severity`, `ix_alerts_active_raised`.

**`data_quality_issues`** ↔ `DataQualityIssueRecord`. `issue_id` PK; `run_id` NN ix;
`detected_at` NN; `code` NN ix; `severity` NN ix; `entity_type` NN; `entity_id` NN ix; `message`
text NN; `field_name`; `recommendation` text; `details` JSON. Indexes
`ix_data_quality_issues_run_code`, `ix_data_quality_issues_run_entity`.

**`sync_runs`** ↔ `SyncRunRecord`. `run_id` PK; `mode` NN ix; `status` NN ix (running |
completed | failed); `connector` NN; `started_at` NN ix; `finished_at`; `since` (watermark);
`records_fetched` JSON; `records_upserted` JSON; `issues_count` NN; `triggered_by`;
`error_message` text; `details` JSON (issue codes, reconciliation report).

**`users`** ↔ `UserRecord`. `user_id` PK; `username` NN **unique**; `password_hash` NN (bcrypt);
`role` NN ix; `display_name` NN; `email`; `active` NN; `last_login_at`.

Spec "DATABASE DESIGN PRINCIPLES" indexes: order status (`ix_orders_order_status`), due date
(`ix_orders_due_date`), customer (`ix_orders_customer_id`), machine (`ix_operations_machine_id`,
`ix_schedule_entries_machine_id`), process (`ix_orders_process_type`,
`ix_operations_operation_type`), priority (`ix_priority_results_score`, `_run_rank`), production
status (`ix_orders_production_status`), schedule date (`ix_schedule_entries_start`, `_end`,
`_version_machine_start`). Materialised analytics views are not created (analytics are computed
from snapshots per request; `docs/REQUIREMENTS.md` F-17).

---

## 9. Snapshot codec (`app/db/snapshot_codec.py`)

`encode_snapshot(snapshot) -> bytes` = `gzip(json)` of `to_jsonable(snapshot)` with sorted keys,
compact separators, enums as values, aware datetimes as ISO-8601 UTC, sets as sorted lists,
private (`_`-prefixed) fields skipped, `mtime=0` so identical snapshots give identical bytes;
`payload_digest` = SHA-256 of the payload (stored in `input_snapshots.sha256`).
`decode_snapshot(payload, codec="gzip+json/v1")` re-instantiates every dataclass by its type hints
(`decode_dataclass`): sets stay sets, tuples tuples, enums re-created, naive datetimes re-tagged
UTC, then `rebuild_indexes()`. Unknown codecs and corrupt payloads raise `ValidationError`; the
codec name is stored next to the payload so the format can evolve. The same `to_jsonable` encodes
factor/adjustment lists and metrics into the JSON columns.

---

## 10. ER diagram

```
             ┌──────────────┐ 1   n ┌────────────────┐
             │  customers   │──────>│ customer_rules │ (FK, planner)
             └──────┬───────┘       └────────────────┘
                    ┆ customer_id (no FK)
             ┌──────▼───────┐ 1   n ┌────────────────┐
             │    orders    │──────>│   operations   │ (FK CASCADE)
             │ due_date idx │       │ machine_id ┆   │
             └──────┬───────┘       │ material_id┆   │
      order_id ┆    ┆ required_*    └──────┬─────┆───┘
   (no FK)     ┆    ┆ (no FK)              ┆     ┆ (no FK)
 ┌─────────────┴──┐ ┆                ┌─────▼─────┴──┐ 1   n ┌──────────────────┐
 │priority_overrides│ ┆              │   machines   │──────>│ machine_downtime │ (FK CASCADE)
 │expedites       │ ┆                │ calendar_id┆ │       └──────────────────┘
 │schedule_locks  │ ┆                └────────────┆─┘       ┌──────────────┐ ┌─────────┐
 └────────────────┘ ┆                             ┆·······> │calendar_specs│ │materials│ │tooling│
                    ┆                                       └──────────────┘ └─────────┘ └───────┘
 ┌───────────────┐  ┆      ┌─────────────────┐ 1  n ┌───────────────────┐
 │input_snapshots│<─────── │optimization_runs│<─────│ schedule_versions │ (FK SET NULL both)
 │ payload gzip  │<───────────────────────────────── │ version_number uq │
 └───────────────┘  ┆      └────────┬────────┘      └─────────┬─────────┘ 1
                    ┆      run_id ┆ (no FK)                   │ n (FK CASCADE)
             ┌──────▼──────────┐  ┆                 ┌─────────▼─────────┐
             │priority_results │<─┘                 │ schedule_entries  │ machine_id/order_id (no FK)
             │ uq(run,order)   │                    │ uq(version,entry) │
             └─────────────────┘                    └───────────────────┘

 ┌──────────────┐ ┌──────────────────┐ ┌────────────────────┐   ┌──────────┐ ┌───────────┐ ┌───────┐
 │system_configs│ │priority_profiles │ │ scheduling_configs │   │audit_log │ │  alerts   │ │ users │
 │ version uq   │ │uq(profile_id,ver)│ │ uq(config_id,ver)  │   └──────────┘ └───────────┘ └───────┘
 └──────────────┘ └──────────────────┘ └────────────────────┘   ┌────────────────────┐ ┌──────────┐
   (linked by system_config_version, no FK)                     │data_quality_issues │ │sync_runs │
                                                                └────────────────────┘ └──────────┘
   ──> foreign key      ┆ / ······> reference by id without foreign key (ERP-owned target)
```

---

## 11. Spec Phase 3 field map

| Spec entity.field | Lives in | Notes |
|---|---|---|
| CUSTOMER: customer_id, customer_name, customer_category, customer_priority, strategic_customer_flag, annual_revenue, customer_revenue, customer_profitability, customer_service_level, escalation_level, historical_on_time_delivery, account_manager, active/inactive | `Customer.<same name>` / `customers.<same>` (`active`) | |
| payment/risk information | `Customer.payment_risk` | enum |
| preferred_delivery expectations | `Customer.preferred_delivery_expectation` | free text |
| ORDER: order_id, order_line_id, customer_id, part_id, order_date, received_date, requested/promised/revised_delivery_date, quantity, completed_quantity, cancelled_quantity, order_status, production_status, material_status, quality_status, shipping_status, order_value, estimated_cost, estimated_margin, actual_margin, process_type, manufacturing_route, machine_group, tooling_requirement, customer_priority, technical_priority, commercial_priority, special_instructions | `Order.<same>` / `orders.<same>` | |
| pending_quantity | `Order.pending_quantity` (property) | derived; not stored |
| priority | `Order.erp_priority` | ERP code |
| current_priority | `PriorityResult.score` / `priority_results.score` | derived per run |
| required_machine, required_material | `Order.required_machine_id`, `required_material_id` | |
| estimated_setup_time, estimated_cycle_time, estimated_total_production_time | `Order.estimated_setup_minutes`, `estimated_cycle_minutes_per_unit`, `estimated_total_production_minutes` | minutes |
| lateness_penalty | `Order.lateness_penalty_per_day` | per day |
| SLA | `Order.sla_hours` (also `Customer.sla_hours`, `CustomerRule.sla_hours`) | hours |
| ORDER OPERATION: operation_id, order_id, sequence, operation_type, machine_group, machine_id, quantity, completed_quantity, operation_status, operator_requirement, quality_requirement, estimated_start, estimated_end, actual_start, actual_end | `Operation.<same>` / `operations.<same>` | |
| setup_time, cycle_time | `Operation.setup_minutes`, `cycle_minutes_per_unit` (+ `machine_cycle_minutes` per machine) | |
| pending_quantity | `Operation.pending_quantity` (property) | derived |
| prerequisite_operation | `Operation.prerequisite_operation_id` | |
| material_requirement | `Operation.material_id` + `material_quantity_per_unit` | |
| tooling_requirement | `Operation.tooling_ids` | |
| MACHINE: machine_id, machine_name, machine_type, process_type, machine_group, location, status, efficiency, utilization, planned_downtime, unplanned_downtime, compatible_materials, compatible_processes, tooling_configuration, setup_requirements | `Machine.<same>` / `machines` (+ `machine_downtime` rows) | |
| availability | `Machine.status` + `available_from` + downtime | |
| capacity | `Machine.capacity_hours_per_day` (reporting); real capacity from the calendar | |
| working_hours, shifts | `Machine.calendar_id` → `CalendarSpec.shifts` | |
| maintenance_schedule | `Machine.maintenance_windows` (`machine_downtime.kind = maintenance`) | |
| compatible_part_sizes | `Machine.max_part_size_mm` (+ order `attributes.part_size_mm`) | |
| MATERIAL: material_id, material_name, material_type, grade, supplier, available_quantity, reserved_quantity, incoming_quantity, expected_receipt_date, minimum_stock | `Material.<same>` | |
| material_machine_compatibility | `Material.compatible_machine_ids` and `Machine.compatible_materials` | checked from both sides |
| TOOLING: tooling_id, tooling_name, expected_life, current_usage | `Tooling.<same>` | |
| availability | `Tooling.available` + `available_from` | |
| machine_compatibility | `Tooling.compatible_machine_ids` | |
| setup_time | `Tooling.setup_minutes` | |
| maintenance status | `Tooling.maintenance_status` | |
| PRODUCTION STATUS (15 values) | `OrderStatus` | |
| Readiness (ready / waiting …) | `ReadinessState` (`PriorityResult.readiness`, `priority_results.readiness`) | derived by the constraint engine |
