"""Shared building blocks of the Data Quality Engine (spec Phase 21).

The engine is a list of small, independent *rules*. Each rule inspects one
aspect of the snapshot (dates, quantities, routing, ...) and yields
:class:`~app.domain.results.DataQualityIssue` objects. Everything the rules need
that would otherwise be recomputed per rule (operations grouped by order,
machines grouped by group/process, the sorted list of open orders, the
severity policy) is prepared once in :class:`DataQualityContext` so that the
whole run stays O(orders + operations + machines) regardless of the number of
rules.

Severity policy
---------------
``DataQualityConfig`` only exposes toggles for the four codes the business
wanted to switch between *blocking* and *warning* (missing due date, cycle
time, setup time, machine assignment). Every other code has a fixed default in
:data:`DEFAULT_SEVERITIES`; the engine caller may still override any of them
through :meth:`SeverityPolicy.from_config`. Rules that report a milder
*variant* of a code (e.g. a zero cycle time instead of a negative one) pass an
explicit severity; everything else takes the policy default so the behaviour is
configured in exactly one place.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from app.domain.config import DataQualityConfig
from app.domain.enums import DataQualityCode, DataQualitySeverity, ProcessType
from app.domain.models import Machine, Operation, Order
from app.domain.results import DataQualityIssue
from app.domain.snapshot import PlanningSnapshot

# Entity type labels used in ``DataQualityIssue.entity_type``. Kept as plain
# strings because the persistence layer stores them verbatim.
ENTITY_ORDER = "order"
ENTITY_OPERATION = "operation"
ENTITY_MACHINE = "machine"
ENTITY_CUSTOMER = "customer"
ENTITY_SNAPSHOT = "snapshot"
ENTITY_LOCK = "lock"
ENTITY_OVERRIDE = "override"
ENTITY_EXPEDITE = "expedite"
ENTITY_CUSTOMER_RULE = "customer_rule"

#: Key stored in ``DataQualityIssue.details`` that links an operation-level
#: issue back to its order so the report can group by order in O(1).
DETAIL_ORDER_ID = "order_id"

#: Fixed severities for the codes that have no toggle in ``DataQualityConfig``.
#: The rationale is "does the scheduler produce a *wrong* plan if we ignore it?"
#: (blocking) versus "the plan is still usable, the ERP team should fix the
#: record" (warning).
DEFAULT_SEVERITIES: Mapping[DataQualityCode, DataQualitySeverity] = {
    DataQualityCode.MISSING_DUE_DATE: DataQualitySeverity.BLOCKING,
    DataQualityCode.INVALID_DATE: DataQualitySeverity.WARNING,
    DataQualityCode.MISSING_CYCLE_TIME: DataQualitySeverity.BLOCKING,
    DataQualityCode.MISSING_SETUP_TIME: DataQualitySeverity.WARNING,
    DataQualityCode.MISSING_MACHINE_ASSIGNMENT: DataQualitySeverity.BLOCKING,
    DataQualityCode.MISSING_MATERIAL: DataQualitySeverity.BLOCKING,
    DataQualityCode.NEGATIVE_QUANTITY: DataQualitySeverity.BLOCKING,
    DataQualityCode.DUPLICATE_ORDER: DataQualitySeverity.WARNING,
    DataQualityCode.INCORRECT_STATUS: DataQualitySeverity.WARNING,
    DataQualityCode.IMPOSSIBLE_PRODUCTION_TIME: DataQualitySeverity.BLOCKING,
    DataQualityCode.MISSING_CUSTOMER: DataQualitySeverity.WARNING,
    DataQualityCode.CONFLICTING_MACHINE_CAPABILITY: DataQualitySeverity.BLOCKING,
    DataQualityCode.INVALID_ROUTING: DataQualitySeverity.BLOCKING,
    DataQualityCode.MISSING_OPERATIONS: DataQualitySeverity.BLOCKING,
    DataQualityCode.UNKNOWN_REFERENCE: DataQualitySeverity.WARNING,
}


def _toggle(flag: bool) -> DataQualitySeverity:
    return DataQualitySeverity.BLOCKING if flag else DataQualitySeverity.WARNING


@dataclass(slots=True, frozen=True)
class SeverityPolicy:
    """Code → severity mapping resolved from configuration.

    ``from_config`` applies the four ``treat_*_as_blocking`` toggles on top of
    :data:`DEFAULT_SEVERITIES`; explicit ``overrides`` win over both so that a
    deployment can, for instance, downgrade ``MISSING_MATERIAL`` to a warning
    without a code change.
    """

    by_code: Mapping[DataQualityCode, DataQualitySeverity]

    @classmethod
    def from_config(
        cls,
        config: DataQualityConfig,
        overrides: Mapping[DataQualityCode, DataQualitySeverity] | None = None,
    ) -> SeverityPolicy:
        table: dict[DataQualityCode, DataQualitySeverity] = dict(DEFAULT_SEVERITIES)
        table[DataQualityCode.MISSING_DUE_DATE] = _toggle(config.treat_missing_due_date_as_blocking)
        table[DataQualityCode.MISSING_CYCLE_TIME] = _toggle(config.treat_missing_cycle_as_blocking)
        table[DataQualityCode.MISSING_SETUP_TIME] = _toggle(config.treat_missing_setup_as_blocking)
        table[DataQualityCode.MISSING_MACHINE_ASSIGNMENT] = _toggle(config.treat_missing_machine_as_blocking)
        if overrides:
            table.update(overrides)
        return cls(by_code=table)

    def for_code(self, code: DataQualityCode) -> DataQualitySeverity:
        return self.by_code.get(code, DataQualitySeverity.WARNING)


def is_naive(dt: datetime) -> bool:
    """True when ``dt`` carries no usable UTC offset (engines require aware UTC)."""
    return dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None


def is_blank(value: str | None) -> bool:
    return value is None or not value.strip()


def fmt_ids(ids: Iterable[str], limit: int = 5) -> str:
    """Render a bounded, sorted id list for messages (``a, b, c, ... (+4 more)``)."""
    ordered = sorted(ids)
    shown = ", ".join(ordered[:limit])
    extra = len(ordered) - limit
    return f"{shown}, ... (+{extra} more)" if extra > 0 else shown


@dataclass(slots=True)
class DataQualityContext:
    """Precomputed, read-only view of a snapshot shared by all rules.

    Built once per engine run. Rules must treat every attribute as immutable.
    """

    snapshot: PlanningSnapshot
    config: DataQualityConfig
    as_of: datetime
    policy: SeverityPolicy
    all_orders: list[Order] = field(default_factory=list)  # sorted by order_id
    open_orders: list[Order] = field(default_factory=list)  # subset, same order
    ops_by_order: dict[str, list[Operation]] = field(default_factory=dict)  # sorted by sequence
    machines_by_group: dict[str, list[Machine]] = field(default_factory=dict)
    machines_by_process: dict[ProcessType, list[Machine]] = field(default_factory=dict)

    # ------------------------------------------------------------------ build
    @classmethod
    def build(
        cls,
        snapshot: PlanningSnapshot,
        config: DataQualityConfig,
        policy: SeverityPolicy | None = None,
    ) -> DataQualityContext:
        all_orders = sorted(snapshot.orders.values(), key=lambda o: o.order_id)
        open_orders = [o for o in all_orders if o.is_open]

        ops_by_order: dict[str, list[Operation]] = defaultdict(list)
        for op in snapshot.operations.values():
            ops_by_order[op.order_id].append(op)
        for ops in ops_by_order.values():
            ops.sort(key=lambda o: (o.sequence, o.operation_id))

        by_group: dict[str, list[Machine]] = defaultdict(list)
        by_process: dict[ProcessType, list[Machine]] = defaultdict(list)
        for machine in snapshot.machines.values():
            by_group[machine.machine_group].append(machine)
            by_process[machine.process_type].append(machine)
            for extra in machine.compatible_processes:
                if extra != machine.process_type:
                    by_process[extra].append(machine)

        def machine_key(m: Machine) -> tuple[int, str]:
            return (m.preferred_rank, m.machine_id)

        for machines in by_group.values():
            machines.sort(key=machine_key)
        for machines in by_process.values():
            machines.sort(key=machine_key)

        return cls(
            snapshot=snapshot,
            config=config,
            as_of=snapshot.as_of,
            policy=policy or SeverityPolicy.from_config(config),
            all_orders=all_orders,
            open_orders=open_orders,
            ops_by_order=dict(ops_by_order),
            machines_by_group=dict(by_group),
            machines_by_process=dict(by_process),
        )

    # ---------------------------------------------------------------- queries
    def operations_for(self, order_id: str) -> list[Operation]:
        """Operations of an order sorted by sequence (shared list: do not mutate)."""
        return self.ops_by_order.get(order_id, _EMPTY_OPS)

    def pending_operations_for(self, order_id: str) -> list[Operation]:
        return [op for op in self.operations_for(order_id) if not op.is_done]

    def machines_supporting(self, process: ProcessType) -> list[Machine]:
        return self.machines_by_process.get(process, _EMPTY_MACHINES)

    @staticmethod
    def resolve_material_id(op: Operation, order: Order) -> str | None:
        """Material the step consumes: operation value first, order-level fallback second."""
        if not is_blank(op.material_id):
            return op.material_id
        if not is_blank(order.required_material_id):
            return order.required_material_id
        return None

    @staticmethod
    def resolve_cycle_minutes(op: Operation, order: Order) -> float | None:
        """Best available cycle time per unit for duration sanity checks.

        Preference: explicit operation value, then the smallest per-machine
        override (a *lower* bound on the run time), then the order estimate.
        """
        if op.cycle_minutes_per_unit is not None:
            return op.cycle_minutes_per_unit
        if op.machine_cycle_minutes:
            return min(op.machine_cycle_minutes.values())
        return order.estimated_cycle_minutes_per_unit

    # ---------------------------------------------------------------- issues
    def issue(
        self,
        code: DataQualityCode,
        entity_type: str,
        entity_id: str,
        message: str,
        *,
        field_name: str | None = None,
        recommendation: str | None = None,
        details: Mapping[str, Any] | None = None,
        severity: DataQualitySeverity | None = None,
    ) -> DataQualityIssue:
        return DataQualityIssue(
            code=code,
            severity=severity if severity is not None else self.policy.for_code(code),
            entity_type=entity_type,
            entity_id=entity_id,
            message=message,
            field_name=field_name,
            recommendation=recommendation,
            details=dict(details or {}),
        )

    def order_issue(
        self,
        order: Order,
        code: DataQualityCode,
        message: str,
        *,
        field_name: str | None = None,
        recommendation: str | None = None,
        details: Mapping[str, Any] | None = None,
        severity: DataQualitySeverity | None = None,
    ) -> DataQualityIssue:
        merged = {DETAIL_ORDER_ID: order.order_id, **(details or {})}
        return self.issue(
            code,
            ENTITY_ORDER,
            order.order_id,
            message,
            field_name=field_name,
            recommendation=recommendation,
            details=merged,
            severity=severity,
        )

    def operation_issue(
        self,
        op: Operation,
        code: DataQualityCode,
        message: str,
        *,
        field_name: str | None = None,
        recommendation: str | None = None,
        details: Mapping[str, Any] | None = None,
        severity: DataQualitySeverity | None = None,
    ) -> DataQualityIssue:
        merged = {DETAIL_ORDER_ID: op.order_id, "sequence": op.sequence, **(details or {})}
        return self.issue(
            code,
            ENTITY_OPERATION,
            op.operation_id,
            message,
            field_name=field_name,
            recommendation=recommendation,
            details=merged,
            severity=severity,
        )


_EMPTY_OPS: list[Operation] = []
_EMPTY_MACHINES: list[Machine] = []


class DataQualityRule(Protocol):
    """One validation concern.

    ``key`` is unique within an engine; ``entity`` names the entity type the
    rule primarily reports on (used for documentation and filtering only).
    ``run`` must be deterministic and must never mutate the snapshot.
    """

    key: str
    entity: str

    def run(
        self,
        snapshot: PlanningSnapshot,
        config: DataQualityConfig,
        ctx: DataQualityContext,
    ) -> Iterable[DataQualityIssue]: ...


__all__ = [
    "DEFAULT_SEVERITIES",
    "DETAIL_ORDER_ID",
    "ENTITY_CUSTOMER",
    "ENTITY_CUSTOMER_RULE",
    "ENTITY_EXPEDITE",
    "ENTITY_LOCK",
    "ENTITY_MACHINE",
    "ENTITY_OPERATION",
    "ENTITY_ORDER",
    "ENTITY_OVERRIDE",
    "ENTITY_SNAPSHOT",
    "DataQualityContext",
    "DataQualityRule",
    "SeverityPolicy",
    "fmt_ids",
    "is_blank",
    "is_naive",
]
