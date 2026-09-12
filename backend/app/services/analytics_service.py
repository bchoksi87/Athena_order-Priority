"""AnalyticsService: KPIs, capacity, bottlenecks, OTD and schedule quality (spec Phases 8, 12, 13, 36).

Numbers come from two places and the response says which:

* **stored** — the ``analytics`` JSON persisted with the active plan by
  :class:`~app.services.schedule_service.ScheduleService.generate`. It is used
  when it is *fresh*: computed on the same (UTC) day and no ERP sync completed
  after the version was generated;
* **live** — otherwise the analytics engines are re-run on the live snapshot
  with the active plan's stored entries (priorities are re-evaluated by the
  priority engine so new orders are covered). The live context is built once
  per service instance (one request / one job) and reused by every view.

Capacity and OTD are always live: their parameters (dimension, period,
horizon, window) vary per call and the engines are cheap. ``refresh_alerts``
is the alert sweep used by the worker: evaluate every alert rule on the live
context, upsert by dedupe key (acknowledgements survive) and resolve alerts
that are no longer raised — except "plan awaiting approval" alerts whose
candidate version is still a draft.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy.orm import Session

from app.core.clock import Clock, ensure_utc
from app.db.records import DataQualityIssueRecord, ScheduleVersionInfo
from app.db.repositories.alerts import AlertRepository
from app.db.repositories.data_quality import DataQualityRepository
from app.db.repositories.schedule import ScheduleRepository
from app.db.repositories.sync_runs import SyncRunRepository
from app.db.snapshot_codec import decode_dataclass, to_jsonable
from app.domain.config import SystemConfig
from app.domain.enums import AlertType, DataQualityCode, DataQualitySeverity, ScheduleStatus
from app.domain.results import (
    Bottleneck,
    DataQualityIssue,
    ExecutiveKpis,
    PriorityResult,
    ScheduleMetrics,
    ScheduleQuality,
    ScheduleResult,
)
from app.domain.snapshot import PlanningSnapshot
from app.engines.analytics import (
    CapacityReport,
    OtdReport,
    compute_capacity,
    compute_executive_kpis,
    evaluate_alerts,
    find_bottlenecks,
    on_time_delivery,
)
from app.engines.analytics.capacity import PERIODS, Period
from app.engines.analytics.common import DIMENSIONS, Dimension
from app.engines.calendar.builder import build_calendars
from app.engines.calendar.calendar import MachineCalendar
from app.engines.constraints.registry import default_constraint_engine
from app.engines.pipeline import PipelineResult
from app.engines.priority.context import PriorityContextBuilder
from app.engines.priority.engine import PriorityEngine
from app.engines.priority.registry import default_factors
from app.engines.replanning.engine import ReplanningEngine
from app.services.base import Service
from app.services.snapshot_service import SnapshotService
from app.services.writeback_service import schedule_result_from_version

log = structlog.get_logger(__name__)

SOURCE_STORED = "stored"
SOURCE_LIVE = "live"
DEFAULT_OTD_WINDOW_DAYS = 30
MAX_OTD_WINDOW_DAYS = 365
MAX_HORIZON_DAYS = 120
#: ``details`` key of alerts that guard a replanning candidate awaiting approval.
PENDING_VERSION_KEY = "pending_version"


# ------------------------------------------------------------------ views


@dataclass(slots=True)
class LiveContext:
    now: datetime
    snapshot: PlanningSnapshot
    config: SystemConfig
    calendars: Mapping[str, MachineCalendar]
    priorities: dict[str, PriorityResult]
    version: ScheduleVersionInfo | None
    schedule: ScheduleResult | None


@dataclass(slots=True)
class KpiView:
    kpis: ExecutiveKpis
    source: str
    as_of: datetime
    version: ScheduleVersionInfo | None


@dataclass(slots=True)
class BottleneckView:
    items: list[Bottleneck]
    source: str
    as_of: datetime
    version: ScheduleVersionInfo | None


@dataclass(slots=True)
class ScheduleComparison:
    a: ScheduleVersionInfo
    b: ScheduleVersionInfo
    metrics: dict[str, dict[str, Any]]
    quality: dict[str, Any]
    changes: dict[str, Any]
    summary: str

    @property
    def moved_orders(self) -> int:
        return len(self.changes.get("changed_orders", []))


@dataclass(slots=True)
class ScheduleQualityView:
    version: ScheduleVersionInfo | None
    quality: ScheduleQuality | None
    metrics: ScheduleMetrics | None
    draft: ScheduleVersionInfo | None
    comparison: ScheduleComparison | None


@dataclass(slots=True)
class AlertRefresh:
    as_of: datetime
    evaluated: int
    upserted: int
    resolved: int
    by_severity: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------- service


class AnalyticsService(Service):
    def __init__(self, session: Session, clock: Clock, snapshots: SnapshotService | None = None) -> None:
        super().__init__(session, clock)
        self._snapshots = snapshots or SnapshotService(session, clock)
        self._versions = ScheduleRepository(session)
        self._sync_runs = SyncRunRepository(session)
        self._dq = DataQualityRepository(session)
        self._alerts = AlertRepository(session)
        self._ctx: LiveContext | None = None
        self._active: ScheduleVersionInfo | None = None
        self._active_loaded = False

    # --------------------------------------------------------------- shared
    def active_version(self) -> ScheduleVersionInfo | None:
        if not self._active_loaded:
            self._active = self._versions.get_current()
            self._active_loaded = True
        return self._active

    def is_fresh(self, version: ScheduleVersionInfo | None) -> bool:
        """Stored analytics are fresh on the day they were computed unless a sync came later."""
        if version is None or not version.analytics:
            return False
        raw = version.analytics.get("as_of")
        if not isinstance(raw, str):
            return False
        try:
            as_of = ensure_utc(datetime.fromisoformat(raw))
        except ValueError:
            return False
        now = self.now()
        if as_of.date() != now.date():
            return False
        latest = self._sync_runs.latest(status="completed")
        if latest is not None and latest.finished_at is not None:
            return ensure_utc(latest.finished_at) <= ensure_utc(version.generated_at)
        return True

    def live(self) -> LiveContext:
        """Snapshot + config + calendars + live priorities + the active plan's entries (built once)."""
        if self._ctx is None:
            now = self.now()
            config = self._snapshots.active_config()
            snapshot = self._snapshots.load_snapshot(now)
            calendars = build_calendars(snapshot)
            priorities = evaluate_priorities(snapshot, config, calendars, self._clock, now)
            version = self.active_version()
            schedule = None
            if version is not None:
                entries = self._versions.get_entries(schedule_version_id=version.schedule_version_id)
                schedule = schedule_result_from_version(version, entries)
            self._ctx = LiveContext(now, snapshot, config, calendars, priorities, version, schedule)
            log.debug(
                "analytics.live_context",
                orders=len(snapshot.orders),
                version=version and version.version_number,
            )
        return self._ctx

    # ----------------------------------------------------------------- views
    def kpis(self) -> KpiView:
        version = self.active_version()
        if version is not None and self.is_fresh(version) and version.analytics:
            stored = version.analytics.get("kpis")
            if isinstance(stored, dict):
                kpis = decode_dataclass(stored, ExecutiveKpis)
                return KpiView(kpis, SOURCE_STORED, kpis.as_of, version)
        ctx = self.live()
        kpis = compute_executive_kpis(
            ctx.snapshot, ctx.priorities, ctx.schedule, ctx.now, ctx.config.scheduling, ctx.calendars
        )
        return KpiView(kpis, SOURCE_LIVE, ctx.now, version)

    def capacity(
        self,
        dimension: Dimension = "machine_group",
        period: Period = "week",
        horizon_days: int | None = None,
    ) -> CapacityReport:
        from app.core.errors import ValidationError

        if dimension not in DIMENSIONS:
            raise ValidationError(
                f"dimension must be one of {', '.join(DIMENSIONS)}", details={"dimension": dimension}
            )
        if period not in PERIODS:
            raise ValidationError(f"period must be one of {', '.join(PERIODS)}", details={"period": period})
        ctx = self.live()
        horizon = horizon_days if horizon_days is not None else ctx.config.scheduling.horizon_days
        if horizon < 1 or horizon > MAX_HORIZON_DAYS:
            raise ValidationError(
                f"horizon_days must be in 1..{MAX_HORIZON_DAYS}", details={"horizon_days": horizon}
            )
        return compute_capacity(
            ctx.snapshot, ctx.schedule, ctx.calendars, ctx.now, horizon, dimension, period
        )

    def bottlenecks(self) -> BottleneckView:
        version = self.active_version()
        if version is not None and self.is_fresh(version) and version.analytics:
            stored = version.analytics.get("bottlenecks")
            raw_as_of = version.analytics.get("as_of")
            if isinstance(stored, list) and isinstance(raw_as_of, str):
                items = [decode_dataclass(b, Bottleneck) for b in stored]
                return BottleneckView(
                    items, SOURCE_STORED, ensure_utc(datetime.fromisoformat(raw_as_of)), version
                )
        ctx = self.live()
        items = find_bottlenecks(
            ctx.snapshot,
            ctx.priorities,
            ctx.schedule,
            ctx.calendars,
            ctx.now,
            ctx.config.scheduling,
            ctx.config.alerts,
        )
        return BottleneckView(items, SOURCE_LIVE, ctx.now, version)

    def on_time_delivery(self, window_days: int = DEFAULT_OTD_WINDOW_DAYS) -> OtdReport:
        from app.core.errors import ValidationError

        if window_days < 1 or window_days > MAX_OTD_WINDOW_DAYS:
            raise ValidationError(
                f"window_days must be in 1..{MAX_OTD_WINDOW_DAYS}", details={"window_days": window_days}
            )
        ctx = self.live()
        # Historical OTD needs the delivered orders, which the planning snapshot leaves out.
        snapshot = self._snapshots.load_snapshot(ctx.now, include_closed_orders=True)
        return on_time_delivery(snapshot, ctx.schedule, ctx.now, window_days)

    def schedule_quality(self) -> ScheduleQualityView:
        version = self.active_version()
        if version is None:
            return ScheduleQualityView(None, None, None, None, None)
        quality = decode_dataclass(version.quality, ScheduleQuality) if version.quality else None
        metrics = decode_dataclass(version.metrics, ScheduleMetrics) if version.metrics else None
        draft = self._versions.get_latest(ScheduleStatus.DRAFT)
        comparison = None
        if draft is not None and draft.version_number != version.version_number:
            comparison = self.compare_versions(version, draft)
        return ScheduleQualityView(version, quality, metrics, draft, comparison)

    def compare_versions(self, a: ScheduleVersionInfo, b: ScheduleVersionInfo) -> ScheduleComparison:
        """Spec Phase 36 "current vs optimized": metric pairs plus what moved between two versions."""
        config = self._snapshots.active_config()
        result_a = schedule_result_from_version(
            a, self._versions.get_entries(schedule_version_id=a.schedule_version_id)
        )
        result_b = schedule_result_from_version(
            b, self._versions.get_entries(schedule_version_id=b.schedule_version_id)
        )
        return compare_results(config, a, result_a, b, result_b)

    # ---------------------------------------------------------------- alerts
    def refresh_alerts(self) -> AlertRefresh:
        """Evaluate every alert rule on the live context, upsert and sweep (see module docstring)."""
        ctx = self.live()
        cfg = ctx.config
        capacity = compute_capacity(
            ctx.snapshot,
            ctx.schedule,
            ctx.calendars,
            ctx.now,
            cfg.scheduling.horizon_days,
            "machine_group",
            "week",
        )
        bottlenecks = find_bottlenecks(
            ctx.snapshot, ctx.priorities, ctx.schedule, ctx.calendars, ctx.now, cfg.scheduling, cfg.alerts
        )
        alerts = evaluate_alerts(
            ctx.snapshot,
            ctx.priorities,
            ctx.schedule,
            bottlenecks,
            self._latest_issues(),
            ctx.now,
            cfg.alerts,
            scheduling_config=cfg.scheduling,
            capacity=capacity,
            profile=cfg.priority_profile,
        )
        records = self._alerts.upsert_many(alerts, ctx.now)
        keep = {r.dedupe_key for r in records} | self._protected_alert_keys()
        resolved = self._alerts.resolve_missing(keep, ctx.now)
        by_severity = Counter(a.severity.value for a in alerts)
        log.info("alerts.refreshed", evaluated=len(alerts), resolved=resolved, by_severity=dict(by_severity))
        return AlertRefresh(ctx.now, len(alerts), len(records), resolved, dict(sorted(by_severity.items())))

    def _latest_issues(self) -> list[DataQualityIssue]:
        run_id = self._dq.latest_run_id()
        if run_id is None:
            return []
        issues: list[DataQualityIssue] = []
        offset = 0
        while True:
            page = self._dq.list_issues(run_id, offset=offset, limit=1000)
            issues.extend(_issue_from_record(r) for r in page.items)
            offset += len(page.items)
            if not page.has_more or not page.items:
                return issues

    def _protected_alert_keys(self) -> set[str]:
        """Dedupe keys of 'plan awaiting approval' alerts whose candidate is still a draft."""
        keys: set[str] = set()
        page = self._alerts.list_active(alert_type=AlertType.SCHEDULE_DISRUPTION, limit=1000)
        for record in page.items:
            pending = record.details.get(PENDING_VERSION_KEY)
            if not isinstance(pending, int):
                continue
            try:
                info = self._versions.get_version(pending)
            except Exception:  # version gone: let the sweep resolve the alert
                continue
            if info.status is ScheduleStatus.DRAFT:
                keys.add(record.dedupe_key)
        return keys


# ---------------------------------------------------------------- helpers


def _issue_from_record(record: DataQualityIssueRecord) -> DataQualityIssue:
    return DataQualityIssue(
        code=DataQualityCode(record.code),
        severity=DataQualitySeverity(record.severity),
        entity_type=record.entity_type,
        entity_id=record.entity_id,
        message=record.message,
        field_name=record.field_name,
        recommendation=record.recommendation,
        details=dict(record.details),
    )


def evaluate_priorities(
    snapshot: PlanningSnapshot,
    config: SystemConfig,
    calendars: Mapping[str, MachineCalendar],
    clock: Clock,
    now: datetime,
) -> dict[str, PriorityResult]:
    """Priority results for every open order, the way the pipeline computes them."""
    constraints = default_constraint_engine(config.scheduling)
    builder = PriorityContextBuilder(constraint_engine=constraints, calendars=calendars, clock=clock)
    ctx = builder.build(snapshot, config.priority_profile, now=now)
    return PriorityEngine(default_factors(), clock).evaluate(snapshot, config.priority_profile, ctx=ctx)


def compare_results(
    config: SystemConfig,
    a: ScheduleVersionInfo,
    result_a: ScheduleResult,
    b: ScheduleVersionInfo,
    result_b: ScheduleResult,
) -> ScheduleComparison:
    engine = ReplanningEngine(config.replanning, config.scheduling.stability)
    out = engine.compare(result_a, result_b)
    changes = out.pop("changes")
    summary = str(out.pop("summary"))
    quality = out.pop("quality_score")
    return ScheduleComparison(
        a=a, b=b, metrics=to_jsonable(out), quality=to_jsonable(quality), changes=changes, summary=summary
    )


def capacity_report_to_dict(report: CapacityReport) -> dict[str, Any]:
    """JSON view of a :class:`CapacityReport` including the derived gap / utilisation numbers."""
    return {
        "dimension": report.dimension,
        "period": report.period,
        "horizon_start": report.horizon_start.isoformat(),
        "horizon_end": report.horizon_end.isoformat(),
        "rows": [
            {
                "key": r.key,
                "period_start": r.period_start.isoformat(),
                "period_end": r.period_end.isoformat(),
                "required_hours": r.required_hours,
                "available_hours": r.available_hours,
                "gap_hours": r.gap_hours,
                "utilization_pct": r.utilization_pct,
            }
            for r in report.rows
        ],
        "totals": [
            {
                "key": t.key,
                "required_hours": t.required_hours,
                "available_hours": t.available_hours,
                "gap_hours": t.gap_hours,
                "utilization_pct": t.utilization_pct,
                "scheduled_hours": t.scheduled_hours,
                "estimated_hours": t.estimated_hours,
                "shortfall_hours": t.shortfall_hours,
            }
            for t in report.totals
        ],
        "total_required_hours": report.total_required_hours,
        "total_available_hours": report.total_available_hours,
        "gap_hours": report.gap_hours,
        "utilization_pct": report.utilization_pct,
        "unallocated_hours": report.unallocated_hours,
        "unallocated_operations": report.unallocated_operations,
        "estimated_hours": report.estimated_hours,
        "scheduled_hours": report.scheduled_hours,
        "notes": list(report.notes),
    }


def analytics_payload(result: PipelineResult, now: datetime) -> dict[str, Any]:
    """The ``schedule_versions.analytics`` JSON: KPIs, capacity, bottlenecks, data quality, alert counts."""
    by_severity = Counter(a.severity.value for a in result.alerts)
    return {
        "as_of": ensure_utc(now).isoformat(),
        "run_id": result.run_id,
        "kpis": to_jsonable(result.kpis),
        "capacity": capacity_report_to_dict(result.capacity),
        "bottlenecks": [to_jsonable(b) for b in result.bottlenecks],
        "data_quality": result.dq_report.summary(),
        "alerts": {"total": len(result.alerts), "by_severity": dict(sorted(by_severity.items()))},
        "timings": {k: round(v, 6) for k, v in result.timings.items()},
    }


__all__ = [
    "DEFAULT_OTD_WINDOW_DAYS",
    "MAX_HORIZON_DAYS",
    "MAX_OTD_WINDOW_DAYS",
    "PENDING_VERSION_KEY",
    "SOURCE_LIVE",
    "SOURCE_STORED",
    "AlertRefresh",
    "AnalyticsService",
    "BottleneckView",
    "KpiView",
    "LiveContext",
    "ScheduleComparison",
    "ScheduleQualityView",
    "analytics_payload",
    "capacity_report_to_dict",
    "compare_results",
    "evaluate_priorities",
]
