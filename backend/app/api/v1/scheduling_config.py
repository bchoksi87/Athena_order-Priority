"""``/scheduling/configuration``: scheduling, replanning, alert and data-quality rule sets."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import ConfigServiceDep, require_min_role, require_read_access
from app.api.schemas.config import (
    ActivateVersionRequest,
    ConfigUpdateResponse,
    ConfigVersionResponse,
    SchedulingConfigUpdateRequest,
    SchedulingConfigurationResponse,
    SystemConfigVersionResponse,
)
from app.core.errors import ValidationError
from app.core.security import CurrentUser
from app.domain.enums import Role
from app.services.config_service import ConfigUpdate

router = APIRouter(prefix="/scheduling/configuration", tags=["scheduling-configuration"])

Reader = Annotated[CurrentUser, Depends(require_read_access(Role.PLANNER))]
Admin = Annotated[CurrentUser, Depends(require_min_role(Role.ADMIN))]


@router.get("", response_model=SchedulingConfigurationResponse, summary="Active scheduling configuration")
def get_scheduling_configuration(_user: Reader, service: ConfigServiceDep) -> SchedulingConfigurationResponse:
    return SchedulingConfigurationResponse.build(service.get_active(), service.get_active_info())


@router.put("", response_model=ConfigUpdateResponse, summary="Save a new configuration version")
def update_scheduling_configuration(
    body: SchedulingConfigUpdateRequest, user: Admin, service: ConfigServiceDep
) -> ConfigUpdateResponse:
    """Each supplied section is saved as its own version (scheduling, replanning, alerts, data_quality)."""
    update: ConfigUpdate | None = None
    if body.scheduling is not None:
        update = service.update_scheduling_config(body.scheduling, user, body.reason)
    if body.replanning is not None:
        update = service.update_replanning(body.replanning, user, body.reason)
    if body.alerts is not None:
        update = service.update_alerts(body.alerts, user, body.reason)
    if body.data_quality is not None:
        update = service.update_data_quality(body.data_quality, user, body.reason)
    if update is None:
        raise ValidationError("provide at least one of scheduling, replanning, alerts, data_quality")
    return ConfigUpdateResponse.from_update(update)


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


__all__ = ["router"]
