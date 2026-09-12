"""PlanningSnapshot (de)serialisation."""

from __future__ import annotations

import gzip
import json
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pytest

from app.core.errors import ValidationError
from app.db.snapshot_codec import (
    CODEC_GZIP_JSON,
    decode_dataclass,
    decode_snapshot,
    decode_value,
    dumps,
    encode_snapshot,
    payload_digest,
    snapshot_from_dict,
    snapshot_to_dict,
    to_jsonable,
)
from app.domain.enums import ProcessType
from app.domain.models import Machine, Shift, TimeWindow
from app.domain.results import FactorScore
from tests.conftest import NOW, SampleData

pytestmark = pytest.mark.unit


def test_snapshot_roundtrip_is_exact(sample: SampleData) -> None:
    snapshot = sample.snapshot()
    payload = encode_snapshot(snapshot)
    back = decode_snapshot(payload, CODEC_GZIP_JSON)
    assert back.as_of == snapshot.as_of
    assert back.orders == snapshot.orders
    assert back.operations == snapshot.operations
    assert back.machines == snapshot.machines
    assert back.materials == snapshot.materials
    assert back.tooling == snapshot.tooling
    assert back.calendars == snapshot.calendars
    assert back.customers == snapshot.customers
    assert back.locks == snapshot.locks
    assert back.overrides == snapshot.overrides
    assert back.expedites == snapshot.expedites
    assert back.customer_rules == snapshot.customer_rules
    assert back.default_calendar_id == snapshot.default_calendar_id
    assert back.source == "test"
    # indexes are rebuilt, queries work
    assert [op.operation_id for op in back.operations_for_order("ORD-1")] == ["ORD-1-10", "ORD-1-20"]
    assert back.dependents_of("ORD-1") == {"ORD-2"}
    assert back.summary() == snapshot.summary()


def test_exact_python_types_survive(sample: SampleData) -> None:
    back = decode_snapshot(encode_snapshot(sample.snapshot()))
    machine = back.machines["CNC-01"]
    assert isinstance(machine.max_part_size_mm, tuple) and machine.max_part_size_mm == (760.0, 400.0, 500.0)
    assert (
        isinstance(machine.compatible_processes, set)
        and ProcessType.DEBURRING in machine.compatible_processes
    )
    assert isinstance(machine.compatible_materials, set)
    assert isinstance(machine.maintenance_windows[0], TimeWindow)
    cal = back.calendars["CAL-PLANT"]
    assert isinstance(cal.shifts[0], Shift) and isinstance(cal.shifts[0].start, time)
    assert isinstance(cal.shifts[1].weekdays, tuple)
    assert isinstance(cal.holidays[0], date) and not isinstance(cal.holidays[0], datetime)
    order = back.orders["ORD-1"]
    assert order.due_date is not None and order.due_date.tzinfo is UTC
    assert order.manufacturing_route == [ProcessType.CNC_MACHINING, ProcessType.DEBURRING]
    assert isinstance(order.tooling_requirement, set)
    assert back.operations["ORD-1-10"].machine_cycle_minutes == {"CNC-02": 9.5}


def test_encoding_is_deterministic(sample: SampleData) -> None:
    a = encode_snapshot(sample.snapshot())
    b = encode_snapshot(sample.snapshot())
    assert a == b
    assert payload_digest(a) == payload_digest(b)
    text = gzip.decompress(a).decode()
    assert text == dumps(sample.snapshot())
    data = json.loads(text)
    assert "_ops_by_order" not in data
    assert data["machines"]["CNC-01"]["compatible_materials"] == ["MAT-AL", "MAT-ST"]  # sets sorted


def test_private_fields_excluded_and_dict_roundtrip(sample: SampleData) -> None:
    as_dict = snapshot_to_dict(sample.snapshot())
    assert set(as_dict) >= {"as_of", "orders", "machines", "locks"}
    assert not any(k.startswith("_") for k in as_dict)
    back = snapshot_from_dict(as_dict)
    assert back.orders == sample.snapshot().orders


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValidationError):
        to_jsonable(datetime(2026, 1, 1))


def test_unencodable_type_rejected() -> None:
    with pytest.raises(ValidationError):
        to_jsonable(object())


def test_corrupt_payload_and_unknown_codec() -> None:
    with pytest.raises(ValidationError, match="corrupt"):
        decode_snapshot(b"not gzip")
    with pytest.raises(ValidationError, match="codec"):
        decode_snapshot(b"", codec="msgpack")


def test_decode_value_handles_optional_union_and_offsets() -> None:
    assert decode_value(None, datetime | None) is None
    parsed = decode_value("2026-09-11T13:30:00+05:30", datetime)
    assert parsed == datetime(2026, 9, 11, 8, 0, tzinfo=UTC)
    assert decode_value("2026-09-11T08:00:00", datetime).tzinfo is UTC
    assert decode_value([3, 1], set[int]) == {1, 3}
    assert decode_value([3, 1], frozenset[int]) == frozenset({1, 3})
    assert decode_value([1, 2], tuple[int, ...]) == (1, 2)
    assert decode_value(["cnc_machining"], list[ProcessType]) == [ProcessType.CNC_MACHINING]
    assert decode_value({"a": "1.5"}, dict[str, float]) == {"a": 1.5}
    assert decode_value("x", Any) == "x"
    assert decode_value(5, int | str) == 5


def test_decode_dataclass_ignores_unknown_and_missing_fields() -> None:
    fs = decode_dataclass(
        {
            "key": "k",
            "name": "n",
            "kind": "bonus",
            "raw_score": 1,
            "weight": 0.5,
            "points": 0.5,
            "reason": "r",
            "extra": 1,
        },
        FactorScore,
    )
    assert fs.details == {}
    assert fs.raw_score == 1.0
    with pytest.raises(ValidationError):
        decode_dataclass("nope", FactorScore)  # type: ignore[arg-type]


def test_machine_roundtrip_via_generic_codec() -> None:
    machine = Machine(
        "M",
        "n",
        "t",
        ProcessType.HEAT_TREATMENT,
        "G",
        available_from=NOW + timedelta(hours=1),
        planned_downtime=[TimeWindow(NOW, NOW + timedelta(hours=2), "pm")],
        tooling_configuration={"b", "a"},
    )
    assert decode_dataclass(to_jsonable(machine), Machine) == machine
