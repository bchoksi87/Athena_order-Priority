"""Typed repositories: the only place that issues SQL. They accept and return
domain objects (``app.domain``) or the light records in ``app.db.records``."""

from __future__ import annotations

from app.db.repositories.alerts import AlertRepository
from app.db.repositories.audit import AuditFilters, AuditRepository
from app.db.repositories.base import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Repository
from app.db.repositories.config import ConfigRepository
from app.db.repositories.customers import CustomerRepository
from app.db.repositories.data_quality import DataQualityRepository
from app.db.repositories.orders import OrderFilters, OrderRepository
from app.db.repositories.overlays import ExpediteRepository, LockRepository, OverrideRepository
from app.db.repositories.priority import PriorityResultRepository
from app.db.repositories.resources import (
    CalendarRepository,
    MachineRepository,
    MaterialRepository,
    ToolingRepository,
)
from app.db.repositories.schedule import (
    SCHEDULE_TRANSITIONS,
    OptimizationRunRepository,
    ScheduleRepository,
)
from app.db.repositories.snapshots import SnapshotRepository
from app.db.repositories.sync_runs import SyncRunRepository
from app.db.repositories.users import UserRepository

__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "SCHEDULE_TRANSITIONS",
    "AlertRepository",
    "AuditFilters",
    "AuditRepository",
    "CalendarRepository",
    "ConfigRepository",
    "CustomerRepository",
    "DataQualityRepository",
    "ExpediteRepository",
    "LockRepository",
    "MachineRepository",
    "MaterialRepository",
    "OptimizationRunRepository",
    "OrderFilters",
    "OrderRepository",
    "OverrideRepository",
    "PriorityResultRepository",
    "Repository",
    "ScheduleRepository",
    "SnapshotRepository",
    "SyncRunRepository",
    "ToolingRepository",
    "UserRepository",
]
