"""Normalized Manufacturing Data Model (pure Python, no I/O).

These dataclasses are the *only* input shape the engines understand. The ERP
integration layer converts whatever the ERP provides into these objects; the
persistence layer maps them to/from ORM rows. Field names follow the
specification (Phase 3). Durations are minutes, money is base-currency floats,
datetimes are timezone-aware UTC.

Optional fields default to ``None`` so that missing ERP data is represented
explicitly rather than by sentinel values; the Data Quality Engine reports on
them and the engines degrade gracefully.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from app.domain.enums import (
    CustomerTier,
    LockType,
    MachineStatus,
    MaterialStatus,
    OperationStatus,
    OrderStatus,
    OverrideType,
    PaymentRisk,
    ProcessType,
    QualityStatus,
    ShippingStatus,
)

# ---------------------------------------------------------------------------
# Time primitives
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class TimeWindow:
    """Half-open interval ``[start, end)`` in UTC."""

    start: datetime
    end: datetime
    reason: str = ""

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(f"TimeWindow end {self.end} before start {self.start}")

    @property
    def minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60.0

    def overlaps(self, other: TimeWindow) -> bool:
        return self.start < other.end and other.start < self.end

    def contains(self, t: datetime) -> bool:
        return self.start <= t < self.end

    def intersect(self, other: TimeWindow) -> TimeWindow | None:
        start = max(self.start, other.start)
        end = min(self.end, other.end)
        if start >= end:
            return None
        return TimeWindow(start, end, self.reason or other.reason)


@dataclass(slots=True, frozen=True)
class Shift:
    """A recurring working shift. ``weekdays`` uses Python convention (0=Monday)."""

    name: str
    start: time
    end: time
    weekdays: tuple[int, ...] = (0, 1, 2, 3, 4)

    @property
    def crosses_midnight(self) -> bool:
        return self.end <= self.start


@dataclass(slots=True)
class CalendarSpec:
    """Working-time definition for a machine, machine group or the whole plant."""

    calendar_id: str
    name: str
    timezone: str = "UTC"
    shifts: list[Shift] = field(default_factory=list)
    holidays: list[date] = field(default_factory=list)
    overtime_windows: list[TimeWindow] = field(default_factory=list)
    extra_working_days: list[date] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Master data
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Customer:
    customer_id: str
    customer_name: str
    customer_category: str = "standard"
    customer_tier: CustomerTier = CustomerTier.STANDARD
    customer_priority: int = 3  # 1 (highest) .. 5 (lowest), ERP-supplied
    strategic_customer_flag: bool = False
    annual_revenue: float | None = None
    customer_revenue: float | None = None  # trailing-12-month revenue with us
    customer_profitability: float | None = None  # contribution margin ratio 0..1
    customer_service_level: float | None = None  # target OTD ratio 0..1
    sla_hours: float | None = None  # contractual turnaround, if any
    escalation_level: int = 0  # 0 none .. 3 executive escalation
    historical_on_time_delivery: float | None = None  # 0..1
    payment_risk: PaymentRisk = PaymentRisk.UNKNOWN
    preferred_delivery_expectation: str | None = None
    account_manager: str | None = None
    active: bool = True
    external_ref: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Material:
    material_id: str
    material_name: str
    material_type: str = ""
    grade: str | None = None
    supplier: str | None = None
    unit: str = "kg"
    available_quantity: float = 0.0
    reserved_quantity: float = 0.0
    incoming_quantity: float = 0.0
    expected_receipt_date: datetime | None = None
    minimum_stock: float = 0.0
    compatible_machine_ids: set[str] = field(default_factory=set)
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def free_quantity(self) -> float:
        return max(0.0, self.available_quantity - self.reserved_quantity)


@dataclass(slots=True)
class Tooling:
    tooling_id: str
    tooling_name: str
    available: bool = True
    available_from: datetime | None = None
    compatible_machine_ids: set[str] = field(default_factory=set)
    setup_minutes: float = 0.0
    expected_life: float | None = None  # in usage units (cycles/minutes)
    current_usage: float | None = None
    maintenance_status: str = "ok"
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def is_usable(self) -> bool:
        if not self.available:
            return False
        if self.expected_life is not None and self.current_usage is not None:
            return self.current_usage < self.expected_life
        return True


@dataclass(slots=True)
class Machine:
    machine_id: str
    machine_name: str
    machine_type: str
    process_type: ProcessType
    machine_group: str
    location: str | None = None
    status: MachineStatus = MachineStatus.AVAILABLE
    calendar_id: str | None = None
    efficiency: float = 1.0  # multiplier on cycle time (0.8 = slower)
    utilization: float | None = None  # trailing utilization 0..1 from ERP
    capacity_hours_per_day: float | None = None
    maintenance_windows: list[TimeWindow] = field(default_factory=list)
    planned_downtime: list[TimeWindow] = field(default_factory=list)
    unplanned_downtime: list[TimeWindow] = field(default_factory=list)
    compatible_materials: set[str] = field(default_factory=set)
    compatible_processes: set[ProcessType] = field(default_factory=set)
    max_part_size_mm: tuple[float, float, float] | None = None
    tooling_configuration: set[str] = field(default_factory=set)  # tooling currently mounted
    setup_requirements: dict[str, Any] = field(default_factory=dict)
    current_material_id: str | None = None
    current_setup_family: str | None = None
    available_from: datetime | None = None  # earliest time new work can start
    preferred_rank: int = 0  # lower = more preferred within its group
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def all_downtime(self) -> list[TimeWindow]:
        return [*self.maintenance_windows, *self.planned_downtime, *self.unplanned_downtime]

    def supports_process(self, process: ProcessType) -> bool:
        return process == self.process_type or process in self.compatible_processes


# ---------------------------------------------------------------------------
# Orders and operations
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Operation:
    operation_id: str
    order_id: str
    sequence: int
    operation_type: ProcessType
    machine_group: str | None = None
    machine_id: str | None = None  # assigned or ERP-preferred machine
    eligible_machine_ids: set[str] = field(default_factory=set)  # explicit list, empty = derive
    setup_minutes: float | None = None
    cycle_minutes_per_unit: float | None = None
    machine_cycle_minutes: dict[str, float] = field(default_factory=dict)  # per-machine override
    quantity: float = 0.0
    completed_quantity: float = 0.0
    operation_status: OperationStatus = OperationStatus.PENDING
    prerequisite_operation_id: str | None = None
    material_id: str | None = None
    material_quantity_per_unit: float | None = None
    tooling_ids: set[str] = field(default_factory=set)
    operator_requirement: str | None = None
    quality_requirement: str | None = None
    setup_family: str | None = None  # jobs sharing a family need no changeover
    estimated_start: datetime | None = None
    estimated_end: datetime | None = None
    actual_start: datetime | None = None
    actual_end: datetime | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def pending_quantity(self) -> float:
        return max(0.0, self.quantity - self.completed_quantity)

    @property
    def is_done(self) -> bool:
        return self.operation_status.is_done or self.pending_quantity <= 0

    def cycle_minutes_on(self, machine_id: str) -> float | None:
        override = self.machine_cycle_minutes.get(machine_id)
        if override is not None:
            return override
        return self.cycle_minutes_per_unit

    def run_minutes_on(self, machine: Machine) -> float | None:
        cycle = self.cycle_minutes_on(machine.machine_id)
        if cycle is None:
            return None
        eff = machine.efficiency if machine.efficiency > 0 else 1.0
        return cycle * self.pending_quantity / eff


@dataclass(slots=True)
class Order:
    """One production order *line*. ``order_id`` is unique per line."""

    order_id: str
    customer_id: str
    part_id: str
    order_line_id: str | None = None
    external_order_ref: str | None = None
    part_name: str | None = None
    part_family: str | None = None
    order_date: datetime | None = None
    received_date: datetime | None = None
    requested_delivery_date: datetime | None = None
    promised_delivery_date: datetime | None = None
    revised_delivery_date: datetime | None = None
    quantity: float = 0.0
    completed_quantity: float = 0.0
    cancelled_quantity: float = 0.0
    order_status: OrderStatus = OrderStatus.NEW
    erp_priority: int | None = None  # priority as stored in the ERP
    production_status: str | None = None  # raw ERP production status text
    material_status: MaterialStatus = MaterialStatus.UNKNOWN
    quality_status: QualityStatus = QualityStatus.NONE
    shipping_status: ShippingStatus = ShippingStatus.NOT_SHIPPED
    order_value: float | None = None
    estimated_cost: float | None = None
    estimated_margin: float | None = None
    actual_margin: float | None = None
    process_type: ProcessType = ProcessType.OTHER
    manufacturing_route: list[ProcessType] = field(default_factory=list)
    machine_group: str | None = None
    required_machine_id: str | None = None
    required_material_id: str | None = None
    tooling_requirement: set[str] = field(default_factory=set)
    estimated_setup_minutes: float | None = None
    estimated_cycle_minutes_per_unit: float | None = None
    estimated_total_production_minutes: float | None = None
    customer_priority: int | None = None
    technical_priority: int | None = None
    commercial_priority: int | None = None
    lateness_penalty_per_day: float | None = None
    sla_hours: float | None = None
    special_instructions: str | None = None
    drawing_approved: bool = True
    on_hold: bool = False
    hold_reason: str | None = None
    depends_on_order_ids: set[str] = field(default_factory=set)  # must finish before this starts
    surface_finish: str | None = None
    technology: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def pending_quantity(self) -> float:
        return max(0.0, self.quantity - self.completed_quantity - self.cancelled_quantity)

    @property
    def due_date(self) -> datetime | None:
        """Effective due date: revised > promised > requested."""
        return self.revised_delivery_date or self.promised_delivery_date or self.requested_delivery_date

    @property
    def is_open(self) -> bool:
        return self.order_status.is_open and self.pending_quantity > 0

    def hours_until_due(self, now: datetime) -> float | None:
        due = self.due_date
        if due is None:
            return None
        return (due - now).total_seconds() / 3600.0


# ---------------------------------------------------------------------------
# Planner-controlled overlays
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ScheduleLock:
    lock_id: str
    lock_type: LockType
    created_by: str
    created_at: datetime
    reason: str
    order_id: str | None = None
    machine_id: str | None = None
    window: TimeWindow | None = None
    sequence_order_ids: list[str] = field(default_factory=list)
    active: bool = True


@dataclass(slots=True)
class PriorityOverride:
    override_id: str
    order_id: str
    override_type: OverrideType
    created_by: str
    created_at: datetime
    reason: str
    value: float | None = None  # delta points or absolute score depending on type
    target_machine_id: str | None = None
    expires_at: datetime | None = None
    active: bool = True

    def is_active_at(self, now: datetime) -> bool:
        return self.active and (self.expires_at is None or now < self.expires_at)


@dataclass(slots=True)
class Expedite:
    expedite_id: str
    order_id: str
    created_by: str
    created_at: datetime
    reason: str
    boost_points: float
    starts_at: datetime
    expires_at: datetime
    active: bool = True

    def is_active_at(self, now: datetime) -> bool:
        return self.active and self.starts_at <= now < self.expires_at


@dataclass(slots=True)
class CustomerRule:
    customer_id: str
    sla_hours: float | None = None
    tier_override: CustomerTier | None = None
    priority_boost_points: float = 0.0
    notes: str | None = None
    active: bool = True


__all__ = [
    "CalendarSpec",
    "Customer",
    "CustomerRule",
    "Expedite",
    "Machine",
    "Material",
    "Operation",
    "Order",
    "PriorityOverride",
    "ScheduleLock",
    "Shift",
    "TimeWindow",
    "Tooling",
    "timedelta",
]
