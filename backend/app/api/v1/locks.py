"""``/schedule/lock``, ``/schedule/unlock`` and ``/schedule/locks`` (spec Phase 10).

Mounted under ``/schedule`` so the schedule generation router can coexist.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps import LockServiceDep, require_min_role, require_read_access
from app.api.schemas.overlays import LockRequest, LockResponse, UnlockRequest
from app.core.security import CurrentUser
from app.domain.enums import LockType, Role

router = APIRouter(prefix="/schedule", tags=["locks"])

Reader = Annotated[CurrentUser, Depends(require_read_access(Role.OPERATOR))]
Manager = Annotated[CurrentUser, Depends(require_min_role(Role.PRODUCTION_MANAGER))]


@router.post(
    "/lock", response_model=LockResponse, status_code=status.HTTP_201_CREATED, summary="Create a lock"
)
def create_lock(body: LockRequest, user: Manager, service: LockServiceDep) -> LockResponse:
    """ORDER (pin), MACHINE (window, default now + lock_window_minutes), SEQUENCE, TIME_SLOT."""
    lock = service.create(
        body.lock_type,
        user,
        body.reason,
        order_id=body.order_id,
        machine_id=body.machine_id,
        window_start=body.window_start,
        window_end=body.window_end,
        sequence_order_ids=body.sequence_order_ids,
    )
    return LockResponse.from_domain(lock)


@router.post("/unlock", response_model=LockResponse, summary="Release a lock")
def unlock(body: UnlockRequest, user: Manager, service: LockServiceDep) -> LockResponse:
    return LockResponse.from_domain(service.unlock(body.lock_id, user, body.reason))


@router.get("/locks", response_model=list[LockResponse], summary="Active locks")
def list_locks(
    _user: Reader,
    service: LockServiceDep,
    machine_id: str | None = None,
    order_id: str | None = None,
    lock_type: LockType | None = None,
) -> list[LockResponse]:
    return [
        LockResponse.from_domain(lk)
        for lk in service.list_active(machine_id=machine_id, order_id=order_id, lock_type=lock_type)
    ]


__all__ = ["router"]
