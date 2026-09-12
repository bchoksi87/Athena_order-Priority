"""``/priority/configuration``: the priority profile, its versions and the weight preview."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import ConfigServiceDep, require_min_role, require_read_access
from app.api.schemas.config import (
    ActivateVersionRequest,
    ConfigUpdateResponse,
    ConfigVersionResponse,
    PreviewRequest,
    PreviewResponse,
    PriorityConfigurationResponse,
    PriorityProfileUpdateRequest,
    SystemConfigVersionResponse,
)
from app.core.security import CurrentUser
from app.domain.enums import Role

router = APIRouter(prefix="/priority/configuration", tags=["priority-configuration"])

Reader = Annotated[CurrentUser, Depends(require_read_access(Role.PLANNER))]
Planner = Annotated[CurrentUser, Depends(require_min_role(Role.PLANNER))]
Admin = Annotated[CurrentUser, Depends(require_min_role(Role.ADMIN))]


@router.get("", response_model=PriorityConfigurationResponse, summary="Active priority profile")
def get_priority_configuration(_user: Reader, service: ConfigServiceDep) -> PriorityConfigurationResponse:
    return PriorityConfigurationResponse.build(service.get_active(), service.get_active_info())


@router.put("", response_model=ConfigUpdateResponse, summary="Save a new priority profile version")
def update_priority_configuration(
    body: PriorityProfileUpdateRequest, user: Admin, service: ConfigServiceDep
) -> ConfigUpdateResponse:
    return ConfigUpdateResponse.from_update(service.update_priority_profile(body.profile, user, body.reason))


@router.get("/versions", response_model=list[ConfigVersionResponse], summary="Configuration versions")
def list_versions(_user: Reader, service: ConfigServiceDep, limit: int = 100) -> list[ConfigVersionResponse]:
    return [ConfigVersionResponse.from_record(v) for v in service.list_versions(limit=limit)]


@router.get(
    "/versions/{version}", response_model=SystemConfigVersionResponse, summary="One configuration version"
)
def get_version(version: int, _user: Reader, service: ConfigServiceDep) -> SystemConfigVersionResponse:
    return SystemConfigVersionResponse(
        version=ConfigVersionResponse.from_record(service.get_version_info(version)),
        config=service.get_version(version),
    )


@router.post(
    "/versions/{version}/activate", response_model=ConfigUpdateResponse, summary="Roll back to a version"
)
def activate_version(
    version: int, body: ActivateVersionRequest, user: Admin, service: ConfigServiceDep
) -> ConfigUpdateResponse:
    return ConfigUpdateResponse.from_update(service.activate_version(version, user, body.reason))


@router.post("/preview", response_model=PreviewResponse, summary="Simulate a weight change")
def preview(body: PreviewRequest, _user: Planner, service: ConfigServiceDep) -> PreviewResponse:
    """'Changing Due Date weight from 30% to 40% would move 27 orders into the top 50.'"""
    return PreviewResponse.from_comparison(service.preview_profile(body.profile, top_n=body.top_n))


__all__ = ["router"]
