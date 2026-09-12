"""Data reconciliation between the ERP and the local store (spec Phase 2).

After a sync the number of records the connector served is compared with the
number stored locally, per entity. Small deltas (records skipped by the
normalizer) are expected; large ones point at a broken sync and are flagged.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

import structlog

log = structlog.get_logger(__name__)

ReconciliationStatus = Literal["ok", "warning", "mismatch"]


@dataclass(frozen=True, slots=True)
class ReconciliationThresholds:
    """Integration tuning (not a business rule): how much drift is tolerable."""

    warning_pct: float = 1.0  # |delta| above this share of the connector count -> warning
    mismatch_pct: float = 5.0  # ... -> mismatch
    absolute_tolerance: int = 2  # tiny absolute differences never escalate


@dataclass(slots=True)
class EntityDelta:
    entity: str
    connector_count: int
    stored_count: int
    status: ReconciliationStatus

    @property
    def delta(self) -> int:
        return self.stored_count - self.connector_count

    @property
    def delta_pct(self) -> float:
        if self.connector_count <= 0:
            return 0.0 if self.stored_count == 0 else 100.0
        return 100.0 * abs(self.delta) / self.connector_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity": self.entity,
            "connector_count": self.connector_count,
            "stored_count": self.stored_count,
            "delta": self.delta,
            "delta_pct": round(self.delta_pct, 2),
            "status": self.status,
        }


@dataclass(slots=True)
class ReconciliationReport:
    status: ReconciliationStatus
    deltas: list[EntityDelta] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "summary": self.summary, "deltas": [d.to_dict() for d in self.deltas]}


_SEVERITY: dict[ReconciliationStatus, int] = {"ok": 0, "warning": 1, "mismatch": 2}


def _classify(connector_count: int, stored_count: int, t: ReconciliationThresholds) -> ReconciliationStatus:
    difference = abs(stored_count - connector_count)
    if difference <= t.absolute_tolerance:
        return "ok"
    if connector_count <= 0:
        return "mismatch"
    pct = 100.0 * difference / connector_count
    if pct >= t.mismatch_pct:
        return "mismatch"
    if pct >= t.warning_pct:
        return "warning"
    return "ok"


def reconcile(
    connector_counts: Mapping[str, int],
    stored_counts: Mapping[str, int],
    thresholds: ReconciliationThresholds | None = None,
) -> ReconciliationReport:
    """Compare per-entity record counts and grade the drift."""
    t = thresholds or ReconciliationThresholds()
    entities = sorted(set(connector_counts) | set(stored_counts))
    deltas: list[EntityDelta] = []
    worst: ReconciliationStatus = "ok"
    for entity in entities:
        connector_count = int(connector_counts.get(entity, 0))
        stored_count = int(stored_counts.get(entity, 0))
        status = _classify(connector_count, stored_count, t)
        deltas.append(EntityDelta(entity, connector_count, stored_count, status))
        if _SEVERITY[status] > _SEVERITY[worst]:
            worst = status
    flagged = [d for d in deltas if d.status != "ok"]
    if flagged:
        summary = "; ".join(
            f"{d.entity}: connector {d.connector_count} vs stored {d.stored_count}" for d in flagged
        )
    else:
        summary = f"all {len(deltas)} entities reconciled"
    report = ReconciliationReport(status=worst, deltas=deltas, summary=summary)
    log.info("reconciliation.done", status=worst, flagged=[d.entity for d in flagged])
    return report


__all__ = [
    "EntityDelta",
    "ReconciliationReport",
    "ReconciliationStatus",
    "ReconciliationThresholds",
    "reconcile",
]
