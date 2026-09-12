"""Setup (changeover) time for an operation on a machine in a given state.

The scheduler must never disagree with the constraint engine about what a
changeover costs, so this module is a thin wrapper around
:func:`app.engines.constraints.soft.estimate_setup` (the code path behind the
``setup_changeover`` soft penalty). Rules, all driven by ``config.setup``:

* same setup family as the machine's current state → ``base x same_family_setup_factor``
* same material                                   → ``base x same_material_setup_factor``
* otherwise                                       → full base setup
* ``op.setup_minutes`` missing → order-level estimate, else
  ``config.setup.default_setup_minutes`` (the reason says so, because the
  Data Quality Engine flags the gap and planners should see it)
* required tooling not mounted on the machine    → ``+ tooling.setup_minutes``
* an operation already *in progress* on this machine needs no setup at all.
"""

from __future__ import annotations

from datetime import datetime

from app.domain.config import SchedulingConfig
from app.domain.enums import OperationStatus
from app.domain.models import Machine, Operation, Order
from app.domain.snapshot import PlanningSnapshot
from app.engines.constraints.base import ConstraintContext, MachineState
from app.engines.constraints.soft import SetupEstimate, estimate_setup

#: Reason used when the operation is already running on the machine.
IN_PROGRESS_REASON = "operation already in progress on this machine; no setup"


def is_in_progress_on(op: Operation, machine_id: str) -> bool:
    """True when ``op`` is physically running on ``machine_id`` right now."""
    return op.operation_status == OperationStatus.IN_PROGRESS and op.machine_id == machine_id


def base_setup_minutes(op: Operation, order: Order | None, config: SchedulingConfig) -> float:
    """Full (state-independent) setup: operation value, else order estimate, else config default."""
    if op.setup_minutes is not None:
        return max(0.0, float(op.setup_minutes))
    if order is not None and order.estimated_setup_minutes is not None:
        return max(0.0, float(order.estimated_setup_minutes))
    return max(0.0, float(config.setup.default_setup_minutes))


def compute_setup(
    op: Operation,
    machine: Machine,
    state: MachineState,
    config: SchedulingConfig,
    *,
    order: Order | None = None,
    snapshot: PlanningSnapshot | None = None,
    at: datetime | None = None,
    ctx: ConstraintContext | None = None,
) -> SetupEstimate:
    """Full :class:`SetupEstimate` (minutes, basis, factor, tooling) for ``op`` on ``machine``.

    ``snapshot`` is needed to price unmounted tooling (``Tooling.setup_minutes``);
    without it tooling is ignored. ``at`` only feeds the constraint context and
    defaults to the machine's next free instant. A prepared ``ctx`` (whose
    ``machine_state`` must be ``state``) is used as is.
    """
    if is_in_progress_on(op, machine.machine_id):
        return SetupEstimate(0.0, 0.0, 0.0, "same_family", 0.0, IN_PROGRESS_REASON, "operation")
    if ctx is not None:
        return estimate_setup(op, order if order is not None else ctx.order, machine, state, config, ctx)
    if snapshot is not None:
        if order is None:
            order = snapshot.orders.get(op.order_id)
        ctx = ConstraintContext(
            snapshot=snapshot,
            at=at if at is not None else state.next_free,
            config=config,
            order=order,
            machine_state=state,
        )
    return estimate_setup(op, order, machine, state, config, ctx)


def describe_setup(estimate: SetupEstimate, config: SchedulingConfig) -> str:
    """Human reason for an estimate, including where the base minutes came from."""
    if estimate.reason == IN_PROGRESS_REASON:
        return IN_PROGRESS_REASON
    text = f"{estimate.minutes:g} min setup: {estimate.reason}"
    if estimate.base_source == "default":
        text += f" (no ERP setup time; default {config.setup.default_setup_minutes:g} min used)"
    elif estimate.base_source == "order":
        text += " (order-level setup estimate)"
    if estimate.factor < 1.0 and estimate.basis in ("same_family", "same_material"):
        text += f" [{estimate.base_minutes:g} min x {estimate.factor:g}]"
    return text


def compute_setup_minutes(
    op: Operation,
    machine: Machine,
    state: MachineState,
    config: SchedulingConfig,
    *,
    order: Order | None = None,
    snapshot: PlanningSnapshot | None = None,
    at: datetime | None = None,
) -> tuple[float, str]:
    """``(setup minutes, human reason)`` for ``op`` on ``machine`` given ``state``."""
    estimate = compute_setup(op, machine, state, config, order=order, snapshot=snapshot, at=at)
    return estimate.minutes, describe_setup(estimate, config)


__all__ = [
    "IN_PROGRESS_REASON",
    "base_setup_minutes",
    "compute_setup",
    "compute_setup_minutes",
    "describe_setup",
    "is_in_progress_on",
]
