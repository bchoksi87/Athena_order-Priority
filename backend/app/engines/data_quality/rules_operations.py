"""Operation-level data quality rules (spec Phase 21).

All rules here look at the *pending* operations of *open* orders only: a
completed step with no cycle time is irrelevant to the schedule, and reporting
it would bury the real problems. Each rule resolves order-level fallbacks
(``Order.estimated_*``, ``Order.machine_group`` ...) exactly the way the
scheduling engines do, so a value is only reported missing when the scheduler
would really have nothing to work with. Order-level machine references
(``required_machine_id``, ``machine_group``) describe the order's primary
process and are therefore only applied to operations of that process type
(:func:`~app.engines.constraints.hard.is_primary_operation`).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.domain.config import DataQualityConfig
from app.domain.enums import DataQualityCode, DataQualitySeverity, ProcessType
from app.domain.models import Machine, Operation, Order
from app.domain.results import DataQualityIssue
from app.domain.snapshot import PlanningSnapshot
from app.engines.constraints.hard import is_primary_operation
from app.engines.data_quality.base import (
    ENTITY_OPERATION,
    DataQualityContext,
    fmt_ids,
    is_blank,
)

#: Processes that consume stock material as an input. Post-processing steps
#: (deburring, inspection, packing ...) work on the part produced upstream and
#: therefore need no material reference. Overridable per rule instance.
MATERIAL_CONSUMING_PROCESSES: frozenset[ProcessType] = frozenset(
    {ProcessType.CNC_MACHINING, ProcessType.ADDITIVE_3D_PRINTING}
)

#: Sanity ceiling for a single setup. ``DataQualityConfig`` has no field for
#: it (contract gap, see report); overridable through the rule constructor.
DEFAULT_MAX_SETUP_MINUTES: float = 24 * 60.0

_MINUTES_PER_DAY = 24 * 60.0


def _pending_ops(ctx: DataQualityContext) -> Iterable[tuple[Order, Operation]]:
    for order in ctx.open_orders:
        for op in ctx.operations_for(order.order_id):
            if not op.is_done:
                yield order, op


class MissingCycleTimeRule:
    """No cycle time on the step, no per-machine override and no order estimate."""

    key = "missing_cycle_time"
    entity = ENTITY_OPERATION

    def run(
        self, snapshot: PlanningSnapshot, config: DataQualityConfig, ctx: DataQualityContext
    ) -> Iterable[DataQualityIssue]:
        for order, op in _pending_ops(ctx):
            if (
                op.cycle_minutes_per_unit is None
                and not op.machine_cycle_minutes
                and order.estimated_cycle_minutes_per_unit is None
            ):
                yield ctx.operation_issue(
                    op,
                    DataQualityCode.MISSING_CYCLE_TIME,
                    f"Operation {op.operation_id} ({op.operation_type.value}) of order {order.order_id} has "
                    f"no cycle time per unit",
                    field_name="cycle_minutes_per_unit",
                    recommendation="Enter the standard cycle time on the routing step (or a per-machine "
                    "cycle time, or an estimated cycle time on the order) in the ERP; the run duration "
                    "cannot be computed without it.",
                    details={"operation_type": op.operation_type.value},
                )


class MissingSetupTimeRule:
    """No setup time on the step and no order-level estimate (scheduler uses a default)."""

    key = "missing_setup_time"
    entity = ENTITY_OPERATION

    def run(
        self, snapshot: PlanningSnapshot, config: DataQualityConfig, ctx: DataQualityContext
    ) -> Iterable[DataQualityIssue]:
        for order, op in _pending_ops(ctx):
            if op.setup_minutes is None and order.estimated_setup_minutes is None:
                yield ctx.operation_issue(
                    op,
                    DataQualityCode.MISSING_SETUP_TIME,
                    f"Operation {op.operation_id} ({op.operation_type.value}) of order {order.order_id} has "
                    f"no setup time",
                    field_name="setup_minutes",
                    recommendation="Enter the setup/changeover time on the routing step in the ERP; the "
                    "scheduler falls back to the configured default setup time until then.",
                    details={"operation_type": op.operation_type.value},
                )


@dataclass(slots=True)
class _MachineRefs:
    """Machine information found on an operation and its order."""

    referenced: list[tuple[str, str]]  # (field_name, id or group)
    resolved: list[Machine]


def collect_machine_refs(op: Operation, order: Order, ctx: DataQualityContext) -> _MachineRefs:
    """Every machine reference on the step/order and the machines they resolve to.

    Precedence mirrors the constraint engine: an explicit machine, then the
    explicit eligible list, then the machine group, then the order-level
    equivalents. Resolved machines are deduplicated in first-seen order.
    """
    machines = ctx.snapshot.machines
    referenced: list[tuple[str, str]] = []
    resolved: list[Machine] = []
    seen: set[str] = set()

    def add(machine: Machine) -> None:
        if machine.machine_id not in seen:
            seen.add(machine.machine_id)
            resolved.append(machine)

    if not is_blank(op.machine_id):
        referenced.append(("machine_id", str(op.machine_id)))
        m = machines.get(str(op.machine_id))
        if m is not None:
            add(m)
    for mid in sorted(op.eligible_machine_ids):
        referenced.append(("eligible_machine_ids", mid))
        m = machines.get(mid)
        if m is not None:
            add(m)
    if not is_blank(op.machine_group):
        referenced.append(("machine_group", str(op.machine_group)))
        for m in ctx.machines_by_group.get(str(op.machine_group), ()):
            add(m)
    primary = is_primary_operation(op, order)
    if primary and not is_blank(order.required_machine_id):
        referenced.append(("order.required_machine_id", str(order.required_machine_id)))
        m = machines.get(str(order.required_machine_id))
        if m is not None:
            add(m)
    if primary and not is_blank(order.machine_group):
        referenced.append(("order.machine_group", str(order.machine_group)))
        for m in ctx.machines_by_group.get(str(order.machine_group), ()):
            add(m)
    return _MachineRefs(referenced=referenced, resolved=resolved)


class MissingMachineAssignmentRule:
    """No machine information at all, or only references that resolve to nothing."""

    key = "missing_machine_assignment"
    entity = ENTITY_OPERATION

    def run(
        self, snapshot: PlanningSnapshot, config: DataQualityConfig, ctx: DataQualityContext
    ) -> Iterable[DataQualityIssue]:
        for order, op in _pending_ops(ctx):
            refs = collect_machine_refs(op, order, ctx)
            if refs.resolved:
                continue
            capable = [m.machine_id for m in ctx.machines_supporting(op.operation_type)]
            hint = (
                f" Machines supporting {op.operation_type.value}: {fmt_ids(capable)}."
                if capable
                else f" No machine in the snapshot supports {op.operation_type.value}."
            )
            if not refs.referenced:
                message = (
                    f"Operation {op.operation_id} ({op.operation_type.value}) of order {order.order_id} "
                    f"has no machine, machine group or eligible machine list"
                )
                field_name = "machine_group"
            else:
                listed = ", ".join(f"{f}={v}" for f, v in refs.referenced)
                message = (
                    f"Operation {op.operation_id} ({op.operation_type.value}) of order {order.order_id} "
                    f"references only unknown machines/groups ({listed})"
                )
                field_name = refs.referenced[0][0]
            yield ctx.operation_issue(
                op,
                DataQualityCode.MISSING_MACHINE_ASSIGNMENT,
                message,
                field_name=field_name,
                recommendation="Assign a work centre / machine group (or an eligible machine list) to the "
                "routing step in the ERP." + hint,
                details={
                    "operation_type": op.operation_type.value,
                    "referenced": [f"{f}={v}" for f, v in refs.referenced],
                    "capable_machine_ids": capable,
                },
            )


class MissingMaterialRule:
    """Material-consuming steps without any material reference."""

    key = "missing_material"
    entity = ENTITY_OPERATION

    def __init__(self, material_processes: frozenset[ProcessType] = MATERIAL_CONSUMING_PROCESSES) -> None:
        self.material_processes = material_processes

    def run(
        self, snapshot: PlanningSnapshot, config: DataQualityConfig, ctx: DataQualityContext
    ) -> Iterable[DataQualityIssue]:
        for order, op in _pending_ops(ctx):
            needs_material = op.operation_type in self.material_processes or (
                op.material_quantity_per_unit is not None and op.material_quantity_per_unit > 0
            )
            if not needs_material or ctx.resolve_material_id(op, order) is not None:
                continue
            yield ctx.operation_issue(
                op,
                DataQualityCode.MISSING_MATERIAL,
                f"Operation {op.operation_id} ({op.operation_type.value}) of order {order.order_id} has no "
                f"material assigned",
                field_name="material_id",
                recommendation="Add the raw material / powder to the bill of materials of the routing step "
                "(or the order) in the ERP; material availability cannot be checked without it.",
                details={"operation_type": op.operation_type.value},
            )


class ImpossibleProductionTimeRule:
    """Cycle, setup or total durations outside the configured plausibility limits.

    A cycle above ``max_cycle_minutes_per_unit`` or a negative value is
    treated as blocking: it is almost always a unit error (seconds entered as
    minutes) and scheduling it would swamp a machine. A zero cycle and an
    over-long setup are warnings. The order total is only checked when none of
    its steps was already flagged, to avoid reporting one root cause twice.
    """

    key = "impossible_production_time"
    entity = ENTITY_OPERATION

    def __init__(self, max_setup_minutes: float = DEFAULT_MAX_SETUP_MINUTES) -> None:
        self.max_setup_minutes = max_setup_minutes

    def run(
        self, snapshot: PlanningSnapshot, config: DataQualityConfig, ctx: DataQualityContext
    ) -> Iterable[DataQualityIssue]:
        max_cycle = config.max_cycle_minutes_per_unit
        max_total = config.max_total_production_days * _MINUTES_PER_DAY
        for order in ctx.open_orders:
            total = 0.0
            flagged = False
            for op in ctx.operations_for(order.order_id):
                if op.is_done:
                    continue
                for issue in self._check_operation(op, order, ctx, max_cycle):
                    flagged = True
                    yield issue
                cycle = ctx.resolve_cycle_minutes(op, order)
                setup = op.setup_minutes if op.setup_minutes is not None else order.estimated_setup_minutes
                total += max(0.0, setup or 0.0) + max(0.0, cycle or 0.0) * op.pending_quantity
            yield from self._check_order(order, ctx, max_cycle, max_total, total, flagged)

    def _check_operation(
        self, op: Operation, order: Order, ctx: DataQualityContext, max_cycle: float
    ) -> Iterable[DataQualityIssue]:
        cycles: list[tuple[str, float]] = []
        if op.cycle_minutes_per_unit is not None:
            cycles.append(("cycle_minutes_per_unit", op.cycle_minutes_per_unit))
        for machine_id in sorted(op.machine_cycle_minutes):
            cycles.append((f"machine_cycle_minutes[{machine_id}]", op.machine_cycle_minutes[machine_id]))
        for field_name, value in cycles:
            if value < 0:
                yield ctx.operation_issue(
                    op,
                    DataQualityCode.IMPOSSIBLE_PRODUCTION_TIME,
                    f"Operation {op.operation_id} of order {order.order_id} has a negative cycle time "
                    f"({value:g} min/unit)",
                    field_name=field_name,
                    recommendation="Correct the cycle time on the routing step in the ERP.",
                    details={"value": value, "problem": "negative_cycle"},
                )
            elif value > max_cycle:
                yield ctx.operation_issue(
                    op,
                    DataQualityCode.IMPOSSIBLE_PRODUCTION_TIME,
                    f"Operation {op.operation_id} of order {order.order_id} has a cycle time of {value:g} "
                    f"min/unit, above the plausible maximum of {max_cycle:g}",
                    field_name=field_name,
                    recommendation="Check the unit of the cycle time in the ERP (seconds vs minutes, "
                    "per batch vs per unit).",
                    details={
                        "value": value,
                        "max_cycle_minutes_per_unit": max_cycle,
                        "problem": "cycle_too_long",
                    },
                )
            elif value == 0:
                yield ctx.operation_issue(
                    op,
                    DataQualityCode.IMPOSSIBLE_PRODUCTION_TIME,
                    f"Operation {op.operation_id} of order {order.order_id} has a cycle time of 0",
                    field_name=field_name,
                    recommendation="Enter the real cycle time on the routing step in the ERP; a zero "
                    "duration schedules the step as instantaneous.",
                    details={"value": 0.0, "problem": "zero_cycle"},
                    severity=DataQualitySeverity.WARNING,
                )
        if op.setup_minutes is not None:
            if op.setup_minutes < 0:
                yield ctx.operation_issue(
                    op,
                    DataQualityCode.IMPOSSIBLE_PRODUCTION_TIME,
                    f"Operation {op.operation_id} of order {order.order_id} has a negative setup time "
                    f"({op.setup_minutes:g} min)",
                    field_name="setup_minutes",
                    recommendation="Correct the setup time on the routing step in the ERP.",
                    details={"value": op.setup_minutes, "problem": "negative_setup"},
                )
            elif op.setup_minutes > self.max_setup_minutes:
                yield ctx.operation_issue(
                    op,
                    DataQualityCode.IMPOSSIBLE_PRODUCTION_TIME,
                    f"Operation {op.operation_id} of order {order.order_id} has a setup time of "
                    f"{op.setup_minutes:g} min, above the plausible maximum of {self.max_setup_minutes:g}",
                    field_name="setup_minutes",
                    recommendation="Check the unit of the setup time in the ERP (seconds vs minutes).",
                    details={
                        "value": op.setup_minutes,
                        "max_setup_minutes": self.max_setup_minutes,
                        "problem": "setup_too_long",
                    },
                    severity=DataQualitySeverity.WARNING,
                )

    def _check_order(
        self,
        order: Order,
        ctx: DataQualityContext,
        max_cycle: float,
        max_total: float,
        computed_total: float,
        flagged: bool,
    ) -> Iterable[DataQualityIssue]:
        est = order.estimated_cycle_minutes_per_unit
        if est is not None and (est < 0 or est > max_cycle):
            yield ctx.order_issue(
                order,
                DataQualityCode.IMPOSSIBLE_PRODUCTION_TIME,
                f"Order {order.order_id} estimated cycle time {est:g} min/unit is outside 0..{max_cycle:g}",
                field_name="estimated_cycle_minutes_per_unit",
                recommendation="Check the unit of the estimated cycle time on the order in the ERP.",
                details={
                    "value": est,
                    "max_cycle_minutes_per_unit": max_cycle,
                    "problem": "cycle_out_of_range",
                },
            )
            flagged = True
        if not flagged and computed_total > max_total:
            days = ctx.config.max_total_production_days
            yield ctx.order_issue(
                order,
                DataQualityCode.IMPOSSIBLE_PRODUCTION_TIME,
                f"Order {order.order_id} needs {computed_total / _MINUTES_PER_DAY:.1f} days of production "
                f"for its pending quantity, above the plausible maximum of {days:g} days",
                field_name="estimated_total_production_minutes",
                recommendation="Check quantity and cycle times on the routing in the ERP; split the order "
                "if the volume is genuine.",
                details={
                    "computed_total_minutes": computed_total,
                    "max_total_production_days": days,
                    "pending_quantity": order.pending_quantity,
                    "problem": "total_too_long",
                },
            )


class ConflictingMachineCapabilityRule:
    """Explicitly referenced machines that cannot run the step.

    A conflict is a machine that does not support the process, or whose
    declared material compatibility excludes the step's material, or that is
    excluded by the tooling's compatibility list. Severity is blocking when
    *every* resolvable candidate conflicts (nowhere to run), warning otherwise.
    A machine group with no capable member is reported as a warning too.
    """

    key = "conflicting_machine_capability"
    entity = ENTITY_OPERATION

    def run(
        self, snapshot: PlanningSnapshot, config: DataQualityConfig, ctx: DataQualityContext
    ) -> Iterable[DataQualityIssue]:
        for order, op in _pending_ops(ctx):
            material_id = ctx.resolve_material_id(op, order)
            candidates = self._explicit_candidates(op, order, ctx)
            conflicts: list[tuple[str, Machine, list[str]]] = []
            for field_name, machine in candidates:
                reasons = self._conflicts(op, machine, material_id, ctx)
                if reasons:
                    conflicts.append((field_name, machine, reasons))
            all_conflict = bool(candidates) and len(conflicts) == len(candidates)
            severity = None if all_conflict else DataQualitySeverity.WARNING
            for field_name, machine, reasons in conflicts:
                yield ctx.operation_issue(
                    op,
                    DataQualityCode.CONFLICTING_MACHINE_CAPABILITY,
                    f"Operation {op.operation_id} of order {order.order_id} is assigned to machine "
                    f"{machine.machine_id} which cannot run it: {'; '.join(reasons)}",
                    field_name=field_name,
                    recommendation="Correct the machine assignment on the routing step, or extend the "
                    "machine's capability record (process / material compatibility) in the ERP.",
                    details={
                        "machine_id": machine.machine_id,
                        "reasons": reasons,
                        "no_alternative": all_conflict,
                    },
                    severity=severity,
                )
            yield from self._check_groups(op, order, ctx)

    @staticmethod
    def _explicit_candidates(
        op: Operation, order: Order, ctx: DataQualityContext
    ) -> list[tuple[str, Machine]]:
        machines = ctx.snapshot.machines
        out: list[tuple[str, Machine]] = []
        if not is_blank(op.machine_id) and (m := machines.get(str(op.machine_id))) is not None:
            out.append(("machine_id", m))
        for mid in sorted(op.eligible_machine_ids):
            if (m := machines.get(mid)) is not None:
                out.append(("eligible_machine_ids", m))
        if (
            is_blank(op.machine_id)
            and is_primary_operation(op, order)
            and not is_blank(order.required_machine_id)
            and (m := machines.get(str(order.required_machine_id))) is not None
        ):
            out.append(("order.required_machine_id", m))
        return out

    @staticmethod
    def _conflicts(
        op: Operation, machine: Machine, material_id: str | None, ctx: DataQualityContext
    ) -> list[str]:
        reasons: list[str] = []
        if not machine.supports_process(op.operation_type):
            reasons.append(f"it runs {machine.process_type.value}, the step needs {op.operation_type.value}")
        if (
            material_id is not None
            and machine.compatible_materials
            and material_id not in machine.compatible_materials
        ):
            reasons.append(f"material {material_id} is not in its compatible materials")
        for tooling_id in sorted(op.tooling_ids):
            tool = ctx.snapshot.tooling.get(tooling_id)
            if (
                tool is not None
                and tool.compatible_machine_ids
                and machine.machine_id not in tool.compatible_machine_ids
            ):
                reasons.append(f"tooling {tooling_id} is not compatible with it")
        return reasons

    @staticmethod
    def _check_groups(op: Operation, order: Order, ctx: DataQualityContext) -> Iterable[DataQualityIssue]:
        for field_name, group in (
            ("machine_group", op.machine_group),
            ("order.machine_group", order.machine_group if is_primary_operation(op, order) else None),
        ):
            if is_blank(group):
                continue
            members = ctx.machines_by_group.get(str(group))
            if members and not any(m.supports_process(op.operation_type) for m in members):
                yield ctx.operation_issue(
                    op,
                    DataQualityCode.CONFLICTING_MACHINE_CAPABILITY,
                    f"Operation {op.operation_id} of order {order.order_id} is routed to machine group "
                    f"{group} but none of its {len(members)} machines supports {op.operation_type.value}",
                    field_name=field_name,
                    recommendation="Route the step to a work centre that runs this process, or correct the "
                    "process type of the step in the ERP.",
                    details={"machine_group": group, "operation_type": op.operation_type.value},
                    severity=DataQualitySeverity.WARNING,
                )


__all__ = [
    "DEFAULT_MAX_SETUP_MINUTES",
    "MATERIAL_CONSUMING_PROCESSES",
    "ConflictingMachineCapabilityRule",
    "ImpossibleProductionTimeRule",
    "MissingCycleTimeRule",
    "MissingMachineAssignmentRule",
    "MissingMaterialRule",
    "MissingSetupTimeRule",
    "collect_machine_refs",
]
