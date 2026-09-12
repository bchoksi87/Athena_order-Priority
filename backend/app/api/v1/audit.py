"""``/audit``: the audit trail (spec Phase 22)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import AuditServiceDep, require_min_role
from app.api.schemas.audit import AuditEntryResponse
from app.api.schemas.common import PageResponse
from app.core.security import CurrentUser
from app.db.repositories.audit import AuditFilters
from app.domain.enums import Role
from app.services.base import Pagination

router = APIRouter(prefix="/audit", tags=["audit"])

Manager = Annotated[CurrentUser, Depends(require_min_role(Role.PRODUCTION_MANAGER))]


@router.get("", response_model=PageResponse[AuditEntryResponse], summary="Query the audit log")
def query_audit(
    _user: Manager,
    service: AuditServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=500)] = 50,
    entity_type: str | None = None,
    entity_id: str | None = None,
    user: Annotated[str | None, Query(description="Acting user id")] = None,
    action: str | None = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
) -> PageResponse[AuditEntryResponse]:
    filters = AuditFilters(
        user_id=user, entity_type=entity_type, entity_id=entity_id, action=action, since=from_, until=to
    )
    result = service.query(filters, Pagination(page, page_size))
    return PageResponse.build(
        [AuditEntryResponse.from_record(e) for e in result.items],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
    )


__all__ = ["router"]
