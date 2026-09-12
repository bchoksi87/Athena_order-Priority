"""Application services: use cases that orchestrate engines, repositories and the audit trail.

Every service receives its collaborators through the constructor (contract §4,
DI) and is wired for FastAPI in :mod:`app.api.deps`.
"""

from __future__ import annotations

from app.services.alert_service import AlertFilters, AlertService, AlertSummary
from app.services.audit_service import AuditService, json_diff
from app.services.base import PagedResult, Pagination, Service, require_reason
from app.services.config_service import ConfigService, ConfigUpdate
from app.services.customer_rule_service import CustomerRuleService, CustomerWithRule
from app.services.data_quality_service import DataQualityIssueFilters, DataQualityOverview, DataQualityService
from app.services.expedite_service import ExpediteService
from app.services.lock_service import LockService
from app.services.machine_query_service import MachineQueryService
from app.services.order_query_service import OrderListFilters, OrderQueryService
from app.services.override_service import MoveResult, OverrideService
from app.services.snapshot_service import SnapshotService, effective_hold
from app.services.user_service import UserService

__all__ = [
    "AlertFilters",
    "AlertService",
    "AlertSummary",
    "AuditService",
    "ConfigService",
    "ConfigUpdate",
    "CustomerRuleService",
    "CustomerWithRule",
    "DataQualityIssueFilters",
    "DataQualityOverview",
    "DataQualityService",
    "ExpediteService",
    "LockService",
    "MachineQueryService",
    "MoveResult",
    "OrderListFilters",
    "OrderQueryService",
    "OverrideService",
    "PagedResult",
    "Pagination",
    "Service",
    "SnapshotService",
    "UserService",
    "effective_hold",
    "json_diff",
    "require_reason",
]
