"""Priority engine (DESIGN_CONTRACT §6.1): configurable weighted scoring with explanations."""

from app.core.clock import Clock, SystemClock
from app.engines.priority.base import PriorityContext, PriorityFactor, make_factor_score
from app.engines.priority.context import PriorityContextBuilder
from app.engines.priority.context_ext import ExtendedPriorityContext
from app.engines.priority.engine import PriorityEngine, assess_risk
from app.engines.priority.explanation import explanation_lines, render_explanation
from app.engines.priority.preview import ProfileComparison, compare_profiles
from app.engines.priority.registry import default_factors, factor_by_key


def default_priority_engine(clock: Clock | None = None) -> PriorityEngine:
    """Engine with every canonical factor; ``clock`` defaults to the system clock."""
    return PriorityEngine(default_factors(), clock if clock is not None else SystemClock())


__all__ = [
    "ExtendedPriorityContext",
    "PriorityContext",
    "PriorityContextBuilder",
    "PriorityEngine",
    "PriorityFactor",
    "ProfileComparison",
    "assess_risk",
    "compare_profiles",
    "default_factors",
    "default_priority_engine",
    "explanation_lines",
    "factor_by_key",
    "make_factor_score",
    "render_explanation",
]
