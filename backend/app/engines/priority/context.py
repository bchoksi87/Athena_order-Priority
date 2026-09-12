"""Build the :class:`PriorityContext` for one evaluation pass (bulk, O(n log n)).

Everything a factor needs that is *snapshot-wide* is computed here once so
each factor stays O(1) per order:

* readiness state, blockers and eligible machines for every order's next
  operation (one constraint-engine ``assessment_map`` pass);
* ``machine_next_free`` from each machine's calendar (downtime, ``available_from``);
* remaining production minutes per order (setup + cycle x pending quantity
  over pending operations, cycle taken on the fastest plausible machine);
* a naive projected completion: earliest eligible machine's next free
  instant (or the blockers' ``resolves_at``, whichever is later) plus the
  remaining minutes of *working* time on that machine's calendar;
* percentiles (mid-rank) for order value, margin, delay penalty, customer
  revenue / profitability and downstream value;
* downstream value (order value of every open transitive dependent) and the
  critical-path flag;
* the batching index (share of the next-due window sharing a batching
  dimension and a machine).

Missing ERP data never raises: the estimate becomes ``None`` and the reason
is recorded in ``ctx.notes[order_id]`` so the explanation can show it.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

import structlog

from app.core.clock import Clock, ensure_utc
from app.core.errors import ConfigurationError
from app.domain.config import PriorityProfile, SchedulingConfig
from app.domain.enums import OperationStatus, ReadinessState
from app.domain.models import CustomerRule, Machine, Operation, Order
from app.domain.results import Blocker
from app.domain.snapshot import PlanningSnapshot
from app.engines.calendar import MachineCalendar, build_calendars
from app.engines.constraints import ConstraintEngine, default_constraint_engine, estimate_setup
from app.engines.constraints.eligibility import CandidateIndex, candidate_machines
from app.engines.constraints.readiness import ReadinessAssessment, machine_available_at, next_operation
from app.engines.priority.context_ext import ExtendedPriorityContext
from app.engines.priority.factors.batching_affinity import WINDOW_PARAM
from app.engines.priority.factors.common import describe_hours, mid_rank_percentiles, param_float
from app.engines.priority.factors.delay_penalty import effective_penalty_per_day

log = structlog.get_logger(__name__)

MINUTES_PER_HOUR = 60.0


# ------------------------------------------------------------------ machines


def compute_machine_next_free(
    snapshot: PlanningSnapshot, calendars: Mapping[str, MachineCalendar], now: datetime
) -> dict[str, datetime]:
    """Earliest working instant ≥ ``now`` per machine; inoperable machines with no return are omitted."""
    out: dict[str, datetime] = {}
    for machine_id in sorted(snapshot.machines):
        machine = snapshot.machines[machine_id]
        base = machine_available_at(machine, now)
        if not machine.status.is_operable and base <= now:
            continue  # down/offline with no scheduled return: unknown
        calendar = calendars.get(machine_id)
        if calendar is None:
            out[machine_id] = base
            continue
        try:
            out[machine_id] = calendar.next_working_time(base)
        except ConfigurationError:
            log.warning("priority.context.machine_never_working", machine_id=machine_id)
    return out


# ---------------------------------------------------------------- remaining


def _base_setup_minutes(op: Operation, order: Order, config: SchedulingConfig) -> float:
    if op.setup_minutes is not None:
        return max(0.0, op.setup_minutes)
    if order.estimated_setup_minutes is not None:
        return max(0.0, order.estimated_setup_minutes)
    return config.setup.default_setup_minutes


def _fastest_machine(op: Operation, machines: list[Machine]) -> tuple[Machine, float] | None:
    best: tuple[float, str, Machine] | None = None
    operable = [m for m in machines if m.status.is_operable] or machines
    for machine in operable:
        run = op.run_minutes_on(machine)
        if run is None:
            continue
        key = (run, machine.machine_id, machine)
        if best is None or key[:2] < best[:2]:
            best = key
    return (best[2], best[0]) if best is not None else None


def estimate_remaining_minutes(
    order: Order,
    snapshot: PlanningSnapshot,
    eligible_next: list[Machine],
    index: CandidateIndex,
    config: SchedulingConfig,
) -> tuple[float | None, dict[str, Any]]:
    """Setup + run minutes over the order's pending operations, with the basis used.

    The next operation uses its eligible machines and a state-aware setup
    estimate; later operations use their plausible candidates and the plain
    base setup (the machine state will have changed by then). When any cycle
    time is unknown the order-level ``estimated_total_production_minutes``
    (pro-rated to the pending quantity) is used; otherwise ``None``.
    """
    notes: dict[str, Any] = {}
    pending = snapshot.pending_operations_for_order(order.order_id)
    order_level = _order_level_minutes(order)
    if not pending:
        notes["remaining_basis"] = "order_level" if order_level is not None else "unknown"
        return order_level, notes
    total = 0.0
    for i, op in enumerate(pending):
        machines = eligible_next if i == 0 else candidate_machines(op, order, snapshot, index).machines
        choice = _fastest_machine(op, machines)
        if choice is not None:
            machine, run = choice
            setup = (
                estimate_setup(op, order, machine, None, config).minutes
                if i == 0
                else _base_setup_minutes(op, order, config)
            )
        elif op.cycle_minutes_per_unit is not None:
            run = op.cycle_minutes_per_unit * op.pending_quantity
            setup = _base_setup_minutes(op, order, config)
        else:
            notes["missing_cycle_operation_id"] = op.operation_id
            notes["remaining_basis"] = "order_level" if order_level is not None else "unknown"
            return order_level, notes
        if op.operation_status is OperationStatus.IN_PROGRESS:
            setup = 0.0
        total += setup + run
    notes["remaining_basis"] = "operations"
    notes["pending_operations"] = len(pending)
    return total, notes


def _order_level_minutes(order: Order) -> float | None:
    if order.estimated_total_production_minutes is not None:
        total = max(0.0, order.estimated_total_production_minutes)
        if order.quantity > 0:
            total *= order.pending_quantity / order.quantity
        return total
    if order.estimated_cycle_minutes_per_unit is not None:
        setup = order.estimated_setup_minutes or 0.0
        return max(0.0, setup) + max(0.0, order.estimated_cycle_minutes_per_unit) * order.pending_quantity
    return None


def project_completion(
    remaining: float | None,
    eligible: list[Machine],
    machine_next_free: Mapping[str, datetime],
    calendars: Mapping[str, MachineCalendar],
    blockers: list[Blocker],
    now: datetime,
) -> tuple[datetime | None, str | None]:
    """Naive completion: earliest eligible machine (after blockers clear) + remaining working minutes."""
    if remaining is None:
        return None, None
    known = sorted(
        (machine_next_free[m.machine_id], m.machine_id) for m in eligible if m.machine_id in machine_next_free
    )
    if not known:
        return None, None
    start, machine_id = known[0]
    for blocker in blockers:
        if blocker.resolves_at is not None:
            start = max(start, ensure_utc(blocker.resolves_at))
    start = max(start, now)
    calendar = calendars.get(machine_id)
    if calendar is None:
        return start + timedelta(minutes=remaining), machine_id
    try:
        return calendar.add_work_minutes(start, remaining), machine_id
    except ConfigurationError:
        return start + timedelta(minutes=remaining), machine_id


# --------------------------------------------------------------- downstream


def compute_downstream(
    snapshot: PlanningSnapshot,
    open_orders: list[Order],
    remaining: Mapping[str, float | None],
    now: datetime,
) -> tuple[dict[str, float], dict[str, str]]:
    """Downstream value (open transitive dependents' order value) and critical-path reasons."""
    memo: dict[str, frozenset[str]] = {}

    def descendants(order_id: str, visiting: set[str]) -> frozenset[str]:
        cached = memo.get(order_id)
        if cached is not None:
            return cached
        visiting.add(order_id)
        out: set[str] = set()
        for dep_id in sorted(snapshot.dependents_of(order_id)):
            if dep_id in visiting:
                continue  # cycle in ERP data: ignore the back edge
            out.add(dep_id)
            out |= descendants(dep_id, visiting)
        visiting.discard(order_id)
        result = frozenset(out)
        memo[order_id] = result
        return result

    values: dict[str, float] = {}
    critical: dict[str, str] = {}
    for order in open_orders:
        descs = descendants(order.order_id, set())
        total = 0.0
        tightest: tuple[float, str, str] | None = None
        own = remaining.get(order.order_id)
        for dep_id in sorted(descs):
            dep = snapshot.orders.get(dep_id)
            if dep is None or not dep.is_open:
                continue
            if dep.order_value is not None:
                total += max(0.0, dep.order_value)
            hours = dep.hours_until_due(now)
            dep_remaining = remaining.get(dep_id)
            if hours is None or (own is None and dep_remaining is None):
                continue
            need_hours = ((own or 0.0) + (dep_remaining or 0.0)) / MINUTES_PER_HOUR
            slack = hours - need_hours
            if slack <= 0 and (tightest is None or slack < tightest[0]):
                tightest = (
                    slack,
                    dep_id,
                    f"dependent {dep_id} due in {describe_hours(hours)} needs {describe_hours(need_hours)}",
                )
        values[order.order_id] = total
        if tightest is not None:
            critical[order.order_id] = tightest[2]
    return values, critical


# ----------------------------------------------------------------- batching


def _dimension_value(dim: str, order: Order, op: Operation) -> str | None:
    if dim == "material":
        return op.material_id or order.required_material_id
    if dim == "part_family":
        return order.part_family
    if dim == "customer":
        return order.customer_id
    if dim == "surface_finish":
        return order.surface_finish
    if dim == "technology":
        return order.technology
    if dim == "process":
        return op.operation_type.value
    return None  # machine / tool / fixture have no order-level counterpart


def compute_batching(
    snapshot: PlanningSnapshot,
    open_orders: list[Order],
    eligible: Mapping[str, list[Machine]],
    dimensions: list[str],
    window: int,
) -> tuple[dict[str, float], dict[str, dict[str, Any]]]:
    """Share of the next-due ``window`` orders that batch with each open order."""
    share: dict[str, float] = {}
    detail: dict[str, dict[str, Any]] = {}
    if window <= 0 or not open_orders:
        return share, detail
    by_due = sorted(open_orders, key=lambda o: (o.due_date is None, o.due_date or datetime.max, o.order_id))
    window_orders = by_due[:window]
    window_ids = {o.order_id for o in window_orders}
    keys: dict[str, dict[str, str | None]] = {}
    index: dict[str, dict[str, list[str]]] = {dim: defaultdict(list) for dim in dimensions}
    machines: dict[str, set[str]] = {oid: {m.machine_id for m in ms} for oid, ms in eligible.items()}
    for order in open_orders:
        op, _ = next_operation(order, snapshot)
        keys[order.order_id] = {dim: _dimension_value(dim, order, op) for dim in dimensions}
    for order in window_orders:
        for dim, value in keys[order.order_id].items():
            if value is not None:
                index[dim][value].append(order.order_id)
    for order in open_orders:
        oid = order.order_id
        own_machines = machines.get(oid, set())
        matched: dict[str, set[str]] = defaultdict(set)
        shared_machines: set[str] = set()
        for dim, value in keys[oid].items():
            if value is None:
                continue
            for peer_id in index[dim].get(value, ()):
                if peer_id == oid:
                    continue
                overlap = own_machines & machines.get(peer_id, set())
                if not overlap:
                    continue
                matched[peer_id].add(dim)
                shared_machines |= overlap
        denominator = len(window_ids) - (1 if oid in window_ids else 0)
        peers = len(matched)
        share[oid] = peers / denominator if denominator > 0 else 0.0
        detail[oid] = {
            "window": len(window_ids),
            "peers": peers,
            "peer_order_ids": sorted(matched)[:10],
            "dimensions": sorted({d for dims in matched.values() for d in dims}),
            "shared_machine_ids": sorted(shared_machines),
        }
    return share, detail


# ------------------------------------------------------------------ builder


class PriorityContextBuilder:
    """Assembles an :class:`ExtendedPriorityContext` from a snapshot in one bulk pass."""

    def __init__(
        self,
        constraint_engine: ConstraintEngine | None = None,
        calendars: Mapping[str, MachineCalendar] | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.constraint_engine = (
            constraint_engine if constraint_engine is not None else default_constraint_engine()
        )
        self.calendars = calendars
        self.clock = clock

    def build(
        self,
        snapshot: PlanningSnapshot,
        profile: PriorityProfile,
        customer_rules: Mapping[str, CustomerRule] | None = None,
        now: datetime | None = None,
    ) -> ExtendedPriorityContext:
        """Context for ``snapshot`` evaluated at ``now`` (default: ``snapshot.as_of``)."""
        now = ensure_utc(now if now is not None else snapshot.as_of)
        rules = {cid: r for cid, r in (customer_rules or snapshot.customer_rules).items() if r.active}
        calendars = self.calendars if self.calendars is not None else build_calendars(snapshot)
        config = self.constraint_engine.config
        assessments = self.constraint_engine.assessment_map(snapshot, now)
        machine_next_free = compute_machine_next_free(snapshot, calendars, now)
        open_orders = snapshot.open_orders()
        index = CandidateIndex(snapshot)

        readiness: dict[str, ReadinessState] = {}
        blockers: dict[str, list[Blocker]] = {}
        eligible: dict[str, list[Machine]] = {}
        remaining: dict[str, float | None] = {}
        projected: dict[str, datetime | None] = {}
        notes: dict[str, dict[str, Any]] = {}
        for order in open_orders:
            oid = order.order_id
            assessment = assessments.get(oid) or ReadinessAssessment(oid, ReadinessState.READY, [])
            readiness[oid] = assessment.state
            blockers[oid] = list(assessment.blockers)
            machines = [
                snapshot.machines[m] for m in assessment.eligible_machine_ids if m in snapshot.machines
            ]
            eligible[oid] = machines
            minutes, order_notes = estimate_remaining_minutes(order, snapshot, machines, index, config)
            remaining[oid] = minutes
            completion, machine_id = project_completion(
                minutes, machines, machine_next_free, calendars, blockers[oid], now
            )
            projected[oid] = completion
            order_notes["projection_machine_id"] = machine_id
            notes[oid] = order_notes

        values = {o.order_id: o.order_value for o in open_orders if o.order_value is not None}
        margins = {
            o.order_id: (o.estimated_margin if o.estimated_margin is not None else o.actual_margin)
            for o in open_orders
        }
        margins_known = {k: v for k, v in margins.items() if v is not None}
        penalties: dict[str, float] = {}
        for order in open_orders:
            penalty, _ = effective_penalty_per_day(
                order, snapshot.customers.get(order.customer_id), profile.delay_penalty
            )
            if penalty is not None:
                penalties[order.order_id] = penalty
        revenue = {
            c.customer_id: c.customer_revenue
            for c in snapshot.customers.values()
            if c.customer_revenue is not None
        }
        profitability = {
            c.customer_id: c.customer_profitability
            for c in snapshot.customers.values()
            if c.customer_profitability is not None
        }
        downstream, critical = compute_downstream(snapshot, open_orders, remaining, now)
        downstream_known = {k: v for k, v in downstream.items() if v > 0}
        window = int(param_float(profile, "batching_affinity", WINDOW_PARAM, float(profile.fairness.top_n)))
        batching_share, batching_detail = compute_batching(
            snapshot, open_orders, eligible, list(config.batching.dimensions), window
        )
        population_max = {
            key: max(pop.values())
            for key, pop in (
                ("order_value", values),
                ("margin", margins_known),
                ("penalty", penalties),
                ("downstream_value", downstream_known),
            )
            if pop
        }
        ctx = ExtendedPriorityContext(
            snapshot=snapshot,
            profile=profile,
            now=now,
            customer_rules=rules,
            readiness=readiness,
            blockers=blockers,
            eligible_machines=eligible,
            projected_completion=projected,
            order_value_percentile=mid_rank_percentiles(values),
            margin_percentile=mid_rank_percentiles(margins_known),
            penalty_percentile=mid_rank_percentiles(penalties),
            customer_revenue_percentile=mid_rank_percentiles(revenue),
            customer_profitability_percentile=mid_rank_percentiles(profitability),
            downstream_value=downstream,
            machine_next_free=machine_next_free,
            remaining_minutes=remaining,
            weights=profile.weight_map(),
            scheduling_config=config,
            population_max=population_max,
            downstream_percentile=mid_rank_percentiles(downstream_known),
            critical_path=critical,
            batching_share=batching_share,
            batching_detail=batching_detail,
            calendars=calendars,
            notes=notes,
        )
        log.debug(
            "priority.context.built",
            orders=len(open_orders),
            machines=len(machine_next_free),
            ready=sum(1 for s in readiness.values() if s is ReadinessState.READY),
            projected=sum(1 for p in projected.values() if p is not None),
            profile=profile.profile_id,
        )
        return ctx


__all__ = [
    "PriorityContextBuilder",
    "compute_batching",
    "compute_downstream",
    "compute_machine_next_free",
    "estimate_remaining_minutes",
    "project_completion",
]
