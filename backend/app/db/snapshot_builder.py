"""Build a :class:`PlanningSnapshot` from the database with bulk queries."""

from __future__ import annotations

from datetime import datetime

import structlog
from sqlalchemy.orm import Session

from app.core.clock import ensure_utc
from app.db.repositories.customers import CustomerRepository
from app.db.repositories.orders import OrderRepository
from app.db.repositories.overlays import ExpediteRepository, LockRepository, OverrideRepository
from app.db.repositories.resources import (
    CalendarRepository,
    MachineRepository,
    MaterialRepository,
    ToolingRepository,
)
from app.domain.snapshot import PlanningSnapshot

log = structlog.get_logger(__name__)

SNAPSHOT_SOURCE_DB = "db"


class DbSnapshotBuilder:
    """Assemble the engine input from persisted state.

    Loads open orders (+ their operations), all machines (+ downtime),
    materials, tooling, calendars, customers referenced by the open orders,
    active locks/overrides/expedites and active customer rules — each with a
    constant number of queries regardless of row counts (no N+1).
    """

    def __init__(self, session: Session) -> None:
        self._orders = OrderRepository(session)
        self._customers = CustomerRepository(session)
        self._machines = MachineRepository(session)
        self._materials = MaterialRepository(session)
        self._tooling = ToolingRepository(session)
        self._calendars = CalendarRepository(session)
        self._locks = LockRepository(session)
        self._overrides = OverrideRepository(session)
        self._expedites = ExpediteRepository(session)

    def build(self, as_of: datetime, *, include_closed_orders: bool = False) -> PlanningSnapshot:
        as_of = ensure_utc(as_of)
        if include_closed_orders:
            page = self._orders.list(limit=1000)
            orders = list(page.items)
            offset = len(orders)
            while offset < page.total:
                page = self._orders.list(offset=offset, limit=1000)
                orders.extend(page.items)
                offset += len(page.items)
            operations_by_order = self._orders.operations_for_orders([o.order_id for o in orders])
        else:
            orders, operations_by_order = self._orders.get_open(with_operations=True)

        customer_ids = {o.customer_id for o in orders}
        snapshot = PlanningSnapshot(
            as_of=as_of,
            customers=self._customers.get_many(customer_ids),
            orders={o.order_id: o for o in orders},
            operations={op.operation_id: op for ops in operations_by_order.values() for op in ops},
            machines={m.machine_id: m for m in self._machines.list_all()},
            materials={m.material_id: m for m in self._materials.list_all()},
            tooling={t.tooling_id: t for t in self._tooling.list_all()},
            calendars={c.calendar_id: c for c in self._calendars.list_all()},
            default_calendar_id=self._calendars.get_default_id(),
            locks=self._locks.list_active(as_of),
            overrides=self._overrides.list_active(as_of),
            expedites=self._expedites.list_active(as_of),
            customer_rules=self._customers.list_rules(active_only=True),
            source=SNAPSHOT_SOURCE_DB,
        )
        snapshot.rebuild_indexes()
        log.info("snapshot_built", as_of=as_of.isoformat(), **snapshot.summary())
        return snapshot


__all__ = ["SNAPSHOT_SOURCE_DB", "DbSnapshotBuilder"]
