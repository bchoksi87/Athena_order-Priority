"""Mock ERP connector backed by a :class:`SyntheticDataset`.

It exposes the dataset the way a real ERP export would: flat dicts with
terse upper-case keys, ISO date strings, ``Y``/``N`` flags, percentages and
status codes such as ``REL``/``WIP``/``HLD``. The normalizer therefore does
real work against it. Records carry ``updated_at`` so ``since`` filtering
exercises incremental synchronisation; test helpers mutate the dataset and
bump timestamps.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from datetime import date, datetime
from typing import Any

import structlog

from app.core.clock import Clock
from app.core.errors import IntegrationError, NotFoundError
from app.domain.enums import MachineStatus, OperationStatus
from app.domain.models import (
    CalendarSpec,
    Customer,
    Machine,
    Material,
    Operation,
    Order,
    Shift,
    TimeWindow,
    Tooling,
)
from app.integration import codes
from app.integration.connector import ConnectorCapabilities, ConnectorHealth, RawRecord
from app.integration.field_maps import target_fields
from synthetic.generator import SyntheticDataset

log = structlog.get_logger(__name__)

_ORDER_STATUS = codes.invert(codes.ORDER_STATUS_CODES)
_OP_STATUS = codes.invert(codes.OPERATION_STATUS_CODES)
_MACHINE_STATUS = codes.invert(codes.MACHINE_STATUS_CODES)
_MATERIAL_STATUS = codes.invert(codes.MATERIAL_STATUS_CODES)
_QUALITY_STATUS = codes.invert(codes.QUALITY_STATUS_CODES)
_SHIPPING_STATUS = codes.invert(codes.SHIPPING_STATUS_CODES)
_TIER = codes.invert(codes.CUSTOMER_TIER_CODES)
_PAYMENT_RISK = codes.invert(codes.PAYMENT_RISK_CODES)
_PROCESS = codes.invert(codes.PROCESS_TYPE_CODES)
_WEEKDAY_NAMES = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")

_PROGRESS_STATUSES = frozenset(
    {OperationStatus.IN_PROGRESS, OperationStatus.COMPLETED, OperationStatus.REWORK, OperationStatus.ON_HOLD}
)


def _iso(value: datetime | None) -> str:
    return value.isoformat() if value is not None else ""


def _flag(value: bool) -> str:
    return "Y" if value else "N"


def _num(value: float | int | None) -> str:
    return "" if value is None else str(value)


def _pct(value: float | None) -> str:
    return "" if value is None else f"{value * 100.0:.2f}"


def _joined(values: Iterable[str]) -> str:
    return codes.LIST_SEPARATOR.join(sorted(values))


def _windows(windows: list[TimeWindow]) -> list[dict[str, str]]:
    return [{"START": _iso(w.start), "END": _iso(w.end), "REASON": w.reason} for w in windows]


def _shifts(shifts: list[Shift]) -> list[dict[str, str]]:
    return [
        {
            "NAME": s.name,
            "START": s.start.strftime("%H:%M"),
            "END": s.end.strftime("%H:%M"),
            "DAYS": ",".join(_WEEKDAY_NAMES[d] for d in s.weekdays),
        }
        for s in shifts
    ]


def _dates(values: list[date]) -> list[str]:
    return [d.isoformat() for d in values]


def _line_no(order: Order) -> str:
    return order.order_line_id or "1"


def customer_payload(c: Customer) -> dict[str, Any]:
    return {
        "CUST_CODE": c.customer_id,
        "CUST_NAME": c.customer_name,
        "CUST_CAT": c.customer_category,
        "TIER_CODE": _TIER[c.customer_tier],
        "PRIO": c.customer_priority,
        "STRATEGIC_FLAG": _flag(c.strategic_customer_flag),
        "ANNUAL_REV": _num(c.annual_revenue),
        "REV_12M": _num(c.customer_revenue),
        "PROFIT_PCT": _pct(c.customer_profitability),
        "SVC_LEVEL_PCT": _pct(c.customer_service_level),
        "SLA_HRS": _num(c.sla_hours),
        "ESC_LVL": c.escalation_level,
        "OTD_PCT": _pct(c.historical_on_time_delivery),
        "PAY_RISK": _PAYMENT_RISK[c.payment_risk],
        "DELIV_EXPECT": c.preferred_delivery_expectation or "",
        "ACCT_MGR": c.account_manager or "",
        "ACTIVE": _flag(c.active),
        "EXT_REF": c.external_ref or "",
    }


def order_payload(o: Order) -> dict[str, Any]:
    return {
        "ORDER_NO": o.external_order_ref or o.order_id,
        "LINE_NO": _line_no(o),
        "CUST_CODE": o.customer_id,
        "PART_NO": o.part_id,
        "PART_DESC": o.part_name or "",
        "PART_FAM": o.part_family or "",
        "ORDER_DT": _iso(o.order_date),
        "RECV_DT": _iso(o.received_date),
        "REQ_DT": _iso(o.requested_delivery_date),
        "PROM_DT": _iso(o.promised_delivery_date),
        "REV_DT": _iso(o.revised_delivery_date),
        "QTY": o.quantity,
        "QTY_DONE": o.completed_quantity,
        "QTY_CANC": o.cancelled_quantity,
        "STATUS": _ORDER_STATUS[o.order_status],
        "PRIO": _num(o.erp_priority),
        "PROD_STATUS": o.production_status or "",
        "MAT_STATUS": _MATERIAL_STATUS[o.material_status],
        "QC_STATUS": _QUALITY_STATUS[o.quality_status],
        "SHIP_STATUS": _SHIPPING_STATUS[o.shipping_status],
        "ORDER_VAL": _num(o.order_value),
        "EST_COST": _num(o.estimated_cost),
        "EST_MARGIN": _num(o.estimated_margin),
        "ACT_MARGIN": _num(o.actual_margin),
        "PROC_TYPE": _PROCESS[o.process_type],
        "ROUTE": ">".join(_PROCESS[p] for p in o.manufacturing_route),
        "WC_GROUP": o.machine_group or "",
        "REQ_MACHINE": o.required_machine_id or "",
        "REQ_MATERIAL": o.required_material_id or "",
        "TOOL_LIST": _joined(o.tooling_requirement),
        "EST_SETUP_MIN": _num(o.estimated_setup_minutes),
        "EST_CYCLE_MIN": _num(o.estimated_cycle_minutes_per_unit),
        "EST_TOTAL_MIN": _num(o.estimated_total_production_minutes),
        "CUST_PRIO": _num(o.customer_priority),
        "TECH_PRIO": _num(o.technical_priority),
        "COMM_PRIO": _num(o.commercial_priority),
        "LATE_PEN_DAY": _num(o.lateness_penalty_per_day),
        "SLA_HRS": _num(o.sla_hours),
        "INSTRUCTIONS": o.special_instructions or "",
        "DWG_APPROVED": _flag(o.drawing_approved),
        "HOLD_FLAG": _flag(o.on_hold),
        "HOLD_REASON": o.hold_reason or "",
        "DEPENDS_ON": _joined(o.depends_on_order_ids),
        "SURF_FINISH": o.surface_finish or "",
        "TECHNOLOGY": o.technology or "",
    }


def operation_payload(op: Operation, order: Order) -> dict[str, Any]:
    return {
        "OP_ID": op.operation_id,
        "ORDER_NO": order.external_order_ref or order.order_id,
        "LINE_NO": _line_no(order),
        "OP_SEQ": op.sequence,
        "OP_TYPE": _PROCESS[op.operation_type],
        "WC_GROUP": op.machine_group or "",
        "MACHINE_NO": op.machine_id or "",
        "ELIG_MACHINES": _joined(op.eligible_machine_ids),
        "SETUP_MIN": _num(op.setup_minutes),
        "CYCLE_MIN": _num(op.cycle_minutes_per_unit),
        "MACHINE_CYCLE": codes.LIST_SEPARATOR.join(
            f"{k}={v}" for k, v in sorted(op.machine_cycle_minutes.items())
        ),
        "QTY": op.quantity,
        "QTY_DONE": op.completed_quantity,
        "OP_STATUS": _OP_STATUS[op.operation_status],
        "PREREQ_OP": op.prerequisite_operation_id or "",
        "MATERIAL_NO": op.material_id or "",
        "MAT_QTY_PER": _num(op.material_quantity_per_unit),
        "TOOL_LIST": _joined(op.tooling_ids),
        "OPERATOR_REQ": op.operator_requirement or "",
        "QC_REQ": op.quality_requirement or "",
        "SETUP_FAMILY": op.setup_family or "",
        "EST_START": _iso(op.estimated_start),
        "EST_END": _iso(op.estimated_end),
        "ACT_START": _iso(op.actual_start),
        "ACT_END": _iso(op.actual_end),
    }


def machine_payload(m: Machine) -> dict[str, Any]:
    size = m.max_part_size_mm
    return {
        "MACHINE_NO": m.machine_id,
        "MACHINE_NAME": m.machine_name,
        "MACHINE_TYPE": m.machine_type,
        "PROC_TYPE": _PROCESS[m.process_type],
        "WC_GROUP": m.machine_group,
        "LOCATION": m.location or "",
        "STATUS": _MACHINE_STATUS[m.status],
        "CALENDAR_CODE": m.calendar_id or "",
        "EFFICIENCY_PCT": _pct(m.efficiency),
        "UTIL_PCT": _pct(m.utilization),
        "CAP_HRS_DAY": _num(m.capacity_hours_per_day),
        "MAINT_WINDOWS": _windows(m.maintenance_windows),
        "PLANNED_DOWN": _windows(m.planned_downtime),
        "UNPLANNED_DOWN": _windows(m.unplanned_downtime),
        "COMPAT_MATERIALS": _joined(m.compatible_materials),
        "COMPAT_PROCS": _joined(_PROCESS[p] for p in m.compatible_processes),
        "MAX_PART_MM": f"{size[0]:g}x{size[1]:g}x{size[2]:g}" if size else "",
        "TOOLS_MOUNTED": _joined(m.tooling_configuration),
        "CUR_MATERIAL": m.current_material_id or "",
        "CUR_SETUP_FAM": m.current_setup_family or "",
        "AVAIL_FROM": _iso(m.available_from),
        "PREF_RANK": m.preferred_rank,
    }


def material_payload(m: Material) -> dict[str, Any]:
    return {
        "MATERIAL_NO": m.material_id,
        "MATERIAL_NAME": m.material_name,
        "MAT_TYPE": m.material_type,
        "GRADE": m.grade or "",
        "SUPPLIER": m.supplier or "",
        "UOM": m.unit,
        "QTY_ONHAND": m.available_quantity,
        "QTY_RESERVED": m.reserved_quantity,
        "QTY_INCOMING": m.incoming_quantity,
        "EXPECTED_DT": _iso(m.expected_receipt_date),
        "MIN_STOCK": m.minimum_stock,
        "COMPAT_MACHINES": _joined(m.compatible_machine_ids),
    }


def tooling_payload(t: Tooling) -> dict[str, Any]:
    return {
        "TOOL_NO": t.tooling_id,
        "TOOL_NAME": t.tooling_name,
        "AVAILABLE": _flag(t.available),
        "AVAIL_FROM": _iso(t.available_from),
        "COMPAT_MACHINES": _joined(t.compatible_machine_ids),
        "SETUP_MIN": t.setup_minutes,
        "LIFE_EXPECTED": _num(t.expected_life),
        "USAGE_CURRENT": _num(t.current_usage),
        "MAINT_STATUS": t.maintenance_status,
    }


def calendar_payload(c: CalendarSpec, is_default: bool) -> dict[str, Any]:
    return {
        "CALENDAR_CODE": c.calendar_id,
        "CALENDAR_NAME": c.name,
        "TZ": c.timezone,
        "SHIFTS": _shifts(c.shifts),
        "HOLIDAYS": _dates(c.holidays),
        "OVERTIME": _windows(c.overtime_windows),
        "EXTRA_DAYS": _dates(c.extra_working_days),
        "IS_DEFAULT": _flag(is_default),
    }


def production_status_payload(op: Operation, order: Order, reported_at: datetime) -> dict[str, Any]:
    return {
        "OP_ID": op.operation_id,
        "ORDER_NO": order.external_order_ref or order.order_id,
        "LINE_NO": _line_no(order),
        "OP_STATUS": _OP_STATUS[op.operation_status],
        "QTY_DONE": op.completed_quantity,
        "MACHINE_NO": op.machine_id or "",
        "ACT_START": _iso(op.actual_start),
        "ACT_END": _iso(op.actual_end),
        "REPORTED_AT": _iso(reported_at),
    }


class MockERPConnector:
    """In-memory ERP that serves a synthetic dataset as raw records.

    The connector shares object references with the dataset it wraps; the
    mutation helpers (``add_order``, ``set_machine_status``,
    ``receive_material``, ``report_progress``) change those objects and bump
    the entity's ``updated_at`` to ``clock.now()`` so incremental fetches see them.
    """

    name = "mock"

    def __init__(self, dataset: SyntheticDataset, clock: Clock, source: str = "MOCK-ERP") -> None:
        self._dataset = dataset
        self._clock = clock
        self._source = source
        self._failure: str | None = None
        self._customers = {c.customer_id: c for c in dataset.customers}
        self._orders = {o.order_id: o for o in dataset.orders}
        self._operations = {op.operation_id: op for op in dataset.operations}
        self._machines = {m.machine_id: m for m in dataset.machines}
        self._materials = {m.material_id: m for m in dataset.materials}
        self._tooling = {t.tooling_id: t for t in dataset.tooling}
        self._calendars = {c.calendar_id: c for c in dataset.calendars}
        self._updated: dict[tuple[str, str], datetime] = {}
        base = dataset.as_of
        for order in dataset.orders:
            stamp = order.received_date or order.order_date or base
            self._updated[("order", order.order_id)] = min(stamp, base)
        for op in dataset.operations:
            stamp = op.actual_end or op.actual_start or base
            self._updated[("operation", op.operation_id)] = min(stamp, base)

    # ------------------------------------------------------------- fetching
    def fetch_customers(self, since: datetime | None = None) -> list[RawRecord]:
        return self._emit(
            "customer", since, ((c.customer_id, customer_payload(c)) for c in self._customers.values())
        )

    def fetch_orders(self, since: datetime | None = None) -> list[RawRecord]:
        return self._emit("order", since, ((o.order_id, order_payload(o)) for o in self._orders.values()))

    def fetch_operations(self, since: datetime | None = None) -> list[RawRecord]:
        rows = (
            (op.operation_id, operation_payload(op, self._orders[op.order_id]))
            for op in self._operations.values()
            if op.order_id in self._orders
        )
        return self._emit("operation", since, rows)

    def fetch_machines(self, since: datetime | None = None) -> list[RawRecord]:
        return self._emit(
            "machine", since, ((m.machine_id, machine_payload(m)) for m in self._machines.values())
        )

    def fetch_materials(self, since: datetime | None = None) -> list[RawRecord]:
        return self._emit(
            "material", since, ((m.material_id, material_payload(m)) for m in self._materials.values())
        )

    def fetch_tooling(self, since: datetime | None = None) -> list[RawRecord]:
        return self._emit(
            "tooling", since, ((t.tooling_id, tooling_payload(t)) for t in self._tooling.values())
        )

    def fetch_calendars(self, since: datetime | None = None) -> list[RawRecord]:
        default_id = self._dataset.default_calendar_id
        rows = (
            (c.calendar_id, calendar_payload(c, c.calendar_id == default_id))
            for c in self._calendars.values()
        )
        return self._emit("calendar", since, rows)

    def fetch_production_status(self, since: datetime | None = None) -> list[RawRecord]:
        rows = []
        for op in self._operations.values():
            if op.operation_status not in _PROGRESS_STATUSES and op.completed_quantity <= 0:
                continue
            order = self._orders.get(op.order_id)
            if order is None:
                continue
            reported_at = self._stamp("operation", op.operation_id)
            rows.append((op.operation_id, production_status_payload(op, order, reported_at)))
        return self._emit("production_status", since, rows, stamp_entity="operation")

    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            connector_name=self.name,
            fields={
                entity: target_fields(entity)
                for entity in ("customer", "order", "operation", "machine", "material", "tooling", "calendar")
            },
            supports_incremental=True,
            supports_webhooks=False,
            notes={"source": "synthetic dataset", "scale": self._dataset.stats.scale},
        )

    def health(self) -> ConnectorHealth:
        started = time.perf_counter()
        healthy = self._failure is None
        return ConnectorHealth(
            connector_name=self.name,
            healthy=healthy,
            checked_at=self._clock.now(),
            latency_ms=(time.perf_counter() - started) * 1000.0,
            message=self._failure or "mock connector ready",
            details={"orders": len(self._orders), "machines": len(self._machines)},
        )

    # ----------------------------------------------------------- mutations
    def set_failure(self, message: str | None) -> None:
        """Make every fetch raise :class:`IntegrationError` (``None`` restores service)."""
        self._failure = message

    def add_order(self, order: Order, operations: Iterable[Operation] = ()) -> None:
        now = self._clock.now()
        self._orders[order.order_id] = order
        self._updated[("order", order.order_id)] = now
        for op in operations:
            self._operations[op.operation_id] = op
            self._updated[("operation", op.operation_id)] = now
        log.info("mock_erp.order_added", order_id=order.order_id, operations=len(list(operations)))

    def set_machine_status(
        self, machine_id: str, status: MachineStatus, downtime: TimeWindow | None = None
    ) -> Machine:
        machine = self._machines.get(machine_id)
        if machine is None:
            raise NotFoundError(f"machine {machine_id} not found in mock ERP")
        machine.status = status
        if downtime is not None:
            machine.unplanned_downtime.append(downtime)
        self._updated[("machine", machine_id)] = self._clock.now()
        log.info("mock_erp.machine_status", machine_id=machine_id, status=status.value)
        return machine

    def receive_material(self, material_id: str, quantity: float) -> Material:
        material = self._materials.get(material_id)
        if material is None:
            raise NotFoundError(f"material {material_id} not found in mock ERP")
        material.available_quantity += quantity
        material.incoming_quantity = max(0.0, material.incoming_quantity - quantity)
        if material.incoming_quantity <= 0:
            material.expected_receipt_date = None
        self._updated[("material", material_id)] = self._clock.now()
        log.info("mock_erp.material_received", material_id=material_id, quantity=quantity)
        return material

    def report_progress(
        self,
        operation_id: str,
        status: OperationStatus,
        completed_quantity: float | None = None,
    ) -> Operation:
        op = self._operations.get(operation_id)
        if op is None:
            raise NotFoundError(f"operation {operation_id} not found in mock ERP")
        now = self._clock.now()
        op.operation_status = status
        if completed_quantity is not None:
            op.completed_quantity = completed_quantity
        if status == OperationStatus.IN_PROGRESS and op.actual_start is None:
            op.actual_start = now
        if status == OperationStatus.COMPLETED:
            op.actual_end = now
            op.completed_quantity = op.quantity
        self._updated[("operation", operation_id)] = now
        return op

    # ------------------------------------------------------------ internals
    def _stamp(self, entity: str, external_id: str) -> datetime:
        return self._updated.get((entity, external_id), self._dataset.as_of)

    def _emit(
        self,
        entity: str,
        since: datetime | None,
        rows: Iterable[tuple[str, dict[str, Any]]],
        stamp_entity: str | None = None,
    ) -> list[RawRecord]:
        if self._failure is not None:
            raise IntegrationError(f"mock ERP unavailable: {self._failure}", details={"entity": entity})
        records: list[RawRecord] = []
        for external_id, payload in rows:
            updated_at = self._stamp(stamp_entity or entity, external_id)
            if since is not None and updated_at <= since:
                continue
            records.append(RawRecord(entity, external_id, payload, updated_at, self._source))
        log.debug("mock_erp.fetch", entity=entity, since=_iso(since), records=len(records))
        return records


__all__ = ["MockERPConnector"]
