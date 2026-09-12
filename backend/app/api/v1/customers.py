"""``/customers``: customers with their planner rules (spec Phase 15)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import CustomerRuleServiceDep, require_min_role, require_read_access
from app.api.schemas.common import PageResponse
from app.api.schemas.customers import (
    CustomerResponse,
    CustomerRuleDeleteRequest,
    CustomerRuleRequest,
    CustomerRuleResponse,
)
from app.core.errors import NotFoundError
from app.core.security import CurrentUser
from app.domain.enums import Role
from app.services.base import Pagination, require_reason

router = APIRouter(prefix="/customers", tags=["customers"])

Reader = Annotated[CurrentUser, Depends(require_read_access(Role.PLANNER))]
Manager = Annotated[CurrentUser, Depends(require_min_role(Role.PRODUCTION_MANAGER))]


@router.get("", response_model=PageResponse[CustomerResponse], summary="Customers with tier and rules")
def list_customers(
    _user: Reader,
    service: CustomerRuleServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=500)] = 50,
    search: str | None = None,
) -> PageResponse[CustomerResponse]:
    result = service.list_customers(Pagination(page, page_size), search=search)
    return PageResponse.build(
        [CustomerResponse.from_domain(i) for i in result.items],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
    )


@router.get("/{customer_id}", response_model=CustomerResponse, summary="Customer with its rule")
def get_customer(customer_id: str, _user: Reader, service: CustomerRuleServiceDep) -> CustomerResponse:
    return CustomerResponse.from_domain(service.get_customer(customer_id))


@router.get("/{customer_id}/rules", response_model=CustomerRuleResponse, summary="Customer rule")
def get_rule(customer_id: str, _user: Reader, service: CustomerRuleServiceDep) -> CustomerRuleResponse:
    rule = service.get_rule(customer_id)
    if rule is None:
        raise NotFoundError(f"customer '{customer_id}' has no rule", details={"customer_id": customer_id})
    return CustomerRuleResponse.from_domain(rule)


@router.put("/{customer_id}/rules", response_model=CustomerRuleResponse, summary="Create or replace the rule")
def put_rule(
    customer_id: str, body: CustomerRuleRequest, user: Manager, service: CustomerRuleServiceDep
) -> CustomerRuleResponse:
    rule = service.upsert_rule(
        customer_id,
        user,
        body.reason,
        sla_hours=body.sla_hours,
        tier_override=body.tier_override,
        priority_boost_points=body.priority_boost_points,
        notes=body.notes,
        active=body.active,
    )
    return CustomerRuleResponse.from_domain(rule)


@router.delete("/{customer_id}/rules", response_model=CustomerRuleResponse, summary="Delete the rule")
def delete_rule(
    customer_id: str,
    user: Manager,
    service: CustomerRuleServiceDep,
    body: CustomerRuleDeleteRequest | None = None,
    reason: Annotated[str | None, Query(description="Alternative to the JSON body")] = None,
) -> CustomerRuleResponse:
    text = require_reason(body.reason if body is not None else reason)
    return CustomerRuleResponse.from_domain(service.delete_rule(customer_id, user, text))


__all__ = ["router"]
