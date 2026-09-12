"""Schedule quality score (spec Phase 36) and schedule comparison.

Every component is a 0..100 number derived from :class:`ScheduleMetrics`:

* ``on_time_delivery``  = on-time percentage;
* ``lateness``          = ``100 / (1 + avg_lateness_hours / at_risk_slack_hours)``:
  an average lateness of one "at-risk slack" (``config.at_risk_slack_hours``)
  halves the component, so the scale follows the plant's own tolerance instead
  of a hard-coded number of hours;
* ``utilization``       = overall calendar utilization;
* ``setup_efficiency``  = run / (run + setup);
* ``at_risk``           = 100 - share of scheduled orders that are at risk.

The score is the weighted mean with ``config.quality_weights`` (normalised;
unknown keys are ignored). The summary line renders the same numbers:
``"On-time 94% · Utilization 87% · Setup efficiency 81% · Avg lateness 2.4 h · At risk 14"``.
"""

from __future__ import annotations

from typing import Any

from app.domain.config import SchedulingConfig
from app.domain.results import ScheduleMetrics, ScheduleQuality, ScheduleResult

COMPONENT_KEYS: tuple[str, ...] = (
    "on_time_delivery",
    "lateness",
    "utilization",
    "setup_efficiency",
    "at_risk",
)


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


def quality_components(metrics: ScheduleMetrics, config: SchedulingConfig) -> dict[str, float]:
    """The five 0..100 components (see module docstring)."""
    slack = config.at_risk_slack_hours if config.at_risk_slack_hours > 0 else 1.0
    lateness = 100.0 / (1.0 + metrics.avg_lateness_hours / slack)
    total_work = metrics.total_run_hours + metrics.total_setup_hours
    setup_eff = 100.0 * metrics.total_run_hours / total_work if total_work > 0 else 100.0
    scheduled = metrics.scheduled_orders
    at_risk = 100.0 * (1.0 - metrics.orders_at_risk / scheduled) if scheduled > 0 else 100.0
    return {
        "on_time_delivery": _clamp(metrics.on_time_pct),
        "lateness": _clamp(lateness),
        "utilization": _clamp(metrics.overall_utilization_pct),
        "setup_efficiency": _clamp(setup_eff),
        "at_risk": _clamp(at_risk),
    }


def quality_summary(metrics: ScheduleMetrics, components: dict[str, float]) -> str:
    return (
        f"On-time {components['on_time_delivery']:.0f}% · "
        f"Utilization {components['utilization']:.0f}% · "
        f"Setup efficiency {components['setup_efficiency']:.0f}% · "
        f"Avg lateness {metrics.avg_lateness_hours:.1f} h · "
        f"At risk {metrics.orders_at_risk}"
    )


def compute_quality(result: ScheduleResult, config: SchedulingConfig) -> ScheduleQuality:
    """Weighted quality score of ``result`` from its metrics and ``config.quality_weights``."""
    components = quality_components(result.metrics, config)
    raw = {k: max(0.0, float(v)) for k, v in config.quality_weights.items() if k in components}
    total = sum(raw.values())
    weights = {k: (v / total if total > 0 else 0.0) for k, v in raw.items()}
    score = sum(components[k] * w for k, w in weights.items()) if total > 0 else 0.0
    return ScheduleQuality(
        score=_clamp(score),
        components=components,
        weights=weights,
        summary=quality_summary(result.metrics, components),
    )


_COMPARE_FIELDS: tuple[tuple[str, str, str], ...] = (
    # key, label, format
    ("on_time_pct", "On-time delivery", "{:.0f}%"),
    ("avg_lateness_hours", "Average lateness", "{:.1f}h"),
    ("overall_utilization_pct", "Machine utilization", "{:.0f}%"),
    ("total_setup_hours", "Setup hours", "{:.0f}"),
    ("late_orders", "Late orders", "{:d}"),
    ("orders_at_risk", "Orders at risk", "{:d}"),
    ("revenue_at_risk", "Revenue at risk", "{:,.0f}"),
    ("scheduled_orders", "Scheduled orders", "{:d}"),
)


def compare_schedules(a: ScheduleResult, b: ScheduleResult) -> dict[str, Any]:
    """Before → after pairs for the spec's comparison view ("On-time delivery: 87% → 94%")."""
    out: dict[str, Any] = {}
    lines: list[str] = []
    for key, label, fmt in _COMPARE_FIELDS:
        before = getattr(a.metrics, key)
        after = getattr(b.metrics, key)
        out[key] = {"label": label, "before": before, "after": after, "delta": after - before}
        lines.append(f"{label}: {fmt.format(before)} → {fmt.format(after)}")
    q_before = a.quality.score if a.quality is not None else None
    q_after = b.quality.score if b.quality is not None else None
    out["quality_score"] = {
        "label": "Schedule quality",
        "before": q_before,
        "after": q_after,
        "delta": (q_after - q_before) if q_before is not None and q_after is not None else None,
    }
    if q_before is not None and q_after is not None:
        lines.insert(0, f"Schedule quality: {q_before:.0f} → {q_after:.0f}")
    out["summary"] = "; ".join(lines)
    return out


__all__ = ["COMPONENT_KEYS", "compare_schedules", "compute_quality", "quality_components", "quality_summary"]
