"""Soft constraints: preferences expressed as a cost in minute-equivalents (§6.2).

A :class:`~app.domain.results.Penalty` cost is comparable to time so the
scheduler can trade it against lateness ("this machine finishes 40 min
earlier but costs 30 min of preference penalty"). Negative costs are bonuses
(e.g. keeping a customer's jobs together). Every number comes from
:class:`~app.domain.config.SchedulingConfig`; a constraint returns ``None``
when it has nothing to say (cost zero or data unknown) so penalty lists stay
readable in explanations.

Setup estimation lives here (:func:`estimate_setup`) because both the
changeover penalty and the batching bonus must agree on what a changeover
costs; the scheduler is expected to reuse it so numbers and explanations
never diverge.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.domain.config import SchedulingConfig
from app.domain.models import Machine, Operation, Order
from app.domain.results import Penalty
from app.engines.constraints.base import ConstraintContext, MachineState, SoftConstraint
from app.engines.constraints.hard import required_tooling_ids

SetupBasis = Literal["same_family", "same_material", "changeover", "unknown"]
SetupSource = Literal["operation", "order", "default"]


@dataclass(slots=True, frozen=True)
class SetupEstimate:
    """Setup minutes needed before ``op`` can run on a machine in a given state."""

    minutes: float
    base_minutes: float
    factor: float
    basis: SetupBasis
    tooling_minutes: float
    reason: str
    base_source: SetupSource

    def to_details(self) -> dict[str, Any]:
        return {
            "setup_minutes": self.minutes,
            "base_setup_minutes": self.base_minutes,
            "setup_factor": self.factor,
            "basis": self.basis,
            "tooling_setup_minutes": self.tooling_minutes,
            "base_source": self.base_source,
        }


def _order_of(op: Operation, ctx: ConstraintContext) -> Order | None:
    if ctx.order is not None and ctx.order.order_id == op.order_id:
        return ctx.order
    return ctx.snapshot.orders.get(op.order_id)


def _effective_state(machine: Machine, state: MachineState | None) -> tuple[str | None, str | None, set[str]]:
    """(setup family, material, mounted tooling) from the scheduler state or the ERP snapshot."""
    if state is not None:
        return state.current_setup_family, state.current_material_id, set(state.mounted_tooling)
    return machine.current_setup_family, machine.current_material_id, set(machine.tooling_configuration)


def estimate_setup(
    op: Operation,
    order: Order | None,
    machine: Machine,
    state: MachineState | None,
    config: SchedulingConfig,
    ctx: ConstraintContext | None = None,
) -> SetupEstimate:
    """Changeover minutes for ``op`` on ``machine`` given what the machine last ran.

    ``base x factor + tooling``: the factor comes from ``config.setup`` (same
    setup family → ``same_family_setup_factor``; same material →
    ``same_material_setup_factor``; otherwise full setup). Tooling that is
    required but not mounted adds its own setup minutes.
    """
    base_source: SetupSource
    if op.setup_minutes is not None:
        base, base_source = float(op.setup_minutes), "operation"
    elif order is not None and order.estimated_setup_minutes is not None:
        base, base_source = float(order.estimated_setup_minutes), "order"
    else:
        base, base_source = float(config.setup.default_setup_minutes), "default"
    base = max(0.0, base)

    family, material, mounted = _effective_state(machine, state)
    op_material = op.material_id or (order.required_material_id if order is not None else None)
    basis: SetupBasis
    if op.setup_family is not None and family is not None and op.setup_family == family:
        factor, basis = config.setup.same_family_setup_factor, "same_family"
        reason = f"same setup family {op.setup_family!r}"
    elif op_material is not None and material is not None and op_material == material:
        factor, basis = config.setup.same_material_setup_factor, "same_material"
        reason = f"same material {op_material!r}"
    elif family is None and material is None:
        factor, basis, reason = 1.0, "unknown", "machine state unknown, full setup assumed"
    else:
        factor, basis = 1.0, "changeover"
        reason = f"changeover from family {family!r}/material {material!r}"

    tooling_minutes = 0.0
    if ctx is not None:
        for tooling_id in sorted(required_tooling_ids(op, order)):
            tool = ctx.snapshot.tooling.get(tooling_id)
            if tool is not None and tooling_id not in mounted:
                tooling_minutes += max(0.0, tool.setup_minutes)
    minutes = base * factor + tooling_minutes
    if tooling_minutes:
        reason += f"; +{tooling_minutes:g} min tooling setup"
    return SetupEstimate(minutes, base, factor, basis, tooling_minutes, reason, base_source)


class PreferredMachine:
    """Cost when the machine is not the ERP-preferred one (``op.machine_id``)."""

    key = "preferred_machine"

    def penalty(self, op: Operation, machine: Machine, ctx: ConstraintContext) -> Penalty | None:
        if op.machine_id is None:
            return None
        prefs = ctx.config.machine_preference
        if op.machine_id == machine.machine_id:
            cost, message = prefs.preferred_machine_cost, f"{machine.machine_id} is the ERP-preferred machine"
        else:
            cost = prefs.non_preferred_machine_cost_minutes
            message = f"{machine.machine_id} is not the ERP-preferred machine ({op.machine_id})"
        if cost == 0:
            return None
        return Penalty(self.key, cost, message, {"preferred_machine_id": op.machine_id})


class SetupChangeover:
    """Setup minutes x ``setup_penalty_cost_per_minute`` given the machine's current state."""

    key = "setup_changeover"

    def penalty(self, op: Operation, machine: Machine, ctx: ConstraintContext) -> Penalty | None:
        est = estimate_setup(op, _order_of(op, ctx), machine, ctx.machine_state, ctx.config, ctx)
        cost = est.minutes * ctx.config.setup.setup_penalty_cost_per_minute
        if cost == 0:
            return None
        return Penalty(
            self.key,
            cost,
            f"{est.minutes:g} min setup on {machine.machine_id}: {est.reason}",
            est.to_details(),
        )


class UtilizationBalance:
    """Cost per percent the machine's planned load sits above its group average."""

    key = "utilization_balance"

    def penalty(self, op: Operation, machine: Machine, ctx: ConstraintContext) -> Penalty | None:
        state, average = ctx.machine_state, ctx.group_average_load_minutes
        if state is None or average is None or average <= 0:
            return None
        pct_above = (state.scheduled_minutes - average) / average * 100.0
        if pct_above <= 0:
            return None
        cost = pct_above * ctx.config.machine_preference.utilization_balance_cost_per_pct
        if cost == 0:
            return None
        return Penalty(
            self.key,
            cost,
            f"{machine.machine_id} load {state.scheduled_minutes:g} min "
            f"is {pct_above:.0f}% above group average",
            {
                "scheduled_minutes": state.scheduled_minutes,
                "group_average_minutes": average,
                "pct_above": pct_above,
            },
        )


class EnergyCost:
    """``energy_cost_per_hour[machine_id]`` x run hours (config values are minute-equivalents)."""

    key = "energy_cost"

    def penalty(self, op: Operation, machine: Machine, ctx: ConstraintContext) -> Penalty | None:
        rate = ctx.config.machine_preference.energy_cost_per_hour.get(machine.machine_id)
        if not rate:
            return None
        run = op.run_minutes_on(machine)
        if run is None or run <= 0:
            return None
        cost = rate * run / 60.0
        return Penalty(
            self.key,
            cost,
            f"energy on {machine.machine_id}: {rate:g}/h x {run / 60.0:.1f} h",
            {"rate_per_hour": rate, "run_minutes": run},
        )


class CustomerSequencePreference:
    """Small bonus (negative cost) for keeping the same customer's jobs consecutive."""

    key = "customer_sequence"

    def __init__(self, bonus_minutes: float) -> None:
        self.bonus_minutes = bonus_minutes

    def penalty(self, op: Operation, machine: Machine, ctx: ConstraintContext) -> Penalty | None:
        state = ctx.machine_state
        order = _order_of(op, ctx)
        if self.bonus_minutes <= 0 or state is None or order is None or state.last_customer_id is None:
            return None
        if state.last_customer_id != order.customer_id:
            return None
        return Penalty(
            self.key,
            -self.bonus_minutes,
            f"follows another job of customer {order.customer_id} on {machine.machine_id}",
            {"customer_id": order.customer_id, "bonus_minutes": self.bonus_minutes},
        )


class BatchPreference:
    """Bonus per configured batching dimension the job shares with the machine's last job.

    Dimensions with a counterpart in :class:`MachineState` are evaluated:
    ``material`` (current material), ``part_family`` (last part family) and
    ``tool`` (all required tooling already mounted). ``customer`` is handled
    by :class:`CustomerSequencePreference`; other dimensions have no machine
    state to compare against and are ignored.
    """

    key = "batch_preference"

    def __init__(self, bonus_minutes_per_dimension: float) -> None:
        self.bonus_minutes_per_dimension = bonus_minutes_per_dimension

    def penalty(self, op: Operation, machine: Machine, ctx: ConstraintContext) -> Penalty | None:
        batching = ctx.config.batching
        state = ctx.machine_state
        if not batching.enabled or self.bonus_minutes_per_dimension <= 0 or state is None:
            return None
        order = _order_of(op, ctx)
        matched: list[str] = []
        for dimension in batching.dimensions:
            if dimension == "material":
                material = op.material_id or (order.required_material_id if order is not None else None)
                if material is not None and material == state.current_material_id:
                    matched.append("material")
            elif dimension == "part_family":
                if (
                    order is not None
                    and order.part_family is not None
                    and order.part_family == state.last_part_family
                ):
                    matched.append("part_family")
            elif dimension == "tool":
                tools = required_tooling_ids(op, order)
                if tools and tools <= state.mounted_tooling:
                    matched.append("tool")
        if not matched:
            return None
        cost = -self.bonus_minutes_per_dimension * len(matched)
        return Penalty(
            self.key,
            cost,
            f"shares {', '.join(matched)} with the previous job on {machine.machine_id}",
            {"matched_dimensions": matched, "bonus_minutes_per_dimension": self.bonus_minutes_per_dimension},
        )


def batch_bonus_minutes(config: SchedulingConfig) -> float:
    """Bonus (minute-equivalents) for keeping a batch together.

    ``SchedulingConfig`` has no dedicated batching-bonus field, so the bonus is
    derived from the setup rules: the changeover saved by a same-material
    follow-on job (``default_setup_minutes x same_material_setup_factor``)
    valued at ``setup_penalty_cost_per_minute``.
    """
    setup = config.setup
    return max(
        0.0,
        setup.default_setup_minutes * setup.same_material_setup_factor * setup.setup_penalty_cost_per_minute,
    )


def default_soft_constraints(config: SchedulingConfig) -> list[SoftConstraint]:
    """The shipped soft-constraint set, in explanation order."""
    bonus = batch_bonus_minutes(config)
    return [
        PreferredMachine(),
        SetupChangeover(),
        UtilizationBalance(),
        EnergyCost(),
        CustomerSequencePreference(bonus_minutes=bonus),
        BatchPreference(bonus_minutes_per_dimension=bonus),
    ]


__all__ = [
    "BatchPreference",
    "CustomerSequencePreference",
    "EnergyCost",
    "PreferredMachine",
    "SetupChangeover",
    "SetupEstimate",
    "UtilizationBalance",
    "batch_bonus_minutes",
    "default_soft_constraints",
    "estimate_setup",
]
