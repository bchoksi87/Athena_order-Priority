"""Engine output types.

Everything an engine produces is a plain dataclass here so that services can
persist, serialise and diff results without knowing engine internals. Every
numeric result carries the reasons that produced it (spec Phase 34).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal

from app.domain.enums import (
    AlertSeverity,
    AlertType,
    DataQualityCode,
    DataQualitySeverity,
    ReadinessState,
    RiskLevel,
)

# ---------------------------------------------------------------------------
# Priority
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FactorScore:
    key: str
    name: str
    kind: Literal["bonus", "penalty"]
    raw_score: float  # 0..100
    weight: float  # normalised 0..1 (0 when disabled)
    points: float  # signed contribution to the final score
    reason: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PriorityAdjustment:
    kind: Literal["aging", "fairness", "expedite", "override", "customer_rule", "erp_priority"]
    points: float
    reason: str
    source_id: str | None = None


@dataclass(slots=True)
class PriorityResult:
    order_id: str
    score: float
    base_score: float
    factors: list[FactorScore]
    adjustments: list[PriorityAdjustment]
    readiness: ReadinessState
    blocked: bool
    blocking_reasons: list[str]
    risk_level: RiskLevel
    explanation: str
    profile_id: str
    profile_version: int
    computed_at: datetime
    hours_until_due: float | None = None
    projected_completion: datetime | None = None
    projected_lateness_hours: float | None = None
    forced_next: bool = False
    rank: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Constraints
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class Violation:
    constraint_key: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class Penalty:
    constraint_key: str
    cost: float  # in "minute-equivalents"; comparable to time
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class Blocker:
    state: ReadinessState
    message: str
    resolves_at: datetime | None = None  # when the blocker is expected to clear
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EligibilityResult:
    operation_id: str
    eligible_machine_ids: list[str]
    rejected: dict[str, list[Violation]]  # machine_id -> violations


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MachineCandidate:
    machine_id: str
    rank: int
    expected_start: datetime | None
    expected_end: datetime | None
    setup_minutes: float
    run_minutes: float
    soft_cost: float
    reasons: list[str]
    recommended: bool = False


@dataclass(slots=True)
class MachineRecommendation:
    order_id: str
    operation_id: str
    eligible: list[MachineCandidate]
    recommended_machine_id: str | None
    rejected: dict[str, list[str]]  # machine_id -> reasons


@dataclass(slots=True)
class ScheduleEntry:
    entry_id: str
    machine_id: str
    order_id: str
    operation_id: str
    sequence_on_machine: int
    setup_start: datetime
    start: datetime
    end: datetime
    setup_minutes: float
    run_minutes: float
    quantity: float
    priority_score: float
    placement_reason: str
    is_last_operation: bool = False
    expected_completion: datetime | None = None  # order-level completion if last op
    due_date: datetime | None = None
    expected_lateness_hours: float | None = None
    locked: bool = False
    batch_key: str | None = None
    setup_family: str | None = None
    material_id: str | None = None
    customer_id: str | None = None


@dataclass(slots=True)
class UnscheduledItem:
    order_id: str
    operation_id: str | None
    reason_code: str
    reason: str
    readiness: ReadinessState | None = None


@dataclass(slots=True)
class ScheduleMetrics:
    scheduled_orders: int = 0
    unscheduled_orders: int = 0
    scheduled_operations: int = 0
    on_time_orders: int = 0
    late_orders: int = 0
    orders_at_risk: int = 0
    on_time_pct: float = 0.0
    avg_lateness_hours: float = 0.0
    max_lateness_hours: float = 0.0
    total_tardiness_hours: float = 0.0
    total_run_hours: float = 0.0
    total_setup_hours: float = 0.0
    setup_count: int = 0
    makespan_hours: float = 0.0
    overall_utilization_pct: float = 0.0
    machine_utilization_pct: dict[str, float] = field(default_factory=dict)
    revenue_scheduled: float = 0.0
    revenue_at_risk: float = 0.0
    margin_at_risk: float = 0.0
    wip_orders_avg: float = 0.0


@dataclass(slots=True)
class ScheduleQuality:
    score: float  # 0..100
    components: dict[str, float]  # component -> 0..100
    weights: dict[str, float]
    summary: str


@dataclass(slots=True)
class ScheduleResult:
    algorithm: str
    algorithm_version: str
    profile_id: str
    profile_version: int
    config_version: int
    generated_at: datetime
    horizon_start: datetime
    horizon_end: datetime
    entries: list[ScheduleEntry] = field(default_factory=list)
    unscheduled: list[UnscheduledItem] = field(default_factory=list)
    metrics: ScheduleMetrics = field(default_factory=ScheduleMetrics)
    quality: ScheduleQuality | None = None
    machine_recommendations: dict[str, MachineRecommendation] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    run_id: str | None = None

    def entries_for_machine(self, machine_id: str) -> list[ScheduleEntry]:
        return sorted(
            (e for e in self.entries if e.machine_id == machine_id),
            key=lambda e: (e.setup_start, e.sequence_on_machine),
        )

    def entries_for_order(self, order_id: str) -> list[ScheduleEntry]:
        return sorted((e for e in self.entries if e.order_id == order_id), key=lambda e: e.start)

    def order_completion(self) -> dict[str, datetime]:
        out: dict[str, datetime] = {}
        for e in self.entries:
            cur = out.get(e.order_id)
            if cur is None or e.end > cur:
                out[e.order_id] = e.end
        return out


# ---------------------------------------------------------------------------
# Simulation / replanning
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class OrderDelta:
    order_id: str
    baseline_completion: datetime | None
    scenario_completion: datetime | None
    delta_hours: float | None
    baseline_late: bool
    scenario_late: bool
    baseline_machine_id: str | None = None
    scenario_machine_id: str | None = None
    baseline_score: float | None = None
    scenario_score: float | None = None


@dataclass(slots=True)
class ScheduleDiff:
    orders_affected: int
    orders_moved_machine: int
    orders_resequenced: int
    orders_newly_late: int
    orders_newly_on_time: int
    late_orders_before: int
    late_orders_after: int
    on_time_pct_before: float
    on_time_pct_after: float
    avg_lateness_before: float
    avg_lateness_after: float
    utilization_before: float
    utilization_after: float
    setup_hours_before: float
    setup_hours_after: float
    revenue_at_risk_before: float
    revenue_at_risk_after: float
    margin_at_risk_before: float
    margin_at_risk_after: float
    additional_overtime_hours: float
    bottlenecks_before: list[str]
    bottlenecks_after: list[str]
    order_deltas: list[OrderDelta] = field(default_factory=list)
    summary: str = ""


@dataclass(slots=True)
class SimulationResult:
    simulation_id: str
    scenarios: list[dict[str, Any]]
    baseline: ScheduleResult
    scenario: ScheduleResult
    diff: ScheduleDiff
    baseline_priorities: dict[str, float]
    scenario_priorities: dict[str, float]
    generated_at: datetime


@dataclass(slots=True)
class ReplanDecision:
    should_replan: bool
    reason: str
    improvement_pct: float
    changed_entries: int
    frozen_violations: int
    requires_approval: bool
    triggers: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CapacityRow:
    dimension: str  # "machine" | "machine_group" | "process" | "department"
    key: str
    period_start: datetime
    period_end: datetime
    required_hours: float
    available_hours: float

    @property
    def gap_hours(self) -> float:
        return self.available_hours - self.required_hours

    @property
    def utilization_pct(self) -> float:
        if self.available_hours <= 0:
            return 100.0 if self.required_hours > 0 else 0.0
        return 100.0 * self.required_hours / self.available_hours


@dataclass(slots=True)
class Bottleneck:
    resource_type: str  # "machine_group" | "machine" | "process" | "material" | "tooling"
    resource_id: str
    resource_name: str
    utilization_pct: float
    orders_waiting: int
    capacity_shortfall_hours: float
    revenue_at_risk: float
    margin_at_risk: float
    severity: RiskLevel
    recommendation: str


@dataclass(slots=True)
class ExecutiveKpis:
    total_open_orders: int
    total_pending_quantity: float
    orders_due_today: int
    orders_due_tomorrow: int
    orders_due_this_week: int
    overdue_orders: int
    at_risk_orders: int
    on_time_delivery_pct: float | None
    expected_on_time_delivery_pct: float | None
    machine_utilization_pct: float | None
    capacity_utilization_pct: float | None
    revenue_at_risk: float
    margin_at_risk: float
    blocked_by_material: int
    blocked_by_tooling: int
    blocked_by_machine: int
    waiting_for_approval: int
    blocked_total: int
    scheduled_orders: int
    unscheduled_orders: int
    as_of: datetime


# ---------------------------------------------------------------------------
# Data quality & alerts
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DataQualityIssue:
    code: DataQualityCode
    severity: DataQualitySeverity
    entity_type: str
    entity_id: str
    message: str
    field_name: str | None = None
    recommendation: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Alert:
    alert_type: AlertType
    severity: AlertSeverity
    title: str
    reason: str
    recommended_action: str
    raised_at: datetime
    order_id: str | None = None
    machine_id: str | None = None
    entity_ref: str | None = None
    dedupe_key: str = ""
    details: dict[str, Any] = field(default_factory=dict)
