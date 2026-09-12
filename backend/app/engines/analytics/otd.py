"""On-time delivery (OTD): historical and projected, by tier, process and day.

* **Historical** OTD looks back ``window_days`` from ``now`` at delivered
  orders (completed / packed / shipped, never cancelled) whose actual
  completion is known: the latest ``actual_end`` of the order's operations,
  else an ``actual_completion_date`` / ``actual_end`` datetime the integration
  layer may have stored in ``Order.attributes``. On time = completed no later
  than the effective due date. Orders without a due date or without a
  completion timestamp are excluded and counted in ``notes``.
* **Projected** OTD looks at open orders the schedule placed completely (the
  same "fully scheduled" definition as ``scheduling.metrics``): on time when
  the last entry ends no later than the due date. Without a schedule the
  projection is empty (``projected_pct`` is ``None``).
* **Trend** buckets both by plant-local completion day so the dashboard can
  draw "delivered on time per day" for the past window and the horizon.

Percentages are ``None`` (not 0) when nothing can be measured.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, tzinfo

from app.core.clock import ensure_utc
from app.domain.models import Order
from app.domain.results import ScheduleResult
from app.domain.snapshot import PlanningSnapshot
from app.engines.analytics.common import (
    DELIVERED_STATUSES,
    entries_by_order,
    fully_scheduled_completion,
    label_for_process,
    local_date,
    plant_timezone,
)

ATTRIBUTE_COMPLETION_KEYS: tuple[str, ...] = ("actual_completion_date", "actual_end", "delivered_at")


@dataclass(slots=True)
class OtdBucket:
    key: str
    total: int = 0
    on_time: int = 0

    @property
    def late(self) -> int:
        return self.total - self.on_time

    @property
    def pct(self) -> float | None:
        return 100.0 * self.on_time / self.total if self.total else None

    def add(self, on_time: bool) -> None:
        self.total += 1
        if on_time:
            self.on_time += 1


@dataclass(slots=True)
class OtdTrendPoint:
    day: date
    historical: OtdBucket | None = None
    projected: OtdBucket | None = None


@dataclass(slots=True)
class OtdReport:
    as_of: datetime
    window_days: int
    historical: OtdBucket
    projected: OtdBucket
    historical_by_tier: dict[str, OtdBucket] = field(default_factory=dict)
    projected_by_tier: dict[str, OtdBucket] = field(default_factory=dict)
    historical_by_process: dict[str, OtdBucket] = field(default_factory=dict)
    projected_by_process: dict[str, OtdBucket] = field(default_factory=dict)
    trend: list[OtdTrendPoint] = field(default_factory=list)
    notes: dict[str, int] = field(default_factory=dict)

    @property
    def historical_pct(self) -> float | None:
        return self.historical.pct

    @property
    def projected_pct(self) -> float | None:
        return self.projected.pct

    def summary(self) -> str:
        hist = f"{self.historical_pct:.0f}%" if self.historical_pct is not None else "n/a"
        proj = f"{self.projected_pct:.0f}%" if self.projected_pct is not None else "n/a"
        return (
            f"On-time delivery: {hist} over the last {self.window_days} days "
            f"({self.historical.on_time}/{self.historical.total} delivered orders); "
            f"expected {proj} for scheduled open orders ({self.projected.on_time}/{self.projected.total})"
        )


def actual_completion(order: Order, snapshot: PlanningSnapshot) -> datetime | None:
    """Latest operation ``actual_end`` (only when every non-cancelled operation ended), else an attribute."""
    ends: list[datetime] = []
    incomplete = False
    for op in snapshot.operations_for_order(order.order_id):
        if op.operation_status.is_done and op.actual_end is None and op.completed_quantity <= 0:
            continue  # cancelled / empty step: no timestamp expected
        if op.actual_end is None:
            incomplete = True
            continue
        ends.append(ensure_utc(op.actual_end))
    if ends and not incomplete:
        return max(ends)
    for key in ATTRIBUTE_COMPLETION_KEYS:
        value = order.attributes.get(key)
        if isinstance(value, datetime):
            return ensure_utc(value)
    return max(ends) if ends else None


def _bucket(buckets: dict[str, OtdBucket], key: str) -> OtdBucket:
    bucket = buckets.get(key)
    if bucket is None:
        bucket = buckets[key] = OtdBucket(key)
    return bucket


def _tier_key(order: Order, snapshot: PlanningSnapshot) -> str:
    customer = snapshot.customers.get(order.customer_id)
    return customer.customer_tier.value if customer is not None else "unknown"


def _record(
    report: OtdReport,
    trend: dict[date, OtdTrendPoint],
    order: Order,
    snapshot: PlanningSnapshot,
    completion: datetime,
    tz: tzinfo,
    historical: bool,
) -> None:
    due = ensure_utc(order.due_date) if order.due_date is not None else None
    if due is None:
        return
    on_time = completion <= due
    day = local_date(completion, tz)
    point = trend.get(day)
    if point is None:
        point = trend[day] = OtdTrendPoint(day)
    tier, process = _tier_key(order, snapshot), label_for_process(order.process_type.value)
    if historical:
        report.historical.add(on_time)
        _bucket(report.historical_by_tier, tier).add(on_time)
        _bucket(report.historical_by_process, process).add(on_time)
        if point.historical is None:
            point.historical = OtdBucket(day.isoformat())
        point.historical.add(on_time)
    else:
        report.projected.add(on_time)
        _bucket(report.projected_by_tier, tier).add(on_time)
        _bucket(report.projected_by_process, process).add(on_time)
        if point.projected is None:
            point.projected = OtdBucket(day.isoformat())
        point.projected.add(on_time)


def on_time_delivery(
    snapshot: PlanningSnapshot,
    schedule: ScheduleResult | None,
    now: datetime,
    window_days: int,
    tz: tzinfo | None = None,
) -> OtdReport:
    """Historical (last ``window_days``) and projected OTD with tier / process / daily breakdowns."""
    now = ensure_utc(now)
    tz = tz if tz is not None else plant_timezone(snapshot)
    window_start = now - timedelta(days=max(0, window_days))
    report = OtdReport(
        as_of=now,
        window_days=window_days,
        historical=OtdBucket("historical"),
        projected=OtdBucket("projected"),
    )
    trend: dict[date, OtdTrendPoint] = {}
    notes: dict[str, int] = {
        "delivered_without_completion_date": 0,
        "delivered_without_due_date": 0,
        "outside_window": 0,
    }

    for order_id in sorted(snapshot.orders):
        order = snapshot.orders[order_id]
        if order.order_status not in DELIVERED_STATUSES:
            continue
        completion = actual_completion(order, snapshot)
        if completion is None:
            notes["delivered_without_completion_date"] += 1
            continue
        if order.due_date is None:
            notes["delivered_without_due_date"] += 1
            continue
        if not (window_start <= completion <= now):
            notes["outside_window"] += 1
            continue
        _record(report, trend, order, snapshot, completion, tz, historical=True)

    completions: Mapping[str, datetime] = fully_scheduled_completion(schedule, entries_by_order(schedule))
    notes["scheduled_without_due_date"] = 0
    for order_id in sorted(completions):
        open_order = snapshot.orders.get(order_id)
        if open_order is None or not open_order.is_open:
            continue
        if open_order.due_date is None:
            notes["scheduled_without_due_date"] += 1
            continue
        _record(report, trend, open_order, snapshot, ensure_utc(completions[order_id]), tz, historical=False)

    report.trend = [trend[d] for d in sorted(trend)]
    report.notes = notes
    return report


__all__ = [
    "ATTRIBUTE_COMPLETION_KEYS",
    "OtdBucket",
    "OtdReport",
    "OtdTrendPoint",
    "actual_completion",
    "on_time_delivery",
]
