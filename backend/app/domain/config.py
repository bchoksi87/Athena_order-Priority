"""Versioned configuration models (spec: CONFIGURATION OVER HARD CODING).

These Pydantic models are stored as JSON in the database (with a version
number) and edited through the API/UI. Defaults below are the shipped
"PriorityProfile-A" / "SchedulingConfig-A" and are the *only* place business
defaults live. Engines never embed their own numbers.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

FactorKey = Literal[
    "due_date_urgency",
    "sla_risk",
    "customer_importance",
    "order_value",
    "margin",
    "delay_penalty",
    "production_readiness",
    "machine_availability",
    "setup_efficiency",
    "batching_affinity",
    "downstream_impact",
]

FACTOR_KEYS: tuple[str, ...] = (
    "due_date_urgency",
    "sla_risk",
    "customer_importance",
    "order_value",
    "margin",
    "delay_penalty",
    "production_readiness",
    "machine_availability",
    "setup_efficiency",
    "batching_affinity",
    "downstream_impact",
)

FACTOR_NAMES: dict[str, str] = {
    "due_date_urgency": "Due Date Urgency",
    "sla_risk": "SLA Risk",
    "customer_importance": "Customer Importance",
    "order_value": "Order Value",
    "margin": "Contribution Margin",
    "delay_penalty": "Delay Penalty",
    "production_readiness": "Production Readiness",
    "machine_availability": "Machine Availability",
    "setup_efficiency": "Setup Efficiency",
    "batching_affinity": "Batching Affinity",
    "downstream_impact": "Downstream Impact",
}


class FactorWeight(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: FactorKey
    weight: float = Field(ge=0, le=100, description="Relative weight in percent (normalised at runtime)")
    enabled: bool = True
    params: dict[str, float | int | str | bool] = Field(default_factory=dict)


class DueDateThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")
    overdue_score: float = 100.0
    critical_hours: float = 24.0  # <= this → score critical_score
    critical_score: float = 95.0
    high_days: float = 2.0  # <= this → high_score
    high_score: float = 80.0
    medium_days: float = 7.0
    medium_score: float = 50.0
    low_days: float = 14.0
    low_score: float = 25.0
    floor_score: float = 5.0  # beyond low_days
    use_projected_lateness: bool = True


class CustomerScoring(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tier_scores: dict[str, float] = Field(
        default_factory=lambda: {"strategic": 100.0, "key": 75.0, "standard": 45.0, "low": 20.0}
    )
    strategic_flag_bonus: float = 10.0
    escalation_points_per_level: float = 8.0
    revenue_weight: float = 0.3  # share of score derived from revenue rank 0..1
    profitability_weight: float = 0.2
    max_score: float = 100.0


class SlaRiskConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    default_sla_hours: float | None = None  # None: no SLA unless customer/order has one
    breach_score: float = 100.0
    imminent_ratio: float = 0.25  # remaining/total <= ratio → high risk
    imminent_score: float = 85.0
    watch_ratio: float = 0.5
    watch_score: float = 55.0
    safe_score: float = 15.0


class OrderValueConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scaling: Literal["log", "linear", "percentile"] = "percentile"
    cap_value: float | None = None  # values above are treated as this (linear/log)
    min_score: float = 5.0


class DelayPenaltyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    default_penalty_per_day_ratio: float = 0.01  # of order value when ERP has no penalty
    escalation_multiplier_per_level: float = 0.25
    strategic_multiplier: float = 1.5
    reference_penalty: float | None = None  # penalty (per day) that maps to score 100; None = percentile


class ReadinessScoring(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ready_score: float = 100.0
    material_partial_score: float = 40.0
    waiting_material_score: float = 10.0
    waiting_tooling_score: float = 15.0
    waiting_approval_score: float = 5.0
    waiting_previous_operation_score: float = 35.0
    machine_unavailable_score: float = 20.0
    hold_score: float = 0.0


class MachineAvailabilityScoring(BaseModel):
    model_config = ConfigDict(extra="forbid")
    available_now_score: float = 100.0
    available_within_hours: float = 8.0
    available_soon_score: float = 60.0
    single_machine_bonus: float = 15.0  # only one machine can make it and it is free
    none_available_score: float = 5.0


class SetupEfficiencyScoring(BaseModel):
    model_config = ConfigDict(extra="forbid")
    same_setup_score: float = 100.0
    same_material_score: float = 70.0
    changeover_score: float = 20.0
    unknown_score: float = 50.0
    large_setup_minutes: float = 90.0  # setups above this count as "large"
    large_setup_penalty_score: float = 100.0  # raw score used for the penalty kind


class AgingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    start_after_days: float = 5.0
    points_per_day: float = 2.0
    max_points: float = 20.0


class FairnessConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    max_wait_days: float = 10.0  # starvation threshold
    starvation_boost_points: float = 25.0
    max_top_n_share_per_customer: float = 0.5  # no customer takes >50% of top-N slots
    top_n: int = 50


class ExpediteConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    default_boost_points: float = 30.0
    max_boost_points: float = 60.0
    default_duration_hours: float = 4.0
    max_duration_hours: float = 72.0


class RiskThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")
    critical_lateness_hours: float = 0.0  # projected late at all → critical
    high_slack_hours: float = 8.0  # slack below → high
    medium_slack_hours: float = 48.0


class PriorityProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: str = "PriorityProfile-A"
    name: str = "Default balanced profile"
    version: int = 1
    weights: list[FactorWeight] = Field(
        default_factory=lambda: [
            FactorWeight(key="due_date_urgency", weight=25),
            FactorWeight(key="customer_importance", weight=20),
            FactorWeight(key="sla_risk", weight=15),
            FactorWeight(key="order_value", weight=10),
            FactorWeight(key="margin", weight=5),
            FactorWeight(key="delay_penalty", weight=10),
            FactorWeight(key="production_readiness", weight=5),
            FactorWeight(key="machine_availability", weight=5),
            FactorWeight(key="setup_efficiency", weight=5),
            FactorWeight(key="batching_affinity", weight=0),
            FactorWeight(key="downstream_impact", weight=0),
        ]
    )
    due_date: DueDateThresholds = Field(default_factory=DueDateThresholds)
    customer: CustomerScoring = Field(default_factory=CustomerScoring)
    sla: SlaRiskConfig = Field(default_factory=SlaRiskConfig)
    order_value: OrderValueConfig = Field(default_factory=OrderValueConfig)
    delay_penalty: DelayPenaltyConfig = Field(default_factory=DelayPenaltyConfig)
    readiness: ReadinessScoring = Field(default_factory=ReadinessScoring)
    machine_availability: MachineAvailabilityScoring = Field(default_factory=MachineAvailabilityScoring)
    setup: SetupEfficiencyScoring = Field(default_factory=SetupEfficiencyScoring)
    aging: AgingConfig = Field(default_factory=AgingConfig)
    fairness: FairnessConfig = Field(default_factory=FairnessConfig)
    expedite: ExpediteConfig = Field(default_factory=ExpediteConfig)
    risk: RiskThresholds = Field(default_factory=RiskThresholds)
    erp_priority_points: dict[str, float] = Field(
        default_factory=lambda: {"1": 10.0, "2": 5.0, "3": 0.0, "4": -3.0, "5": -6.0},
        description="Adjustment points by ERP priority code",
    )
    blocked_order_cap: float | None = Field(
        default=70.0,
        description=(
            "Cap on the score of orders that cannot run now (waiting for material, tooling, approval, "
            "on hold ...). Keeps blocked orders visible but below ready work in the queue (spec Phase 3: "
            "an order due tomorrow but lacking material should not simply be placed first). None = no cap; "
            "FORCE_NEXT overrides ignore the cap."
        ),
    )

    @field_validator("weights")
    @classmethod
    def _unique_keys(cls, v: list[FactorWeight]) -> list[FactorWeight]:
        keys = [w.key for w in v]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate factor keys in profile weights")
        return v

    @model_validator(mode="after")
    def _has_positive_weight(self) -> PriorityProfile:
        if not any(w.enabled and w.weight > 0 for w in self.weights):
            raise ValueError("at least one enabled factor must have a positive weight")
        return self

    def weight_map(self) -> dict[str, float]:
        """Weights normalised so that enabled weights sum to 1.0."""
        active = {w.key: w.weight for w in self.weights if w.enabled and w.weight > 0}
        total = sum(active.values())
        return {k: v / total for k, v in active.items()} if total else {}

    def factor_params(self, key: str) -> dict[str, float | int | str | bool]:
        for w in self.weights:
            if w.key == key:
                return dict(w.params)
        return {}


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------


class StabilityRules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    frozen_window_minutes: float = 30.0  # entries starting within this window are not moved
    min_improvement_pct: float = 3.0  # replan only if quality improves by this much
    max_moves_per_replan: int | None = None


class SetupRules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    same_family_setup_factor: float = 0.0  # fraction of setup needed when same family
    same_material_setup_factor: float = 0.5
    default_setup_minutes: float = 30.0  # used when ERP has no setup time (flagged by DQ)
    setup_penalty_cost_per_minute: float = 1.0


BatchDimension = Literal[
    "material",
    "machine",
    "tool",
    "fixture",
    "process",
    "part_family",
    "customer",
    "surface_finish",
    "technology",
]


def _default_batch_dimensions() -> list[BatchDimension]:
    return ["material", "part_family"]


class BatchingRules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    dimensions: list[BatchDimension] = Field(default_factory=_default_batch_dimensions)
    max_delay_hours: float = 4.0  # never delay a job more than this to batch it
    min_priority_gap: float = 15.0  # only pull a job forward if within this score gap of the head


class ObjectiveWeights(BaseModel):
    """Relative importance of objectives (spec Phase 19). Used by quality score and V2 optimizers."""

    model_config = ConfigDict(extra="forbid")
    on_time_delivery: float = 40.0
    total_tardiness: float = 20.0
    setup_time: float = 10.0
    machine_utilization: float = 15.0
    contribution_margin: float = 10.0
    wip: float = 5.0

    def normalised(self) -> dict[str, float]:
        raw = self.model_dump()
        total = sum(raw.values())
        return {k: (v / total if total else 0.0) for k, v in raw.items()}


class OvertimeRules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allow_overtime: bool = False
    max_overtime_hours_per_day: float = 2.0
    overtime_cost_per_hour: float = 0.0


class MachinePreferenceRules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preferred_machine_cost: float = 0.0
    non_preferred_machine_cost_minutes: float = 30.0  # soft cost when not the ERP-preferred machine
    utilization_balance_cost_per_pct: float = 0.5  # cost per % above group average load
    energy_cost_per_hour: dict[str, float] = Field(default_factory=dict)  # machine_id -> cost


class SchedulingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config_id: str = "SchedulingConfig-A"
    name: str = "Default rule-based configuration"
    version: int = 1
    algorithm: str = "rule_based"
    horizon_days: int = 14
    lock_window_minutes: float = 240.0  # manager default "lock the next 4 hours"
    stability: StabilityRules = Field(default_factory=StabilityRules)
    setup: SetupRules = Field(default_factory=SetupRules)
    batching: BatchingRules = Field(default_factory=BatchingRules)
    objectives: ObjectiveWeights = Field(default_factory=ObjectiveWeights)
    overtime: OvertimeRules = Field(default_factory=OvertimeRules)
    machine_preference: MachinePreferenceRules = Field(default_factory=MachinePreferenceRules)
    schedule_blocked_orders: bool = False  # if True, place blocked orders after their blocker resolves
    at_risk_slack_hours: float = 8.0  # completion within this of due date = "at risk"
    max_orders_per_run: int | None = None  # None = all
    quality_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "on_time_delivery": 40.0,
            "lateness": 20.0,
            "utilization": 15.0,
            "setup_efficiency": 15.0,
            "at_risk": 10.0,
        }
    )


class ReplanningConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    auto_replan_interval_minutes: float = 30.0
    trigger_on: list[str] = Field(
        default_factory=lambda: [
            "new_order",
            "order_completed",
            "machine_down",
            "machine_up",
            "material_arrived",
            "quality_failure",
            "rework",
            "production_delay",
            "customer_priority_change",
            "config_change",
        ]
    )
    require_approval: bool = True
    significant_change_orders: int = 5  # number of changed orders that counts as "significant"
    notify_roles: list[str] = Field(default_factory=lambda: ["planner", "production_manager"])


class AlertConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    likely_late_slack_hours: float = 8.0
    capacity_overload_pct: float = 95.0
    bottleneck_utilization_pct: float = 90.0
    material_shortage_days_ahead: int = 3
    starvation_days: float = 10.0
    behind_schedule_minutes: float = 60.0


class DataQualityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_cycle_minutes_per_unit: float = 24 * 60.0
    max_total_production_days: float = 90.0
    max_due_date_years_ahead: float = 2.0
    treat_missing_setup_as_blocking: bool = False
    treat_missing_cycle_as_blocking: bool = True
    treat_missing_due_date_as_blocking: bool = True
    treat_missing_machine_as_blocking: bool = True


class SystemConfig(BaseModel):
    """Aggregate of all configurable rule sets (one row per version in the DB)."""

    model_config = ConfigDict(extra="forbid")
    priority_profile: PriorityProfile = Field(default_factory=PriorityProfile)
    scheduling: SchedulingConfig = Field(default_factory=SchedulingConfig)
    replanning: ReplanningConfig = Field(default_factory=ReplanningConfig)
    alerts: AlertConfig = Field(default_factory=AlertConfig)
    data_quality: DataQualityConfig = Field(default_factory=DataQualityConfig)
    currency: str = "INR"
