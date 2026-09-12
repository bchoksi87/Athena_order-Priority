"""Helpers shared by the priority factors (interpolation, wording, parameters).

Kept tiny and pure so every factor module reads top-to-bottom as "the rule".
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Mapping, Sequence
from datetime import datetime
from itertools import pairwise

from app.domain.config import PriorityProfile

Anchors = Sequence[tuple[float, float]]


def interpolate(anchors: Anchors, x: float) -> float:
    """Piecewise-linear value of ``x`` over ``anchors`` (sorted by x); flat beyond both ends.

    Anchors sharing the same x collapse to the first one, so a degenerate
    configuration never divides by zero.
    """
    points = sorted(anchors, key=lambda a: a[0])
    if not points:
        return 0.0
    if x <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in pairwise(points):
        if x <= x1:
            if x1 <= x0:
                return y0
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1]


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def describe_hours(hours: float) -> str:
    """Human wording for a duration in hours: ``45 minutes`` / ``18 hours`` / ``3.5 days``."""
    h = abs(hours)
    if h < 1.0:
        return f"{h * 60:.0f} minutes"
    if h < 48.0:
        return f"{h:.0f} hours"
    return f"{h / 24:.1f} days"


def describe_when(dt: datetime) -> str:
    """Short date wording used in reasons (``14 Sep``)."""
    return f"{dt.day} {dt.strftime('%b')}"


def param_float(profile: PriorityProfile, key: str, name: str, default: float) -> float:
    """Numeric factor parameter from ``FactorWeight.params`` with a typed fallback."""
    raw = profile.factor_params(key).get(name)
    if raw is None or isinstance(raw, bool | str):
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def mid_rank_percentiles(values: Mapping[str, float]) -> dict[str, float]:
    """Mid-rank percentile (0..1) of every value in ``values`` within that population.

    ``(#less + #less_or_equal) / (2n)``: ties share one percentile, a single
    value sits at 0.5, and the result is deterministic without numpy.
    """
    if not values:
        return {}
    ordered = sorted(values.values())
    n = len(ordered)
    return {
        key: (bisect_left(ordered, value) + bisect_right(ordered, value)) / (2.0 * n)
        for key, value in values.items()
    }


__all__ = [
    "Anchors",
    "clamp",
    "describe_hours",
    "describe_when",
    "interpolate",
    "mid_rank_percentiles",
    "param_float",
]
