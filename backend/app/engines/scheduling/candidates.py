"""Which orders the rule-based scheduler considers, and in what order.

An order is a candidate when it is open, in a schedulable status, has a
priority result and is not blocked. Blocked orders are dropped (reason
``blocked``) unless ``config.schedule_blocked_orders`` is on *and* every real
blocker has a known ``resolves_at`` — then the order is released at that
instant. Two readiness states are handled structurally by the scheduler
rather than as blockers: ``WAITING_PREVIOUS_OPERATION`` (dependencies are
placed first and their completion becomes the release) and
``MACHINE_UNAVAILABLE`` (calendars and machine states already model downtime;
"no eligible machine at all" surfaces as ``no_eligible_machine`` at placement).

Processing order (spec Phases 9/10: human decisions first):

1. operations already in progress (the machine is physically busy with them),
2. order-locked orders in lock creation order,
3. ``forced_next`` orders,
4. everything else by priority score desc, due date asc, order id.

``SEQUENCE`` locks are then applied by re-assigning the listed orders to the
positions they occupy, in the locked order, so their relative sequence on the
machine is fixed without moving any other order.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.clock import ensure_utc
from app.domain.config import SchedulingConfig
from app.domain.enums import OperationStatus, ReadinessState
from app.domain.models import Order
from app.domain.results import Blocker, PriorityResult, UnscheduledItem
from app.domain.snapshot import PlanningSnapshot
from app.engines.constraints.readiness import ReadinessAssessment
from app.engines.scheduling.locks import LockIndex

#: Readiness states the scheduler resolves itself (see module docstring).
STRUCTURAL_STATES: frozenset[ReadinessState] = frozenset(
    {ReadinessState.WAITING_PREVIOUS_OPERATION, ReadinessState.MACHINE_UNAVAILABLE}
)

BUCKET_IN_PROGRESS = 0
BUCKET_LOCKED = 1
BUCKET_FORCED = 2
BUCKET_NORMAL = 3

_FAR_FUTURE = datetime.max.replace(tzinfo=UTC)  # sentinel for "no due date" (sorts last)


@dataclass(slots=True)
class OrderCandidate:
    order: Order
    priority: PriorityResult
    release: datetime  # earliest instant any operation may start
    bucket: int
    lock_key: tuple[datetime, str] | None = None
    in_progress: bool = False
    blocker_note: str | None = None

    @property
    def order_id(self) -> str:
        return self.order.order_id

    @property
    def forced_next(self) -> bool:
        return self.priority.forced_next

    @property
    def locked(self) -> bool:
        return self.bucket in (BUCKET_IN_PROGRESS, BUCKET_LOCKED)

    def sort_key(self) -> tuple[int, datetime, str, float, datetime, str]:
        lock_at, lock_id = self.lock_key if self.lock_key is not None else (_FAR_FUTURE, "")
        due = self.order.due_date
        return (
            self.bucket,
            lock_at,
            lock_id,
            -self.priority.score,
            ensure_utc(due) if due is not None else _FAR_FUTURE,
            self.order.order_id,
        )


@dataclass(slots=True)
class CandidateSelection:
    candidates: list[OrderCandidate]
    unscheduled: list[UnscheduledItem]


def _real_blockers(blockers: list[Blocker]) -> list[Blocker]:
    return [b for b in blockers if b.state not in STRUCTURAL_STATES]


def _has_in_progress_operation(snapshot: PlanningSnapshot, order_id: str) -> bool:
    return any(
        op.operation_status == OperationStatus.IN_PROGRESS and op.machine_id is not None
        for op in snapshot.pending_operations_for_order(order_id)
    )


def select_candidates(
    snapshot: PlanningSnapshot,
    priorities: Mapping[str, PriorityResult],
    config: SchedulingConfig,
    assessments: Mapping[str, ReadinessAssessment],
    now: datetime,
    locks: LockIndex,
) -> CandidateSelection:
    """Split the snapshot's open orders into ordered candidates and unscheduled items."""
    now = ensure_utc(now)
    candidates: list[OrderCandidate] = []
    unscheduled: list[UnscheduledItem] = []
    for order in snapshot.open_orders():
        order_id = order.order_id
        if not order.order_status.is_schedulable:
            unscheduled.append(
                UnscheduledItem(
                    order_id,
                    None,
                    "status_not_schedulable",
                    f"order status {order.order_status.value} is not schedulable",
                    assessments[order_id].state if order_id in assessments else None,
                )
            )
            continue
        priority = priorities.get(order_id)
        if priority is None:
            unscheduled.append(
                UnscheduledItem(order_id, None, "no_priority", "no priority result for this order")
            )
            continue
        release = now
        note: str | None = None
        assessment = assessments.get(order_id)
        readiness = assessment.state if assessment is not None else priority.readiness
        blockers = _real_blockers(assessment.blockers) if assessment is not None else []
        if blockers:
            messages = "; ".join(b.message for b in blockers)
            resolves = [b.resolves_at for b in blockers]
            if not config.schedule_blocked_orders or any(r is None for r in resolves):
                reason = f"blocked ({readiness.value}): {messages}"
                if config.schedule_blocked_orders:
                    reason += " (no known resolution time)"
                unscheduled.append(UnscheduledItem(order_id, None, "blocked", reason, readiness))
                continue
            release = max(ensure_utc(r) for r in resolves if r is not None)
            release = max(release, now)
            note = f"deferred until blockers resolve at {release.isoformat()} ({messages})"
        in_progress = _has_in_progress_operation(snapshot, order_id)
        lock_key = locks.lock_sort_key(order_id)
        if in_progress:
            bucket = BUCKET_IN_PROGRESS
        elif lock_key is not None:
            bucket = BUCKET_LOCKED
        elif priority.forced_next:
            bucket = BUCKET_FORCED
        else:
            bucket = BUCKET_NORMAL
        candidates.append(OrderCandidate(order, priority, release, bucket, lock_key, in_progress, note))
    candidates.sort(key=OrderCandidate.sort_key)
    apply_sequence_locks(candidates, locks)
    if config.max_orders_per_run is not None and len(candidates) > config.max_orders_per_run:
        for dropped in candidates[config.max_orders_per_run :]:
            unscheduled.append(
                UnscheduledItem(
                    dropped.order_id,
                    None,
                    "run_limit",
                    f"beyond max_orders_per_run ({config.max_orders_per_run})",
                    dropped.priority.readiness,
                )
            )
        del candidates[config.max_orders_per_run :]
    return CandidateSelection(candidates, unscheduled)


def apply_sequence_locks(candidates: list[OrderCandidate], locks: LockIndex) -> None:
    """Re-assign orders of each SEQUENCE lock to their current positions in the locked order."""
    if not locks.sequence_locks:
        return
    position = {c.order_id: i for i, c in enumerate(candidates)}
    for lock in locks.sequence_locks:
        listed = [oid for oid in lock.sequence_order_ids if oid in position]
        if len(listed) < 2:
            continue
        slots = sorted(position[oid] for oid in listed)
        items = [candidates[position[oid]] for oid in listed]
        for slot, item in zip(slots, items, strict=True):
            candidates[slot] = item
            position[item.order_id] = slot


__all__ = [
    "BUCKET_FORCED",
    "BUCKET_IN_PROGRESS",
    "BUCKET_LOCKED",
    "BUCKET_NORMAL",
    "STRUCTURAL_STATES",
    "CandidateSelection",
    "OrderCandidate",
    "apply_sequence_locks",
    "select_candidates",
]
