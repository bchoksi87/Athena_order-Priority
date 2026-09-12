"""CustomerRepository: customers and their planner rules."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import select

from app.core.errors import NotFoundError
from app.db.mappers import (
    customer_from_row,
    customer_rule_from_row,
    customer_rule_to_row,
    customer_to_row,
)
from app.db.models import CustomerRow, CustomerRuleRow
from app.db.records import Page
from app.db.repositories.base import DEFAULT_PAGE_SIZE, Repository
from app.domain.models import Customer, CustomerRule


class CustomerRepository(Repository):
    """Customers are ERP master data (upserted by sync); rules are planner-owned."""

    # ------------------------------------------------------------ customers
    def upsert(self, customers: Iterable[Customer], synced_at: datetime | None = None) -> int:
        """Insert or update customers by id. Returns the number of rows touched."""
        items = list(customers)
        if not items:
            return 0
        existing = self._rows_by_ids(CustomerRow, CustomerRow.customer_id, [c.customer_id for c in items])
        for customer in items:
            row = customer_to_row(customer, existing.get(customer.customer_id))
            row.synced_at = synced_at
            if customer.customer_id not in existing:
                self._session.add(row)
        self._flush()
        return len(items)

    def get(self, customer_id: str) -> Customer:
        row = self._session.get(CustomerRow, customer_id)
        if row is None:
            raise NotFoundError(f"customer '{customer_id}' not found", details={"customer_id": customer_id})
        return customer_from_row(row)

    def get_many(self, customer_ids: Iterable[str]) -> dict[str, Customer]:
        rows = self._rows_by_ids(CustomerRow, CustomerRow.customer_id, customer_ids)
        return {cid: customer_from_row(row) for cid, row in rows.items()}

    def list_all(self, active_only: bool = False) -> list[Customer]:
        stmt = select(CustomerRow).order_by(CustomerRow.customer_id)
        if active_only:
            stmt = stmt.where(CustomerRow.active.is_(True))
        return [customer_from_row(r) for r in self._session.execute(stmt).scalars()]

    def list(self, *, search: str | None = None, offset: int = 0, limit: int = DEFAULT_PAGE_SIZE) -> Page:
        stmt = select(CustomerRow).order_by(CustomerRow.customer_name, CustomerRow.customer_id)
        if search:
            pattern = f"%{search}%"
            stmt = stmt.where(
                CustomerRow.customer_name.ilike(pattern) | CustomerRow.customer_id.ilike(pattern)
            )
        page = self._paginate(stmt, offset, limit)
        page.items = [customer_from_row(r) for r in page.items]
        return page

    def count(self) -> int:
        return self._count(select(CustomerRow))

    # ---------------------------------------------------------------- rules
    def get_rule(self, customer_id: str) -> CustomerRule | None:
        row = self._session.get(CustomerRuleRow, customer_id)
        return customer_rule_from_row(row) if row else None

    def save_rule(
        self, rule: CustomerRule, updated_by: str | None = None, at: datetime | None = None
    ) -> CustomerRule:
        """Create or replace the rule for ``rule.customer_id`` (customer must exist)."""
        if self._session.get(CustomerRow, rule.customer_id) is None:
            raise NotFoundError(
                f"customer '{rule.customer_id}' not found", details={"customer_id": rule.customer_id}
            )
        row = self._session.get(CustomerRuleRow, rule.customer_id)
        is_new = row is None
        row = customer_rule_to_row(rule, row)
        row.updated_by = updated_by
        row.last_changed_at = at
        if is_new:
            self._session.add(row)
        self._flush()
        return customer_rule_from_row(row)

    def delete_rule(self, customer_id: str) -> bool:
        row = self._session.get(CustomerRuleRow, customer_id)
        if row is None:
            return False
        self._session.delete(row)
        self._flush()
        return True

    def list_rules(self, active_only: bool = True) -> dict[str, CustomerRule]:
        stmt = select(CustomerRuleRow).order_by(CustomerRuleRow.customer_id)
        if active_only:
            stmt = stmt.where(CustomerRuleRow.active.is_(True))
        return {r.customer_id: customer_rule_from_row(r) for r in self._session.execute(stmt).scalars()}


__all__ = ["CustomerRepository"]
