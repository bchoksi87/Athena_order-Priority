"""Normalizer: happy path from the mock ERP plus every issue code."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.core.clock import FrozenClock
from app.domain.enums import (
    CustomerTier,
    MachineStatus,
    OperationStatus,
    OrderStatus,
    PaymentRisk,
    ProcessType,
)
from app.domain.models import Operation
from app.integration.connector import RawRecord
from app.integration.mock_connector import MockERPConnector
from app.integration.normalizer import NormalizationIssueCode, Normalizer
from app.integration.parsers import (
    UnknownCodeError,
    parse_bool,
    parse_datetime,
    parse_float_map,
    parse_percent,
    parse_percent_ratio,
    parse_shifts,
    parse_size_mm,
    parse_weekdays,
)
from synthetic.generator import SyntheticDataGenerator

pytestmark = pytest.mark.unit

AS_OF = datetime(2026, 9, 14, 3, 30, tzinfo=UTC)


def raw(entity: str, external_id: str, payload: dict[str, Any]) -> RawRecord:
    return RawRecord(entity, external_id, payload, AS_OF, "TEST")


def order_row(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "ORDER_NO": "SO-100",
        "LINE_NO": "1",
        "CUST_CODE": "CUST-0001",
        "PART_NO": "P-1",
        "REQ_DT": "2026-09-20T12:00:00Z",
        "PROM_DT": "2026-09-21",
        "QTY": "10",
        "QTY_DONE": "2",
        "STATUS": "rel",
        "MAT_STATUS": "AVL",
        "QC_STATUS": "NON",
        "SHIP_STATUS": "NS",
        "ORDER_VAL": "12,500.50",
        "PROC_TYPE": "CNC",
        "ROUTE": "CNC>DEB>INS",
        "TOOL_LIST": "TL-1;TL-2",
        "DWG_APPROVED": "Y",
        "HOLD_FLAG": "N",
        "DEPENDS_ON": "",
    }
    base.update(overrides)
    return base


# ------------------------------------------------------------------ happy path


def test_round_trip_from_mock_connector() -> None:
    dataset = SyntheticDataGenerator(seed=2, scale="small", dq_defect_ratio=0.0).generate()
    connector = MockERPConnector(dataset, FrozenClock(dataset.as_of))
    normalizer = Normalizer()
    customers = normalizer.normalize_customers(connector.fetch_customers())
    orders = normalizer.normalize_orders(connector.fetch_orders())
    ops = normalizer.normalize_operations(connector.fetch_operations())
    machines = normalizer.normalize_machines(connector.fetch_machines())
    materials = normalizer.normalize_materials(connector.fetch_materials())
    tooling = normalizer.normalize_tooling(connector.fetch_tooling())
    calendars = normalizer.normalize_calendars(connector.fetch_calendars())
    for result in (customers, orders, ops, machines, materials, tooling, calendars):
        assert result.issues == [] and result.skipped == 0

    def same(a: Any, b: Any) -> bool:
        for name in a.__slots__:
            if name == "attributes":
                continue
            if getattr(a, name) != getattr(b, name):
                return False
        return True

    assert all(same(a, b) for a, b in zip(dataset.customers, customers.items, strict=True))
    assert all(same(a, b) for a, b in zip(dataset.orders, orders.items, strict=True))
    assert all(same(a, b) for a, b in zip(dataset.operations, ops.items, strict=True))
    assert all(same(a, b) for a, b in zip(dataset.machines, machines.items, strict=True))
    assert all(same(a, b) for a, b in zip(dataset.materials, materials.items, strict=True))
    assert all(same(a, b) for a, b in zip(dataset.tooling, tooling.items, strict=True))
    assert dataset.calendars == calendars.items
    assert orders.items[0].attributes["erp_row_id"] == dataset.orders[0].order_id


def test_order_happy_path_parses_erp_shapes() -> None:
    result = Normalizer().normalize_orders([raw("order", "r1", order_row())])
    assert result.issues == []
    order = result.items[0]
    assert (
        order.order_id == "SO-100-01" and order.external_order_ref == "SO-100" and order.order_line_id == "1"
    )
    assert order.order_status == OrderStatus.RELEASED
    assert order.requested_delivery_date == datetime(2026, 9, 20, 12, tzinfo=UTC)
    assert order.promised_delivery_date == datetime(2026, 9, 21, tzinfo=UTC)
    assert order.quantity == 10.0 and order.completed_quantity == 2.0 and order.pending_quantity == 8.0
    assert order.order_value == 12500.5
    assert order.manufacturing_route == [
        ProcessType.CNC_MACHINING,
        ProcessType.DEBURRING,
        ProcessType.INSPECTION,
    ]
    assert order.tooling_requirement == {"TL-1", "TL-2"}
    assert order.drawing_approved is True and order.on_hold is False
    assert order.depends_on_order_ids == set()


def test_customer_percent_and_codes() -> None:
    record = raw(
        "customer",
        "c1",
        {
            "CUST_CODE": "C1",
            "CUST_NAME": "Apex",
            "TIER_CODE": "a",
            "PROFIT_PCT": "37.5",
            "PAY_RISK": "H",
            "ACTIVE": "1",
        },
    )
    result = Normalizer().normalize_customers([record])
    customer = result.items[0]
    assert customer.customer_tier == CustomerTier.STRATEGIC
    assert customer.customer_profitability == 0.375
    assert customer.payment_risk == PaymentRisk.HIGH and customer.active is True


# ------------------------------------------------------------------ issue codes


def test_missing_required_skips_record() -> None:
    result = Normalizer().normalize_orders([raw("order", "r1", order_row(CUST_CODE=""))])
    assert result.items == [] and result.skipped == 1
    [issue] = result.issues
    assert issue.code == NormalizationIssueCode.MISSING_REQUIRED
    assert issue.field == "customer_id" and issue.external_id == "r1" and issue.entity == "order"


def test_invalid_optional_value_degrades() -> None:
    result = Normalizer().normalize_orders([raw("order", "r1", order_row(REQ_DT="31/12/2026", QTY="ten"))])
    order = result.items[0]
    assert order.requested_delivery_date is None
    assert order.quantity == 0.0  # declared default applied
    codes = {(i.field, i.code) for i in result.issues}
    assert codes == {
        ("requested_delivery_date", NormalizationIssueCode.INVALID_VALUE),
        ("quantity", NormalizationIssueCode.INVALID_VALUE),
    }


def test_invalid_required_value_skips() -> None:
    result = Normalizer().normalize_operations(
        [raw("operation", "op1", {"OP_ID": "OP1", "ORDER_NO": "SO-1", "LINE_NO": "1", "OP_SEQ": "first"})]
    )
    assert result.items == []
    assert (
        result.issues[0].code == NormalizationIssueCode.INVALID_VALUE and result.issues[0].field == "sequence"
    )


def test_invalid_line_number_skips() -> None:
    result = Normalizer().normalize_orders([raw("order", "r1", order_row(LINE_NO="A"))])
    assert result.items == []
    assert result.issues[0].code == NormalizationIssueCode.INVALID_VALUE
    assert result.issues[0].field == "order_line_id"


def test_unknown_code_maps_to_default_with_issue() -> None:
    result = Normalizer().normalize_orders([raw("order", "r1", order_row(STATUS="ZZZ", MAT_STATUS="???"))])
    order = result.items[0]
    assert order.order_status == OrderStatus.NEW
    assert order.material_status.value == "unknown"
    issues = result.issues_with(NormalizationIssueCode.UNKNOWN_CODE)
    assert {i.field for i in issues} == {"order_status", "material_status"}
    assert "ZZZ" in issues[0].message


def test_duplicate_order_lines_are_kept_and_flagged() -> None:
    records = [raw("order", "r1", order_row()), raw("order", "r2", order_row(QTY="11"))]
    result = Normalizer().normalize_orders(records)
    assert [o.order_id for o in result.items] == ["SO-100-01", "SO-100-01-DUP1"]
    assert result.items[1].attributes["duplicate_of"] == "SO-100-01"
    [issue] = result.issues
    assert issue.code == NormalizationIssueCode.DUPLICATE_ID and issue.external_id == "r2"


def test_duplicate_master_ids_skip_later_copy() -> None:
    rows = [
        raw(
            "machine", "m1", {"MACHINE_NO": "M1", "MACHINE_NAME": "VMC", "WC_GROUP": "CNC3", "STATUS": "AVL"}
        ),
        raw("machine", "m1b", {"MACHINE_NO": "M1", "MACHINE_NAME": "VMC copy", "WC_GROUP": "CNC3"}),
    ]
    result = Normalizer().normalize_machines(rows)
    assert len(result.items) == 1 and result.items[0].machine_name == "VMC"
    assert result.issues[0].code == NormalizationIssueCode.DUPLICATE_ID
    assert result.items[0].status == MachineStatus.AVAILABLE


def test_malformed_and_wrong_entity_records() -> None:
    bad_payload = RawRecord("customer", "x", "not-a-dict", AS_OF, "TEST")  # type: ignore[arg-type]
    wrong_entity = raw("order", "y", order_row())
    result = Normalizer().normalize_customers([bad_payload, wrong_entity])
    assert result.items == []
    assert [i.code for i in result.issues] == [
        NormalizationIssueCode.MALFORMED_RECORD,
        NormalizationIssueCode.WRONG_ENTITY,
    ]


def test_domain_constructor_failure_is_malformed_record() -> None:
    row = {
        "MACHINE_NO": "M1",
        "MACHINE_NAME": "VMC",
        "WC_GROUP": "CNC3",
        "MAINT_WINDOWS": [{"START": "2026-09-20T10:00:00Z", "END": "2026-09-20T08:00:00Z"}],
    }
    result = Normalizer().normalize_machines([raw("machine", "m1", row)])
    # TimeWindow raises ValueError (end before start) inside the parser -> invalid value, machine kept
    assert len(result.items) == 1 and result.items[0].maintenance_windows == []
    assert result.issues[0].code == NormalizationIssueCode.INVALID_VALUE


def test_production_status_updates_apply() -> None:
    row = {
        "OP_ID": "OP1",
        "OP_STATUS": "WIP",
        "QTY_DONE": "4",
        "MACHINE_NO": "M2",
        "ACT_START": "2026-09-14T01:00:00Z",
        "REPORTED_AT": "2026-09-14T03:00:00Z",
    }
    result = Normalizer().normalize_production_status([raw("production_status", "OP1", row)])
    [update] = result.items
    op = Operation("OP1", "SO-1-01", 10, ProcessType.CNC_MACHINING, quantity=10)
    update.apply(op)
    assert op.operation_status == OperationStatus.IN_PROGRESS
    assert op.completed_quantity == 4.0 and op.machine_id == "M2"
    assert op.actual_start == datetime(2026, 9, 14, 1, tzinfo=UTC)
    assert op.attributes["last_progress_report_at"] == "2026-09-14T03:00:00+00:00"


def test_generic_dispatch_and_unknown_entity() -> None:
    normalizer = Normalizer()
    assert normalizer.normalize("customer", []).entity == "customer"
    with pytest.raises(KeyError):
        normalizer.normalize("invoice", [])


# ------------------------------------------------------------------ parsers


def test_parsers() -> None:
    assert parse_datetime("2026-09-14T09:00:00+05:30") == datetime(2026, 9, 14, 3, 30, tzinfo=UTC)
    assert parse_datetime("2026-09-14") == datetime(2026, 9, 14, tzinfo=UTC)
    assert parse_datetime(datetime(2026, 1, 1, 12)) == datetime(2026, 1, 1, 12, tzinfo=UTC)
    assert parse_bool("Y") and parse_bool("true") and not parse_bool("0")
    with pytest.raises(ValueError):
        parse_bool("maybe")
    assert parse_percent("110") == 1.1 and parse_percent_ratio("96.9") == 0.969
    with pytest.raises(ValueError):
        parse_percent_ratio("120")
    assert parse_float_map("M1=3.5;M2=4") == {"M1": 3.5, "M2": 4.0}
    with pytest.raises(ValueError):
        parse_float_map("M1:3")
    assert parse_size_mm("500x400x300") == (500.0, 400.0, 300.0)
    assert parse_weekdays("MON,TUE,SAT") == (0, 1, 5)
    shifts = parse_shifts([{"NAME": "A", "START": "22:00", "END": "06:00", "DAYS": "MON,TUE"}])
    assert shifts[0].crosses_midnight and shifts[0].weekdays == (0, 1)
    with pytest.raises(UnknownCodeError):
        from app.integration.field_maps import parse_order_status

        parse_order_status("NOPE")
