"""Factory for the shipped constraint set."""

from __future__ import annotations

from app.domain.config import SchedulingConfig
from app.engines.constraints.engine import ConstraintEngine
from app.engines.constraints.hard import default_hard_constraints
from app.engines.constraints.soft import default_soft_constraints


def default_constraint_engine(config: SchedulingConfig | None = None) -> ConstraintEngine:
    """ConstraintEngine with every built-in hard and soft constraint, driven by ``config``."""
    config = config if config is not None else SchedulingConfig()
    return ConstraintEngine(
        hard=default_hard_constraints(), soft=default_soft_constraints(config), config=config
    )


__all__ = ["default_constraint_engine"]
