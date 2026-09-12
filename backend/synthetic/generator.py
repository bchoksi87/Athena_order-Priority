"""Deterministic synthetic data generator (spec Phase 31).

Usage::

    dataset = SyntheticDataGenerator(seed=42, scale="medium").generate()
    snapshot = dataset.to_snapshot()

The same ``seed``/``scale``/``as_of`` always yields an identical dataset. All
randomness flows through one ``random.Random`` instance; nothing touches the
global random state or the wall clock.
"""

from __future__ import annotations

import random
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

from app.core.clock import ensure_utc
from app.core.errors import ValidationError
from app.domain.enums import MachineStatus, OrderStatus
from app.domain.models import CalendarSpec, Customer, Machine, Material, Operation, Order, Tooling
from app.domain.snapshot import PlanningSnapshot
from synthetic import catalog
from synthetic.catalog import Scale
from synthetic.defects import DefectInjectionResult, inject_defects
from synthetic.masters import build_calendars, build_customers, build_machines, build_materials, build_tooling
from synthetic.orders import OrderBuildContext, OrderFactory

log = structlog.get_logger(__name__)

#: Fixed default reference instant (Monday 2026-09-14 09:00 IST) so that datasets
#: are reproducible without a clock.
DEFAULT_AS_OF = datetime(2026, 9, 14, 3, 30, tzinfo=UTC)

_BLOCKED_STATUSES = frozenset(
    {OrderStatus.ON_HOLD, OrderStatus.MATERIAL_WAITING, OrderStatus.TOOLING_WAITING, OrderStatus.REWORK}
)


@dataclass(slots=True)
class GenerationStats:
    """Summary statistics about a generated dataset."""

    seed: int
    scale: str
    as_of: datetime
    generation_seconds: float
    customers: int
    orders: int
    open_orders: int
    operations: int
    machines: int
    machines_down: int
    materials: int
    materials_short: int
    tooling: int
    tooling_unavailable: int
    overdue_share: float
    due_today_or_tomorrow_share: float
    on_hold_share: float
    drawing_unapproved_share: float
    quality_hold_or_rework_share: float
    blocked_share: float
    orders_with_dependencies: int
    status_distribution: dict[str, int] = field(default_factory=dict)
    tier_distribution: dict[str, int] = field(default_factory=dict)
    kind_distribution: dict[str, int] = field(default_factory=dict)
    dq_defects: dict[str, int] = field(default_factory=dict)
    dq_defect_total: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "scale": self.scale,
            "as_of": self.as_of.isoformat(),
            "generation_seconds": round(self.generation_seconds, 3),
            "customers": self.customers,
            "orders": self.orders,
            "open_orders": self.open_orders,
            "operations": self.operations,
            "machines": self.machines,
            "machines_down": self.machines_down,
            "materials": self.materials,
            "materials_short": self.materials_short,
            "tooling": self.tooling,
            "tooling_unavailable": self.tooling_unavailable,
            "overdue_share": round(self.overdue_share, 4),
            "due_today_or_tomorrow_share": round(self.due_today_or_tomorrow_share, 4),
            "on_hold_share": round(self.on_hold_share, 4),
            "drawing_unapproved_share": round(self.drawing_unapproved_share, 4),
            "quality_hold_or_rework_share": round(self.quality_hold_or_rework_share, 4),
            "blocked_share": round(self.blocked_share, 4),
            "orders_with_dependencies": self.orders_with_dependencies,
            "status_distribution": dict(self.status_distribution),
            "tier_distribution": dict(self.tier_distribution),
            "kind_distribution": dict(self.kind_distribution),
            "dq_defects": dict(self.dq_defects),
            "dq_defect_total": self.dq_defect_total,
        }


@dataclass(slots=True)
class SyntheticDataset:
    """A complete, self-consistent synthetic plant + order book."""

    as_of: datetime
    customers: list[Customer]
    orders: list[Order]
    operations: list[Operation]
    machines: list[Machine]
    materials: list[Material]
    tooling: list[Tooling]
    calendars: list[CalendarSpec]
    default_calendar_id: str
    stats: GenerationStats

    def to_snapshot(self, as_of: datetime | None = None) -> PlanningSnapshot:
        """Bundle the dataset into a :class:`PlanningSnapshot` with indexes built."""
        snapshot = PlanningSnapshot(
            as_of=ensure_utc(as_of) if as_of is not None else self.as_of,
            customers={c.customer_id: c for c in self.customers},
            orders={o.order_id: o for o in self.orders},
            operations={op.operation_id: op for op in self.operations},
            machines={m.machine_id: m for m in self.machines},
            materials={m.material_id: m for m in self.materials},
            tooling={t.tooling_id: t for t in self.tooling},
            calendars={c.calendar_id: c for c in self.calendars},
            default_calendar_id=self.default_calendar_id,
            snapshot_id=f"synthetic-{self.stats.scale}-{self.stats.seed}",
            source="synthetic",
        )
        snapshot.rebuild_indexes()
        return snapshot


class SyntheticDataGenerator:
    """Seeded generator for realistic manufacturing datasets at three scales."""

    def __init__(
        self,
        seed: int = 42,
        scale: Scale = "medium",
        as_of: datetime | None = None,
        dq_defect_ratio: float = 0.03,
    ) -> None:
        if scale not in catalog.SCALES:
            raise ValidationError(f"unknown scale {scale!r}", details={"allowed": sorted(catalog.SCALES)})
        if not 0.0 <= dq_defect_ratio < 1.0:
            raise ValidationError("dq_defect_ratio must be in [0, 1)", details={"value": dq_defect_ratio})
        self._seed = seed
        self._scale = scale
        self._profile = catalog.SCALES[scale]
        self._as_of = ensure_utc(as_of) if as_of is not None else DEFAULT_AS_OF
        self._dq_defect_ratio = dq_defect_ratio

    @property
    def seed(self) -> int:
        return self._seed

    @property
    def scale(self) -> Scale:
        return self._scale

    @property
    def as_of(self) -> datetime:
        return self._as_of

    def generate(self) -> SyntheticDataset:
        started = time.perf_counter()
        rng = random.Random(self._seed)
        profile = self._profile
        as_of = self._as_of
        log.info("synthetic.generate.start", seed=self._seed, scale=self._scale, as_of=as_of.isoformat())

        customers = build_customers(rng, profile)
        materials = build_materials(rng, profile, as_of)
        machines = build_machines(rng, profile, as_of, materials)
        tooling = build_tooling(rng, profile, machines)
        calendars = build_calendars()
        ctx = OrderBuildContext(
            rng=rng,
            as_of=as_of,
            profile=profile,
            customers=customers,
            machines=machines,
            materials=materials,
            tooling=tooling,
        )
        orders, operations = OrderFactory(ctx).build()
        defects = inject_defects(rng, orders, operations, self._dq_defect_ratio)

        stats = self._compute_stats(
            customers,
            orders,
            operations,
            machines,
            materials,
            tooling,
            defects,
            time.perf_counter() - started,
        )
        log.info(
            "synthetic.generate.done",
            seed=self._seed,
            scale=self._scale,
            orders=stats.orders,
            operations=stats.operations,
            seconds=round(stats.generation_seconds, 3),
            dq_defects=stats.dq_defect_total,
        )
        return SyntheticDataset(
            as_of=as_of,
            customers=customers,
            orders=orders,
            operations=operations,
            machines=machines,
            materials=materials,
            tooling=tooling,
            calendars=calendars,
            default_calendar_id=catalog.DEFAULT_CALENDAR_ID,
            stats=stats,
        )

    def _compute_stats(
        self,
        customers: list[Customer],
        orders: list[Order],
        operations: list[Operation],
        machines: list[Machine],
        materials: list[Material],
        tooling: list[Tooling],
        defects: DefectInjectionResult,
        seconds: float,
    ) -> GenerationStats:
        as_of = self._as_of
        open_orders = [o for o in orders if o.is_open]
        open_count = max(1, len(open_orders))
        overdue = 0
        due_soon = 0
        for order in open_orders:
            due = order.due_date
            if due is None:
                continue
            if due < as_of:
                overdue += 1
            elif (due.date() - as_of.date()).days <= 1:
                due_soon += 1
        quality = sum(1 for o in open_orders if o.quality_status.value in ("hold", "rework"))
        blocked = sum(
            1
            for o in open_orders
            if o.on_hold or not o.drawing_approved or o.order_status in _BLOCKED_STATUSES
        )
        return GenerationStats(
            seed=self._seed,
            scale=self._scale,
            as_of=as_of,
            generation_seconds=seconds,
            customers=len(customers),
            orders=len(orders),
            open_orders=len(open_orders),
            operations=len(operations),
            machines=len(machines),
            machines_down=sum(1 for m in machines if m.status == MachineStatus.DOWN),
            materials=len(materials),
            materials_short=sum(1 for m in materials if m.available_quantity <= 0),
            tooling=len(tooling),
            tooling_unavailable=sum(1 for t in tooling if not t.available),
            overdue_share=overdue / open_count,
            due_today_or_tomorrow_share=due_soon / open_count,
            on_hold_share=sum(1 for o in open_orders if o.on_hold) / open_count,
            drawing_unapproved_share=sum(1 for o in open_orders if not o.drawing_approved) / open_count,
            quality_hold_or_rework_share=quality / open_count,
            blocked_share=blocked / open_count,
            orders_with_dependencies=sum(1 for o in orders if o.depends_on_order_ids),
            status_distribution=dict(Counter(o.order_status.value for o in orders)),
            tier_distribution=dict(Counter(c.customer_tier.value for c in customers)),
            kind_distribution=dict(Counter(str(o.attributes.get("kind", "unknown")) for o in orders)),
            dq_defects=dict(defects.counts),
            dq_defect_total=defects.total,
        )


__all__ = ["DEFAULT_AS_OF", "GenerationStats", "SyntheticDataGenerator", "SyntheticDataset"]
