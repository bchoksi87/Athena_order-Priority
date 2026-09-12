"""Human-readable explanation rendered from a :class:`PriorityResult` (spec Phase 34).

The text is produced *only* from the ``FactorScore`` / ``PriorityAdjustment``
lists the engine computed — never from a parallel calculation — so the lines
always re-sum to the score. Rounding to the display precision is the only
difference; clamping and the blocked-order cap appear as their own lines so
the arithmetic stays visible::

    ORDER #R3D-10482
    Priority: 91
    Why?
    +28 — Due Date Urgency: Due in 18 hours
    +20 — SLA Risk: SLA 48 h: 9 hours remaining (19%)
    ...
    +6 — Aging: Waiting 8 days (3 beyond 5)
    Total: 91
"""

from __future__ import annotations

from typing import Any

from app.domain.results import PriorityResult

ADJUSTMENT_LABELS: dict[str, str] = {
    "aging": "Aging",
    "fairness": "Fairness",
    "expedite": "Expedite",
    "override": "Override",
    "customer_rule": "Customer rule",
    "erp_priority": "ERP priority",
}
SCORE_MIN = 0.0
SCORE_MAX = 100.0
_EPS = 1e-9


def format_points(points: float) -> str:
    """``+28`` / ``-3`` / ``+27.4`` / ``0`` — integers shown without a decimal."""
    if abs(points) < 0.05:
        return "0"
    text = f"{points:+.1f}"
    return text[:-2] if text.endswith(".0") else text


def explanation_lines(result: PriorityResult) -> list[dict[str, Any]]:
    """Structured lines (label, points, reason, kind, key) for the API and the text renderer."""
    lines: list[dict[str, Any]] = []
    for factor in result.factors:
        reason = factor.reason if factor.weight > 0 else f"{factor.reason} (not weighted)"
        lines.append(
            {
                "kind": "factor",
                "key": factor.key,
                "label": factor.name,
                "points": factor.points,
                "reason": reason,
                "raw_score": factor.raw_score,
                "weight": factor.weight,
            }
        )
    for adj in result.adjustments:
        lines.append(
            {
                "kind": "adjustment",
                "key": adj.kind,
                "label": ADJUSTMENT_LABELS.get(adj.kind, adj.kind),
                "points": adj.points,
                "reason": adj.reason,
                "source_id": adj.source_id,
            }
        )
    raw_total = result.base_score + sum(a.points for a in result.adjustments)
    clamped = max(SCORE_MIN, min(SCORE_MAX, raw_total))
    if abs(clamped - raw_total) > _EPS:
        lines.append(
            {
                "kind": "cap",
                "key": "clamp",
                "label": "Cap",
                "points": clamped - raw_total,
                "reason": f"Score limited to {SCORE_MIN:g}..{SCORE_MAX:g} (raw total {raw_total:.1f})",
            }
        )
    if abs(result.score - clamped) > _EPS:
        lines.append(
            {
                "kind": "cap",
                "key": "blocked_cap",
                "label": "Cap",
                "points": result.score - clamped,
                "reason": "Blocked order: score capped by the priority profile",
            }
        )
    return lines


def render_explanation(result: PriorityResult) -> str:
    out = [f"ORDER #{result.order_id}", f"Priority: {result.score:.0f}", "Why?"]
    for line in explanation_lines(result):
        out.append(f"{format_points(line['points'])} — {line['label']}: {line['reason']}")
    out.append(f"Total: {result.score:.0f}")
    if result.blocked:
        reasons = "; ".join(result.blocking_reasons) if result.blocking_reasons else result.readiness.value
        out.append(f"Blocked: {reasons}")
    if result.forced_next:
        out.append("Forced next by planner override")
    return "\n".join(out)


__all__ = ["ADJUSTMENT_LABELS", "explanation_lines", "format_points", "render_explanation"]
