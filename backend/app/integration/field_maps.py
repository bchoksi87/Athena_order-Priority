"""Declarative ERP -> domain field mappings.

Each entity has an ordered list of :class:`FieldMap` entries: which raw key
to read, which domain attribute to fill, how to parse it and whether the
record is unusable without it. Adapting to a real ERP export means editing
these tables (or supplying alternative ones to the :class:`Normalizer`), not
touching engine code.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final

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
from app.integration import codes
from app.integration.parsers import (
    Parser,
    make_enum_parser,
    parse_bool,
    parse_date_list,
    parse_datetime,
    parse_float,
    parse_float_map,
    parse_int,
    parse_percent,
    parse_percent_ratio,
    parse_shifts,
    parse_size_mm,
    parse_str,
    parse_str_list,
    parse_str_set,
    parse_windows,
)


class _Omit:
    """Sentinel: leave the domain dataclass default in place."""

    def __repr__(self) -> str:
        return "OMIT"


OMIT: Final = _Omit()


@dataclass(frozen=True, slots=True)
class FieldMap:
    source: str
    target: str
    parser: Parser
    required: bool = False
    default: Any = OMIT
    default_factory: Callable[[], Any] | None = None

    def fallback(self) -> Any:
        if self.default_factory is not None:
            return self.default_factory()
        return self.default


parse_order_status = make_enum_parser(codes.ORDER_STATUS_CODES, "order_status")
parse_operation_status = make_enum_parser(codes.OPERATION_STATUS_CODES, "operation_status")
parse_machine_status = make_enum_parser(codes.MACHINE_STATUS_CODES, "machine_status")
parse_material_status = make_enum_parser(codes.MATERIAL_STATUS_CODES, "material_status")
parse_quality_status = make_enum_parser(codes.QUALITY_STATUS_CODES, "quality_status")
parse_shipping_status = make_enum_parser(codes.SHIPPING_STATUS_CODES, "shipping_status")
parse_customer_tier = make_enum_parser(codes.CUSTOMER_TIER_CODES, "customer_tier")
parse_payment_risk = make_enum_parser(codes.PAYMENT_RISK_CODES, "payment_risk")
parse_process_type = make_enum_parser(codes.PROCESS_TYPE_CODES, "process_type")


def parse_route(value: Any) -> list[ProcessType]:
    """``"CNC>DEB>INS"`` (or ``;``-separated) -> list of process types."""
    text = str(value).replace(">", codes.LIST_SEPARATOR)
    return [parse_process_type(token) for token in parse_str_list(text)]


def parse_process_set(value: Any) -> set[ProcessType]:
    return set(parse_route(value))


CUSTOMER_FIELDS: tuple[FieldMap, ...] = (
    FieldMap("CUST_CODE", "customer_id", parse_str, required=True),
    FieldMap("CUST_NAME", "customer_name", parse_str, required=True),
    FieldMap("CUST_CAT", "customer_category", parse_str),
    FieldMap("TIER_CODE", "customer_tier", parse_customer_tier, default=CustomerTier.STANDARD),
    FieldMap("PRIO", "customer_priority", parse_int),
    FieldMap("STRATEGIC_FLAG", "strategic_customer_flag", parse_bool),
    FieldMap("ANNUAL_REV", "annual_revenue", parse_float),
    FieldMap("REV_12M", "customer_revenue", parse_float),
    FieldMap("PROFIT_PCT", "customer_profitability", parse_percent_ratio),
    FieldMap("SVC_LEVEL_PCT", "customer_service_level", parse_percent_ratio),
    FieldMap("SLA_HRS", "sla_hours", parse_float),
    FieldMap("ESC_LVL", "escalation_level", parse_int),
    FieldMap("OTD_PCT", "historical_on_time_delivery", parse_percent_ratio),
    FieldMap("PAY_RISK", "payment_risk", parse_payment_risk, default=PaymentRisk.UNKNOWN),
    FieldMap("DELIV_EXPECT", "preferred_delivery_expectation", parse_str),
    FieldMap("ACCT_MGR", "account_manager", parse_str),
    FieldMap("ACTIVE", "active", parse_bool),
    FieldMap("EXT_REF", "external_ref", parse_str),
)

#: Order maps deliberately omit ``order_id``: it is composed from ORDER_NO + LINE_NO.
ORDER_FIELDS: tuple[FieldMap, ...] = (
    FieldMap("ORDER_NO", "external_order_ref", parse_str, required=True),
    FieldMap("LINE_NO", "order_line_id", parse_str, required=True),
    FieldMap("CUST_CODE", "customer_id", parse_str, required=True),
    FieldMap("PART_NO", "part_id", parse_str, required=True),
    FieldMap("PART_DESC", "part_name", parse_str),
    FieldMap("PART_FAM", "part_family", parse_str),
    FieldMap("ORDER_DT", "order_date", parse_datetime),
    FieldMap("RECV_DT", "received_date", parse_datetime),
    FieldMap("REQ_DT", "requested_delivery_date", parse_datetime),
    FieldMap("PROM_DT", "promised_delivery_date", parse_datetime),
    FieldMap("REV_DT", "revised_delivery_date", parse_datetime),
    FieldMap("QTY", "quantity", parse_float, default=0.0),
    FieldMap("QTY_DONE", "completed_quantity", parse_float, default=0.0),
    FieldMap("QTY_CANC", "cancelled_quantity", parse_float, default=0.0),
    FieldMap("STATUS", "order_status", parse_order_status, default=OrderStatus.NEW),
    FieldMap("PRIO", "erp_priority", parse_int),
    FieldMap("PROD_STATUS", "production_status", parse_str),
    FieldMap("MAT_STATUS", "material_status", parse_material_status, default=MaterialStatus.UNKNOWN),
    FieldMap("QC_STATUS", "quality_status", parse_quality_status, default=QualityStatus.NONE),
    FieldMap("SHIP_STATUS", "shipping_status", parse_shipping_status, default=ShippingStatus.NOT_SHIPPED),
    FieldMap("ORDER_VAL", "order_value", parse_float),
    FieldMap("EST_COST", "estimated_cost", parse_float),
    FieldMap("EST_MARGIN", "estimated_margin", parse_float),
    FieldMap("ACT_MARGIN", "actual_margin", parse_float),
    FieldMap("PROC_TYPE", "process_type", parse_process_type, default=ProcessType.OTHER),
    FieldMap("ROUTE", "manufacturing_route", parse_route, default_factory=list),
    FieldMap("WC_GROUP", "machine_group", parse_str),
    FieldMap("REQ_MACHINE", "required_machine_id", parse_str),
    FieldMap("REQ_MATERIAL", "required_material_id", parse_str),
    FieldMap("TOOL_LIST", "tooling_requirement", parse_str_set, default_factory=set),
    FieldMap("EST_SETUP_MIN", "estimated_setup_minutes", parse_float),
    FieldMap("EST_CYCLE_MIN", "estimated_cycle_minutes_per_unit", parse_float),
    FieldMap("EST_TOTAL_MIN", "estimated_total_production_minutes", parse_float),
    FieldMap("CUST_PRIO", "customer_priority", parse_int),
    FieldMap("TECH_PRIO", "technical_priority", parse_int),
    FieldMap("COMM_PRIO", "commercial_priority", parse_int),
    FieldMap("LATE_PEN_DAY", "lateness_penalty_per_day", parse_float),
    FieldMap("SLA_HRS", "sla_hours", parse_float),
    FieldMap("INSTRUCTIONS", "special_instructions", parse_str),
    FieldMap("DWG_APPROVED", "drawing_approved", parse_bool, default=True),
    FieldMap("HOLD_FLAG", "on_hold", parse_bool, default=False),
    FieldMap("HOLD_REASON", "hold_reason", parse_str),
    FieldMap("DEPENDS_ON", "depends_on_order_ids", parse_str_set, default_factory=set),
    FieldMap("SURF_FINISH", "surface_finish", parse_str),
    FieldMap("TECHNOLOGY", "technology", parse_str),
)

#: Operation maps omit ``order_id`` (composed from ORDER_NO + LINE_NO like orders).
OPERATION_FIELDS: tuple[FieldMap, ...] = (
    FieldMap("OP_ID", "operation_id", parse_str, required=True),
    FieldMap("ORDER_NO", "external_order_ref", parse_str, required=True),
    FieldMap("LINE_NO", "order_line_id", parse_str, required=True),
    FieldMap("OP_SEQ", "sequence", parse_int, required=True),
    FieldMap("OP_TYPE", "operation_type", parse_process_type, default=ProcessType.OTHER),
    FieldMap("WC_GROUP", "machine_group", parse_str),
    FieldMap("MACHINE_NO", "machine_id", parse_str),
    FieldMap("ELIG_MACHINES", "eligible_machine_ids", parse_str_set, default_factory=set),
    FieldMap("SETUP_MIN", "setup_minutes", parse_float),
    FieldMap("CYCLE_MIN", "cycle_minutes_per_unit", parse_float),
    FieldMap("MACHINE_CYCLE", "machine_cycle_minutes", parse_float_map, default_factory=dict),
    FieldMap("QTY", "quantity", parse_float, default=0.0),
    FieldMap("QTY_DONE", "completed_quantity", parse_float, default=0.0),
    FieldMap("OP_STATUS", "operation_status", parse_operation_status, default=OperationStatus.PENDING),
    FieldMap("PREREQ_OP", "prerequisite_operation_id", parse_str),
    FieldMap("MATERIAL_NO", "material_id", parse_str),
    FieldMap("MAT_QTY_PER", "material_quantity_per_unit", parse_float),
    FieldMap("TOOL_LIST", "tooling_ids", parse_str_set, default_factory=set),
    FieldMap("OPERATOR_REQ", "operator_requirement", parse_str),
    FieldMap("QC_REQ", "quality_requirement", parse_str),
    FieldMap("SETUP_FAMILY", "setup_family", parse_str),
    FieldMap("EST_START", "estimated_start", parse_datetime),
    FieldMap("EST_END", "estimated_end", parse_datetime),
    FieldMap("ACT_START", "actual_start", parse_datetime),
    FieldMap("ACT_END", "actual_end", parse_datetime),
)

MACHINE_FIELDS: tuple[FieldMap, ...] = (
    FieldMap("MACHINE_NO", "machine_id", parse_str, required=True),
    FieldMap("MACHINE_NAME", "machine_name", parse_str, required=True),
    FieldMap("MACHINE_TYPE", "machine_type", parse_str, default="unknown"),
    FieldMap("PROC_TYPE", "process_type", parse_process_type, default=ProcessType.OTHER),
    FieldMap("WC_GROUP", "machine_group", parse_str, required=True),
    FieldMap("LOCATION", "location", parse_str),
    FieldMap("STATUS", "status", parse_machine_status, default=MachineStatus.AVAILABLE),
    FieldMap("CALENDAR_CODE", "calendar_id", parse_str),
    FieldMap("EFFICIENCY_PCT", "efficiency", parse_percent, default=1.0),
    FieldMap("UTIL_PCT", "utilization", parse_percent_ratio),
    FieldMap("CAP_HRS_DAY", "capacity_hours_per_day", parse_float),
    FieldMap("MAINT_WINDOWS", "maintenance_windows", parse_windows, default_factory=list),
    FieldMap("PLANNED_DOWN", "planned_downtime", parse_windows, default_factory=list),
    FieldMap("UNPLANNED_DOWN", "unplanned_downtime", parse_windows, default_factory=list),
    FieldMap("COMPAT_MATERIALS", "compatible_materials", parse_str_set, default_factory=set),
    FieldMap("COMPAT_PROCS", "compatible_processes", parse_process_set, default_factory=set),
    FieldMap("MAX_PART_MM", "max_part_size_mm", parse_size_mm),
    FieldMap("TOOLS_MOUNTED", "tooling_configuration", parse_str_set, default_factory=set),
    FieldMap("CUR_MATERIAL", "current_material_id", parse_str),
    FieldMap("CUR_SETUP_FAM", "current_setup_family", parse_str),
    FieldMap("AVAIL_FROM", "available_from", parse_datetime),
    FieldMap("PREF_RANK", "preferred_rank", parse_int, default=0),
)

MATERIAL_FIELDS: tuple[FieldMap, ...] = (
    FieldMap("MATERIAL_NO", "material_id", parse_str, required=True),
    FieldMap("MATERIAL_NAME", "material_name", parse_str, required=True),
    FieldMap("MAT_TYPE", "material_type", parse_str, default=""),
    FieldMap("GRADE", "grade", parse_str),
    FieldMap("SUPPLIER", "supplier", parse_str),
    FieldMap("UOM", "unit", parse_str, default="kg"),
    FieldMap("QTY_ONHAND", "available_quantity", parse_float, default=0.0),
    FieldMap("QTY_RESERVED", "reserved_quantity", parse_float, default=0.0),
    FieldMap("QTY_INCOMING", "incoming_quantity", parse_float, default=0.0),
    FieldMap("EXPECTED_DT", "expected_receipt_date", parse_datetime),
    FieldMap("MIN_STOCK", "minimum_stock", parse_float, default=0.0),
    FieldMap("COMPAT_MACHINES", "compatible_machine_ids", parse_str_set, default_factory=set),
)

TOOLING_FIELDS: tuple[FieldMap, ...] = (
    FieldMap("TOOL_NO", "tooling_id", parse_str, required=True),
    FieldMap("TOOL_NAME", "tooling_name", parse_str, required=True),
    FieldMap("AVAILABLE", "available", parse_bool, default=True),
    FieldMap("AVAIL_FROM", "available_from", parse_datetime),
    FieldMap("COMPAT_MACHINES", "compatible_machine_ids", parse_str_set, default_factory=set),
    FieldMap("SETUP_MIN", "setup_minutes", parse_float, default=0.0),
    FieldMap("LIFE_EXPECTED", "expected_life", parse_float),
    FieldMap("USAGE_CURRENT", "current_usage", parse_float),
    FieldMap("MAINT_STATUS", "maintenance_status", parse_str, default="ok"),
)

CALENDAR_FIELDS: tuple[FieldMap, ...] = (
    FieldMap("CALENDAR_CODE", "calendar_id", parse_str, required=True),
    FieldMap("CALENDAR_NAME", "name", parse_str, required=True),
    FieldMap("TZ", "timezone", parse_str, default="UTC"),
    FieldMap("SHIFTS", "shifts", parse_shifts, default_factory=list),
    FieldMap("HOLIDAYS", "holidays", parse_date_list, default_factory=list),
    FieldMap("OVERTIME", "overtime_windows", parse_windows, default_factory=list),
    FieldMap("EXTRA_DAYS", "extra_working_days", parse_date_list, default_factory=list),
)

#: Production status feed: one row per operation progress report.
PRODUCTION_STATUS_FIELDS: tuple[FieldMap, ...] = (
    FieldMap("OP_ID", "operation_id", parse_str, required=True),
    FieldMap("ORDER_NO", "external_order_ref", parse_str),
    FieldMap("LINE_NO", "order_line_id", parse_str),
    FieldMap("OP_STATUS", "operation_status", parse_operation_status, default=OperationStatus.PENDING),
    FieldMap("QTY_DONE", "completed_quantity", parse_float),
    FieldMap("MACHINE_NO", "machine_id", parse_str),
    FieldMap("ACT_START", "actual_start", parse_datetime),
    FieldMap("ACT_END", "actual_end", parse_datetime),
    FieldMap("REPORTED_AT", "reported_at", parse_datetime, required=True),
)

FIELD_MAPS: dict[str, tuple[FieldMap, ...]] = {
    "customer": CUSTOMER_FIELDS,
    "order": ORDER_FIELDS,
    "operation": OPERATION_FIELDS,
    "machine": MACHINE_FIELDS,
    "material": MATERIAL_FIELDS,
    "tooling": TOOLING_FIELDS,
    "calendar": CALENDAR_FIELDS,
    "production_status": PRODUCTION_STATUS_FIELDS,
}

#: Domain attributes that are derived from several raw keys rather than mapped 1:1.
COMPOSED_TARGETS: dict[str, tuple[str, ...]] = {
    "order": ("order_id",),
    "operation": ("order_id",),
}


def target_fields(entity: str) -> list[str]:
    """Domain attribute names a connector using these maps can populate."""
    names = [m.target for m in FIELD_MAPS.get(entity, ())]
    names.extend(COMPOSED_TARGETS.get(entity, ()))
    return names


__all__ = [
    "CALENDAR_FIELDS",
    "COMPOSED_TARGETS",
    "CUSTOMER_FIELDS",
    "FIELD_MAPS",
    "MACHINE_FIELDS",
    "MATERIAL_FIELDS",
    "OMIT",
    "OPERATION_FIELDS",
    "ORDER_FIELDS",
    "PRODUCTION_STATUS_FIELDS",
    "TOOLING_FIELDS",
    "FieldMap",
    "parse_process_set",
    "parse_route",
    "target_fields",
]
