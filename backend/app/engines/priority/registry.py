"""Factory for the shipped factor set, in canonical key order."""

from __future__ import annotations

from app.core.errors import ConfigurationError
from app.domain.config import FACTOR_KEYS
from app.engines.priority.base import PriorityFactor
from app.engines.priority.factors import (
    BatchingAffinity,
    CustomerImportance,
    DelayPenalty,
    DownstreamImpact,
    DueDateUrgency,
    MachineAvailability,
    Margin,
    OrderValue,
    ProductionReadiness,
    SetupEfficiency,
    SlaRisk,
)

_FACTOR_CLASSES: dict[str, type] = {
    DueDateUrgency.key: DueDateUrgency,
    SlaRisk.key: SlaRisk,
    CustomerImportance.key: CustomerImportance,
    OrderValue.key: OrderValue,
    Margin.key: Margin,
    DelayPenalty.key: DelayPenalty,
    ProductionReadiness.key: ProductionReadiness,
    MachineAvailability.key: MachineAvailability,
    SetupEfficiency.key: SetupEfficiency,
    BatchingAffinity.key: BatchingAffinity,
    DownstreamImpact.key: DownstreamImpact,
}


def factor_by_key(key: str) -> PriorityFactor:
    """A fresh factor instance for a canonical key (``ConfigurationError`` for unknown keys)."""
    cls = _FACTOR_CLASSES.get(key)
    if cls is None:
        raise ConfigurationError(f"unknown priority factor {key!r}", details={"known": list(FACTOR_KEYS)})
    factor: PriorityFactor = cls()
    return factor


def default_factors() -> list[PriorityFactor]:
    """Every canonical factor, ordered as ``FACTOR_KEYS`` (the explanation order)."""
    return [factor_by_key(key) for key in FACTOR_KEYS]


__all__ = ["default_factors", "factor_by_key"]
