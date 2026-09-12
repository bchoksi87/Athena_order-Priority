"""Synthetic data generator tests (determinism, scale counts, stats sanity, snapshot)."""

from __future__ import annotations

import dataclasses
import time
from datetime import UTC, datetime, timedelta

import pytest

from app.core.errors import ValidationError
from app.domain.enums import MachineStatus, OperationStatus, OrderStatus, ProcessType
from synthetic.catalog import DEFAULT_CALENDAR_ID, PRINTER_CALENDAR_ID, SCALES
from synthetic.defects import DEFECT_KINDS
from synthetic.generator import DEFAULT_AS_OF, SyntheticDataGenerator, SyntheticDataset

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def small() -> SyntheticDataset:
    return SyntheticDataGenerator(seed=42, scale="small").generate()


def _fingerprint(ds: SyntheticDataset) -> list[tuple[object, ...]]:
    return [dataclasses.astuple(o) for o in ds.orders] + [dataclasses.astuple(op) for op in ds.operations]


def test_same_seed_is_identical() -> None:
    a = SyntheticDataGenerator(seed=7, scale="small").generate()
    b = SyntheticDataGenerator(seed=7, scale="small").generate()
    assert _fingerprint(a) == _fingerprint(b)
    assert [dataclasses.astuple(m) for m in a.machines] == [dataclasses.astuple(m) for m in b.machines]
    assert [dataclasses.astuple(c) for c in a.customers] == [dataclasses.astuple(c) for c in b.customers]
    assert a.stats.to_dict() | {"generation_seconds": 0} == b.stats.to_dict() | {"generation_seconds": 0}


def test_different_seed_differs() -> None:
    a = SyntheticDataGenerator(seed=1, scale="small").generate()
    b = SyntheticDataGenerator(seed=2, scale="small").generate()
    assert _fingerprint(a) != _fingerprint(b)


def test_small_scale_counts(small: SyntheticDataset) -> None:
    profile = SCALES["small"]
    assert len(small.customers) == 80
    # duplicate-order defects add rows on top of the nominal volume
    assert profile.orders <= len(small.orders) <= profile.orders + profile.orders * 0.05
    assert len(small.machines) == sum(profile.machines_per_group.values()) == 12
    assert len(small.materials) >= profile.materials
    assert len(small.tooling) == profile.tooling
    assert len(small.calendars) == 2
    assert small.default_calendar_id == DEFAULT_CALENDAR_ID


def test_medium_scale_counts() -> None:
    ds = SyntheticDataGenerator(seed=3, scale="medium").generate()
    assert len(ds.customers) == 800
    assert 5_000 <= len(ds.orders) <= 5_250
    assert len(ds.machines) == 45
    assert len(ds.materials) == 33
    assert len(ds.tooling) == 40
    assert ds.stats.machines_down == 2
    assert ds.stats.generation_seconds < 5.0


@pytest.mark.slow
def test_large_scale_performance() -> None:
    started = time.perf_counter()
    ds = SyntheticDataGenerator(seed=5, scale="large").generate()
    assert time.perf_counter() - started < 20.0
    assert 20_000 <= len(ds.orders) <= 21_000
    assert len(ds.machines) == 120


def test_customer_tier_mix(small: SyntheticDataset) -> None:
    ds = SyntheticDataGenerator(seed=11, scale="medium").generate()
    tiers = ds.stats.tier_distribution
    total = sum(tiers.values())
    assert 0.02 <= tiers["strategic"] / total <= 0.09
    assert 0.10 <= tiers["key"] / total <= 0.20
    assert 0.55 <= tiers["standard"] / total <= 0.75
    assert any(c.sla_hours for c in ds.customers)
    assert any(c.escalation_level > 0 for c in ds.customers)
    assert len({c.customer_name for c in ds.customers}) == len(ds.customers)


def test_stats_sanity_medium() -> None:
    stats = SyntheticDataGenerator(seed=42, scale="medium").generate().stats
    assert 0.03 <= stats.overdue_share <= 0.08
    assert 0.06 <= stats.due_today_or_tomorrow_share <= 0.14
    assert 0.02 <= stats.on_hold_share <= 0.06
    assert 0.01 <= stats.drawing_unapproved_share <= 0.04
    assert 0.015 <= stats.quality_hold_or_rework_share <= 0.05
    assert 0.08 <= stats.blocked_share <= 0.20
    assert stats.orders_with_dependencies > 0
    assert stats.materials_short >= 2
    assert stats.tooling_unavailable >= 1
    assert stats.dq_defect_total == sum(stats.dq_defects.values())
    assert set(stats.dq_defects) == set(DEFECT_KINDS)
    assert all(v > 0 for v in stats.dq_defects.values())
    assert stats.to_dict()["scale"] == "medium"


def test_machines_are_realistic(small: SyntheticDataset) -> None:
    groups = {m.machine_group for m in small.machines}
    assert {"CNC3", "CNC5", "LATHE", "AM_SLA", "AM_FDM", "DEBURR", "CMM", "SURF", "ASSY", "PACK"} <= groups
    printers = [m for m in small.machines if m.process_type == ProcessType.ADDITIVE_3D_PRINTING]
    assert printers and all(m.calendar_id == PRINTER_CALENDAR_ID for m in printers)
    cncs = [m for m in small.machines if m.process_type == ProcessType.CNC_MACHINING]
    assert all(m.compatible_materials for m in cncs)
    down = [m for m in small.machines if m.status == MachineStatus.DOWN]
    assert len(down) == 1 and down[0].unplanned_downtime
    assert any(m.maintenance_windows for m in small.machines)
    assert all(m.efficiency > 0 for m in small.machines)


def test_materials_and_tooling(small: SyntheticDataset) -> None:
    short = [m for m in small.materials if m.available_quantity <= 0]
    assert short and all(m.incoming_quantity > 0 and m.expected_receipt_date is not None for m in short)
    assert all(
        m.expected_receipt_date is None or m.expected_receipt_date.tzinfo is not None for m in small.materials
    )
    assert any(m.reserved_quantity > 0 for m in small.materials)
    machine_ids = {m.machine_id for m in small.machines}
    assert all(t.compatible_machine_ids <= machine_ids and t.compatible_machine_ids for t in small.tooling)
    assert any(not t.available for t in small.tooling)


def test_calendars(small: SyntheticDataset) -> None:
    default = next(c for c in small.calendars if c.calendar_id == DEFAULT_CALENDAR_ID)
    assert default.timezone == "Asia/Kolkata"
    assert [(s.start.hour, s.end.hour) for s in default.shifts] == [(6, 14), (14, 22)]
    assert all(s.weekdays == (0, 1, 2, 3, 4) for s in default.shifts)
    assert default.holidays
    printers = next(c for c in small.calendars if c.calendar_id == PRINTER_CALENDAR_ID)
    assert len(printers.shifts) == 3 and printers.shifts[-1].crosses_midnight


def test_orders_and_operations_are_consistent(small: SyntheticDataset) -> None:
    ops_by_order: dict[str, list] = {}
    for op in small.operations:
        ops_by_order.setdefault(op.order_id, []).append(op)
    customer_ids = {c.customer_id for c in small.customers}
    material_ids = {m.material_id for m in small.materials}
    for order in small.orders:
        assert order.customer_id in customer_ids
        ops = sorted(ops_by_order[order.order_id], key=lambda o: o.sequence)
        assert [op.operation_type for op in ops] == order.manufacturing_route
        assert ops[0].prerequisite_operation_id is None
        assert all(b.prerequisite_operation_id == a.operation_id for a, b in zip(ops, ops[1:], strict=False))
        for dt in (order.order_date, order.received_date, order.due_date):
            assert dt is None or dt.tzinfo is not None
        if order.order_date and order.due_date:
            assert order.order_date < order.due_date
        if order.attributes.get("injected_defect") != "unknown_material_ref" and order.required_material_id:
            assert order.required_material_id in material_ids
        if order.order_status in (OrderStatus.COMPLETED, OrderStatus.SHIPPED, OrderStatus.PACKED):
            assert all(op.operation_status == OperationStatus.COMPLETED for op in ops)
            assert order.pending_quantity == 0
        if order.order_status == OrderStatus.IN_PRODUCTION:
            assert any(op.operation_status == OperationStatus.IN_PROGRESS for op in ops)
        if order.on_hold:
            assert order.order_status == OrderStatus.ON_HOLD and order.hold_reason
    cnc_first_ops = [ops_by_order[o.order_id][0] for o in small.orders if o.attributes.get("kind") == "cnc"]
    assert all(op.tooling_ids for op in cnc_first_ops)
    assert all(op.setup_family for op in cnc_first_ops)
    assert any(op.machine_cycle_minutes for op in cnc_first_ops)


def test_assembly_dependencies(small: SyntheticDataset) -> None:
    by_id = {o.order_id: o for o in small.orders}
    assemblies = [o for o in small.orders if o.depends_on_order_ids]
    assert assemblies
    for assembly in assemblies:
        assert assembly.process_type == ProcessType.ASSEMBLY
        for dep in assembly.depends_on_order_ids:
            part = by_id[dep]
            assert part.customer_id == assembly.customer_id
            if part.due_date and assembly.due_date:
                assert part.due_date <= assembly.due_date


def test_defect_injection_tags(small: SyntheticDataset) -> None:
    tagged = [o for o in small.orders if "injected_defect" in o.attributes]
    kinds = {o.attributes["injected_defect"] for o in tagged}
    assert kinds == set(DEFECT_KINDS)
    assert small.stats.dq_defect_total == len(tagged) - sum(
        1 for o in tagged if "duplicate_of" in o.attributes
    )
    missing_due = [o for o in tagged if o.attributes["injected_defect"] == "missing_due_date"]
    assert all(o.due_date is None for o in missing_due)
    negative = [o for o in tagged if o.attributes["injected_defect"] == "negative_quantity"]
    assert all(o.quantity < 0 for o in negative)
    dup = next(o for o in tagged if "duplicate_of" in o.attributes)
    original = next(o for o in small.orders if o.order_id == dup.attributes["duplicate_of"])
    assert (dup.external_order_ref, dup.order_line_id) == (
        original.external_order_ref,
        original.order_line_id,
    )


def test_zero_defect_ratio() -> None:
    ds = SyntheticDataGenerator(seed=42, scale="small", dq_defect_ratio=0.0).generate()
    assert ds.stats.dq_defect_total == 0
    assert not any("injected_defect" in o.attributes for o in ds.orders)
    assert len(ds.orders) == SCALES["small"].orders


def test_invalid_arguments() -> None:
    with pytest.raises(ValidationError):
        SyntheticDataGenerator(scale="huge")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        SyntheticDataGenerator(dq_defect_ratio=1.5)


def test_as_of_handling() -> None:
    assert SyntheticDataGenerator().as_of == DEFAULT_AS_OF
    naive = datetime(2026, 10, 5, 4, 0)
    generator = SyntheticDataGenerator(seed=1, scale="small", as_of=naive)
    assert generator.as_of == naive.replace(tzinfo=UTC)
    ds = generator.generate()
    assert ds.as_of == generator.as_of
    assert all(o.order_date is None or o.order_date < ds.as_of for o in ds.orders)


def test_to_snapshot_indexes(small: SyntheticDataset) -> None:
    snapshot = small.to_snapshot()
    assert snapshot.as_of == small.as_of
    assert snapshot.source == "synthetic"
    assert snapshot.default_calendar_id == DEFAULT_CALENDAR_ID
    assert snapshot.summary()["orders"] == len(small.orders)
    order = next(o for o in small.orders if o.is_open and o.order_status == OrderStatus.RELEASED)
    ops = snapshot.operations_for_order(order.order_id)
    assert [op.sequence for op in ops] == sorted(op.sequence for op in ops)
    assert snapshot.next_operation_for_order(order.order_id) is ops[0]
    assert snapshot.machines_in_group("CNC3")
    assembly = next(o for o in small.orders if o.depends_on_order_ids)
    dep = next(iter(assembly.depends_on_order_ids))
    assert assembly.order_id in snapshot.dependents_of(dep)
    assert (
        snapshot.calendar_for_machine(snapshot.machines_in_group("AM_SLA")[0]).calendar_id
        == PRINTER_CALENDAR_ID
    )
    later = small.to_snapshot(as_of=small.as_of + timedelta(days=1))
    assert later.as_of == small.as_of + timedelta(days=1)
