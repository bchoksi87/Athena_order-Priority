"""ERP synchronisation use case (docs/DESIGN_CONTRACT.md §8, spec Phase 2).

``SyncService.run(mode)`` = fetch -> normalise -> validate -> upsert -> reconcile
-> record a ``sync_runs`` row. The connector is **read-only**: this service only
ever calls its ``fetch_*`` methods; nothing is written back to the ERP.

Transactions
------------
The service owns the transaction boundaries of the session it is given: the
``running`` row is committed before any data is touched, the data upserts are
committed together with the ``completed`` row, and on failure the partial
upserts are rolled back before the ``failed`` row is committed. A failed run
therefore always leaves an auditable trace and never a half-written dataset.

Incremental mode
----------------
The ``since`` watermark is the ``started_at`` of the most recent *completed*
run (not its ``finished_at``), so records changed while that run was fetching
are picked up again rather than lost. Without a completed run an incremental
sync degrades to a full fetch (logged and recorded in the run details).
"""

from __future__ import annotations

import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from functools import partial
from typing import Any, Literal

import structlog
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.errors import AppError, IntegrationError, NotFoundError, ValidationError
from app.core.ids import new_id
from app.db.records import SyncRunRecord
from app.db.repositories import (
    CalendarRepository,
    CustomerRepository,
    MachineRepository,
    MaterialRepository,
    OrderRepository,
    SyncRunRepository,
    ToolingRepository,
)
from app.domain.enums import SyncMode
from app.domain.models import CalendarSpec, Customer, Machine, Material, Operation, Order, Tooling
from app.integration.connector import ERPConnector, RawRecord
from app.integration.normalizer import (
    NormalizationIssue,
    NormalizationResult,
    Normalizer,
    ProductionStatusUpdate,
)
from app.integration.parsers import parse_bool
from app.integration.reconciliation import ReconciliationReport, ReconciliationThresholds, reconcile
from app.integration.retry import RetryPolicy, SleepFn, call_with_retry

log = structlog.get_logger(__name__)

SYNC_STATUS_RUNNING = "running"
SYNC_STATUS_COMPLETED = "completed"
SYNC_STATUS_FAILED = "failed"

#: Connector methods in dependency order (masters before orders, progress last).
_FETCHERS: tuple[tuple[str, str], ...] = (
    ("customer", "fetch_customers"),
    ("material", "fetch_materials"),
    ("machine", "fetch_machines"),
    ("tooling", "fetch_tooling"),
    ("calendar", "fetch_calendars"),
    ("order", "fetch_orders"),
    ("operation", "fetch_operations"),
    ("production_status", "fetch_production_status"),
)

SyncStage = Literal["fetch", "normalize", "validate", "upsert"]


class SyncIssueCode(StrEnum):
    """Referential problems found while validating a normalised batch against the store."""

    UNKNOWN_ORDER = "unknown_order"  # operation for an order neither fetched nor stored (skipped)
    UNKNOWN_OPERATION = "unknown_operation"  # progress report for an unknown operation (skipped)
    UNKNOWN_CUSTOMER = "unknown_customer"  # order references a customer we do not know (kept)
    UNKNOWN_MACHINE = "unknown_machine"  # operation assigned to an unknown machine (kept)
    UNKNOWN_CALENDAR = "unknown_calendar"  # machine references an unknown calendar (kept)


@dataclass(slots=True)
class SyncIssue:
    """One problem detected during a run; normalisation and validation issues share this shape."""

    stage: SyncStage
    entity: str
    external_id: str
    field: str | None
    code: str
    message: str

    @classmethod
    def from_normalization(cls, issue: NormalizationIssue) -> SyncIssue:
        return cls("normalize", issue.entity, issue.external_id, issue.field, issue.code.value, issue.message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "entity": self.entity,
            "external_id": self.external_id,
            "field": self.field,
            "code": self.code,
            "message": self.message,
        }


@dataclass(slots=True, frozen=True)
class SyncOptions:
    """Deployment tuning for a sync run (not business rules)."""

    retry: RetryPolicy = field(default_factory=RetryPolicy)
    reconciliation: ReconciliationThresholds = field(default_factory=ReconciliationThresholds)
    #: Full mode only: delete stored orders the ERP no longer serves.
    prune_missing_orders: bool = False
    #: How many issue records are kept in ``sync_runs.details`` (the count is always exact).
    max_stored_issues: int = 200
    triggered_by: str | None = None


@dataclass(slots=True)
class SyncRunSummary:
    """Outcome of one ``SyncService.run`` call (mirrors the persisted ``sync_runs`` row)."""

    run_id: str
    mode: SyncMode
    status: str
    connector: str
    started_at: datetime
    since: datetime | None = None
    finished_at: datetime | None = None
    duration_seconds: float = 0.0
    records_fetched: dict[str, int] = field(default_factory=dict)
    records_upserted: dict[str, int] = field(default_factory=dict)
    issues: list[SyncIssue] = field(default_factory=list)
    reconciliation: ReconciliationReport | None = None
    stored_totals: dict[str, int] = field(default_factory=dict)
    pruned_orders: int = 0
    watermark_source: str = "none"
    error_message: str | None = None

    @property
    def issues_count(self) -> int:
        return len(self.issues)

    @property
    def succeeded(self) -> bool:
        return self.status == SYNC_STATUS_COMPLETED

    def issue_counts(self) -> dict[str, int]:
        return dict(sorted(Counter(i.code for i in self.issues).items()))

    def to_dict(self, *, max_issues: int | None = None) -> dict[str, Any]:
        shown = self.issues if max_issues is None else self.issues[:max_issues]
        return {
            "run_id": self.run_id,
            "mode": self.mode.value,
            "status": self.status,
            "connector": self.connector,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "since": self.since.isoformat() if self.since else None,
            "duration_seconds": round(self.duration_seconds, 3),
            "records_fetched": dict(self.records_fetched),
            "records_upserted": dict(self.records_upserted),
            "issues_count": self.issues_count,
            "issue_counts": self.issue_counts(),
            "issues": [i.to_dict() for i in shown],
            "issues_truncated": len(shown) < len(self.issues),
            "reconciliation": self.reconciliation.to_dict() if self.reconciliation else None,
            "stored_totals": dict(self.stored_totals),
            "pruned_orders": self.pruned_orders,
            "watermark_source": self.watermark_source,
            "error_message": self.error_message,
        }


@dataclass(slots=True)
class _Batch:
    """Normalised entities of one run, before persistence."""

    customers: list[Customer] = field(default_factory=list)
    materials: list[Material] = field(default_factory=list)
    machines: list[Machine] = field(default_factory=list)
    tooling: list[Tooling] = field(default_factory=list)
    calendars: list[CalendarSpec] = field(default_factory=list)
    default_calendar_id: str | None = None
    orders: list[Order] = field(default_factory=list)
    operations: list[Operation] = field(default_factory=list)
    #: Progress reports whose operation was not part of this batch (applied against the store).
    deferred_progress: list[ProductionStatusUpdate] = field(default_factory=list)
    progress_applied_in_batch: int = 0


class SyncService:
    """Synchronise the local store from an :class:`ERPConnector` (read-only)."""

    def __init__(
        self,
        session: Session,
        connector: ERPConnector,
        clock: Clock,
        *,
        normalizer: Normalizer | None = None,
        options: SyncOptions | None = None,
        sleep: SleepFn = time.sleep,
    ) -> None:
        self._session = session
        self._connector = connector
        self._clock = clock
        self._normalizer = normalizer or Normalizer()
        self._options = options or SyncOptions()
        self._sleep = sleep
        self._customers = CustomerRepository(session)
        self._materials = MaterialRepository(session)
        self._machines = MachineRepository(session)
        self._tooling = ToolingRepository(session)
        self._calendars = CalendarRepository(session)
        self._orders = OrderRepository(session)
        self._runs = SyncRunRepository(session)

    @property
    def connector_name(self) -> str:
        return str(getattr(self._connector, "name", "connector"))

    # ------------------------------------------------------------------ run
    def run(self, mode: SyncMode | str = SyncMode.FULL) -> SyncRunSummary:
        """Execute one synchronisation run and return its summary.

        Raises :class:`IntegrationError` (connector failed after retries or an
        unexpected error) or another :class:`AppError`; in every case the run is
        recorded as ``failed`` first.
        """
        sync_mode = self._parse_mode(mode)
        started_at = self._clock.now()
        timer = time.perf_counter()
        run_id = new_id("sync")
        since, watermark_source = self._watermark(sync_mode)
        summary = SyncRunSummary(
            run_id=run_id,
            mode=sync_mode,
            status=SYNC_STATUS_RUNNING,
            connector=self.connector_name,
            started_at=started_at,
            since=since,
            watermark_source=watermark_source,
        )
        record = SyncRunRecord(
            run_id=run_id,
            mode=sync_mode,
            status=SYNC_STATUS_RUNNING,
            started_at=started_at,
            connector=self.connector_name,
            since=since,
            triggered_by=self._options.triggered_by,
            details={"watermark_source": watermark_source},
        )
        self._runs.start(record)
        self._session.commit()

        with structlog.contextvars.bound_contextvars(run_id=run_id, sync_mode=sync_mode.value):
            log.info(
                "sync.started",
                connector=self.connector_name,
                since=since.isoformat() if since else None,
                watermark_source=watermark_source,
            )
            try:
                raw = self._fetch(since, summary)
                batch = self._normalize(raw, summary)
                self._validate(batch, summary)
                self._upsert(batch, summary, sync_mode, started_at)
                summary.reconciliation = reconcile(
                    summary.records_fetched, summary.records_upserted, self._options.reconciliation
                )
                if sync_mode is SyncMode.FULL:
                    summary.stored_totals = self._stored_totals()
                self._session.flush()
            except Exception as exc:
                self._session.rollback()
                self._finish(summary, record, SYNC_STATUS_FAILED, timer, error=str(exc))
                log.error("sync.failed", error=str(exc), error_type=type(exc).__name__)
                if isinstance(exc, AppError):
                    raise
                raise IntegrationError(
                    f"sync run {run_id} failed: {exc}", details={"run_id": run_id, "stage": "unknown"}
                ) from exc

            self._finish(summary, record, SYNC_STATUS_COMPLETED, timer)
            log.info(
                "sync.completed",
                duration_seconds=round(summary.duration_seconds, 3),
                fetched=summary.records_fetched,
                upserted=summary.records_upserted,
                issues=summary.issues_count,
                reconciliation=summary.reconciliation.status if summary.reconciliation else None,
            )
        return summary

    # ---------------------------------------------------------------- stages
    def _fetch(self, since: datetime | None, summary: SyncRunSummary) -> dict[str, list[RawRecord]]:
        raw: dict[str, list[RawRecord]] = {}
        for entity, method_name in _FETCHERS:
            fetch = getattr(self._connector, method_name)
            records = call_with_retry(
                f"{self.connector_name}.{method_name}",
                partial(fetch, since),
                self._options.retry,
                sleep=self._sleep,
                details={"entity": entity, "run_id": summary.run_id},
            )
            raw[entity] = list(records)
            summary.records_fetched[entity] = len(raw[entity])
        log.info("sync.fetched", **summary.records_fetched)
        return raw

    def _normalize(self, raw: dict[str, list[RawRecord]], summary: SyncRunSummary) -> _Batch:
        n = self._normalizer
        results: dict[str, NormalizationResult[Any]] = {
            "customer": n.normalize_customers(raw.get("customer", [])),
            "material": n.normalize_materials(raw.get("material", [])),
            "machine": n.normalize_machines(raw.get("machine", [])),
            "tooling": n.normalize_tooling(raw.get("tooling", [])),
            "calendar": n.normalize_calendars(raw.get("calendar", [])),
            "order": n.normalize_orders(raw.get("order", [])),
            "operation": n.normalize_operations(raw.get("operation", [])),
            "production_status": n.normalize_production_status(raw.get("production_status", [])),
        }
        for result in results.values():
            summary.issues.extend(SyncIssue.from_normalization(i) for i in result.issues)

        batch = _Batch(
            customers=results["customer"].items,
            materials=results["material"].items,
            machines=results["machine"].items,
            tooling=results["tooling"].items,
            calendars=results["calendar"].items,
            orders=results["order"].items,
            operations=results["operation"].items,
        )
        batch.default_calendar_id = _default_calendar_id(
            raw.get("calendar", []), [c.calendar_id for c in batch.calendars]
        )
        # Progress reports for operations in this batch are applied in memory (chronologically);
        # the rest are applied against the stored operations during upsert.
        by_id = {op.operation_id: op for op in batch.operations}
        for update in sorted(
            results["production_status"].items, key=lambda u: (u.reported_at, u.operation_id)
        ):
            operation = by_id.get(update.operation_id)
            if operation is None:
                batch.deferred_progress.append(update)
                continue
            update.apply(operation)
            batch.progress_applied_in_batch += 1
        log.info(
            "sync.normalized",
            issues=summary.issues_count,
            progress_in_batch=batch.progress_applied_in_batch,
            progress_deferred=len(batch.deferred_progress),
        )
        return batch

    def _validate(self, batch: _Batch, summary: SyncRunSummary) -> None:
        """Referential checks against batch + store. Only operations without an order are dropped."""
        issues = summary.issues

        customer_ids = {c.customer_id for c in batch.customers}
        wanted_customers = {o.customer_id for o in batch.orders} - customer_ids
        known_customers = set(self._customers.get_many(wanted_customers)) if wanted_customers else set()
        for order in batch.orders:
            if order.customer_id not in customer_ids and order.customer_id not in known_customers:
                issues.append(
                    _issue(
                        SyncIssueCode.UNKNOWN_CUSTOMER,
                        "order",
                        order.order_id,
                        "customer_id",
                        f"customer {order.customer_id!r} is not known",
                    )
                )

        order_ids = {o.order_id for o in batch.orders}
        wanted_orders = {op.order_id for op in batch.operations} - order_ids
        known_orders = set(self._orders.get_many(wanted_orders)) if wanted_orders else set()
        kept: list[Operation] = []
        for op in batch.operations:
            if op.order_id in order_ids or op.order_id in known_orders:
                kept.append(op)
                continue
            issues.append(
                _issue(
                    SyncIssueCode.UNKNOWN_ORDER,
                    "operation",
                    op.operation_id,
                    "order_id",
                    f"order {op.order_id!r} is neither in this batch nor stored; operation skipped",
                )
            )
        batch.operations = kept

        machine_ids = {m.machine_id for m in batch.machines}
        referenced = {op.machine_id for op in kept if op.machine_id} - machine_ids
        if referenced:
            machine_ids |= {m.machine_id for m in self._machines.list_all()}
        for op in kept:
            if op.machine_id and op.machine_id not in machine_ids:
                issues.append(
                    _issue(
                        SyncIssueCode.UNKNOWN_MACHINE,
                        "operation",
                        op.operation_id,
                        "machine_id",
                        f"machine {op.machine_id!r} is not known",
                    )
                )

        calendar_ids = {c.calendar_id for c in batch.calendars}
        referenced_calendars = {m.calendar_id for m in batch.machines if m.calendar_id} - calendar_ids
        if referenced_calendars:
            calendar_ids |= {c.calendar_id for c in self._calendars.list_all()}
        for machine in batch.machines:
            if machine.calendar_id and machine.calendar_id not in calendar_ids:
                issues.append(
                    _issue(
                        SyncIssueCode.UNKNOWN_CALENDAR,
                        "machine",
                        machine.machine_id,
                        "calendar_id",
                        f"calendar {machine.calendar_id!r} is not known",
                    )
                )
        log.info("sync.validated", issues=summary.issues_count, operations_kept=len(kept))

    def _upsert(self, batch: _Batch, summary: SyncRunSummary, mode: SyncMode, synced_at: datetime) -> None:
        upserted = summary.records_upserted
        upserted["customer"] = self._customers.upsert(batch.customers, synced_at=synced_at)
        upserted["material"] = self._materials.upsert(batch.materials, synced_at=synced_at)
        upserted["machine"] = self._machines.upsert(batch.machines, synced_at=synced_at)
        upserted["tooling"] = self._tooling.upsert(batch.tooling, synced_at=synced_at)
        upserted["calendar"] = self._calendars.upsert(batch.calendars)
        if batch.default_calendar_id and self._calendars.get_default_id() != batch.default_calendar_id:
            self._calendars.set_default(batch.default_calendar_id)

        # Orders with their own operations. In full mode the ERP routing is authoritative
        # (operations missing from the feed are deleted); incremental slices only carry
        # changed operations, so nothing is removed there.
        fetched_ids = {o.order_id for o in batch.orders}
        own_ops = [op for op in batch.operations if op.order_id in fetched_ids]
        upserted["order"] = self._orders.upsert(
            batch.orders, own_ops, synced_at=synced_at, replace_operations=mode is SyncMode.FULL
        )
        operations_written = len(own_ops)

        # Changed operations of orders that were not re-fetched (validated to exist in the store).
        foreign: dict[str, list[Operation]] = defaultdict(list)
        for op in batch.operations:
            if op.order_id not in fetched_ids:
                foreign[op.order_id].append(op)
        if foreign:
            stored = self._orders.get_many(foreign)
            ops = [op for group in foreign.values() for op in group]
            self._orders.upsert(stored.values(), ops, synced_at=synced_at, replace_operations=False)
            operations_written += len(ops)
        upserted["operation"] = operations_written

        upserted["production_status"] = batch.progress_applied_in_batch + self._apply_deferred_progress(
            batch.deferred_progress, summary, synced_at
        )

        if mode is SyncMode.FULL and self._options.prune_missing_orders:
            summary.pruned_orders = self._orders.delete_missing(fetched_ids)
            log.info("sync.pruned_orders", count=summary.pruned_orders)
        log.info("sync.upserted", **upserted)

    def _apply_deferred_progress(
        self, updates: list[ProductionStatusUpdate], summary: SyncRunSummary, synced_at: datetime
    ) -> int:
        applied = 0
        for update in updates:  # already chronological
            try:
                operation = self._orders.get_operation(update.operation_id)
            except NotFoundError:
                summary.issues.append(
                    _issue(
                        SyncIssueCode.UNKNOWN_OPERATION,
                        "production_status",
                        update.operation_id,
                        "operation_id",
                        f"operation {update.operation_id!r} is not stored; report skipped",
                    )
                )
                continue
            update.apply(operation)
            order = self._orders.get(operation.order_id)
            self._orders.upsert([order], [operation], synced_at=synced_at, replace_operations=False)
            applied += 1
        return applied

    # -------------------------------------------------------------- helpers
    def _watermark(self, mode: SyncMode) -> tuple[datetime | None, str]:
        if mode is SyncMode.FULL:
            return None, "none"
        latest = self._runs.latest_successful_started_at()
        if latest is None:
            log.info("sync.watermark_missing", reason="no completed run; incremental sync fetches everything")
            return None, "none"
        return latest.started_at, latest.run_id

    def _stored_totals(self) -> dict[str, int]:
        return {
            "customer": self._customers.count(),
            "order": self._orders.count(),
            "machine": len(self._machines.list_all()),
            "material": len(self._materials.list_all()),
            "tooling": len(self._tooling.list_all()),
            "calendar": len(self._calendars.list_all()),
        }

    def _finish(
        self,
        summary: SyncRunSummary,
        record: SyncRunRecord,
        status: str,
        timer: float,
        *,
        error: str | None = None,
    ) -> None:
        summary.status = status
        summary.finished_at = self._clock.now()
        summary.duration_seconds = time.perf_counter() - timer
        summary.error_message = error
        record.status = status
        record.finished_at = summary.finished_at
        record.records_fetched = dict(summary.records_fetched)
        record.records_upserted = dict(summary.records_upserted)
        record.issues_count = summary.issues_count
        record.error_message = error
        record.details = summary.to_dict(max_issues=self._options.max_stored_issues)
        self._runs.save(record)
        self._session.commit()

    @staticmethod
    def _parse_mode(mode: SyncMode | str) -> SyncMode:
        try:
            return SyncMode(mode)
        except ValueError as exc:
            raise ValidationError(
                f"unknown sync mode {mode!r}", details={"allowed": [m.value for m in SyncMode]}
            ) from exc


def _issue(code: SyncIssueCode, entity: str, external_id: str, field_name: str, message: str) -> SyncIssue:
    return SyncIssue("validate", entity, external_id, field_name, code.value, message)


def _default_calendar_id(raw_calendars: list[RawRecord], known_ids: list[str]) -> str | None:
    """The calendar flagged ``IS_DEFAULT`` by the ERP (first known one otherwise)."""
    for record in raw_calendars:
        flag = record.payload.get("IS_DEFAULT") if isinstance(record.payload, dict) else None
        try:
            if flag is not None and parse_bool(flag) and record.external_id in known_ids:
                return record.external_id
        except ValueError:
            continue
    return known_ids[0] if known_ids else None


__all__ = [
    "SYNC_STATUS_COMPLETED",
    "SYNC_STATUS_FAILED",
    "SYNC_STATUS_RUNNING",
    "SyncIssue",
    "SyncIssueCode",
    "SyncOptions",
    "SyncRunSummary",
    "SyncService",
    "SyncStage",
]
