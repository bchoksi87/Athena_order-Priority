"""SQLAlchemy ORM models (one module per aggregate). Importing this package
registers every table on ``Base.metadata``."""

from __future__ import annotations

from app.db.models.base import Base, TimestampMixin, UTCDateTime
from app.db.models.config import PriorityProfileRow, SchedulingConfigRow, SystemConfigRow
from app.db.models.customers import CustomerRow, CustomerRuleRow
from app.db.models.machines import (
    DOWNTIME_KIND_MAINTENANCE,
    DOWNTIME_KIND_PLANNED,
    DOWNTIME_KIND_UNPLANNED,
    DOWNTIME_KINDS,
    CalendarSpecRow,
    MachineDowntimeRow,
    MachineRow,
)
from app.db.models.operations import (
    AlertRow,
    AuditLogRow,
    DataQualityIssueRow,
    InputSnapshotRow,
    SyncRunRow,
)
from app.db.models.orders import OperationRow, OrderRow
from app.db.models.overlays import ExpediteRow, PriorityOverrideRow
from app.db.models.priority import PriorityResultRow
from app.db.models.resources import MaterialRow, ToolingRow
from app.db.models.schedule import (
    OptimizationRunRow,
    ScheduleEntryRow,
    ScheduleLockRow,
    ScheduleVersionRow,
)
from app.db.models.users import UserRow

__all__ = [
    "DOWNTIME_KINDS",
    "DOWNTIME_KIND_MAINTENANCE",
    "DOWNTIME_KIND_PLANNED",
    "DOWNTIME_KIND_UNPLANNED",
    "AlertRow",
    "AuditLogRow",
    "Base",
    "CalendarSpecRow",
    "CustomerRow",
    "CustomerRuleRow",
    "DataQualityIssueRow",
    "ExpediteRow",
    "InputSnapshotRow",
    "MachineDowntimeRow",
    "MachineRow",
    "MaterialRow",
    "OperationRow",
    "OptimizationRunRow",
    "OrderRow",
    "PriorityOverrideRow",
    "PriorityProfileRow",
    "PriorityResultRow",
    "ScheduleEntryRow",
    "ScheduleLockRow",
    "ScheduleVersionRow",
    "SchedulingConfigRow",
    "SyncRunRow",
    "SystemConfigRow",
    "TimestampMixin",
    "ToolingRow",
    "UTCDateTime",
    "UserRow",
]
