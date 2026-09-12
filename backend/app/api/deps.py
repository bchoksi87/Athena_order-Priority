"""FastAPI dependencies: settings, DB session, clock, current user and role guards.

Other routers import from here only; nothing here holds module-level state
(everything hangs off ``request.app.state`` which ``create_app`` populates).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.clock import Clock, SystemClock
from app.core.config import Settings, get_settings
from app.core.db import get_session
from app.core.errors import AuthenticationError
from app.core.logging import bind_request_context
from app.core.security import CurrentUser, decode_access_token, ensure_min_role, ensure_read_access
from app.domain.enums import Role
from app.engines.pipeline import PlanningPipeline
from app.integration.connector import ERPConnector
from app.services.alert_service import AlertService
from app.services.analytics_service import AnalyticsService
from app.services.audit_service import AuditService
from app.services.config_service import ConfigService
from app.services.customer_rule_service import CustomerRuleService
from app.services.data_quality_service import DataQualityService
from app.services.expedite_service import ExpediteService
from app.services.lock_service import LockService
from app.services.machine_query_service import MachineQueryService
from app.services.order_query_service import OrderQueryService
from app.services.override_service import OverrideService
from app.services.replanning_service import ReplanningService
from app.services.schedule_service import ScheduleService
from app.services.schedule_view_service import ScheduleViewService
from app.services.simulation_service import SimulationService
from app.services.snapshot_service import SnapshotService
from app.services.sync_admin_service import SyncAdminService, build_connector
from app.services.user_service import UserService
from app.services.writeback_service import WritebackService

_bearer = HTTPBearer(auto_error=False)


def get_app_settings(request: Request) -> Settings:
    """Settings attached to the app at start-up (falls back to the cached global)."""
    settings: Settings | None = getattr(request.app.state, "settings", None)
    return settings or get_settings()


def get_clock(request: Request) -> Clock:
    clock: Clock | None = getattr(request.app.state, "clock", None)
    return clock or SystemClock()


def get_db(request: Request) -> Iterator[Session]:
    yield from get_session(request)


def get_request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> CurrentUser:
    """Validate the bearer token and return the principal; binds ``user_id`` to logs."""
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise AuthenticationError("missing bearer token")
    payload = decode_access_token(
        credentials.credentials,
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        now=clock.now(),
    )
    user = payload.user
    request_id = getattr(request.state, "request_id", None)
    if request_id:
        bind_request_context(request_id, user_id=user.user_id, role=user.role.value)
    return user


def require_min_role(role: Role) -> Callable[..., CurrentUser]:
    """Dependency factory: the caller must hold ``role`` or a higher one."""

    def _guard(user: Annotated[CurrentUser, Depends(get_current_user)]) -> CurrentUser:
        ensure_min_role(user, role)
        return user

    return _guard


def require_read_access(min_role: Role) -> Callable[..., CurrentUser]:
    """Dependency factory for read endpoints: ``min_role``+ **or** the executive role."""

    def _guard(user: Annotated[CurrentUser, Depends(get_current_user)]) -> CurrentUser:
        ensure_read_access(user, min_role)
        return user

    return _guard


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
SessionDep = Annotated[Session, Depends(get_db)]
ClockDep = Annotated[Clock, Depends(get_clock)]
CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]

__all__ = [
    "ClockDep",
    "CurrentUserDep",
    "SessionDep",
    "SettingsDep",
    "get_app_settings",
    "get_clock",
    "get_current_user",
    "get_db",
    "get_request_id",
    "require_min_role",
    "require_read_access",
]


# ------------------------------------------------------------------ services
# Service factories: one instance per request, sharing the request's session/clock.


def get_audit_service(
    session: SessionDep, clock: ClockDep, request_id: Annotated[str | None, Depends(get_request_id)]
) -> AuditService:
    return AuditService(session, clock, request_id=request_id)


AuditServiceDep = Annotated[AuditService, Depends(get_audit_service)]


def get_snapshot_service(session: SessionDep, clock: ClockDep) -> SnapshotService:
    return SnapshotService(session, clock)


SnapshotServiceDep = Annotated[SnapshotService, Depends(get_snapshot_service)]


def get_override_service(
    session: SessionDep, clock: ClockDep, audit: AuditServiceDep, snapshots: SnapshotServiceDep
) -> OverrideService:
    return OverrideService(session, clock, audit, snapshots)


def get_lock_service(
    session: SessionDep, clock: ClockDep, audit: AuditServiceDep, snapshots: SnapshotServiceDep
) -> LockService:
    return LockService(session, clock, audit, snapshots)


def get_expedite_service(
    session: SessionDep, clock: ClockDep, audit: AuditServiceDep, snapshots: SnapshotServiceDep
) -> ExpediteService:
    return ExpediteService(session, clock, audit, snapshots)


def get_config_service(
    session: SessionDep, clock: ClockDep, audit: AuditServiceDep, snapshots: SnapshotServiceDep
) -> ConfigService:
    return ConfigService(session, clock, audit, snapshots)


def get_customer_rule_service(
    session: SessionDep, clock: ClockDep, audit: AuditServiceDep
) -> CustomerRuleService:
    return CustomerRuleService(session, clock, audit)


def get_alert_service(session: SessionDep, clock: ClockDep, audit: AuditServiceDep) -> AlertService:
    return AlertService(session, clock, audit)


def get_order_query_service(
    session: SessionDep, clock: ClockDep, snapshots: SnapshotServiceDep
) -> OrderQueryService:
    return OrderQueryService(session, clock, snapshots)


def get_machine_query_service(session: SessionDep, clock: ClockDep) -> MachineQueryService:
    return MachineQueryService(session, clock)


def get_data_quality_service(
    session: SessionDep, clock: ClockDep, snapshots: SnapshotServiceDep, audit: AuditServiceDep
) -> DataQualityService:
    return DataQualityService(session, clock, snapshots, audit)


def get_user_service(session: SessionDep, clock: ClockDep, audit: AuditServiceDep) -> UserService:
    return UserService(session, clock, audit)


OverrideServiceDep = Annotated[OverrideService, Depends(get_override_service)]
LockServiceDep = Annotated[LockService, Depends(get_lock_service)]
ExpediteServiceDep = Annotated[ExpediteService, Depends(get_expedite_service)]
ConfigServiceDep = Annotated[ConfigService, Depends(get_config_service)]
CustomerRuleServiceDep = Annotated[CustomerRuleService, Depends(get_customer_rule_service)]
AlertServiceDep = Annotated[AlertService, Depends(get_alert_service)]
OrderQueryServiceDep = Annotated[OrderQueryService, Depends(get_order_query_service)]
MachineQueryServiceDep = Annotated[MachineQueryService, Depends(get_machine_query_service)]
DataQualityServiceDep = Annotated[DataQualityService, Depends(get_data_quality_service)]
UserServiceDep = Annotated[UserService, Depends(get_user_service)]

__all__ += [
    "AlertServiceDep",
    "AuditServiceDep",
    "ConfigServiceDep",
    "CustomerRuleServiceDep",
    "DataQualityServiceDep",
    "ExpediteServiceDep",
    "LockServiceDep",
    "MachineQueryServiceDep",
    "OrderQueryServiceDep",
    "OverrideServiceDep",
    "SnapshotServiceDep",
    "UserServiceDep",
    "get_alert_service",
    "get_audit_service",
    "get_config_service",
    "get_customer_rule_service",
    "get_data_quality_service",
    "get_expedite_service",
    "get_lock_service",
    "get_machine_query_service",
    "get_order_query_service",
    "get_override_service",
    "get_snapshot_service",
    "get_user_service",
]


# ------------------------------------------------- planning / analytics / sync
# Appended by the schedule/analytics/sync owner. The planning pipeline and the ERP
# connector are built once per application (``app.state``); services stay per request.


def get_pipeline(request: Request, clock: ClockDep) -> PlanningPipeline:
    pipeline: PlanningPipeline | None = getattr(request.app.state, "pipeline", None)
    if pipeline is None:
        pipeline = PlanningPipeline(clock)
        request.app.state.pipeline = pipeline
    return pipeline


def get_connector(request: Request, settings: SettingsDep, clock: ClockDep) -> ERPConnector:
    connector: ERPConnector | None = getattr(request.app.state, "connector", None)
    if connector is None:
        connector = build_connector(settings, clock)
        request.app.state.connector = connector
    return connector


PipelineDep = Annotated[PlanningPipeline, Depends(get_pipeline)]
ConnectorDep = Annotated[ERPConnector, Depends(get_connector)]


def get_writeback_service(session: SessionDep, clock: ClockDep, settings: SettingsDep) -> WritebackService:
    return WritebackService(session, clock, settings)


WritebackServiceDep = Annotated[WritebackService, Depends(get_writeback_service)]


def get_schedule_service(
    session: SessionDep,
    clock: ClockDep,
    settings: SettingsDep,
    pipeline: PipelineDep,
    audit: AuditServiceDep,
    snapshots: SnapshotServiceDep,
    writeback: WritebackServiceDep,
) -> ScheduleService:
    return ScheduleService(
        session, clock, settings, pipeline, audit=audit, snapshots=snapshots, writeback=writeback
    )


ScheduleServiceDep = Annotated[ScheduleService, Depends(get_schedule_service)]


def get_schedule_view_service(
    session: SessionDep, clock: ClockDep, schedules: ScheduleServiceDep
) -> ScheduleViewService:
    return ScheduleViewService(session, clock, schedules)


def get_analytics_service(
    session: SessionDep, clock: ClockDep, snapshots: SnapshotServiceDep
) -> AnalyticsService:
    return AnalyticsService(session, clock, snapshots)


def get_simulation_service(
    session: SessionDep,
    clock: ClockDep,
    audit: AuditServiceDep,
    snapshots: SnapshotServiceDep,
    pipeline: PipelineDep,
) -> SimulationService:
    return SimulationService(session, clock, audit, snapshots, pipeline)


def get_replanning_service(
    session: SessionDep,
    clock: ClockDep,
    settings: SettingsDep,
    schedules: ScheduleServiceDep,
    audit: AuditServiceDep,
    snapshots: SnapshotServiceDep,
) -> ReplanningService:
    return ReplanningService(session, clock, settings, schedules, audit=audit, snapshots=snapshots)


def get_sync_admin_service(
    session: SessionDep,
    clock: ClockDep,
    settings: SettingsDep,
    connector: ConnectorDep,
    audit: AuditServiceDep,
) -> SyncAdminService:
    return SyncAdminService(session, clock, settings, connector, audit=audit)


ScheduleViewServiceDep = Annotated[ScheduleViewService, Depends(get_schedule_view_service)]
AnalyticsServiceDep = Annotated[AnalyticsService, Depends(get_analytics_service)]
SimulationServiceDep = Annotated[SimulationService, Depends(get_simulation_service)]
ReplanningServiceDep = Annotated[ReplanningService, Depends(get_replanning_service)]
SyncAdminServiceDep = Annotated[SyncAdminService, Depends(get_sync_admin_service)]

__all__ += [
    "AnalyticsServiceDep",
    "ConnectorDep",
    "PipelineDep",
    "ReplanningServiceDep",
    "ScheduleServiceDep",
    "ScheduleViewServiceDep",
    "SimulationServiceDep",
    "SyncAdminServiceDep",
    "WritebackServiceDep",
    "get_analytics_service",
    "get_connector",
    "get_pipeline",
    "get_replanning_service",
    "get_schedule_service",
    "get_schedule_view_service",
    "get_simulation_service",
    "get_sync_admin_service",
    "get_writeback_service",
]
