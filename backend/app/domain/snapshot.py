"""PlanningSnapshot: everything the engines need, frozen at one instant.

The snapshot is the single input type of every engine. It is built by the
persistence layer (from synced ERP data plus planner overlays) or by the
simulation engine (by cloning and mutating a baseline). Engines never mutate a
snapshot they were given; simulation works on ``snapshot.clone()``.
"""

from __future__ import annotations

import copy
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from app.domain.enums import ProcessType
from app.domain.models import (
    CalendarSpec,
    Customer,
    CustomerRule,
    Expedite,
    Machine,
    Material,
    Operation,
    Order,
    PriorityOverride,
    ScheduleLock,
    Tooling,
)


@dataclass(slots=True)
class PlanningSnapshot:
    as_of: datetime
    customers: dict[str, Customer] = field(default_factory=dict)
    orders: dict[str, Order] = field(default_factory=dict)
    operations: dict[str, Operation] = field(default_factory=dict)
    machines: dict[str, Machine] = field(default_factory=dict)
    materials: dict[str, Material] = field(default_factory=dict)
    tooling: dict[str, Tooling] = field(default_factory=dict)
    calendars: dict[str, CalendarSpec] = field(default_factory=dict)
    default_calendar_id: str | None = None
    locks: list[ScheduleLock] = field(default_factory=list)
    overrides: list[PriorityOverride] = field(default_factory=list)
    expedites: list[Expedite] = field(default_factory=list)
    customer_rules: dict[str, CustomerRule] = field(default_factory=dict)
    snapshot_id: str | None = None
    source: str = "unknown"
    _ops_by_order: dict[str, list[Operation]] = field(default_factory=dict, repr=False)
    _machines_by_group: dict[str, list[Machine]] = field(default_factory=dict, repr=False)
    _dependents: dict[str, set[str]] = field(default_factory=dict, repr=False)

    # ------------------------------------------------------------------ build
    def rebuild_indexes(self) -> None:
        by_order: dict[str, list[Operation]] = defaultdict(list)
        for op in self.operations.values():
            by_order[op.order_id].append(op)
        for ops in by_order.values():
            ops.sort(key=lambda o: (o.sequence, o.operation_id))
        self._ops_by_order = dict(by_order)

        by_group: dict[str, list[Machine]] = defaultdict(list)
        for m in self.machines.values():
            by_group[m.machine_group].append(m)
        for ms in by_group.values():
            ms.sort(key=lambda m: (m.preferred_rank, m.machine_id))
        self._machines_by_group = dict(by_group)

        dependents: dict[str, set[str]] = defaultdict(set)
        for order in self.orders.values():
            for upstream in order.depends_on_order_ids:
                dependents[upstream].add(order.order_id)
        self._dependents = dict(dependents)

    def add_operations(self, ops: Iterable[Operation]) -> None:
        for op in ops:
            self.operations[op.operation_id] = op
        self.rebuild_indexes()

    # ---------------------------------------------------------------- queries
    def operations_for_order(self, order_id: str) -> list[Operation]:
        if not self._ops_by_order and self.operations:
            self.rebuild_indexes()
        return list(self._ops_by_order.get(order_id, ()))

    def pending_operations_for_order(self, order_id: str) -> list[Operation]:
        return [op for op in self.operations_for_order(order_id) if not op.is_done]

    def next_operation_for_order(self, order_id: str) -> Operation | None:
        pending = self.pending_operations_for_order(order_id)
        return pending[0] if pending else None

    def machines_in_group(self, group: str) -> list[Machine]:
        if not self._machines_by_group and self.machines:
            self.rebuild_indexes()
        return list(self._machines_by_group.get(group, ()))

    def machines_for_process(self, process: ProcessType) -> list[Machine]:
        return sorted(
            (m for m in self.machines.values() if m.supports_process(process)),
            key=lambda m: (m.preferred_rank, m.machine_id),
        )

    def dependents_of(self, order_id: str) -> set[str]:
        if not self._dependents and self.orders:
            self.rebuild_indexes()
        return set(self._dependents.get(order_id, ()))

    def open_orders(self) -> list[Order]:
        return sorted((o for o in self.orders.values() if o.is_open), key=lambda o: o.order_id)

    def calendar_for_machine(self, machine: Machine) -> CalendarSpec | None:
        cal_id = machine.calendar_id or self.default_calendar_id
        if cal_id is None:
            return None
        return self.calendars.get(cal_id)

    def active_expedites(self, now: datetime | None = None) -> dict[str, Expedite]:
        now = now or self.as_of
        result: dict[str, Expedite] = {}
        for e in self.expedites:
            if e.is_active_at(now):
                cur = result.get(e.order_id)
                if cur is None or e.boost_points > cur.boost_points:
                    result[e.order_id] = e
        return result

    def active_overrides(self, now: datetime | None = None) -> dict[str, list[PriorityOverride]]:
        now = now or self.as_of
        result: dict[str, list[PriorityOverride]] = defaultdict(list)
        for o in self.overrides:
            if o.is_active_at(now):
                result[o.order_id].append(o)
        return dict(result)

    def active_locks(self, now: datetime | None = None) -> list[ScheduleLock]:
        now = now or self.as_of
        out: list[ScheduleLock] = []
        for lock in self.locks:
            if not lock.active:
                continue
            if lock.window is not None and lock.window.end <= now:
                continue
            out.append(lock)
        return out

    # ----------------------------------------------------------------- clone
    def clone(self) -> PlanningSnapshot:
        """Deep copy for what-if simulation. Indexes are rebuilt lazily."""
        new = copy.deepcopy(self)
        new.rebuild_indexes()
        return new

    def summary(self) -> dict[str, int]:
        return {
            "customers": len(self.customers),
            "orders": len(self.orders),
            "open_orders": sum(1 for o in self.orders.values() if o.is_open),
            "operations": len(self.operations),
            "machines": len(self.machines),
            "materials": len(self.materials),
            "tooling": len(self.tooling),
            "locks": len(self.locks),
            "overrides": len(self.overrides),
            "expedites": len(self.expedites),
        }
