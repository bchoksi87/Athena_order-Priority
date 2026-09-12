"""CustomerRuleService: customer-specific prioritisation rules (spec Phase 15)."""

from __future__ import annotations

from dataclasses import dataclass

import structlog
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.errors import NotFoundError, ValidationError
from app.core.security import CurrentUser
from app.db.repositories.customers import CustomerRepository
from app.domain.enums import CustomerTier
from app.domain.models import Customer, CustomerRule
from app.services.audit_service import ENTITY_CUSTOMER_RULE, AuditService
from app.services.base import PagedResult, Pagination, Service, actor_id, require_reason

log = structlog.get_logger(__name__)

MAX_BOOST_POINTS = 100.0
MAX_SLA_HOURS = 24.0 * 365


@dataclass(slots=True)
class CustomerWithRule:
    customer: Customer
    rule: CustomerRule | None


class CustomerRuleService(Service):
    def __init__(self, session: Session, clock: Clock, audit: AuditService) -> None:
        super().__init__(session, clock)
        self._audit = audit
        self._customers = CustomerRepository(session)

    def get_customer(self, customer_id: str) -> CustomerWithRule:
        customer = self._customers.get(customer_id)
        return CustomerWithRule(customer, self._customers.get_rule(customer_id))

    def list_customers(
        self, pagination: Pagination, *, search: str | None = None
    ) -> PagedResult[CustomerWithRule]:
        page = self._customers.list(search=search, offset=pagination.offset, limit=pagination.limit)
        rules = self._customers.list_rules(active_only=False)
        items = [CustomerWithRule(c, rules.get(c.customer_id)) for c in page.items]
        return PagedResult(
            items=items, total=page.total, page=pagination.page, page_size=pagination.page_size
        )

    def get_rule(self, customer_id: str) -> CustomerRule | None:
        self._customers.get(customer_id)
        return self._customers.get_rule(customer_id)

    def upsert_rule(
        self,
        customer_id: str,
        user: CurrentUser | str,
        reason: str,
        *,
        sla_hours: float | None = None,
        tier_override: CustomerTier | None = None,
        priority_boost_points: float = 0.0,
        notes: str | None = None,
        active: bool = True,
    ) -> CustomerRule:
        reason = require_reason(reason)
        self._customers.get(customer_id)
        if sla_hours is not None and (sla_hours <= 0 or sla_hours > MAX_SLA_HOURS):
            raise ValidationError(
                f"sla_hours must be in (0, {MAX_SLA_HOURS:g}]", details={"sla_hours": sla_hours}
            )
        if abs(priority_boost_points) > MAX_BOOST_POINTS:
            raise ValidationError(
                f"priority_boost_points must be within ±{MAX_BOOST_POINTS:g}",
                details={"priority_boost_points": priority_boost_points},
            )
        previous = self._customers.get_rule(customer_id)
        rule = self._customers.save_rule(
            CustomerRule(
                customer_id=customer_id,
                sla_hours=sla_hours,
                tier_override=tier_override,
                priority_boost_points=float(priority_boost_points),
                notes=notes,
                active=active,
            ),
            updated_by=actor_id(user),
            at=self.now(),
        )
        self._audit.record(
            user,
            ENTITY_CUSTOMER_RULE,
            customer_id,
            "customer_rule.create" if previous is None else "customer_rule.update",
            previous,
            rule,
            reason,
            {"customer_id": customer_id},
        )
        log.info("customer_rule.saved", customer_id=customer_id, user_id=actor_id(user))
        return rule

    def delete_rule(self, customer_id: str, user: CurrentUser | str, reason: str) -> CustomerRule:
        reason = require_reason(reason)
        self._customers.get(customer_id)
        previous = self._customers.get_rule(customer_id)
        if previous is None:
            raise NotFoundError(f"customer '{customer_id}' has no rule", details={"customer_id": customer_id})
        self._customers.delete_rule(customer_id)
        self._audit.record(
            user, ENTITY_CUSTOMER_RULE, customer_id, "customer_rule.delete", previous, None, reason
        )
        return previous


__all__ = ["CustomerRuleService", "CustomerWithRule"]
