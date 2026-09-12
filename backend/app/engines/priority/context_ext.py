"""Extended priority context: snapshot-wide data beyond the frozen base contract.

:class:`~app.engines.priority.base.PriorityContext` (frozen contract, §6.1)
carries the percentiles and look-ups every factor needs. A few factors need
more (the batching index, the population maxima for log/linear value
scaling, the critical-path flags, the scheduling configuration used to
estimate setups). Rather than widening the contract, this module subclasses
it: :class:`ExtendedPriorityContext` adds those fields with safe defaults,
and :func:`extended` lets a factor degrade gracefully when it is handed a
plain base context (tests, other engines).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.domain.config import SchedulingConfig
from app.engines.priority.base import PriorityContext

if TYPE_CHECKING:
    from app.engines.calendar.calendar import MachineCalendar


@dataclass(slots=True)
class ExtendedPriorityContext(PriorityContext):
    """Base context plus the extras computed by :class:`PriorityContextBuilder`.

    Every extra field has a default so the class can be built incrementally
    (or partially, in tests) and so :func:`dataclasses.replace` works for
    profile previews.
    """

    #: Normalised factor weights (``profile.weight_map()``) computed once.
    weights: Mapping[str, float] = field(default_factory=dict)
    #: Scheduling configuration used for setup estimates (shared with the constraint engine).
    scheduling_config: SchedulingConfig = field(default_factory=SchedulingConfig)
    #: Population maxima for absolute scalings (order_value, margin, penalty, downstream_value).
    population_max: Mapping[str, float] = field(default_factory=dict)
    #: order_id -> 0..1 percentile of ``downstream_value``.
    downstream_percentile: Mapping[str, float] = field(default_factory=dict)
    #: order_id -> human reason when the order sits on a critical dependency chain.
    critical_path: Mapping[str, str] = field(default_factory=dict)
    #: order_id -> share (0..1) of the batching window sharing material/family and a machine.
    batching_share: Mapping[str, float] = field(default_factory=dict)
    #: order_id -> details behind ``batching_share`` (window size, peers, dimension matched).
    batching_detail: Mapping[str, dict[str, Any]] = field(default_factory=dict)
    #: machine_id -> calendar, when the builder had them (used for explanations only).
    calendars: Mapping[str, MachineCalendar] = field(default_factory=dict)
    #: order_id -> notes gathered while estimating (data gaps, fallbacks used).
    notes: Mapping[str, dict[str, Any]] = field(default_factory=dict)


def extended(ctx: PriorityContext) -> ExtendedPriorityContext | None:
    """The extended view of ``ctx`` or ``None`` when only the base contract is available."""
    return ctx if isinstance(ctx, ExtendedPriorityContext) else None


def factor_weight(ctx: PriorityContext, key: str) -> float:
    """Normalised weight of ``key`` (0 when disabled or absent from the profile)."""
    ext = extended(ctx)
    if ext is not None and ext.weights:
        return float(ext.weights.get(key, 0.0))
    return float(ctx.profile.weight_map().get(key, 0.0))


def scheduling_config(ctx: PriorityContext) -> SchedulingConfig:
    ext = extended(ctx)
    return ext.scheduling_config if ext is not None else SchedulingConfig()


__all__ = ["ExtendedPriorityContext", "extended", "factor_weight", "scheduling_config"]
