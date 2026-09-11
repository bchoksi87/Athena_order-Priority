"""Enumerations of the Normalized Manufacturing Data Model.

All enums are ``str`` based so that they serialise naturally to JSON and to
``String`` database columns (see docs/DESIGN_CONTRACT.md §7).
"""

from __future__ import annotations

from enum import StrEnum


class ProcessType(StrEnum):
    CNC_MACHINING = "cnc_machining"
    ADDITIVE_3D_PRINTING = "additive_3d_printing"
    SUPPORT_REMOVAL = "support_removal"
    DEBURRING = "deburring"
    FINISHING = "finishing"
    HEAT_TREATMENT = "heat_treatment"
    SURFACE_TREATMENT = "surface_treatment"
    INSPECTION = "inspection"
    ASSEMBLY = "assembly"
    PACKING = "packing"
    OTHER = "other"


class OrderStatus(StrEnum):
    """Production status of an order line (spec Phase 3, PRODUCTION STATUS)."""

    NEW = "new"
    RELEASED = "released"
    PLANNED = "planned"
    SCHEDULED = "scheduled"
    MATERIAL_WAITING = "material_waiting"
    TOOLING_WAITING = "tooling_waiting"
    IN_PRODUCTION = "in_production"
    PARTIALLY_COMPLETED = "partially_completed"
    QUALITY_INSPECTION = "quality_inspection"
    REWORK = "rework"
    COMPLETED = "completed"
    PACKED = "packed"
    SHIPPED = "shipped"
    ON_HOLD = "on_hold"
    CANCELLED = "cancelled"

    @property
    def is_open(self) -> bool:
        return self not in CLOSED_ORDER_STATUSES

    @property
    def is_schedulable(self) -> bool:
        return self in SCHEDULABLE_ORDER_STATUSES


CLOSED_ORDER_STATUSES: frozenset[OrderStatus] = frozenset(
    {OrderStatus.COMPLETED, OrderStatus.PACKED, OrderStatus.SHIPPED, OrderStatus.CANCELLED}
)

SCHEDULABLE_ORDER_STATUSES: frozenset[OrderStatus] = frozenset(
    {
        OrderStatus.NEW,
        OrderStatus.RELEASED,
        OrderStatus.PLANNED,
        OrderStatus.SCHEDULED,
        OrderStatus.MATERIAL_WAITING,
        OrderStatus.TOOLING_WAITING,
        OrderStatus.IN_PRODUCTION,
        OrderStatus.PARTIALLY_COMPLETED,
        OrderStatus.REWORK,
    }
)


class OperationStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ON_HOLD = "on_hold"
    REWORK = "rework"
    CANCELLED = "cancelled"

    @property
    def is_done(self) -> bool:
        return self in (OperationStatus.COMPLETED, OperationStatus.CANCELLED)


class ReadinessState(StrEnum):
    """Why an order can or cannot run right now (spec Phase 3, MATERIAL)."""

    READY = "ready"
    WAITING_MATERIAL = "waiting_material"
    WAITING_TOOLING = "waiting_tooling"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_PREVIOUS_OPERATION = "waiting_previous_operation"
    MACHINE_UNAVAILABLE = "machine_unavailable"
    QUALITY_HOLD = "quality_hold"
    ON_HOLD = "on_hold"
    OTHER_CONSTRAINT = "other_constraint"


class MachineStatus(StrEnum):
    AVAILABLE = "available"
    RUNNING = "running"
    DOWN = "down"
    MAINTENANCE = "maintenance"
    OFFLINE = "offline"

    @property
    def is_operable(self) -> bool:
        return self in (MachineStatus.AVAILABLE, MachineStatus.RUNNING)


class MaterialStatus(StrEnum):
    AVAILABLE = "available"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    ON_ORDER = "on_order"
    UNKNOWN = "unknown"


class QualityStatus(StrEnum):
    NONE = "none"
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    REWORK = "rework"
    HOLD = "hold"


class ShippingStatus(StrEnum):
    NOT_SHIPPED = "not_shipped"
    PARTIAL = "partial"
    SHIPPED = "shipped"


class CustomerTier(StrEnum):
    STRATEGIC = "strategic"
    KEY = "key"
    STANDARD = "standard"
    LOW = "low"


class PaymentRisk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ScheduleStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    PUBLISHED = "published"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class LockType(StrEnum):
    ORDER = "order"
    MACHINE = "machine"
    SEQUENCE = "sequence"
    TIME_SLOT = "time_slot"


class OverrideType(StrEnum):
    INCREASE_PRIORITY = "increase_priority"
    DECREASE_PRIORITY = "decrease_priority"
    SET_PRIORITY = "set_priority"
    FORCE_NEXT = "force_next"
    HOLD_ORDER = "hold_order"
    RELEASE_HOLD = "release_hold"
    MOVE_ORDER = "move_order"
    LOCK_MACHINE_ASSIGNMENT = "lock_machine_assignment"


class AlertSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    HIGH = "high"
    CRITICAL = "critical"


class AlertType(StrEnum):
    ORDER_LIKELY_LATE = "order_likely_late"
    ORDER_OVERDUE = "order_overdue"
    MACHINE_DOWNTIME = "machine_downtime"
    MATERIAL_SHORTAGE = "material_shortage"
    TOOL_SHORTAGE = "tool_shortage"
    CAPACITY_OVERLOAD = "capacity_overload"
    BOTTLENECK = "bottleneck"
    SLA_BREACH_RISK = "sla_breach_risk"
    PRODUCTION_BEHIND_SCHEDULE = "production_behind_schedule"
    SCHEDULE_DISRUPTION = "schedule_disruption"
    STARVATION = "starvation"
    DATA_QUALITY = "data_quality"


class Role(StrEnum):
    ADMIN = "admin"
    PRODUCTION_MANAGER = "production_manager"
    PLANNER = "planner"
    SUPERVISOR = "supervisor"
    OPERATOR = "operator"
    EXECUTIVE = "executive"


#: Role precedence used for "role X or higher" checks. Executive is read-only
#: and sits below operator for write purposes, but read endpoints grant it explicitly.
ROLE_RANK: dict[Role, int] = {
    Role.EXECUTIVE: 0,
    Role.OPERATOR: 1,
    Role.SUPERVISOR: 2,
    Role.PLANNER: 3,
    Role.PRODUCTION_MANAGER: 4,
    Role.ADMIN: 5,
}


class WritebackMode(StrEnum):
    READ_ONLY = "read_only"
    APPROVAL = "approval"
    WRITEBACK = "writeback"
    CONTROLLED_AUTO = "controlled_auto"


class DataQualitySeverity(StrEnum):
    BLOCKING = "blocking"
    WARNING = "warning"
    INFO = "info"


class DataQualityCode(StrEnum):
    MISSING_DUE_DATE = "missing_due_date"
    INVALID_DATE = "invalid_date"
    MISSING_CYCLE_TIME = "missing_cycle_time"
    MISSING_SETUP_TIME = "missing_setup_time"
    MISSING_MACHINE_ASSIGNMENT = "missing_machine_assignment"
    MISSING_MATERIAL = "missing_material"
    NEGATIVE_QUANTITY = "negative_quantity"
    DUPLICATE_ORDER = "duplicate_order"
    INCORRECT_STATUS = "incorrect_status"
    IMPOSSIBLE_PRODUCTION_TIME = "impossible_production_time"
    MISSING_CUSTOMER = "missing_customer"
    CONFLICTING_MACHINE_CAPABILITY = "conflicting_machine_capability"
    INVALID_ROUTING = "invalid_routing"
    MISSING_OPERATIONS = "missing_operations"
    UNKNOWN_REFERENCE = "unknown_reference"


class SyncMode(StrEnum):
    FULL = "full"
    INCREMENTAL = "incremental"


class ReplanTriggerType(StrEnum):
    NEW_ORDER = "new_order"
    ORDER_COMPLETED = "order_completed"
    MACHINE_DOWN = "machine_down"
    MACHINE_UP = "machine_up"
    MATERIAL_ARRIVED = "material_arrived"
    QUALITY_FAILURE = "quality_failure"
    REWORK = "rework"
    PRODUCTION_DELAY = "production_delay"
    CUSTOMER_PRIORITY_CHANGE = "customer_priority_change"
    MANUAL = "manual"
    CONFIG_CHANGE = "config_change"
    SCHEDULED = "scheduled"
