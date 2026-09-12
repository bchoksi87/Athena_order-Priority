"""``/orders``: priority queue, order detail, explanation, machine options and overrides."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import (
    ExpediteServiceDep,
    OrderQueryServiceDep,
    OverrideServiceDep,
    require_min_role,
    require_read_access,
)
from app.api.schemas.common import PageResponse, ReasonBody
from app.api.schemas.orders import (
    ExplanationResponse,
    MachineOptionsResponse,
    OrderDetailResponse,
    OrderListItemResponse,
)
from app.api.schemas.overlays import (
    CancelRequest,
    ExpediteRequest,
    ExpediteResponse,
    ForceNextRequest,
    HoldRequest,
    LockMachineAssignmentRequest,
    LockResponse,
    MoveOrderRequest,
    MoveOrderResponse,
    OverridePriorityRequest,
    OverrideResponse,
    ReleaseRequest,
)
from app.core.security import CurrentUser
from app.domain.enums import OrderStatus, ProcessType, ReadinessState, RiskLevel, Role
from app.services.base import Pagination, require_reason
from app.services.order_query_service import SORT_KEYS, OrderListFilters

router = APIRouter(tags=["orders"])

Reader = Annotated[CurrentUser, Depends(require_read_access(Role.OPERATOR))]
Manager = Annotated[CurrentUser, Depends(require_min_role(Role.PRODUCTION_MANAGER))]
Planner = Annotated[CurrentUser, Depends(require_min_role(Role.PLANNER))]


def _reason(body: ReasonBody | None, query_reason: str | None) -> str:
    """DELETE endpoints take the mandatory reason from the JSON body or the ``reason`` query parameter."""
    return require_reason(body.reason if body is not None else query_reason)


@router.get("/orders", response_model=PageResponse[OrderListItemResponse], summary="Priority queue")
def list_orders(
    _user: Reader,
    service: OrderQueryServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=500)] = 50,
    sort: Annotated[str, Query(description=f"One of: {', '.join(SORT_KEYS)}")] = "priority",
    order: Annotated[str | None, Query(pattern="^(asc|desc)$")] = None,
    customer_id: str | None = None,
    status_filter: Annotated[list[OrderStatus] | None, Query(alias="status")] = None,
    machine_group: str | None = None,
    process_type: ProcessType | None = None,
    machine_id: str | None = None,
    due_from: datetime | None = None,
    due_to: datetime | None = None,
    risk: RiskLevel | None = None,
    readiness: ReadinessState | None = None,
    on_hold: bool | None = None,
    search: str | None = None,
    open_only: bool = True,
) -> PageResponse[OrderListItemResponse]:
    """Orders joined with the latest priority result and their placement in the current schedule."""
    filters = OrderListFilters(
        customer_id=customer_id,
        statuses=list(status_filter or []),
        machine_group=machine_group,
        process_type=process_type,
        machine_id=machine_id,
        due_from=due_from,
        due_to=due_to,
        risk=risk,
        readiness=readiness,
        search=search,
        open_only=open_only,
        on_hold=on_hold,
    )
    descending = None if order is None else order == "desc"
    result = service.list_orders(filters, Pagination(page, page_size), sort=sort, descending=descending)
    return PageResponse.build(
        [OrderListItemResponse.from_item(i) for i in result.items],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
    )


@router.get("/orders/{order_id}", response_model=OrderDetailResponse, summary="Order detail view")
def get_order(order_id: str, _user: Reader, service: OrderQueryServiceDep) -> OrderDetailResponse:
    return OrderDetailResponse.from_detail(service.get_order_detail(order_id))


@router.get(
    "/orders/{order_id}/explanation",
    response_model=ExplanationResponse,
    summary="Why is this order prioritised?",
)
def get_explanation(order_id: str, _user: Reader, service: OrderQueryServiceDep) -> ExplanationResponse:
    return ExplanationResponse.from_data(service.get_explanation(order_id))


@router.get(
    "/orders/{order_id}/machines",
    response_model=MachineOptionsResponse,
    summary="Eligible machines for the order's next operation",
)
def get_machine_options(
    order_id: str, _user: Reader, service: OrderQueryServiceDep
) -> MachineOptionsResponse:
    return MachineOptionsResponse.from_domain(service.get_machine_options(order_id))


# ------------------------------------------------------------------ overrides


@router.post(
    "/orders/{order_id}/expedite",
    response_model=ExpediteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Expedite an order for a limited time",
)
def expedite_order(
    order_id: str, body: ExpediteRequest, user: Manager, service: ExpediteServiceDep
) -> ExpediteResponse:
    expedite = service.expedite(
        order_id,
        user,
        body.reason,
        boost_points=body.boost_points,
        duration_hours=body.duration_hours,
        starts_at=body.starts_at,
        expires_at=body.expires_at,
    )
    return ExpediteResponse.from_domain(expedite)


@router.post(
    "/orders/{order_id}/hold",
    response_model=OverrideResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Put an order on hold",
)
def hold_order(
    order_id: str, body: HoldRequest, user: Planner, service: OverrideServiceDep
) -> OverrideResponse:
    return OverrideResponse.from_domain(service.hold(order_id, user, body.reason, expires_at=body.expires_at))


@router.post(
    "/orders/{order_id}/release",
    response_model=OverrideResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Release a planner hold",
)
def release_order(
    order_id: str, body: ReleaseRequest, user: Planner, service: OverrideServiceDep
) -> OverrideResponse:
    return OverrideResponse.from_domain(service.release(order_id, user, body.reason))


@router.post(
    "/orders/{order_id}/override-priority",
    response_model=OverrideResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Increase, decrease or set the priority score",
)
def override_priority(
    order_id: str, body: OverridePriorityRequest, user: Manager, service: OverrideServiceDep
) -> OverrideResponse:
    if body.type == "increase":
        override = service.increase_priority(order_id, body.value, user, body.reason, body.expires_at)
    elif body.type == "decrease":
        override = service.decrease_priority(order_id, body.value, user, body.reason, body.expires_at)
    else:
        override = service.set_priority(order_id, body.value, user, body.reason, body.expires_at)
    return OverrideResponse.from_domain(override)


@router.post(
    "/orders/{order_id}/force-next",
    response_model=OverrideResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Force the order to be produced next",
)
def force_next(
    order_id: str, body: ForceNextRequest, user: Manager, service: OverrideServiceDep
) -> OverrideResponse:
    return OverrideResponse.from_domain(service.force_next(order_id, user, body.reason, body.expires_at))


@router.post(
    "/orders/{order_id}/move",
    response_model=MoveOrderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Move the order to a machine (optionally at a time)",
)
def move_order(
    order_id: str, body: MoveOrderRequest, user: Manager, service: OverrideServiceDep
) -> MoveOrderResponse:
    result = service.move_order(
        order_id,
        body.target_machine_id,
        user,
        body.reason,
        start_at=body.start_at,
        expires_at=body.expires_at,
    )
    return MoveOrderResponse(
        override=OverrideResponse.from_domain(result.override), lock=LockResponse.from_domain(result.lock)
    )


@router.post(
    "/orders/{order_id}/lock-machine",
    response_model=OverrideResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Lock the machine assignment of an order",
)
def lock_machine_assignment(
    order_id: str, body: LockMachineAssignmentRequest, user: Manager, service: OverrideServiceDep
) -> OverrideResponse:
    return OverrideResponse.from_domain(
        service.lock_machine_assignment(order_id, body.machine_id, user, body.reason, body.expires_at)
    )


@router.get(
    "/orders/{order_id}/overrides",
    response_model=list[OverrideResponse],
    summary="Active overrides of an order",
)
def list_order_overrides(order_id: str, _user: Reader, service: OverrideServiceDep) -> list[OverrideResponse]:
    return [OverrideResponse.from_domain(o) for o in service.list_for_order(order_id)]


@router.delete(
    "/overrides/{override_id}",
    response_model=OverrideResponse,
    summary="Cancel an override",
    tags=["overrides"],
)
def cancel_override(
    override_id: str,
    user: Manager,
    service: OverrideServiceDep,
    body: CancelRequest | None = None,
    reason: Annotated[str | None, Query(description="Alternative to the JSON body")] = None,
) -> OverrideResponse:
    return OverrideResponse.from_domain(service.cancel(override_id, user, _reason(body, reason)))


@router.delete(
    "/expedites/{expedite_id}",
    response_model=ExpediteResponse,
    summary="Cancel an expedite",
    tags=["overrides"],
)
def cancel_expedite(
    expedite_id: str,
    user: Manager,
    service: ExpediteServiceDep,
    body: CancelRequest | None = None,
    reason: Annotated[str | None, Query(description="Alternative to the JSON body")] = None,
) -> ExpediteResponse:
    return ExpediteResponse.from_domain(service.cancel(expedite_id, user, _reason(body, reason)))


@router.get(
    "/expedites", response_model=list[ExpediteResponse], summary="Active expedites", tags=["overrides"]
)
def list_expedites(
    _user: Reader, service: ExpediteServiceDep, order_id: str | None = None
) -> list[ExpediteResponse]:
    return [ExpediteResponse.from_domain(e) for e in service.list_active(order_id)]


@router.get(
    "/overrides", response_model=list[OverrideResponse], summary="Active overrides", tags=["overrides"]
)
def list_overrides(_user: Reader, service: OverrideServiceDep) -> list[OverrideResponse]:
    return [OverrideResponse.from_domain(o) for o in service.list_active()]


__all__ = ["router"]
