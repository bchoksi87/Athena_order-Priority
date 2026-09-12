"""Customer and customer-rule schemas (spec Phase 15)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.api.schemas.common import ReasonBody
from app.domain.enums import CustomerTier, PaymentRisk
from app.domain.models import Customer, CustomerRule
from app.services.customer_rule_service import CustomerWithRule


class CustomerRuleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_id: str
    sla_hours: float | None = None
    tier_override: CustomerTier | None = None
    priority_boost_points: float = 0.0
    notes: str | None = None
    active: bool = True

    @classmethod
    def from_domain(cls, rule: CustomerRule) -> CustomerRuleResponse:
        return cls(
            customer_id=rule.customer_id,
            sla_hours=rule.sla_hours,
            tier_override=rule.tier_override,
            priority_boost_points=rule.priority_boost_points,
            notes=rule.notes,
            active=rule.active,
        )


class CustomerRuleRequest(ReasonBody):
    sla_hours: float | None = Field(default=None, gt=0)
    tier_override: CustomerTier | None = None
    priority_boost_points: float = Field(default=0.0, ge=-100, le=100)
    notes: str | None = Field(default=None, max_length=2000)
    active: bool = True


class CustomerRuleDeleteRequest(ReasonBody):
    pass


class CustomerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_id: str
    customer_name: str
    customer_category: str
    customer_tier: CustomerTier
    effective_tier: CustomerTier = Field(description="Rule tier override when set, else the ERP tier")
    customer_priority: int
    strategic_customer_flag: bool
    customer_revenue: float | None = None
    customer_profitability: float | None = None
    sla_hours: float | None = None
    effective_sla_hours: float | None = Field(default=None, description="Rule SLA when set, else the ERP SLA")
    escalation_level: int
    payment_risk: PaymentRisk
    account_manager: str | None = None
    active: bool
    rule: CustomerRuleResponse | None = None

    @classmethod
    def from_domain(cls, item: CustomerWithRule) -> CustomerResponse:
        c: Customer = item.customer
        rule = item.rule if item.rule is not None and item.rule.active else None
        return cls(
            customer_id=c.customer_id,
            customer_name=c.customer_name,
            customer_category=c.customer_category,
            customer_tier=c.customer_tier,
            effective_tier=rule.tier_override if rule and rule.tier_override else c.customer_tier,
            customer_priority=c.customer_priority,
            strategic_customer_flag=c.strategic_customer_flag,
            customer_revenue=c.customer_revenue,
            customer_profitability=c.customer_profitability,
            sla_hours=c.sla_hours,
            effective_sla_hours=rule.sla_hours if rule and rule.sla_hours is not None else c.sla_hours,
            escalation_level=c.escalation_level,
            payment_risk=c.payment_risk,
            account_manager=c.account_manager,
            active=c.active,
            rule=CustomerRuleResponse.from_domain(item.rule) if item.rule else None,
        )


__all__ = ["CustomerResponse", "CustomerRuleDeleteRequest", "CustomerRuleRequest", "CustomerRuleResponse"]
