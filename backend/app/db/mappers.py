"""ORM row <-> domain dataclass conversions.

Every ``*_to_row`` function accepts an optional existing row and updates it in
place (used by upserts); without one it creates a new row. Every ``*_from_row``
returns a fresh domain object. Sets, tuples, enums and datetimes round-trip
exactly (see ``tests/unit/test_db_mappers.py``).
"""

from __future__ import annotations

from collections.abc import Iterable

from app.core.ids import new_id
from app.db.mappers_results import (
    expedite_from_row,
    expedite_to_row,
    lock_from_row,
    lock_to_row,
    override_from_row,
    override_to_row,
    priority_result_from_row,
    priority_result_to_row,
    schedule_entry_from_row,
    schedule_entry_to_row,
)
from app.db.models import (
    DOWNTIME_KIND_MAINTENANCE,
    DOWNTIME_KIND_PLANNED,
    DOWNTIME_KIND_UNPLANNED,
    CalendarSpecRow,
    CustomerRow,
    CustomerRuleRow,
    MachineDowntimeRow,
    MachineRow,
    MaterialRow,
    OperationRow,
    OrderRow,
    ToolingRow,
)
from app.db.snapshot_codec import (
    decode_date,
    decode_shift,
    decode_time_window,
    encode_date,
    encode_shift,
    encode_time_window,
)
from app.domain.enums import (
    CustomerTier,
    MachineStatus,
    MaterialStatus,
    OperationStatus,
    OrderStatus,
    PaymentRisk,
    ProcessType,
    QualityStatus,
    ShippingStatus,
)
from app.domain.models import (
    CalendarSpec,
    Customer,
    CustomerRule,
    Machine,
    Material,
    Operation,
    Order,
    TimeWindow,
    Tooling,
)


def _sorted(values: Iterable[str]) -> list[str]:
    return sorted(values)


def _strset(values: Iterable[str] | None) -> set[str]:
    return set(values or ())


# ------------------------------------------------------------------ customers


def customer_to_row(customer: Customer, row: CustomerRow | None = None) -> CustomerRow:
    row = row or CustomerRow(customer_id=customer.customer_id)
    row.customer_name = customer.customer_name
    row.customer_category = customer.customer_category
    row.customer_tier = customer.customer_tier.value
    row.customer_priority = customer.customer_priority
    row.strategic_customer_flag = customer.strategic_customer_flag
    row.annual_revenue = customer.annual_revenue
    row.customer_revenue = customer.customer_revenue
    row.customer_profitability = customer.customer_profitability
    row.customer_service_level = customer.customer_service_level
    row.sla_hours = customer.sla_hours
    row.escalation_level = customer.escalation_level
    row.historical_on_time_delivery = customer.historical_on_time_delivery
    row.payment_risk = customer.payment_risk.value
    row.preferred_delivery_expectation = customer.preferred_delivery_expectation
    row.account_manager = customer.account_manager
    row.active = customer.active
    row.external_ref = customer.external_ref
    row.attributes = dict(customer.attributes)
    return row


def customer_from_row(row: CustomerRow) -> Customer:
    return Customer(
        customer_id=row.customer_id,
        customer_name=row.customer_name,
        customer_category=row.customer_category,
        customer_tier=CustomerTier(row.customer_tier),
        customer_priority=row.customer_priority,
        strategic_customer_flag=row.strategic_customer_flag,
        annual_revenue=row.annual_revenue,
        customer_revenue=row.customer_revenue,
        customer_profitability=row.customer_profitability,
        customer_service_level=row.customer_service_level,
        sla_hours=row.sla_hours,
        escalation_level=row.escalation_level,
        historical_on_time_delivery=row.historical_on_time_delivery,
        payment_risk=PaymentRisk(row.payment_risk),
        preferred_delivery_expectation=row.preferred_delivery_expectation,
        account_manager=row.account_manager,
        active=row.active,
        external_ref=row.external_ref,
        attributes=dict(row.attributes or {}),
    )


def customer_rule_to_row(rule: CustomerRule, row: CustomerRuleRow | None = None) -> CustomerRuleRow:
    row = row or CustomerRuleRow(customer_id=rule.customer_id)
    row.sla_hours = rule.sla_hours
    row.tier_override = rule.tier_override.value if rule.tier_override else None
    row.priority_boost_points = rule.priority_boost_points
    row.notes = rule.notes
    row.active = rule.active
    return row


def customer_rule_from_row(row: CustomerRuleRow) -> CustomerRule:
    return CustomerRule(
        customer_id=row.customer_id,
        sla_hours=row.sla_hours,
        tier_override=CustomerTier(row.tier_override) if row.tier_override else None,
        priority_boost_points=row.priority_boost_points,
        notes=row.notes,
        active=row.active,
    )


# --------------------------------------------------------------------- orders


def order_to_row(order: Order, row: OrderRow | None = None) -> OrderRow:
    row = row or OrderRow(order_id=order.order_id)
    row.customer_id = order.customer_id
    row.part_id = order.part_id
    row.order_line_id = order.order_line_id
    row.external_order_ref = order.external_order_ref
    row.part_name = order.part_name
    row.part_family = order.part_family
    row.order_date = order.order_date
    row.received_date = order.received_date
    row.requested_delivery_date = order.requested_delivery_date
    row.promised_delivery_date = order.promised_delivery_date
    row.revised_delivery_date = order.revised_delivery_date
    row.due_date = order.due_date
    row.quantity = order.quantity
    row.completed_quantity = order.completed_quantity
    row.cancelled_quantity = order.cancelled_quantity
    row.order_status = order.order_status.value
    row.erp_priority = order.erp_priority
    row.production_status = order.production_status
    row.material_status = order.material_status.value
    row.quality_status = order.quality_status.value
    row.shipping_status = order.shipping_status.value
    row.order_value = order.order_value
    row.estimated_cost = order.estimated_cost
    row.estimated_margin = order.estimated_margin
    row.actual_margin = order.actual_margin
    row.process_type = order.process_type.value
    row.manufacturing_route = [p.value for p in order.manufacturing_route]
    row.machine_group = order.machine_group
    row.required_machine_id = order.required_machine_id
    row.required_material_id = order.required_material_id
    row.tooling_requirement = _sorted(order.tooling_requirement)
    row.estimated_setup_minutes = order.estimated_setup_minutes
    row.estimated_cycle_minutes_per_unit = order.estimated_cycle_minutes_per_unit
    row.estimated_total_production_minutes = order.estimated_total_production_minutes
    row.customer_priority = order.customer_priority
    row.technical_priority = order.technical_priority
    row.commercial_priority = order.commercial_priority
    row.lateness_penalty_per_day = order.lateness_penalty_per_day
    row.sla_hours = order.sla_hours
    row.special_instructions = order.special_instructions
    row.drawing_approved = order.drawing_approved
    row.on_hold = order.on_hold
    row.hold_reason = order.hold_reason
    row.depends_on_order_ids = _sorted(order.depends_on_order_ids)
    row.surface_finish = order.surface_finish
    row.technology = order.technology
    row.attributes = dict(order.attributes)
    return row


def order_from_row(row: OrderRow) -> Order:
    return Order(
        order_id=row.order_id,
        customer_id=row.customer_id,
        part_id=row.part_id,
        order_line_id=row.order_line_id,
        external_order_ref=row.external_order_ref,
        part_name=row.part_name,
        part_family=row.part_family,
        order_date=row.order_date,
        received_date=row.received_date,
        requested_delivery_date=row.requested_delivery_date,
        promised_delivery_date=row.promised_delivery_date,
        revised_delivery_date=row.revised_delivery_date,
        quantity=row.quantity,
        completed_quantity=row.completed_quantity,
        cancelled_quantity=row.cancelled_quantity,
        order_status=OrderStatus(row.order_status),
        erp_priority=row.erp_priority,
        production_status=row.production_status,
        material_status=MaterialStatus(row.material_status),
        quality_status=QualityStatus(row.quality_status),
        shipping_status=ShippingStatus(row.shipping_status),
        order_value=row.order_value,
        estimated_cost=row.estimated_cost,
        estimated_margin=row.estimated_margin,
        actual_margin=row.actual_margin,
        process_type=ProcessType(row.process_type),
        manufacturing_route=[ProcessType(p) for p in row.manufacturing_route or []],
        machine_group=row.machine_group,
        required_machine_id=row.required_machine_id,
        required_material_id=row.required_material_id,
        tooling_requirement=_strset(row.tooling_requirement),
        estimated_setup_minutes=row.estimated_setup_minutes,
        estimated_cycle_minutes_per_unit=row.estimated_cycle_minutes_per_unit,
        estimated_total_production_minutes=row.estimated_total_production_minutes,
        customer_priority=row.customer_priority,
        technical_priority=row.technical_priority,
        commercial_priority=row.commercial_priority,
        lateness_penalty_per_day=row.lateness_penalty_per_day,
        sla_hours=row.sla_hours,
        special_instructions=row.special_instructions,
        drawing_approved=row.drawing_approved,
        on_hold=row.on_hold,
        hold_reason=row.hold_reason,
        depends_on_order_ids=_strset(row.depends_on_order_ids),
        surface_finish=row.surface_finish,
        technology=row.technology,
        attributes=dict(row.attributes or {}),
    )


def operation_to_row(op: Operation, row: OperationRow | None = None) -> OperationRow:
    row = row or OperationRow(operation_id=op.operation_id)
    row.order_id = op.order_id
    row.sequence = op.sequence
    row.operation_type = op.operation_type.value
    row.machine_group = op.machine_group
    row.machine_id = op.machine_id
    row.eligible_machine_ids = _sorted(op.eligible_machine_ids)
    row.setup_minutes = op.setup_minutes
    row.cycle_minutes_per_unit = op.cycle_minutes_per_unit
    row.machine_cycle_minutes = dict(op.machine_cycle_minutes)
    row.quantity = op.quantity
    row.completed_quantity = op.completed_quantity
    row.operation_status = op.operation_status.value
    row.prerequisite_operation_id = op.prerequisite_operation_id
    row.material_id = op.material_id
    row.material_quantity_per_unit = op.material_quantity_per_unit
    row.tooling_ids = _sorted(op.tooling_ids)
    row.operator_requirement = op.operator_requirement
    row.quality_requirement = op.quality_requirement
    row.setup_family = op.setup_family
    row.estimated_start = op.estimated_start
    row.estimated_end = op.estimated_end
    row.actual_start = op.actual_start
    row.actual_end = op.actual_end
    row.attributes = dict(op.attributes)
    return row


def operation_from_row(row: OperationRow) -> Operation:
    return Operation(
        operation_id=row.operation_id,
        order_id=row.order_id,
        sequence=row.sequence,
        operation_type=ProcessType(row.operation_type),
        machine_group=row.machine_group,
        machine_id=row.machine_id,
        eligible_machine_ids=_strset(row.eligible_machine_ids),
        setup_minutes=row.setup_minutes,
        cycle_minutes_per_unit=row.cycle_minutes_per_unit,
        machine_cycle_minutes={k: float(v) for k, v in (row.machine_cycle_minutes or {}).items()},
        quantity=row.quantity,
        completed_quantity=row.completed_quantity,
        operation_status=OperationStatus(row.operation_status),
        prerequisite_operation_id=row.prerequisite_operation_id,
        material_id=row.material_id,
        material_quantity_per_unit=row.material_quantity_per_unit,
        tooling_ids=_strset(row.tooling_ids),
        operator_requirement=row.operator_requirement,
        quality_requirement=row.quality_requirement,
        setup_family=row.setup_family,
        estimated_start=row.estimated_start,
        estimated_end=row.estimated_end,
        actual_start=row.actual_start,
        actual_end=row.actual_end,
        attributes=dict(row.attributes or {}),
    )


# ------------------------------------------------------------------- machines


def machine_to_row(machine: Machine, row: MachineRow | None = None) -> MachineRow:
    """Map a machine; downtime lists become child ``MachineDowntimeRow`` rows."""
    row = row or MachineRow(machine_id=machine.machine_id)
    row.machine_name = machine.machine_name
    row.machine_type = machine.machine_type
    row.process_type = machine.process_type.value
    row.machine_group = machine.machine_group
    row.location = machine.location
    row.status = machine.status.value
    row.calendar_id = machine.calendar_id
    row.efficiency = machine.efficiency
    row.utilization = machine.utilization
    row.capacity_hours_per_day = machine.capacity_hours_per_day
    row.compatible_materials = _sorted(machine.compatible_materials)
    row.compatible_processes = sorted(p.value for p in machine.compatible_processes)
    row.max_part_size_mm = list(machine.max_part_size_mm) if machine.max_part_size_mm else None
    row.tooling_configuration = _sorted(machine.tooling_configuration)
    row.setup_requirements = dict(machine.setup_requirements)
    row.current_material_id = machine.current_material_id
    row.current_setup_family = machine.current_setup_family
    row.available_from = machine.available_from
    row.preferred_rank = machine.preferred_rank
    row.attributes = dict(machine.attributes)
    row.downtime = [
        *_downtime_rows(machine.machine_id, DOWNTIME_KIND_MAINTENANCE, machine.maintenance_windows),
        *_downtime_rows(machine.machine_id, DOWNTIME_KIND_PLANNED, machine.planned_downtime),
        *_downtime_rows(machine.machine_id, DOWNTIME_KIND_UNPLANNED, machine.unplanned_downtime),
    ]
    return row


def _downtime_rows(machine_id: str, kind: str, windows: Iterable[TimeWindow]) -> list[MachineDowntimeRow]:
    return [
        MachineDowntimeRow(
            downtime_id=new_id("dt"),
            machine_id=machine_id,
            kind=kind,
            start=w.start,
            end=w.end,
            reason=w.reason,
        )
        for w in windows
    ]


def machine_from_row(row: MachineRow, downtime: Iterable[MachineDowntimeRow] | None = None) -> Machine:
    """``downtime`` may be supplied explicitly to avoid lazy loads in bulk paths."""
    rows = list(downtime) if downtime is not None else list(row.downtime)
    by_kind: dict[str, list[TimeWindow]] = {
        DOWNTIME_KIND_MAINTENANCE: [],
        DOWNTIME_KIND_PLANNED: [],
        DOWNTIME_KIND_UNPLANNED: [],
    }
    for dt in sorted(rows, key=lambda r: (r.start, r.end, r.downtime_id)):
        by_kind.setdefault(dt.kind, []).append(TimeWindow(dt.start, dt.end, dt.reason))
    size = row.max_part_size_mm
    return Machine(
        machine_id=row.machine_id,
        machine_name=row.machine_name,
        machine_type=row.machine_type,
        process_type=ProcessType(row.process_type),
        machine_group=row.machine_group,
        location=row.location,
        status=MachineStatus(row.status),
        calendar_id=row.calendar_id,
        efficiency=row.efficiency,
        utilization=row.utilization,
        capacity_hours_per_day=row.capacity_hours_per_day,
        maintenance_windows=by_kind[DOWNTIME_KIND_MAINTENANCE],
        planned_downtime=by_kind[DOWNTIME_KIND_PLANNED],
        unplanned_downtime=by_kind[DOWNTIME_KIND_UNPLANNED],
        compatible_materials=_strset(row.compatible_materials),
        compatible_processes={ProcessType(p) for p in row.compatible_processes or []},
        max_part_size_mm=(float(size[0]), float(size[1]), float(size[2])) if size else None,
        tooling_configuration=_strset(row.tooling_configuration),
        setup_requirements=dict(row.setup_requirements or {}),
        current_material_id=row.current_material_id,
        current_setup_family=row.current_setup_family,
        available_from=row.available_from,
        preferred_rank=row.preferred_rank,
        attributes=dict(row.attributes or {}),
    )


# ------------------------------------------------------------------ resources


def material_to_row(material: Material, row: MaterialRow | None = None) -> MaterialRow:
    row = row or MaterialRow(material_id=material.material_id)
    row.material_name = material.material_name
    row.material_type = material.material_type
    row.grade = material.grade
    row.supplier = material.supplier
    row.unit = material.unit
    row.available_quantity = material.available_quantity
    row.reserved_quantity = material.reserved_quantity
    row.incoming_quantity = material.incoming_quantity
    row.expected_receipt_date = material.expected_receipt_date
    row.minimum_stock = material.minimum_stock
    row.compatible_machine_ids = _sorted(material.compatible_machine_ids)
    row.attributes = dict(material.attributes)
    return row


def material_from_row(row: MaterialRow) -> Material:
    return Material(
        material_id=row.material_id,
        material_name=row.material_name,
        material_type=row.material_type,
        grade=row.grade,
        supplier=row.supplier,
        unit=row.unit,
        available_quantity=row.available_quantity,
        reserved_quantity=row.reserved_quantity,
        incoming_quantity=row.incoming_quantity,
        expected_receipt_date=row.expected_receipt_date,
        minimum_stock=row.minimum_stock,
        compatible_machine_ids=_strset(row.compatible_machine_ids),
        attributes=dict(row.attributes or {}),
    )


def tooling_to_row(tooling: Tooling, row: ToolingRow | None = None) -> ToolingRow:
    row = row or ToolingRow(tooling_id=tooling.tooling_id)
    row.tooling_name = tooling.tooling_name
    row.available = tooling.available
    row.available_from = tooling.available_from
    row.compatible_machine_ids = _sorted(tooling.compatible_machine_ids)
    row.setup_minutes = tooling.setup_minutes
    row.expected_life = tooling.expected_life
    row.current_usage = tooling.current_usage
    row.maintenance_status = tooling.maintenance_status
    row.attributes = dict(tooling.attributes)
    return row


def tooling_from_row(row: ToolingRow) -> Tooling:
    return Tooling(
        tooling_id=row.tooling_id,
        tooling_name=row.tooling_name,
        available=row.available,
        available_from=row.available_from,
        compatible_machine_ids=_strset(row.compatible_machine_ids),
        setup_minutes=row.setup_minutes,
        expected_life=row.expected_life,
        current_usage=row.current_usage,
        maintenance_status=row.maintenance_status,
        attributes=dict(row.attributes or {}),
    )


# ------------------------------------------------------------------ calendars


def calendar_to_row(spec: CalendarSpec, row: CalendarSpecRow | None = None) -> CalendarSpecRow:
    row = row or CalendarSpecRow(calendar_id=spec.calendar_id)
    row.name = spec.name
    row.timezone = spec.timezone
    row.shifts = [encode_shift(s) for s in spec.shifts]
    row.holidays = [encode_date(d) for d in spec.holidays]
    row.overtime_windows = [encode_time_window(w) for w in spec.overtime_windows]
    row.extra_working_days = [encode_date(d) for d in spec.extra_working_days]
    return row


def calendar_from_row(row: CalendarSpecRow) -> CalendarSpec:
    return CalendarSpec(
        calendar_id=row.calendar_id,
        name=row.name,
        timezone=row.timezone,
        shifts=[decode_shift(s) for s in row.shifts or []],
        holidays=[decode_date(d) for d in row.holidays or []],
        overtime_windows=[decode_time_window(w) for w in row.overtime_windows or []],
        extra_working_days=[decode_date(d) for d in row.extra_working_days or []],
    )


__all__ = [
    "calendar_from_row",
    "calendar_to_row",
    "customer_from_row",
    "customer_rule_from_row",
    "customer_rule_to_row",
    "customer_to_row",
    "expedite_from_row",
    "expedite_to_row",
    "lock_from_row",
    "lock_to_row",
    "machine_from_row",
    "machine_to_row",
    "material_from_row",
    "material_to_row",
    "operation_from_row",
    "operation_to_row",
    "order_from_row",
    "order_to_row",
    "override_from_row",
    "override_to_row",
    "priority_result_from_row",
    "priority_result_to_row",
    "schedule_entry_from_row",
    "schedule_entry_to_row",
    "tooling_from_row",
    "tooling_to_row",
]
