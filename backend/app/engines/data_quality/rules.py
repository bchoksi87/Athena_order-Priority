"""Rule catalogue of the Data Quality Engine (spec Phase 21).

One concrete rule exists for every :class:`~app.domain.enums.DataQualityCode`
(``MISSING_OPERATIONS`` is emitted by :class:`InvalidRoutingRule`). The rules
are split across ``rules_orders``, ``rules_operations`` and ``rules_routing``
to keep modules small; this module re-exports them and defines the default
ordering used by :func:`default_rules`.

The order of ``default_rules()`` is documentation only: the engine sorts the
resulting issues by a stable key, so the report never depends on it.
"""

from __future__ import annotations

from app.engines.data_quality.base import DataQualityRule
from app.engines.data_quality.rules_operations import (
    DEFAULT_MAX_SETUP_MINUTES,
    MATERIAL_CONSUMING_PROCESSES,
    ConflictingMachineCapabilityRule,
    ImpossibleProductionTimeRule,
    MissingCycleTimeRule,
    MissingMachineAssignmentRule,
    MissingMaterialRule,
    MissingSetupTimeRule,
)
from app.engines.data_quality.rules_orders import (
    DuplicateOrderRule,
    IncorrectStatusRule,
    InvalidDateRule,
    MissingCustomerRule,
    MissingDueDateRule,
    NegativeQuantityRule,
)
from app.engines.data_quality.rules_routing import InvalidRoutingRule, UnknownReferenceRule


def default_rules() -> list[DataQualityRule]:
    """Fresh instances of every shipped rule, one per data quality code."""
    return [
        MissingDueDateRule(),
        InvalidDateRule(),
        MissingCycleTimeRule(),
        MissingSetupTimeRule(),
        MissingMachineAssignmentRule(),
        MissingMaterialRule(),
        NegativeQuantityRule(),
        DuplicateOrderRule(),
        IncorrectStatusRule(),
        ImpossibleProductionTimeRule(),
        MissingCustomerRule(),
        ConflictingMachineCapabilityRule(),
        InvalidRoutingRule(),
        UnknownReferenceRule(),
    ]


__all__ = [
    "DEFAULT_MAX_SETUP_MINUTES",
    "MATERIAL_CONSUMING_PROCESSES",
    "ConflictingMachineCapabilityRule",
    "DataQualityRule",
    "DuplicateOrderRule",
    "ImpossibleProductionTimeRule",
    "IncorrectStatusRule",
    "InvalidDateRule",
    "InvalidRoutingRule",
    "MissingCustomerRule",
    "MissingCycleTimeRule",
    "MissingDueDateRule",
    "MissingMachineAssignmentRule",
    "MissingMaterialRule",
    "MissingSetupTimeRule",
    "NegativeQuantityRule",
    "UnknownReferenceRule",
    "default_rules",
]
