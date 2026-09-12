"""Scheduling engine (DESIGN_CONTRACT §6.4): rule-based V1 plus optional CP-SAT V2."""

from app.engines.scheduling.base import Scheduler
from app.engines.scheduling.batching import BatchCandidate, BatchDecision, pick_next
from app.engines.scheduling.machine_assignment import build_recommendation, place_on_calendar, rank_machines
from app.engines.scheduling.metrics import compute_metrics
from app.engines.scheduling.quality import compare_schedules, compute_quality
from app.engines.scheduling.registry import SchedulerRegistry, cpsat_available, default_registry
from app.engines.scheduling.rule_based import RuleBasedScheduler
from app.engines.scheduling.setup import compute_setup, compute_setup_minutes

__all__ = [
    "BatchCandidate",
    "BatchDecision",
    "RuleBasedScheduler",
    "Scheduler",
    "SchedulerRegistry",
    "build_recommendation",
    "compare_schedules",
    "compute_metrics",
    "compute_quality",
    "compute_setup",
    "compute_setup_minutes",
    "cpsat_available",
    "default_registry",
    "pick_next",
    "place_on_calendar",
    "rank_machines",
]
