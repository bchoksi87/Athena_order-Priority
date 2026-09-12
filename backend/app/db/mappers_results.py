"""ORM row <-> domain conversions for planner overlays and engine results.

Split from :mod:`app.db.mappers` (which re-exports everything here) purely to
keep module size in check; import from ``app.db.mappers``.
"""

from __future__ import annotations

from app.core.ids import new_id
from app.db.models import (
    ExpediteRow,
    PriorityOverrideRow,
    PriorityResultRow,
    ScheduleEntryRow,
    ScheduleLockRow,
)
from app.db.snapshot_codec import decode_dataclass, to_jsonable
from app.domain.enums import LockType, OverrideType, ReadinessState, RiskLevel
from app.domain.models import Expedite, PriorityOverride, ScheduleLock, TimeWindow
from app.domain.results import FactorScore, PriorityAdjustment, PriorityResult, ScheduleEntry

# ------------------------------------------------------------------- overlays


def lock_to_row(lock: ScheduleLock, row: ScheduleLockRow | None = None) -> ScheduleLockRow:
    row = row or ScheduleLockRow(lock_id=lock.lock_id)
    row.lock_type = lock.lock_type.value
    row.order_id = lock.order_id
    row.machine_id = lock.machine_id
    row.window_start = lock.window.start if lock.window else None
    row.window_end = lock.window.end if lock.window else None
    row.window_reason = lock.window.reason if lock.window else ""
    row.sequence_order_ids = list(lock.sequence_order_ids)
    row.reason = lock.reason
    row.created_by = lock.created_by
    row.lock_created_at = lock.created_at
    row.active = lock.active
    return row


def lock_from_row(row: ScheduleLockRow) -> ScheduleLock:
    window = None
    if row.window_start is not None and row.window_end is not None:
        window = TimeWindow(row.window_start, row.window_end, row.window_reason or "")
    return ScheduleLock(
        lock_id=row.lock_id,
        lock_type=LockType(row.lock_type),
        created_by=row.created_by,
        created_at=row.lock_created_at,
        reason=row.reason,
        order_id=row.order_id,
        machine_id=row.machine_id,
        window=window,
        sequence_order_ids=list(row.sequence_order_ids or []),
        active=row.active,
    )


def override_to_row(
    override: PriorityOverride, row: PriorityOverrideRow | None = None
) -> PriorityOverrideRow:
    row = row or PriorityOverrideRow(override_id=override.override_id)
    row.order_id = override.order_id
    row.override_type = override.override_type.value
    row.value = override.value
    row.target_machine_id = override.target_machine_id
    row.reason = override.reason
    row.created_by = override.created_by
    row.override_created_at = override.created_at
    row.expires_at = override.expires_at
    row.active = override.active
    return row


def override_from_row(row: PriorityOverrideRow) -> PriorityOverride:
    return PriorityOverride(
        override_id=row.override_id,
        order_id=row.order_id,
        override_type=OverrideType(row.override_type),
        created_by=row.created_by,
        created_at=row.override_created_at,
        reason=row.reason,
        value=row.value,
        target_machine_id=row.target_machine_id,
        expires_at=row.expires_at,
        active=row.active,
    )


def expedite_to_row(expedite: Expedite, row: ExpediteRow | None = None) -> ExpediteRow:
    row = row or ExpediteRow(expedite_id=expedite.expedite_id)
    row.order_id = expedite.order_id
    row.boost_points = expedite.boost_points
    row.starts_at = expedite.starts_at
    row.expires_at = expedite.expires_at
    row.reason = expedite.reason
    row.created_by = expedite.created_by
    row.expedite_created_at = expedite.created_at
    row.active = expedite.active
    return row


def expedite_from_row(row: ExpediteRow) -> Expedite:
    return Expedite(
        expedite_id=row.expedite_id,
        order_id=row.order_id,
        created_by=row.created_by,
        created_at=row.expedite_created_at,
        reason=row.reason,
        boost_points=row.boost_points,
        starts_at=row.starts_at,
        expires_at=row.expires_at,
        active=row.active,
    )


# -------------------------------------------------------------------- results


def priority_result_to_row(result: PriorityResult, run_id: str) -> PriorityResultRow:
    return PriorityResultRow(
        result_id=new_id("pr"),
        run_id=run_id,
        order_id=result.order_id,
        score=result.score,
        base_score=result.base_score,
        factors=[to_jsonable(f) for f in result.factors],
        adjustments=[to_jsonable(a) for a in result.adjustments],
        readiness=result.readiness.value,
        blocked=result.blocked,
        blocking_reasons=list(result.blocking_reasons),
        risk_level=result.risk_level.value,
        explanation=result.explanation,
        profile_id=result.profile_id,
        profile_version=result.profile_version,
        computed_at=result.computed_at,
        hours_until_due=result.hours_until_due,
        projected_completion=result.projected_completion,
        projected_lateness_hours=result.projected_lateness_hours,
        forced_next=result.forced_next,
        rank=result.rank,
    )


def priority_result_from_row(row: PriorityResultRow) -> PriorityResult:
    return PriorityResult(
        order_id=row.order_id,
        score=row.score,
        base_score=row.base_score,
        factors=[decode_dataclass(f, FactorScore) for f in row.factors or []],
        adjustments=[decode_dataclass(a, PriorityAdjustment) for a in row.adjustments or []],
        readiness=ReadinessState(row.readiness),
        blocked=row.blocked,
        blocking_reasons=list(row.blocking_reasons or []),
        risk_level=RiskLevel(row.risk_level),
        explanation=row.explanation,
        profile_id=row.profile_id,
        profile_version=row.profile_version,
        computed_at=row.computed_at,
        hours_until_due=row.hours_until_due,
        projected_completion=row.projected_completion,
        projected_lateness_hours=row.projected_lateness_hours,
        forced_next=row.forced_next,
        rank=row.rank,
    )


def schedule_entry_to_row(entry: ScheduleEntry, schedule_version_id: str) -> ScheduleEntryRow:
    return ScheduleEntryRow(
        row_id=new_id("se"),
        schedule_version_id=schedule_version_id,
        entry_id=entry.entry_id,
        machine_id=entry.machine_id,
        order_id=entry.order_id,
        operation_id=entry.operation_id,
        sequence_on_machine=entry.sequence_on_machine,
        setup_start=entry.setup_start,
        start=entry.start,
        end=entry.end,
        setup_minutes=entry.setup_minutes,
        run_minutes=entry.run_minutes,
        quantity=entry.quantity,
        priority_score=entry.priority_score,
        placement_reason=entry.placement_reason,
        is_last_operation=entry.is_last_operation,
        expected_completion=entry.expected_completion,
        due_date=entry.due_date,
        expected_lateness_hours=entry.expected_lateness_hours,
        locked=entry.locked,
        batch_key=entry.batch_key,
        setup_family=entry.setup_family,
        material_id=entry.material_id,
        customer_id=entry.customer_id,
    )


def schedule_entry_from_row(row: ScheduleEntryRow) -> ScheduleEntry:
    return ScheduleEntry(
        entry_id=row.entry_id,
        machine_id=row.machine_id,
        order_id=row.order_id,
        operation_id=row.operation_id,
        sequence_on_machine=row.sequence_on_machine,
        setup_start=row.setup_start,
        start=row.start,
        end=row.end,
        setup_minutes=row.setup_minutes,
        run_minutes=row.run_minutes,
        quantity=row.quantity,
        priority_score=row.priority_score,
        placement_reason=row.placement_reason,
        is_last_operation=row.is_last_operation,
        expected_completion=row.expected_completion,
        due_date=row.due_date,
        expected_lateness_hours=row.expected_lateness_hours,
        locked=row.locked,
        batch_key=row.batch_key,
        setup_family=row.setup_family,
        material_id=row.material_id,
        customer_id=row.customer_id,
    )


__all__ = [
    "expedite_from_row",
    "expedite_to_row",
    "lock_from_row",
    "lock_to_row",
    "override_from_row",
    "override_to_row",
    "priority_result_from_row",
    "priority_result_to_row",
    "schedule_entry_from_row",
    "schedule_entry_to_row",
]
