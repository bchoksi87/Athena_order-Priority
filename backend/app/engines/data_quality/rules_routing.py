"""Routing and referential-integrity rules (spec Phase 21).

``InvalidRoutingRule`` validates the operation graph of every open order:
missing routings, duplicate sequence numbers, prerequisites that point
outside the order / forward in the route / into a cycle, order-level
dependency cycles and a manufacturing route that disagrees with the steps.
Gaps in sequence numbers (10/20/30) are *accepted* because that is how ERP
routings are conventionally numbered.

``UnknownReferenceRule`` reports ids that do not resolve in the snapshot
(materials, tooling, calendars, machines, orders, customers). It always
reports individual bad ids; the missing-machine rule separately decides
whether a step is left with *no* machine at all.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator

from app.domain.config import DataQualityConfig
from app.domain.enums import DataQualityCode, DataQualitySeverity
from app.domain.models import Operation, Order
from app.domain.results import DataQualityIssue
from app.domain.snapshot import PlanningSnapshot
from app.engines.data_quality.base import (
    ENTITY_CUSTOMER_RULE,
    ENTITY_EXPEDITE,
    ENTITY_LOCK,
    ENTITY_MACHINE,
    ENTITY_OPERATION,
    ENTITY_ORDER,
    ENTITY_OVERRIDE,
    ENTITY_SNAPSHOT,
    DataQualityContext,
    fmt_ids,
    is_blank,
)


def find_cycle_members(successors: dict[str, set[str]]) -> set[str]:
    """Nodes that lie on at least one directed cycle (iterative Tarjan-free colouring).

    O(V + E). Works for the functional prerequisite graph (one edge per op)
    and the order-dependency DAG alike. Self-loops count as cycles.
    """
    white, grey, black = 0, 1, 2
    colour: dict[str, int] = defaultdict(int)
    on_cycle: set[str] = set()
    for root in sorted(successors):
        if colour[root] != white:
            continue
        path: list[str] = []
        stack: list[tuple[str, Iterator[str]]] = [(root, iter(sorted(successors.get(root, ()))))]
        colour[root] = grey
        path.append(root)
        while stack:
            node, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                colour[node] = black
                stack.pop()
                path.pop()
                continue
            if colour[nxt] == grey:
                # back edge: everything from nxt to the top of the path is on the cycle
                on_cycle.update(path[path.index(nxt) :])
            elif colour[nxt] == white:
                colour[nxt] = grey
                path.append(nxt)
                stack.append((nxt, iter(sorted(successors.get(nxt, ())))))
    return on_cycle


class InvalidRoutingRule:
    """Routing graph validation for open orders (also emits MISSING_OPERATIONS)."""

    key = "invalid_routing"
    entity = ENTITY_ORDER

    def run(
        self, snapshot: PlanningSnapshot, config: DataQualityConfig, ctx: DataQualityContext
    ) -> Iterable[DataQualityIssue]:
        for order in ctx.open_orders:
            ops = ctx.operations_for(order.order_id)
            if not ops:
                yield ctx.order_issue(
                    order,
                    DataQualityCode.MISSING_OPERATIONS,
                    f"Order {order.order_id} is {order.order_status.value} with {order.pending_quantity:g} "
                    f"units pending but has no routing steps",
                    field_name="operations",
                    recommendation="Create the routing (operation list) for the part in the ERP"
                    + (
                        f"; the order's manufacturing route says: "
                        f"{' -> '.join(p.value for p in order.manufacturing_route)}."
                        if order.manufacturing_route
                        else "."
                    ),
                    details={
                        "order_status": order.order_status.value,
                        "pending_quantity": order.pending_quantity,
                    },
                )
                continue
            yield from self._check_sequences(order, ops, ctx)
            yield from self._check_prerequisites(order, ops, ctx)
            yield from self._check_route(order, ops, ctx)
        yield from self._check_prerequisite_cycles(ctx)
        yield from self._check_order_dependency_cycles(ctx)

    @staticmethod
    def _check_sequences(
        order: Order, ops: list[Operation], ctx: DataQualityContext
    ) -> Iterable[DataQualityIssue]:
        by_seq: dict[int, list[str]] = defaultdict(list)
        for op in ops:
            by_seq[op.sequence].append(op.operation_id)
            if op.sequence < 0:
                yield ctx.operation_issue(
                    op,
                    DataQualityCode.INVALID_ROUTING,
                    f"Operation {op.operation_id} of order {order.order_id} has a negative sequence "
                    f"({op.sequence})",
                    field_name="sequence",
                    recommendation="Renumber the routing steps in the ERP (e.g. 10, 20, 30).",
                    details={"problem": "negative_sequence"},
                    severity=DataQualitySeverity.WARNING,
                )
        for seq in sorted(by_seq):
            ids = by_seq[seq]
            if len(ids) > 1:
                yield ctx.order_issue(
                    order,
                    DataQualityCode.INVALID_ROUTING,
                    f"Order {order.order_id} has {len(ids)} routing steps with sequence {seq} "
                    f"({fmt_ids(ids)}); their order is ambiguous",
                    field_name="operations.sequence",
                    recommendation="Give every routing step a distinct sequence number in the ERP.",
                    details={"sequence": seq, "operation_ids": sorted(ids), "problem": "duplicate_sequence"},
                )

    @staticmethod
    def _check_prerequisites(
        order: Order, ops: list[Operation], ctx: DataQualityContext
    ) -> Iterable[DataQualityIssue]:
        all_ops = ctx.snapshot.operations
        for op in ops:
            pre_id = op.prerequisite_operation_id
            if is_blank(pre_id):
                continue
            pre = all_ops.get(str(pre_id))
            if pre_id == op.operation_id:
                problem, text = "self_prerequisite", "depends on itself"
            elif pre is None:
                problem, text = "unknown_prerequisite", f"depends on unknown operation {pre_id}"
            elif pre.order_id != op.order_id:
                problem, text = (
                    "cross_order_prerequisite",
                    f"depends on operation {pre_id} of another order ({pre.order_id})",
                )
            elif pre.sequence >= op.sequence:
                problem, text = (
                    "prerequisite_not_earlier",
                    f"depends on operation {pre_id} (sequence {pre.sequence}) which is not earlier in the "
                    f"route than sequence {op.sequence}",
                )
            else:
                continue
            yield ctx.operation_issue(
                op,
                DataQualityCode.INVALID_ROUTING,
                f"Operation {op.operation_id} of order {order.order_id} {text}",
                field_name="prerequisite_operation_id",
                recommendation="Fix the predecessor link on the routing step in the ERP; a step may only "
                "depend on an earlier step of the same order.",
                details={"prerequisite_operation_id": pre_id, "problem": problem},
            )

    @staticmethod
    def _check_route(
        order: Order, ops: list[Operation], ctx: DataQualityContext
    ) -> Iterable[DataQualityIssue]:
        if not order.manufacturing_route:
            return
        actual = [op.operation_type for op in ops]
        if actual != list(order.manufacturing_route):
            yield ctx.order_issue(
                order,
                DataQualityCode.INVALID_ROUTING,
                f"Order {order.order_id} manufacturing route "
                f"({' -> '.join(p.value for p in order.manufacturing_route)}) does not match its routing "
                f"steps ({' -> '.join(p.value for p in actual)})",
                field_name="manufacturing_route",
                recommendation="Align the order's manufacturing route with the routing steps in the ERP; "
                "the routing steps are used for scheduling.",
                details={
                    "manufacturing_route": [p.value for p in order.manufacturing_route],
                    "operation_types": [p.value for p in actual],
                    "problem": "route_mismatch",
                },
                severity=DataQualitySeverity.WARNING,
            )

    @staticmethod
    def _check_prerequisite_cycles(ctx: DataQualityContext) -> Iterable[DataQualityIssue]:
        graph: dict[str, set[str]] = {}
        for order in ctx.open_orders:
            for op in ctx.operations_for(order.order_id):
                pre = op.prerequisite_operation_id
                if not is_blank(pre) and pre != op.operation_id and str(pre) in ctx.snapshot.operations:
                    graph[op.operation_id] = {str(pre)}
        for op_id in sorted(find_cycle_members(graph)):
            op = ctx.snapshot.operations[op_id]
            yield ctx.operation_issue(
                op,
                DataQualityCode.INVALID_ROUTING,
                f"Operation {op.operation_id} of order {op.order_id} is part of a prerequisite cycle",
                field_name="prerequisite_operation_id",
                recommendation="Break the circular predecessor links on the routing in the ERP.",
                details={
                    "prerequisite_operation_id": op.prerequisite_operation_id,
                    "problem": "prerequisite_cycle",
                },
            )

    @staticmethod
    def _check_order_dependency_cycles(ctx: DataQualityContext) -> Iterable[DataQualityIssue]:
        graph: dict[str, set[str]] = {}
        for order in ctx.open_orders:
            if order.depends_on_order_ids:
                graph[order.order_id] = {d for d in order.depends_on_order_ids if d in ctx.snapshot.orders}
        for order_id in sorted(find_cycle_members(graph)):
            order = ctx.snapshot.orders[order_id]
            yield ctx.order_issue(
                order,
                DataQualityCode.INVALID_ROUTING,
                f"Order {order.order_id} is part of an order dependency cycle "
                f"(depends on {fmt_ids(order.depends_on_order_ids)})",
                field_name="depends_on_order_ids",
                recommendation="Break the circular order dependency in the ERP (an assembly cannot depend "
                "on its own component chain).",
                details={
                    "depends_on_order_ids": sorted(order.depends_on_order_ids),
                    "problem": "dependency_cycle",
                },
            )


class UnknownReferenceRule:
    """Ids on open orders / operations / master data that resolve to nothing."""

    key = "unknown_reference"
    entity = ENTITY_ORDER

    def run(
        self, snapshot: PlanningSnapshot, config: DataQualityConfig, ctx: DataQualityContext
    ) -> Iterable[DataQualityIssue]:
        for order in ctx.open_orders:
            yield from self._check_order(order, ctx)
            for op in ctx.operations_for(order.order_id):
                if not op.is_done:
                    yield from self._check_operation(op, ctx)
        for op_id in sorted(snapshot.operations):
            op = snapshot.operations[op_id]
            if op.order_id not in snapshot.orders:
                yield ctx.operation_issue(
                    op,
                    DataQualityCode.UNKNOWN_REFERENCE,
                    f"Operation {op.operation_id} belongs to unknown order {op.order_id}",
                    field_name="order_id",
                    recommendation="Include the parent order in the ERP export or delete the orphan "
                    "routing step.",
                    details={"referenced_id": op.order_id, "ref_kind": "order"},
                )
        yield from self._check_master_data(ctx)

    @staticmethod
    def _unknown(
        ctx: DataQualityContext,
        entity_type: str,
        entity_id: str,
        field_name: str,
        ref_kind: str,
        value: str,
        registry: str,
        order_id: str | None = None,
    ) -> DataQualityIssue:
        details = {"referenced_id": value, "ref_kind": ref_kind}
        if order_id is not None:
            details["order_id"] = order_id
        return ctx.issue(
            DataQualityCode.UNKNOWN_REFERENCE,
            entity_type,
            entity_id,
            f"{entity_type.capitalize()} {entity_id}.{field_name} references {ref_kind} {value!r} "
            f"which is not in the snapshot",
            field_name=field_name,
            recommendation=f"Include the {ref_kind} in the ERP {registry} export or correct the id "
            f"on the record.",
            details=details,
        )

    def _check_order(self, order: Order, ctx: DataQualityContext) -> Iterable[DataQualityIssue]:
        s = ctx.snapshot
        oid = order.order_id
        if not is_blank(order.required_material_id) and order.required_material_id not in s.materials:
            yield self._unknown(
                ctx,
                ENTITY_ORDER,
                oid,
                "required_material_id",
                "material",
                str(order.required_material_id),
                "material master",
                oid,
            )
        for tid in sorted(order.tooling_requirement):
            if tid not in s.tooling:
                yield self._unknown(
                    ctx, ENTITY_ORDER, oid, "tooling_requirement", "tooling", tid, "tooling master", oid
                )
        if not is_blank(order.required_machine_id) and order.required_machine_id not in s.machines:
            yield self._unknown(
                ctx,
                ENTITY_ORDER,
                oid,
                "required_machine_id",
                "machine",
                str(order.required_machine_id),
                "machine master",
                oid,
            )
        if not is_blank(order.machine_group) and str(order.machine_group) not in ctx.machines_by_group:
            yield self._unknown(
                ctx,
                ENTITY_ORDER,
                oid,
                "machine_group",
                "machine group",
                str(order.machine_group),
                "machine master",
                oid,
            )
        for dep in sorted(order.depends_on_order_ids):
            if dep not in s.orders:
                yield self._unknown(
                    ctx, ENTITY_ORDER, oid, "depends_on_order_ids", "order", dep, "order", oid
                )

    def _check_operation(self, op: Operation, ctx: DataQualityContext) -> Iterable[DataQualityIssue]:
        s = ctx.snapshot
        eid, oid = op.operation_id, op.order_id
        if not is_blank(op.material_id) and op.material_id not in s.materials:
            yield self._unknown(
                ctx,
                ENTITY_OPERATION,
                eid,
                "material_id",
                "material",
                str(op.material_id),
                "material master",
                oid,
            )
        for tid in sorted(op.tooling_ids):
            if tid not in s.tooling:
                yield self._unknown(
                    ctx, ENTITY_OPERATION, eid, "tooling_ids", "tooling", tid, "tooling master", oid
                )
        if not is_blank(op.machine_id) and op.machine_id not in s.machines:
            yield self._unknown(
                ctx, ENTITY_OPERATION, eid, "machine_id", "machine", str(op.machine_id), "machine master", oid
            )
        for mid in sorted(op.eligible_machine_ids):
            if mid not in s.machines:
                yield self._unknown(
                    ctx, ENTITY_OPERATION, eid, "eligible_machine_ids", "machine", mid, "machine master", oid
                )
        for mid in sorted(op.machine_cycle_minutes):
            if mid not in s.machines:
                yield self._unknown(
                    ctx, ENTITY_OPERATION, eid, "machine_cycle_minutes", "machine", mid, "machine master", oid
                )
        if not is_blank(op.machine_group) and str(op.machine_group) not in ctx.machines_by_group:
            yield self._unknown(
                ctx,
                ENTITY_OPERATION,
                eid,
                "machine_group",
                "machine group",
                str(op.machine_group),
                "machine master",
                oid,
            )

    def _check_master_data(self, ctx: DataQualityContext) -> Iterable[DataQualityIssue]:
        s = ctx.snapshot
        if s.default_calendar_id is not None and s.default_calendar_id not in s.calendars:
            yield self._unknown(
                ctx,
                ENTITY_SNAPSHOT,
                s.snapshot_id or "snapshot",
                "default_calendar_id",
                "calendar",
                s.default_calendar_id,
                "calendar",
            )
        for mid in sorted(s.machines):
            machine = s.machines[mid]
            if machine.calendar_id is not None and machine.calendar_id not in s.calendars:
                yield self._unknown(
                    ctx, ENTITY_MACHINE, mid, "calendar_id", "calendar", machine.calendar_id, "calendar"
                )
        for cid in sorted(s.customer_rules):
            if cid not in s.customers:
                yield self._unknown(
                    ctx, ENTITY_CUSTOMER_RULE, cid, "customer_id", "customer", cid, "customer master"
                )
        for lock in sorted(s.locks, key=lambda x: x.lock_id):
            if lock.order_id is not None and lock.order_id not in s.orders:
                yield self._unknown(
                    ctx, ENTITY_LOCK, lock.lock_id, "order_id", "order", lock.order_id, "order"
                )
            if lock.machine_id is not None and lock.machine_id not in s.machines:
                yield self._unknown(
                    ctx, ENTITY_LOCK, lock.lock_id, "machine_id", "machine", lock.machine_id, "machine master"
                )
        for ov in sorted(s.overrides, key=lambda x: x.override_id):
            if ov.order_id not in s.orders:
                yield self._unknown(
                    ctx, ENTITY_OVERRIDE, ov.override_id, "order_id", "order", ov.order_id, "order"
                )
        for ex in sorted(s.expedites, key=lambda x: x.expedite_id):
            if ex.order_id not in s.orders:
                yield self._unknown(
                    ctx, ENTITY_EXPEDITE, ex.expedite_id, "order_id", "order", ex.order_id, "order"
                )


__all__ = ["InvalidRoutingRule", "UnknownReferenceRule", "find_cycle_members"]
