"""OrderQueryService: read models for the priority queue and order detail views.

Orders are joined in memory with the latest stored :class:`PriorityResult`
(score, rank, risk, readiness, explanation), the entries of the *current*
schedule version (latest PUBLISHED, else APPROVED, else DRAFT — the version is
reported so callers know which) and the active planner overlays. Filters that
only exist on joined data (risk, readiness, scheduled machine, effective hold)
and non-due-date sorts are applied after the join, so the repository fetches
the database-filtered set first and the page is cut last.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.orm import Session

from app.core.clock import Clock, ensure_utc
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.db.records import AuditEntry, ScheduleVersionInfo
from app.db.repositories.base import MAX_PAGE_SIZE as REPO_MAX_PAGE_SIZE
from app.db.repositories.customers import CustomerRepository
from app.db.repositories.data_quality import DataQualityRepository
from app.db.repositories.orders import OrderFilters, OrderRepository
from app.db.repositories.overlays import ExpediteRepository, LockRepository, OverrideRepository
from app.db.repositories.priority import PriorityResultRepository
from app.db.repositories.resources import MaterialRepository, ToolingRepository
from app.db.repositories.schedule import ScheduleRepository
from app.domain.enums import OrderStatus, ProcessType, ReadinessState, RiskLevel
from app.domain.models import (
    Customer,
    Expedite,
    Material,
    Operation,
    Order,
    PriorityOverride,
    ScheduleLock,
    Tooling,
)
from app.domain.results import DataQualityIssue, Penalty, PriorityResult, ScheduleEntry
from app.engines.constraints.base import ConstraintContext
from app.engines.constraints.hard import pinned_machine_for_order
from app.engines.constraints.readiness import next_operation
from app.engines.constraints.registry import default_constraint_engine
from app.engines.priority.explanation import explanation_lines
from app.services.audit_service import ENTITY_LOCK, ENTITY_ORDER, AuditService
from app.services.base import PagedResult, Pagination, Service
from app.services.snapshot_service import SnapshotService, effective_hold

log = structlog.get_logger(__name__)

SORT_KEYS: tuple[str, ...] = (
    "priority",
    "rank",
    "due_date",
    "customer",
    "order_value",
    "margin",
    "risk",
    "status",
    "quantity",
    "order_id",
)
_RISK_ORDER = {RiskLevel.CRITICAL: 0, RiskLevel.HIGH: 1, RiskLevel.MEDIUM: 2, RiskLevel.LOW: 3}
_FAR_FUTURE = datetime.max.replace(tzinfo=UTC)


@dataclass(slots=True)
class OrderListFilters:
    customer_id: str | None = None
    statuses: list[OrderStatus] = field(default_factory=list)
    machine_group: str | None = None
    process_type: ProcessType | None = None
    machine_id: str | None = None
    due_from: datetime | None = None
    due_to: datetime | None = None
    risk: RiskLevel | None = None
    readiness: ReadinessState | None = None
    search: str | None = None
    open_only: bool = True
    on_hold: bool | None = None


@dataclass(slots=True)
class OrderScheduleInfo:
    version_number: int
    status: str
    machine_id: str | None
    start: datetime | None
    end: datetime | None
    expected_completion: datetime | None
    expected_lateness_hours: float | None
    entries: list[ScheduleEntry] = field(default_factory=list)


@dataclass(slots=True)
class OrderListItem:
    order: Order
    customer: Customer | None
    priority: PriorityResult | None
    schedule: OrderScheduleInfo | None
    on_hold: bool
    hold_reason: str | None


@dataclass(slots=True)
class OrderDetail:
    order: Order
    customer: Customer | None
    priority: PriorityResult | None
    breakdown: list[dict[str, Any]]
    schedule: OrderScheduleInfo | None
    operations: list[Operation]
    materials: list[Material]
    tooling: list[Tooling]
    production_minutes: float | None
    dependencies: list[str]
    dependents: list[str]
    overrides: list[PriorityOverride]
    expedites: list[Expedite]
    locks: list[ScheduleLock]
    audit: list[AuditEntry]
    data_quality_issues: list[DataQualityIssue]
    on_hold: bool
    hold_reason: str | None


@dataclass(slots=True)
class ExplanationData:
    result: PriorityResult
    lines: list[dict[str, Any]]


@dataclass(slots=True)
class MachineOption:
    machine_id: str
    machine_name: str
    rank: int
    recommended: bool
    setup_minutes: float | None
    run_minutes: float | None
    soft_cost: float
    penalties: list[Penalty]
    reasons: list[str]


@dataclass(slots=True)
class MachineOptions:
    order_id: str
    operation_id: str
    synthetic_operation: bool
    source: str
    evaluated_at: datetime
    recommended_machine_id: str | None
    scheduled_machine_id: str | None
    pinned_machine_id: str | None
    pinned_by: str | None
    eligible: list[MachineOption]
    rejected: dict[str, list[str]]


class OrderQueryService(Service):
    def __init__(self, session: Session, clock: Clock, snapshots: SnapshotService | None = None) -> None:
        super().__init__(session, clock)
        self._snapshots = snapshots or SnapshotService(session, clock)
        self._orders = OrderRepository(session)
        self._customers = CustomerRepository(session)
        self._results = PriorityResultRepository(session)
        self._schedule = ScheduleRepository(session)
        self._overrides = OverrideRepository(session)
        self._expedites = ExpediteRepository(session)
        self._locks = LockRepository(session)
        self._materials = MaterialRepository(session)
        self._tooling = ToolingRepository(session)
        self._dq = DataQualityRepository(session)
        self._audit = AuditService(session, clock)

    # ----------------------------------------------------------------- list
    def list_orders(
        self,
        filters: OrderListFilters | None,
        pagination: Pagination,
        *,
        sort: str = "priority",
        descending: bool | None = None,
    ) -> PagedResult[OrderListItem]:
        filters = filters or OrderListFilters()
        if sort not in SORT_KEYS:
            raise ValidationError(f"sort must be one of {', '.join(SORT_KEYS)}", details={"sort": sort})
        now = self.now()
        orders = self._fetch_all(
            OrderFilters(
                statuses=tuple(filters.statuses),
                customer_id=filters.customer_id,
                machine_group=filters.machine_group,
                process_type=filters.process_type,
                due_from=ensure_utc(filters.due_from) if filters.due_from else None,
                due_to=ensure_utc(filters.due_to) if filters.due_to else None,
                search=filters.search,
                open_only=filters.open_only,
            )
        )
        ids = [o.order_id for o in orders]
        customers = self._customers.get_many({o.customer_id for o in orders})
        results = self._results.latest_for_orders(ids)
        version, by_order = self._current_schedule()
        overrides = self._overrides.list_active(now)

        items: list[OrderListItem] = []
        for order in orders:
            on_hold, hold_reason, _ = effective_hold(order, overrides, now)
            schedule = _schedule_info(version, by_order.get(order.order_id, [])) if version else None
            items.append(
                OrderListItem(
                    order=order,
                    customer=customers.get(order.customer_id),
                    priority=results.get(order.order_id),
                    schedule=schedule,
                    on_hold=on_hold,
                    hold_reason=hold_reason,
                )
            )
        items = [it for it in items if _matches(it, filters)]
        reverse = descending if descending is not None else sort in ("priority", "order_value", "margin")
        items.sort(key=lambda it: _sort_key(it, sort), reverse=reverse)
        log.debug("orders.listed", matched=len(items), sort=sort, page=pagination.page)
        return PagedResult.slice(items, pagination)

    # --------------------------------------------------------------- detail
    def get_order_detail(self, order_id: str) -> OrderDetail:
        now = self.now()
        order = self._orders.get(order_id)
        customer = self._customers.get_many([order.customer_id]).get(order.customer_id)
        result = self._results.latest_for_order(order_id)
        operations = self._orders.get_operations(order_id)
        version = self._schedule.get_current()
        entries = self._schedule.get_entries(order_id=order_id) if version else []
        overrides = [o for o in self._overrides.list_for_order(order_id) if o.is_active_at(now)]
        expedites = [e for e in self._expedites.list_for_order(order_id) if e.is_active_at(now)]
        locks = [
            lk
            for lk in self._locks.list_active(now)
            if lk.order_id == order_id or order_id in lk.sequence_order_ids
        ]
        all_locks = self._locks.list_for_order(order_id, active_only=False)
        audit = self._audit.for_entities(
            [(ENTITY_ORDER, order_id), *[(ENTITY_LOCK, lk.lock_id) for lk in all_locks]]
        )
        on_hold, hold_reason, _ = effective_hold(order, overrides, now)
        material_ids: set[str] = {
            mid for mid in (order.required_material_id, *(op.material_id for op in operations)) if mid
        }
        tooling_ids = set(order.tooling_requirement)
        for op in operations:
            tooling_ids |= op.tooling_ids
        materials = [m for mid in sorted(material_ids) if (m := self._material(mid)) is not None]
        tooling = [t for tid in sorted(tooling_ids) if (t := self._tool(tid)) is not None]
        dependents = sorted(
            o.order_id for o in self._orders.get_open()[0] if order_id in o.depends_on_order_ids
        )
        return OrderDetail(
            order=order,
            customer=customer,
            priority=result,
            breakdown=explanation_lines(result) if result else [],
            schedule=_schedule_info(version, entries) if version else None,
            operations=operations,
            materials=materials,
            tooling=tooling,
            production_minutes=_production_minutes(order, operations),
            dependencies=sorted(order.depends_on_order_ids),
            dependents=dependents,
            overrides=overrides,
            expedites=expedites,
            locks=locks,
            audit=audit,
            data_quality_issues=self._dq.issues_for_entity("order", order_id),
            on_hold=on_hold,
            hold_reason=hold_reason,
        )

    def get_explanation(self, order_id: str) -> ExplanationData:
        self._orders.get(order_id)
        result = self._results.latest_for_order(order_id)
        if result is None:
            raise NotFoundError(
                f"no priority result stored for order '{order_id}'; run the priority evaluation first",
                details={"order_id": order_id},
            )
        return ExplanationData(result=result, lines=explanation_lines(result))

    # ------------------------------------------------------------- machines
    def get_machine_options(self, order_id: str) -> MachineOptions:
        """Eligible machines for the order's next operation, evaluated live on the snapshot."""
        now = self.now()
        stored = self._orders.get(order_id)
        snapshot = self._snapshots.load_snapshot(now)
        order = snapshot.orders.get(order_id)
        if order is None:
            raise ConflictError(
                f"order '{order_id}' is not open ({stored.order_status.value}); no machine options",
                details={"order_status": stored.order_status.value},
            )
        config = self._snapshots.active_config().scheduling
        engine = default_constraint_engine(config)
        op, synthetic = next_operation(order, snapshot)
        eligibility = engine.eligible_machines(op, snapshot, now, order=order)
        ctx = ConstraintContext(snapshot=snapshot, at=now, config=config, order=order)
        options: list[MachineOption] = []
        for machine_id in eligibility.eligible_machine_ids:
            machine = snapshot.machines[machine_id]
            penalties = engine.soft_penalties(op, machine, ctx)
            options.append(
                MachineOption(
                    machine_id=machine_id,
                    machine_name=machine.machine_name,
                    rank=0,
                    recommended=False,
                    setup_minutes=op.setup_minutes,
                    run_minutes=op.run_minutes_on(machine),
                    soft_cost=sum(p.cost for p in penalties),
                    penalties=penalties,
                    reasons=[p.message for p in penalties] or ["no soft-constraint penalties"],
                )
            )
        options.sort(
            key=lambda o: (o.soft_cost, snapshot.machines[o.machine_id].preferred_rank, o.machine_id)
        )
        for index, option in enumerate(options, start=1):
            option.rank = index
            option.recommended = index == 1
        pin = pinned_machine_for_order(snapshot, order_id, now)
        entries = self._schedule.get_entries(order_id=order_id) if self._schedule.get_current() else []
        return MachineOptions(
            order_id=order_id,
            operation_id=op.operation_id,
            synthetic_operation=synthetic,
            source="live",
            evaluated_at=now,
            recommended_machine_id=options[0].machine_id if options else None,
            scheduled_machine_id=entries[0].machine_id if entries else None,
            pinned_machine_id=pin[0] if pin else None,
            pinned_by=pin[1] if pin else None,
            eligible=options,
            rejected={mid: [v.message for v in vs] for mid, vs in eligibility.rejected.items()},
        )

    # ------------------------------------------------------------ internals
    def _fetch_all(self, filters: OrderFilters) -> list[Order]:
        orders: list[Order] = []
        offset = 0
        while True:
            page = self._orders.list(filters, offset=offset, limit=REPO_MAX_PAGE_SIZE)
            orders.extend(page.items)
            offset += len(page.items)
            if not page.has_more or not page.items:
                return orders

    def _current_schedule(self) -> tuple[ScheduleVersionInfo | None, dict[str, list[ScheduleEntry]]]:
        version = self._schedule.get_current()
        if version is None:
            return None, {}
        by_order: dict[str, list[ScheduleEntry]] = defaultdict(list)
        for entry in self._schedule.get_entries(schedule_version_id=version.schedule_version_id):
            by_order[entry.order_id].append(entry)
        return version, dict(by_order)

    def _material(self, material_id: str) -> Material | None:
        try:
            return self._materials.get(material_id)
        except NotFoundError:
            return None

    def _tool(self, tooling_id: str) -> Tooling | None:
        try:
            return self._tooling.get(tooling_id)
        except NotFoundError:
            return None


# --------------------------------------------------------------------- helpers


def _schedule_info(version: ScheduleVersionInfo, entries: list[ScheduleEntry]) -> OrderScheduleInfo | None:
    if not entries:
        return None
    ordered = sorted(entries, key=lambda e: (e.start, e.sequence_on_machine))
    last = max(ordered, key=lambda e: e.end)
    lateness = next((e.expected_lateness_hours for e in ordered if e.is_last_operation), None)
    completion = next((e.expected_completion for e in ordered if e.expected_completion), None) or last.end
    if lateness is None and last.due_date is not None:
        lateness = (completion - last.due_date).total_seconds() / 3600.0
    return OrderScheduleInfo(
        version_number=version.version_number,
        status=version.status.value,
        machine_id=ordered[0].machine_id,
        start=ordered[0].start,
        end=last.end,
        expected_completion=completion,
        expected_lateness_hours=lateness,
        entries=ordered,
    )


def _production_minutes(order: Order, operations: list[Operation]) -> float | None:
    if order.estimated_total_production_minutes is not None:
        return order.estimated_total_production_minutes
    total = 0.0
    known = False
    for op in operations:
        if op.is_done:
            continue
        cycle = op.cycle_minutes_per_unit
        if cycle is not None:
            total += cycle * op.pending_quantity + (op.setup_minutes or 0.0)
            known = True
    if known:
        return total
    if order.estimated_cycle_minutes_per_unit is not None:
        return order.estimated_cycle_minutes_per_unit * order.pending_quantity + (
            order.estimated_setup_minutes or 0.0
        )
    return None


def _matches(item: OrderListItem, filters: OrderListFilters) -> bool:
    if filters.risk is not None and (item.priority is None or item.priority.risk_level is not filters.risk):
        return False
    if filters.readiness is not None and (
        item.priority is None or item.priority.readiness is not filters.readiness
    ):
        return False
    if filters.on_hold is not None and item.on_hold != filters.on_hold:
        return False
    if filters.machine_id is not None:
        scheduled = item.schedule.machine_id if item.schedule else None
        if filters.machine_id not in (scheduled, item.order.required_machine_id):
            return False
    return True


def _sort_key(item: OrderListItem, sort: str) -> tuple[Any, ...]:
    order, result = item.order, item.priority
    due = order.due_date or _FAR_FUTURE
    if sort == "priority":
        return (result.score if result else -1.0, -due.timestamp(), order.order_id)
    if sort == "rank":
        return (result.rank if result and result.rank is not None else 10**9, due, order.order_id)
    if sort == "customer":
        return (item.customer.customer_name if item.customer else order.customer_id, due, order.order_id)
    if sort == "order_value":
        return (order.order_value or 0.0, due, order.order_id)
    if sort == "margin":
        value = order.estimated_margin if order.estimated_margin is not None else order.actual_margin
        return (value if value is not None else -1.0, due, order.order_id)
    if sort == "risk":
        return (_RISK_ORDER[result.risk_level] if result else 9, due, order.order_id)
    if sort == "status":
        return (order.order_status.value, due, order.order_id)
    if sort == "quantity":
        return (order.pending_quantity, due, order.order_id)
    if sort == "order_id":
        return (order.order_id,)
    return (due, order.order_id)


__all__ = [
    "SORT_KEYS",
    "ExplanationData",
    "MachineOption",
    "MachineOptions",
    "OrderDetail",
    "OrderListFilters",
    "OrderListItem",
    "OrderQueryService",
    "OrderScheduleInfo",
]
