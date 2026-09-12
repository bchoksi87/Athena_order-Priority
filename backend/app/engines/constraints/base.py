"""Constraint engine interfaces (see docs/DESIGN_CONTRACT.md §6.2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from app.domain.config import SchedulingConfig
from app.domain.models import Machine, Operation, Order
from app.domain.results import Penalty, Violation
from app.domain.snapshot import PlanningSnapshot


@dataclass(slots=True)
class MachineState:
    """Mutable, scheduler-owned view of a machine while a schedule is built.

    This is the *tail* view: ``next_free`` is the end of the machine's last
    placed job, and the setup family / material / mounted tooling / last
    customer / last part family describe the state after that job. The
    rule-based scheduler back-fills earlier idle gaps through a separate
    ``MachineTimeline`` (``app.engines.scheduling.timeline``); a transient
    ``MachineState`` built from the slot before a gap represents the state a
    back-filled job follows. ``scheduled_minutes`` is the machine's total
    planned load, wherever the entries sit.
    """

    machine_id: str
    next_free: datetime
    current_setup_family: str | None = None
    current_material_id: str | None = None
    mounted_tooling: set[str] = field(default_factory=set)
    scheduled_minutes: float = 0.0
    last_customer_id: str | None = None
    last_part_family: str | None = None


@dataclass(slots=True)
class ConstraintContext:
    snapshot: PlanningSnapshot
    at: datetime
    config: SchedulingConfig
    order: Order | None = None
    machine_state: MachineState | None = None
    group_average_load_minutes: float | None = None


class HardConstraint(Protocol):
    key: str

    def check(self, op: Operation, machine: Machine, ctx: ConstraintContext) -> Violation | None: ...


class SoftConstraint(Protocol):
    key: str

    def penalty(self, op: Operation, machine: Machine, ctx: ConstraintContext) -> Penalty | None: ...
