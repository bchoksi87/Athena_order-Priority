"""OrderRepository: order lines with their operations."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import Select, delete, func, or_, select

from app.core.errors import NotFoundError
from app.db.mappers import operation_from_row, operation_to_row, order_from_row, order_to_row
from app.db.models import OperationRow, OrderRow
from app.db.records import Page
from app.db.repositories.base import DEFAULT_PAGE_SIZE, Repository, chunked
from app.domain.enums import CLOSED_ORDER_STATUSES, OrderStatus, ProcessType
from app.domain.models import Operation, Order


@dataclass(slots=True)
class OrderFilters:
    """Filter set for :meth:`OrderRepository.list`. All fields are optional."""

    statuses: Sequence[OrderStatus] = field(default_factory=tuple)
    customer_id: str | None = None
    machine_group: str | None = None
    process_type: ProcessType | None = None
    required_machine_id: str | None = None
    part_family: str | None = None
    due_from: datetime | None = None
    due_to: datetime | None = None
    search: str | None = None
    open_only: bool = False
    on_hold: bool | None = None


class OrderRepository(Repository):
    def upsert(
        self,
        orders: Iterable[Order],
        operations: Iterable[Operation] = (),
        *,
        synced_at: datetime | None = None,
        replace_operations: bool = True,
    ) -> int:
        """Insert/update orders and their operations.

        Operations are matched by ``operation_id``; when ``replace_operations`` is
        set, operations of the given orders that are absent from ``operations``
        are deleted (the ERP routing is authoritative).
        """
        order_list = list(orders)
        if not order_list:
            return 0
        order_ids = [o.order_id for o in order_list]
        existing = self._rows_by_ids(OrderRow, OrderRow.order_id, order_ids)
        for order in order_list:
            row = order_to_row(order, existing.get(order.order_id))
            row.synced_at = synced_at
            if order.order_id not in existing:
                self._session.add(row)
        self._flush()

        wanted = set(order_ids)
        ops = [op for op in operations if op.order_id in wanted]
        op_ids = {op.operation_id for op in ops}
        existing_ops = self._rows_by_ids(OperationRow, OperationRow.operation_id, op_ids)
        if replace_operations:
            for ids in chunked(order_ids):
                stmt = delete(OperationRow).where(OperationRow.order_id.in_(ids))
                if op_ids:
                    stmt = stmt.where(OperationRow.operation_id.not_in(list(op_ids)))
                self._session.execute(stmt)
        for op in ops:
            op_row = operation_to_row(op, existing_ops.get(op.operation_id))
            if op.operation_id not in existing_ops:
                self._session.add(op_row)
        self._flush()
        return len(order_list)

    # ---------------------------------------------------------------- reads
    def get(self, order_id: str) -> Order:
        row = self._session.get(OrderRow, order_id)
        if row is None:
            raise NotFoundError(f"order '{order_id}' not found", details={"order_id": order_id})
        return order_from_row(row)

    def exists(self, order_id: str) -> bool:
        return self._session.get(OrderRow, order_id) is not None

    def get_many(self, order_ids: Iterable[str]) -> dict[str, Order]:
        rows = self._rows_by_ids(OrderRow, OrderRow.order_id, order_ids)
        return {oid: order_from_row(row) for oid, row in rows.items()}

    def get_operations(self, order_id: str) -> list[Operation]:
        stmt = (
            select(OperationRow)
            .where(OperationRow.order_id == order_id)
            .order_by(OperationRow.sequence, OperationRow.operation_id)
        )
        return [operation_from_row(r) for r in self._session.execute(stmt).scalars()]

    def get_operation(self, operation_id: str) -> Operation:
        row = self._session.get(OperationRow, operation_id)
        if row is None:
            raise NotFoundError(
                f"operation '{operation_id}' not found", details={"operation_id": operation_id}
            )
        return operation_from_row(row)

    def operations_for_orders(self, order_ids: Iterable[str]) -> dict[str, list[Operation]]:
        """Bulk load operations grouped by order (chunked ``IN`` queries, no N+1)."""
        out: dict[str, list[Operation]] = defaultdict(list)
        for ids in chunked(set(order_ids)):
            stmt = (
                select(OperationRow)
                .where(OperationRow.order_id.in_(ids))
                .order_by(OperationRow.order_id, OperationRow.sequence, OperationRow.operation_id)
            )
            for row in self._session.execute(stmt).scalars():
                out[row.order_id].append(operation_from_row(row))
        return dict(out)

    def get_open(self, with_operations: bool = False) -> tuple[list[Order], dict[str, list[Operation]]]:
        """Open orders (status not closed and pending quantity > 0), sorted by id."""
        stmt = (
            select(OrderRow)
            .where(OrderRow.order_status.not_in([s.value for s in CLOSED_ORDER_STATUSES]))
            .where(OrderRow.quantity - OrderRow.completed_quantity - OrderRow.cancelled_quantity > 0)
            .order_by(OrderRow.order_id)
        )
        orders = [order_from_row(r) for r in self._session.execute(stmt).scalars()]
        ops = self.operations_for_orders([o.order_id for o in orders]) if with_operations else {}
        return orders, ops

    def list(
        self, filters: OrderFilters | None = None, *, offset: int = 0, limit: int = DEFAULT_PAGE_SIZE
    ) -> Page:
        filters = filters or OrderFilters()
        stmt = self._apply_filters(select(OrderRow), filters).order_by(
            OrderRow.due_date.is_(None), OrderRow.due_date, OrderRow.order_id
        )
        page = self._paginate(stmt, offset, limit)
        page.items = [order_from_row(r) for r in page.items]
        return page

    def count(self, filters: OrderFilters | None = None) -> int:
        return self._count(self._apply_filters(select(OrderRow), filters or OrderFilters()))

    def status_counts(self) -> dict[str, int]:
        stmt = select(OrderRow.order_status, func.count(OrderRow.order_id)).group_by(OrderRow.order_status)
        return {status: int(n) for status, n in self._session.execute(stmt).all()}

    def update_status(self, order_id: str, status: OrderStatus) -> Order:
        row = self._session.get(OrderRow, order_id)
        if row is None:
            raise NotFoundError(f"order '{order_id}' not found", details={"order_id": order_id})
        row.order_status = status.value
        self._flush()
        return order_from_row(row)

    def set_hold(self, order_id: str, on_hold: bool, reason: str | None) -> Order:
        row = self._session.get(OrderRow, order_id)
        if row is None:
            raise NotFoundError(f"order '{order_id}' not found", details={"order_id": order_id})
        row.on_hold = on_hold
        row.hold_reason = reason if on_hold else None
        self._flush()
        return order_from_row(row)

    def delete_missing(self, keep_order_ids: Iterable[str]) -> int:
        """Delete orders not in ``keep_order_ids`` (full sync reconciliation)."""
        keep = set(keep_order_ids)
        all_ids = [r for (r,) in self._session.execute(select(OrderRow.order_id)).all()]
        doomed = [oid for oid in all_ids if oid not in keep]
        for ids in chunked(doomed):
            self._session.execute(delete(OrderRow).where(OrderRow.order_id.in_(ids)))
        self._flush()
        return len(doomed)

    @staticmethod
    def _apply_filters(stmt: Select, filters: OrderFilters) -> Select:
        if filters.statuses:
            stmt = stmt.where(OrderRow.order_status.in_([s.value for s in filters.statuses]))
        if filters.open_only:
            stmt = stmt.where(OrderRow.order_status.not_in([s.value for s in CLOSED_ORDER_STATUSES])).where(
                OrderRow.quantity - OrderRow.completed_quantity - OrderRow.cancelled_quantity > 0
            )
        if filters.customer_id:
            stmt = stmt.where(OrderRow.customer_id == filters.customer_id)
        if filters.machine_group:
            stmt = stmt.where(OrderRow.machine_group == filters.machine_group)
        if filters.process_type:
            stmt = stmt.where(OrderRow.process_type == filters.process_type.value)
        if filters.required_machine_id:
            stmt = stmt.where(OrderRow.required_machine_id == filters.required_machine_id)
        if filters.part_family:
            stmt = stmt.where(OrderRow.part_family == filters.part_family)
        if filters.due_from is not None:
            stmt = stmt.where(OrderRow.due_date >= filters.due_from)
        if filters.due_to is not None:
            stmt = stmt.where(OrderRow.due_date < filters.due_to)
        if filters.on_hold is not None:
            stmt = stmt.where(OrderRow.on_hold.is_(filters.on_hold))
        if filters.search:
            pattern = f"%{filters.search.strip()}%"
            stmt = stmt.where(
                or_(
                    OrderRow.order_id.ilike(pattern),
                    OrderRow.external_order_ref.ilike(pattern),
                    OrderRow.part_id.ilike(pattern),
                    OrderRow.part_name.ilike(pattern),
                    OrderRow.customer_id.ilike(pattern),
                )
            )
        return stmt


__all__ = ["OrderFilters", "OrderRepository"]
