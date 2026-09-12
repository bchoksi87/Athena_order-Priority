"""Timing test: 5,000 orders / ~12,000 operations / 45 machines in under 10 s (slow-marked)."""

from __future__ import annotations

import time

import pytest

from app.core.clock import FrozenClock
from app.domain.config import SchedulingConfig
from app.domain.enums import ProcessType
from app.engines.calendar import build_calendars
from app.engines.constraints import default_constraint_engine
from app.engines.scheduling.rule_based import RuleBasedScheduler
from tests.engines.factories import (
    NOW,
    at,
    make_calendar_spec,
    make_machine,
    make_order_with_ops,
    make_priorities,
    make_snapshot,
)

ORDERS = 5000
MACHINES = 45


def _big_snapshot():
    machines = []
    for i in range(MACHINES):
        if i < 20:
            machines.append(
                make_machine(
                    f"CNC-{i:02d}", ProcessType.CNC_MACHINING, "CNC", calendar_id="CAL", preferred_rank=i % 3
                )
            )
        elif i < 32:
            machines.append(
                make_machine(f"AM-{i:02d}", ProcessType.ADDITIVE_3D_PRINTING, "AM", calendar_id="CAL")
            )
        elif i < 40:
            machines.append(make_machine(f"DEB-{i:02d}", ProcessType.DEBURRING, "DEB", calendar_id="CAL"))
        else:
            machines.append(make_machine(f"INS-{i:02d}", ProcessType.INSPECTION, "INS", calendar_id="CAL"))
    orders, ops = [], []
    routes = [
        (ProcessType.CNC_MACHINING, ProcessType.DEBURRING, ProcessType.INSPECTION),
        (ProcessType.ADDITIVE_3D_PRINTING, ProcessType.DEBURRING),
        (ProcessType.CNC_MACHINING, ProcessType.INSPECTION),
        (ProcessType.CNC_MACHINING, ProcessType.DEBURRING),
        (ProcessType.ADDITIVE_3D_PRINTING, ProcessType.INSPECTION, ProcessType.DEBURRING),
    ]
    for i in range(ORDERS):
        route = routes[i % len(routes)]
        material = f"MAT-{i % 7}"
        overrides = [
            {
                "setup_minutes": 20.0 + (i % 4) * 10,
                "cycle_minutes_per_unit": 2.0 + (i % 5),
                "material_id": material,
                "setup_family": f"F{i % 9}",
            }
            for _ in route
        ]
        order, order_ops = make_order_with_ops(
            f"O{i:05d}",
            route,
            customer_id=f"C{i % 800}",
            op_overrides=overrides,
            requested_delivery_date=at(days=1 + (i % 20), hours=i % 8),
            quantity=float(5 + i % 10),
            part_family=f"PF{i % 30}",
        )
        orders.append(order)
        ops.extend(order_ops)
    return make_snapshot(
        orders=orders,
        operations=ops,
        machines=machines,
        calendars=[make_calendar_spec("CAL")],
        default_calendar_id="CAL",
    )


@pytest.mark.slow
def test_large_plant_under_ten_seconds() -> None:
    snap = _big_snapshot()
    assert len(snap.operations) >= 12_000
    config = SchedulingConfig(horizon_days=30)
    calendars = build_calendars(snap)
    engine = default_constraint_engine(config)
    priorities = make_priorities(
        {oid: float((i * 37) % 1000) / 10 for i, oid in enumerate(sorted(snap.orders))}
    )
    scheduler = RuleBasedScheduler(FrozenClock(NOW))
    start = time.perf_counter()
    result = scheduler.schedule(snap, priorities, config, calendars, engine)
    elapsed = time.perf_counter() - start
    assert len(result.entries) == len(snap.operations)
    assert not result.unscheduled
    for machine_id in snap.machines:
        entries = result.entries_for_machine(machine_id)
        for a, b in zip(entries, entries[1:], strict=False):
            assert a.end <= b.setup_start
    assert elapsed < 10.0, f"scheduling {ORDERS} orders / {len(snap.operations)} ops took {elapsed:.1f}s"
