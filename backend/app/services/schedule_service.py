"""ScheduleService: generate, approve, publish, reject and read schedule versions.

``generate`` runs the :class:`PlanningPipeline` on the live snapshot (the
entries of the current plan feed the frozen window) and persists everything
the run produced in one unit of work (contract §10, spec Phases 6, 22, 36, 37):

* ``input_snapshots``   — the compressed snapshot the run saw;
* ``optimization_runs`` — run id, start/end, counts, objective/quality score,
  algorithm + version, profile/config versions, status, timings JSON;
* ``priority_results``  — one row per open order with rank, factors, explanation;
* ``schedule_versions`` — monotonic ``version_number``, status ``DRAFT``, who/when,
  metrics + quality JSON, ``analytics`` JSON (KPIs, capacity, bottlenecks, data
  quality) and lifecycle ``details``;
* ``schedule_entries``  — the machine-sequenced placements;
* ``data_quality_issues`` (replaced for the run) and ``alerts`` (upserted by
  dedupe key so acknowledgements survive);
* an ``audit_log`` row ``schedule.generated``.

The status machine is ``DRAFT → APPROVED → PUBLISHED → SUPERSEDED`` (plus
``REJECTED``); every transition carries a mandatory reason and an audit row.
Publishing goes through :class:`WritebackService` (mode from the settings);
the previously published/approved versions become ``SUPERSEDED``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog
from sqlalchemy.orm import Session

from app.core.clock import Clock, ensure_utc
from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError
from app.core.security import CurrentUser
from app.db.records import OptimizationRunRecord, ScheduleVersionInfo, SnapshotInfo
from app.db.repositories.alerts import AlertRepository
from app.db.repositories.data_quality import DataQualityRepository
from app.db.repositories.priority import PriorityResultRepository
from app.db.repositories.schedule import OptimizationRunRepository, ScheduleRepository
from app.db.repositories.snapshots import SnapshotRepository
from app.domain.enums import ScheduleStatus
from app.domain.results import ScheduleEntry
from app.engines.pipeline import PipelineResult, PlanningPipeline
from app.integration.writeback import WritebackReceipt
from app.services.analytics_service import ScheduleComparison, analytics_payload, compare_results
from app.services.audit_service import AuditService
from app.services.base import PagedResult, Pagination, Service, actor_id, require_reason
from app.services.snapshot_service import SnapshotService
from app.services.writeback_service import WritebackService, schedule_result_from_version

log = structlog.get_logger(__name__)

ENTITY_SCHEDULE_VERSION = "schedule_version"
RUN_KIND_SCHEDULE = "schedule"
RUN_KIND_REPLAN = "replan"
RUN_STATUS_COMPLETED = "completed"
RUN_STATUS_FAILED = "failed"
TRIGGER_MANUAL = "manual"
TRIGGER_REPLAN = "replan"
_MAX_LABEL = 255


@dataclass(slots=True)
class GenerationSummary:
    """What one ``generate`` call persisted (the pipeline result rides along for callers)."""

    version: ScheduleVersionInfo
    run: OptimizationRunRecord
    snapshot: SnapshotInfo
    previous_version: int | None
    priority_results: int
    entries: int
    unscheduled: int
    data_quality_issues: int
    alerts: int
    timings: dict[str, float]
    result: PipelineResult


@dataclass(slots=True)
class CurrentPlan:
    version: ScheduleVersionInfo | None

    @property
    def status(self) -> str:
        return self.version.status.value if self.version is not None else "none"


@dataclass(slots=True)
class VersionEntries:
    version: ScheduleVersionInfo
    page: PagedResult[ScheduleEntry]


@dataclass(slots=True)
class PublishOutcome:
    version: ScheduleVersionInfo
    receipt: WritebackReceipt
    superseded: int


@dataclass(slots=True)
class RunDetails:
    run: OptimizationRunRecord
    version: ScheduleVersionInfo | None
    snapshot: SnapshotInfo | None
    priority_results: int
    data_quality_issues: int


class ScheduleService(Service):
    def __init__(
        self,
        session: Session,
        clock: Clock,
        settings: Settings,
        pipeline: PlanningPipeline | None = None,
        *,
        audit: AuditService | None = None,
        snapshots: SnapshotService | None = None,
        writeback: WritebackService | None = None,
    ) -> None:
        super().__init__(session, clock)
        self._settings = settings
        self._pipeline = pipeline or PlanningPipeline(clock)
        self._audit = audit or AuditService(session, clock)
        self._snapshots = snapshots or SnapshotService(session, clock)
        self._writeback = writeback or WritebackService(session, clock, settings)
        self._versions = ScheduleRepository(session)
        self._runs = OptimizationRunRepository(session)
        self._priorities = PriorityResultRepository(session)
        self._snapshot_repo = SnapshotRepository(session)
        self._dq = DataQualityRepository(session)
        self._alerts = AlertRepository(session)

    @property
    def pipeline(self) -> PlanningPipeline:
        return self._pipeline

    # ------------------------------------------------------------- generate
    def generate(
        self, user: CurrentUser | str, note: str | None = None, trigger: str = TRIGGER_MANUAL
    ) -> GenerationSummary:
        """Run the planning pipeline on the live snapshot and persist a new DRAFT version."""
        now = self.now()
        user_id = actor_id(user)
        config = self._snapshots.active_config()
        config_info = self._snapshots.active_config_info()
        snapshot = self._snapshots.load_snapshot(now)
        current = self._versions.get_current()
        previous_entries = (
            self._versions.get_entries(schedule_version_id=current.schedule_version_id) if current else None
        )
        logger = log.bind(user_id=user_id, trigger=trigger)
        kind = RUN_KIND_REPLAN if trigger.startswith(TRIGGER_REPLAN) else RUN_KIND_SCHEDULE
        try:
            result = self._pipeline.run(snapshot, config, previous_entries)
        except Exception as exc:
            self._record_failed_run(kind, now, user_id, trigger, snapshot.summary(), exc)
            raise
        started = time.perf_counter()
        snapshot_info = self._snapshot_repo.save(snapshot, created_by=user_id)
        metrics = result.schedule.metrics
        quality = result.schedule.quality
        dq_summary = result.dq_report.summary()
        run = self._runs.start(
            OptimizationRunRecord(
                run_id=result.run_id,
                kind=kind,
                status=RUN_STATUS_COMPLETED,
                started_at=result.generated_at,
                finished_at=result.generated_at + timedelta(seconds=result.total_seconds),
                orders_considered=len(result.priorities),
                orders_scheduled=metrics.scheduled_orders,
                orders_blocked=sum(1 for r in result.priorities.values() if r.blocked),
                objective_score=quality.score if quality is not None else None,
                quality_score=quality.score if quality is not None else None,
                algorithm=result.scheduler_name,
                algorithm_version=result.scheduler_version,
                profile_id=result.profile_id,
                profile_version=result.profile_version,
                config_version=result.config_version,
                input_snapshot_id=snapshot_info.snapshot_id,
                triggered_by=user_id,
                trigger_reason=f"{trigger}: {note}" if note else trigger,
                metrics={
                    "objective": "quality_score",
                    "timings": {k: round(v, 6) for k, v in result.timings.items()},
                    "summary": result.summary(),
                    "data_quality": dq_summary,
                    "alerts": len(result.alerts),
                    "bottlenecks": len(result.bottlenecks),
                    "previous_version": current.version_number if current else None,
                    "previous_entries": len(previous_entries or ()),
                    "system_config_version": config_info.version if config_info else None,
                    "trigger": trigger,
                },
                warnings=list(result.schedule.warnings),
            )
        )
        stored_priorities = self._priorities.save_run(result.run_id, result.priorities.values())
        version = self._versions.create_version(
            result.schedule,
            generated_by=user_id,
            run_id=result.run_id,
            input_snapshot_id=snapshot_info.snapshot_id,
            status=ScheduleStatus.DRAFT,
            label=(note or "")[:_MAX_LABEL] or None,
            notes=note,
            analytics=analytics_payload(result, now),
            details={
                "trigger": trigger,
                "previous_version": current.version_number if current else None,
                "previous_status": current.status.value if current else None,
                "system_config_version": config_info.version if config_info else None,
                "note": note,
                "dq_excluded_orders": len(result.dq_excluded_order_ids),
            },
        )
        stored_issues = self._dq.replace_run(result.run_id, result.dq_report.issues, now)
        stored_alerts = self._alerts.upsert_many(result.alerts, now)
        self._audit.record(
            user,
            ENTITY_SCHEDULE_VERSION,
            version.schedule_version_id,
            "schedule.generated",
            {"version": current.version_number, "status": current.status.value} if current else None,
            {
                "version": version.version_number,
                "status": version.status.value,
                "run_id": result.run_id,
                "quality_score": quality.score if quality is not None else None,
                "entries": len(result.schedule.entries),
                "scheduled_orders": metrics.scheduled_orders,
                "unscheduled_orders": metrics.unscheduled_orders,
            },
            note or f"{trigger} schedule generation",
            {
                "version": version.version_number,
                "run_id": result.run_id,
                "trigger": trigger,
                "input_snapshot_id": snapshot_info.snapshot_id,
                "algorithm": f"{result.scheduler_name} {result.scheduler_version}",
            },
        )
        persist_seconds = time.perf_counter() - started
        timings = {
            **{k: round(v, 6) for k, v in result.timings.items()},
            "persist": round(persist_seconds, 6),
        }
        logger.info(
            "schedule.generated",
            run_id=result.run_id,
            schedule_version=version.version_number,
            entries=len(result.schedule.entries),
            quality_score=quality.score if quality is not None else None,
            alerts=len(stored_alerts),
            persist_seconds=round(persist_seconds, 3),
        )
        return GenerationSummary(
            version=version,
            run=run,
            snapshot=snapshot_info,
            previous_version=current.version_number if current else None,
            priority_results=stored_priorities,
            entries=len(result.schedule.entries),
            unscheduled=len(result.schedule.unscheduled),
            data_quality_issues=stored_issues,
            alerts=len(stored_alerts),
            timings=timings,
            result=result,
        )

    def _record_failed_run(
        self,
        kind: str,
        started_at: datetime,
        user_id: str,
        trigger: str,
        snapshot_summary: dict[str, int],
        exc: Exception,
    ) -> None:
        """Keep an auditable trace of a failed generation (committed on its own)."""
        from app.core.ids import new_id

        log.error(
            "schedule.generation_failed", error=str(exc), error_type=type(exc).__name__, trigger=trigger
        )
        try:
            self._session.rollback()
            self._runs.start(
                OptimizationRunRecord(
                    run_id=new_id("run"),
                    kind=kind,
                    status=RUN_STATUS_FAILED,
                    started_at=started_at,
                    finished_at=self.now(),
                    algorithm=self._pipeline.scheduler.name,
                    algorithm_version=self._pipeline.scheduler.version,
                    triggered_by=user_id,
                    trigger_reason=trigger,
                    error_message=f"{type(exc).__name__}: {exc}",
                    metrics={"snapshot": snapshot_summary},
                )
            )
            self._session.commit()
        except Exception as inner:  # never mask the original failure
            log.warning("schedule.failed_run_not_recorded", error=str(inner))
            self._session.rollback()

    # ------------------------------------------------------- state machine
    def approve(self, version_number: int, user: CurrentUser | str, reason: str) -> ScheduleVersionInfo:
        reason = require_reason(reason)
        version = self._versions.get_version(version_number)
        if version.status is not ScheduleStatus.DRAFT:
            raise ConflictError(
                f"schedule v{version_number} is {version.status.value}; only a draft can be approved",
                details={"version": version_number, "status": version.status.value},
            )
        now = self.now()
        superseded = self._supersede(ScheduleStatus.APPROVED, keep=version_number, at=now)
        updated = self._versions.set_status(
            version_number, ScheduleStatus.APPROVED, user_id=actor_id(user), at=now
        )
        self._audit.record(
            user,
            ENTITY_SCHEDULE_VERSION,
            updated.schedule_version_id,
            "schedule.approved",
            {"status": version.status.value},
            {
                "status": updated.status.value,
                "approved_by": updated.approved_by,
                "approved_at": updated.approved_at,
            },
            reason,
            {"version": version_number, "superseded_versions": superseded},
        )
        log.info("schedule.approved", schedule_version=version_number, user_id=actor_id(user))
        return updated

    def reject(self, version_number: int, user: CurrentUser | str, reason: str) -> ScheduleVersionInfo:
        reason = require_reason(reason)
        version = self._versions.get_version(version_number)
        if version.status not in (ScheduleStatus.DRAFT, ScheduleStatus.APPROVED):
            raise ConflictError(
                f"schedule v{version_number} is {version.status.value}; only a draft or approved version "
                "can be rejected",
                details={"version": version_number, "status": version.status.value},
            )
        updated = self._versions.set_status(
            version_number, ScheduleStatus.REJECTED, user_id=actor_id(user), at=self.now()
        )
        self._versions.update_details(
            version_number, {"rejected_by": actor_id(user), "rejection_reason": reason}
        )
        self._audit.record(
            user,
            ENTITY_SCHEDULE_VERSION,
            updated.schedule_version_id,
            "schedule.rejected",
            {"status": version.status.value},
            {"status": updated.status.value},
            reason,
            {"version": version_number},
        )
        log.info("schedule.rejected", schedule_version=version_number, user_id=actor_id(user))
        return self._versions.get_version(version_number)

    def publish(
        self, version_number: int, user: CurrentUser | str, reason: str, *, auto: bool = False
    ) -> PublishOutcome:
        """APPROVED → PUBLISHED through the writeback gateway; other live versions are superseded."""
        reason = require_reason(reason)
        version = self._versions.get_version(version_number)
        if version.status is not ScheduleStatus.APPROVED:
            hint = "approve it first" if version.status is ScheduleStatus.DRAFT else "it cannot be published"
            raise ConflictError(
                f"schedule v{version_number} is {version.status.value}; {hint}",
                details={"version": version_number, "status": version.status.value},
            )
        entries = self._versions.get_entries(schedule_version_id=version.schedule_version_id)
        receipt = self._writeback.publish(version, entries, user, auto=auto)
        now = self.now()
        superseded = self._versions.supersede_others(version_number, now)
        updated = self._versions.set_status(
            version_number, ScheduleStatus.PUBLISHED, user_id=actor_id(user), at=now
        )
        self._audit.record(
            user,
            ENTITY_SCHEDULE_VERSION,
            updated.schedule_version_id,
            "schedule.published",
            {"status": version.status.value},
            {
                "status": updated.status.value,
                "published_by": updated.published_by,
                "receipt": receipt.to_dict(),
            },
            reason,
            {
                "version": version_number,
                "writeback_mode": self._writeback.mode.value,
                "receipt_status": receipt.status,
                "superseded": superseded,
                "auto": auto,
            },
        )
        log.info(
            "schedule.published",
            schedule_version=version_number,
            user_id=actor_id(user),
            receipt_status=receipt.status,
            superseded=superseded,
        )
        return PublishOutcome(
            version=self._versions.get_version(version_number), receipt=receipt, superseded=superseded
        )

    def _supersede(self, status: ScheduleStatus, *, keep: int, at: datetime) -> list[int]:
        page = self._versions.list_versions(status=status, limit=1000)
        superseded: list[int] = []
        for info in page.items:
            if info.version_number == keep:
                continue
            self._versions.set_status(info.version_number, ScheduleStatus.SUPERSEDED, user_id=None, at=at)
            superseded.append(info.version_number)
        return superseded

    # ----------------------------------------------------------------- reads
    def current(self) -> CurrentPlan:
        return CurrentPlan(self._versions.get_current())

    def get_version(self, version_number: int) -> ScheduleVersionInfo:
        return self._versions.get_version(version_number)

    def list_versions(
        self, pagination: Pagination, status: ScheduleStatus | None = None
    ) -> PagedResult[ScheduleVersionInfo]:
        page = self._versions.list_versions(status=status, offset=pagination.offset, limit=pagination.limit)
        return PagedResult(
            items=list(page.items), total=page.total, page=pagination.page, page_size=pagination.page_size
        )

    def resolve_version(self, version_number: int | None) -> ScheduleVersionInfo:
        """``version_number`` or the current plan; :class:`NotFoundError` when there is none."""
        if version_number is not None:
            return self._versions.get_version(version_number)
        current = self._versions.get_current()
        if current is None:
            raise NotFoundError("no schedule version exists yet; generate one first")
        return current

    def entries(
        self,
        pagination: Pagination,
        *,
        version_number: int | None = None,
        machine_ids: list[str] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        order_id: str | None = None,
        customer_id: str | None = None,
    ) -> VersionEntries:
        version = self.resolve_version(version_number)
        items = self.entries_of(
            version, machine_ids=machine_ids, start=start, end=end, order_id=order_id, customer_id=customer_id
        )
        return VersionEntries(version=version, page=PagedResult.slice(items, pagination))

    def entries_of(
        self,
        version: ScheduleVersionInfo,
        *,
        machine_ids: list[str] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        order_id: str | None = None,
        customer_id: str | None = None,
    ) -> list[ScheduleEntry]:
        """Filtered entries of ``version`` sorted by machine, setup start and sequence."""
        window_start = ensure_utc(start) if start is not None else None
        window_end = ensure_utc(end) if end is not None else None
        overlapping = (window_start, window_end) if window_start and window_end else None
        entries = self._versions.get_entries(
            schedule_version_id=version.schedule_version_id,
            order_id=order_id,
            start_from=window_start if overlapping is None else None,
            start_to=window_end if overlapping is None else None,
            overlapping=overlapping,
        )
        if machine_ids:
            wanted = set(machine_ids)
            entries = [e for e in entries if e.machine_id in wanted]
        if customer_id:
            entries = [e for e in entries if e.customer_id == customer_id]
        entries.sort(key=lambda e: (e.machine_id, e.setup_start, e.sequence_on_machine))
        return entries

    def compare(self, a: int, b: int) -> ScheduleComparison:
        """Spec Phase 36 "current vs optimized" view: metric pairs plus what moved."""
        info_a, info_b = self._versions.get_version(a), self._versions.get_version(b)
        result_a = schedule_result_from_version(
            info_a, self._versions.get_entries(schedule_version_id=info_a.schedule_version_id)
        )
        result_b = schedule_result_from_version(
            info_b, self._versions.get_entries(schedule_version_id=info_b.schedule_version_id)
        )
        return compare_results(self._snapshots.active_config(), info_a, result_a, info_b, result_b)

    def run_details(self, run_id: str) -> RunDetails:
        run = self._runs.get(run_id)
        version = self._versions.get_by_run_id(run_id)
        snapshot = None
        if run.input_snapshot_id:
            try:
                snapshot = self._snapshot_repo.get_info(run.input_snapshot_id)
            except NotFoundError:
                snapshot = None
        return RunDetails(
            run=run,
            version=version,
            snapshot=snapshot,
            priority_results=self._priorities.count_for_run(run_id),
            data_quality_issues=self._dq.count(run_id),
        )


__all__ = [
    "ENTITY_SCHEDULE_VERSION",
    "RUN_KIND_REPLAN",
    "RUN_KIND_SCHEDULE",
    "TRIGGER_MANUAL",
    "TRIGGER_REPLAN",
    "CurrentPlan",
    "GenerationSummary",
    "PublishOutcome",
    "RunDetails",
    "ScheduleComparison",
    "ScheduleService",
    "VersionEntries",
]
