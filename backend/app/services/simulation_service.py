"""SimulationService: what-if scenarios on the live plan (spec Phase 7).

``simulate`` runs :meth:`PlanningPipeline.simulate` on the live snapshot with
the active plan's entries as the frozen-window baseline, so the *baseline*
equals what ``POST /schedule/generate`` would produce right now. Nothing is
persisted except an ``audit_log`` row ``simulation.run`` carrying the
scenario JSON — a simulation never creates a schedule version.

The outcome carries the diff, baseline vs scenario metrics and quality, the
top-N affected orders (joined with customer / part), bottlenecks before and
after and the management summary sentence rendered by the diff engine.
``list_scenario_types`` exposes the ``Scenario`` union as JSON schema for the
UI form builder.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog
from pydantic import TypeAdapter
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.errors import ValidationError
from app.core.security import CurrentUser
from app.db.records import ScheduleVersionInfo
from app.db.repositories.customers import CustomerRepository
from app.db.repositories.orders import OrderRepository
from app.db.repositories.schedule import ScheduleRepository
from app.domain.results import OrderDelta, ScheduleQuality, SimulationResult
from app.engines.pipeline import PlanningPipeline
from app.engines.scheduling.quality import compare_schedules, compute_quality
from app.engines.simulation.base import ScenarioBase
from app.engines.simulation.scenarios import SCENARIO_KINDS, Scenario, parse_scenarios
from app.services.audit_service import AuditService
from app.services.base import Service, actor_id
from app.services.snapshot_service import SnapshotService

log = structlog.get_logger(__name__)

ENTITY_SIMULATION = "simulation"
DEFAULT_TOP_N = 20
MAX_TOP_N = 500
MAX_SCENARIOS = 20


@dataclass(slots=True)
class AffectedOrder:
    delta: OrderDelta
    customer_id: str | None
    customer_name: str | None
    part_id: str | None
    part_name: str | None
    due_date: datetime | None
    order_value: float | None


@dataclass(slots=True)
class SimulationOutcome:
    result: SimulationResult
    baseline_quality: ScheduleQuality | None
    scenario_quality: ScheduleQuality | None
    comparison: dict[str, Any]
    affected: list[AffectedOrder]
    baseline_version: ScheduleVersionInfo | None
    note: str | None
    top_n: int

    @property
    def summary(self) -> str:
        return self.result.diff.summary


@dataclass(slots=True)
class ScenarioType:
    kind: str
    title: str
    description: str
    schema: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ScenarioTypes:
    kinds: list[ScenarioType]
    schema: dict[str, Any]


class SimulationService(Service):
    def __init__(
        self,
        session: Session,
        clock: Clock,
        audit: AuditService | None = None,
        snapshots: SnapshotService | None = None,
        pipeline: PlanningPipeline | None = None,
    ) -> None:
        super().__init__(session, clock)
        self._audit = audit or AuditService(session, clock)
        self._snapshots = snapshots or SnapshotService(session, clock)
        self._pipeline = pipeline or PlanningPipeline(clock)
        self._versions = ScheduleRepository(session)
        self._orders = OrderRepository(session)
        self._customers = CustomerRepository(session)

    def simulate(
        self,
        scenarios: Sequence[ScenarioBase | Mapping[str, Any]],
        user: CurrentUser | str,
        note: str | None = None,
        *,
        top_n: int = DEFAULT_TOP_N,
    ) -> SimulationOutcome:
        if not scenarios:
            raise ValidationError("at least one scenario is required", details={"field": "scenarios"})
        if len(scenarios) > MAX_SCENARIOS:
            raise ValidationError(f"at most {MAX_SCENARIOS} scenarios per simulation")
        if top_n < 1 or top_n > MAX_TOP_N:
            raise ValidationError(f"top_n must be in 1..{MAX_TOP_N}", details={"top_n": top_n})
        parsed = parse_scenarios(scenarios)
        now = self.now()
        config = self._snapshots.active_config()
        snapshot = self._snapshots.load_snapshot(now)
        current = self._versions.get_current()
        previous_entries = (
            self._versions.get_entries(schedule_version_id=current.schedule_version_id) if current else None
        )
        result = self._pipeline.simulate(snapshot, parsed, config, previous_entries)
        if result.baseline.quality is None:
            result.baseline.quality = compute_quality(result.baseline, config.scheduling)
        if result.scenario.quality is None:
            result.scenario.quality = compute_quality(result.scenario, config.scheduling)
        affected = self._affected(result.diff.order_deltas[:top_n], snapshot)
        outcome = SimulationOutcome(
            result=result,
            baseline_quality=result.baseline.quality,
            scenario_quality=result.scenario.quality,
            comparison=compare_schedules(result.baseline, result.scenario),
            affected=affected,
            baseline_version=current,
            note=note,
            top_n=top_n,
        )
        self._audit.record(
            user,
            ENTITY_SIMULATION,
            result.simulation_id,
            "simulation.run",
            None,
            {
                "scenarios": result.scenarios,
                "orders_affected": result.diff.orders_affected,
                "late_orders_before": result.diff.late_orders_before,
                "late_orders_after": result.diff.late_orders_after,
                "summary": result.diff.summary,
            },
            note or "what-if simulation",
            {
                "simulation_id": result.simulation_id,
                "scenario_kinds": [s.kind for s in parsed],
                "baseline_version": current.version_number if current else None,
            },
        )
        log.info(
            "simulation.run",
            simulation_id=result.simulation_id,
            user_id=actor_id(user),
            scenarios=[s.kind for s in parsed],
            orders_affected=result.diff.orders_affected,
        )
        return outcome

    def list_scenario_types(self) -> ScenarioTypes:
        """JSON schema of the ``Scenario`` union plus one entry per kind for the UI form builder."""
        adapter: TypeAdapter[Any] = TypeAdapter(Scenario)
        schema = adapter.json_schema()
        defs = schema.get("$defs", {})
        kinds: list[ScenarioType] = []
        for kind in SCENARIO_KINDS:
            model_schema = _schema_for_kind(defs, kind)
            kinds.append(
                ScenarioType(
                    kind=kind,
                    title=str(model_schema.get("title", kind)),
                    description=str(model_schema.get("description", "")).strip(),
                    schema=model_schema,
                )
            )
        return ScenarioTypes(kinds=kinds, schema=schema)

    def _affected(self, deltas: Sequence[OrderDelta], snapshot: Any) -> list[AffectedOrder]:
        ids = {d.order_id for d in deltas}
        orders = {oid: o for oid, o in snapshot.orders.items() if oid in ids}
        missing = ids - set(orders)
        if missing:
            orders.update(self._orders.get_many(missing))
        customers = dict(snapshot.customers)
        wanted = {o.customer_id for o in orders.values()} - set(customers)
        if wanted:
            customers.update(self._customers.get_many(wanted))
        out: list[AffectedOrder] = []
        for delta in deltas:
            order = orders.get(delta.order_id)
            customer = customers.get(order.customer_id) if order is not None else None
            out.append(
                AffectedOrder(
                    delta=delta,
                    customer_id=order.customer_id if order else None,
                    customer_name=customer.customer_name if customer else None,
                    part_id=order.part_id if order else None,
                    part_name=order.part_name if order else None,
                    due_date=order.due_date if order else None,
                    order_value=order.order_value if order else None,
                )
            )
        return out


def _schema_for_kind(defs: Mapping[str, Any], kind: str) -> dict[str, Any]:
    for name, model_schema in defs.items():
        props = model_schema.get("properties", {}) if isinstance(model_schema, dict) else {}
        const = props.get("kind", {}).get("const") if isinstance(props.get("kind"), dict) else None
        if const == kind:
            return {"name": name, **model_schema}
    return {"title": kind}


__all__ = [
    "DEFAULT_TOP_N",
    "ENTITY_SIMULATION",
    "MAX_SCENARIOS",
    "MAX_TOP_N",
    "AffectedOrder",
    "ScenarioType",
    "ScenarioTypes",
    "SimulationOutcome",
    "SimulationService",
]
