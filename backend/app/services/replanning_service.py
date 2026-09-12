"""ReplanningService: continuous replanning decisions (spec Phase 11, Phase 26).

``evaluate(trigger)`` compares the live snapshot with the input snapshot stored
with the active plan (:func:`detect_events`), asks the
:class:`ReplanningEngine` whether the events warrant a replan and, if so:

1. generates a *candidate* DRAFT through :class:`ScheduleService.generate`
   (the active plan's entries feed the frozen window);
2. evaluates the candidate against the active plan (stability rules, hard
   events, improvement threshold) and stores the decision on the candidate's
   optimization run (``metrics["replan"]``) and version (``details["replan"]``)
   plus an audit row ``replan.decision``;
3. acts on the decision:

   * **no replan** → the candidate is *rejected* with the engine's reason so
     the shop floor is never reshuffled unnecessarily;
   * **replan, approval required** (``ReplanningConfig.require_approval`` or a
     frozen-window / move-cap / significant-change guard) → the candidate stays
     a DRAFT, older pending replanning drafts are superseded and a
     ``SCHEDULE_DISRUPTION`` alert "new plan awaiting approval" carries the
     old-vs-new comparison;
   * **replan, no approval required** → auto-approve; publish as well only when
     ``settings.writeback_mode`` is ``CONTROLLED_AUTO``.

The outcome carries the events, the decision and the old-vs-new comparison.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.config import Settings
from app.core.errors import NotFoundError
from app.core.security import CurrentUser
from app.db.records import AlertRecord, ScheduleVersionInfo
from app.db.repositories.alerts import AlertRepository
from app.db.repositories.schedule import OptimizationRunRepository, ScheduleRepository
from app.db.repositories.snapshots import SnapshotRepository
from app.domain.enums import AlertSeverity, AlertType, ReplanTriggerType, ScheduleStatus, WritebackMode
from app.domain.results import Alert, ReplanDecision
from app.domain.snapshot import PlanningSnapshot
from app.engines.replanning import ReplanEvent, ReplanningEngine, detect_events, manual_event
from app.services.analytics_service import PENDING_VERSION_KEY, ScheduleComparison, compare_results
from app.services.audit_service import AuditService
from app.services.base import Service, actor_id
from app.services.schedule_service import (
    ENTITY_SCHEDULE_VERSION,
    TRIGGER_REPLAN,
    GenerationSummary,
    ScheduleService,
)
from app.services.snapshot_service import SnapshotService
from app.services.writeback_service import schedule_result_from_version

log = structlog.get_logger(__name__)

SYSTEM_USER = "system"
ACTION_NOT_TRIGGERED = "not_triggered"
ACTION_REJECTED = "rejected"
ACTION_AWAITING_APPROVAL = "awaiting_approval"
ACTION_APPROVED = "approved"
ACTION_PUBLISHED = "published"
_MAX_EVENTS_STORED = 200


@dataclass(slots=True)
class ReplanOutcome:
    trigger: ReplanTriggerType
    evaluated_at: datetime
    triggered: bool
    action: str
    reason: str
    events: list[ReplanEvent] = field(default_factory=list)
    decision: ReplanDecision | None = None
    active_version: ScheduleVersionInfo | None = None
    candidate_version: ScheduleVersionInfo | None = None
    comparison: ScheduleComparison | None = None
    alert: AlertRecord | None = None
    generation: GenerationSummary | None = None

    @property
    def event_types(self) -> list[str]:
        return sorted({e.type.value for e in self.events})


class ReplanningService(Service):
    def __init__(
        self,
        session: Session,
        clock: Clock,
        settings: Settings,
        schedules: ScheduleService | None = None,
        *,
        audit: AuditService | None = None,
        snapshots: SnapshotService | None = None,
    ) -> None:
        super().__init__(session, clock)
        self._settings = settings
        self._audit = audit or AuditService(session, clock)
        self._snapshots = snapshots or SnapshotService(session, clock)
        self._schedules = schedules or ScheduleService(
            session, clock, settings, audit=self._audit, snapshots=self._snapshots
        )
        self._versions = ScheduleRepository(session)
        self._runs = OptimizationRunRepository(session)
        self._snapshot_repo = SnapshotRepository(session)
        self._alerts = AlertRepository(session)

    # ------------------------------------------------------------- evaluate
    def evaluate(
        self, trigger: ReplanTriggerType, user: CurrentUser | str = SYSTEM_USER, reason: str | None = None
    ) -> ReplanOutcome:
        now = self.now()
        config = self._snapshots.active_config()
        engine = ReplanningEngine(config.replanning, config.scheduling.stability)
        active = self._versions.get_current()
        current_snapshot = self._snapshots.load_snapshot(now)
        events = self._events(active, current_snapshot, config.alerts)
        events.append(
            manual_event(
                now,
                reason or f"{trigger.value} replanning requested by {actor_id(user)}",
                type=trigger,
                entity_id=actor_id(user),
            )
        )
        logger = log.bind(trigger=trigger.value, active_version=active.version_number if active else None)
        if not engine.should_trigger(events):
            why = "replanning disabled" if not config.replanning.enabled else "no triggering events"
            logger.info("replan.not_triggered", reason=why, events=len(events))
            return ReplanOutcome(
                trigger, now, False, ACTION_NOT_TRIGGERED, why, events, active_version=active
            )

        generation = self._schedules.generate(
            user, note=f"{TRIGGER_REPLAN}: {trigger.value}", trigger=f"{TRIGGER_REPLAN}:{trigger.value}"
        )
        candidate = generation.version
        current_schedule = None
        if active is not None:
            current_schedule = schedule_result_from_version(
                active, self._versions.get_entries(schedule_version_id=active.schedule_version_id)
            )
        decision = engine.evaluate(
            events, current_schedule, generation.result.schedule, now, snapshot=current_snapshot
        )
        comparison = (
            compare_results(config, active, current_schedule, candidate, generation.result.schedule)
            if active is not None and current_schedule is not None
            else None
        )
        self._persist_decision(generation, decision, events, comparison, trigger)
        outcome = ReplanOutcome(
            trigger=trigger,
            evaluated_at=now,
            triggered=True,
            action="",
            reason=decision.reason,
            events=events,
            decision=decision,
            active_version=active,
            candidate_version=candidate,
            comparison=comparison,
            generation=generation,
        )
        if not decision.should_replan:
            outcome.candidate_version = self._schedules.reject(
                candidate.version_number, user, decision.reason
            )
            outcome.action = ACTION_REJECTED
        elif decision.requires_approval:
            self._supersede_pending_drafts(candidate.version_number, user)
            outcome.alert = self._raise_alert(candidate, active, decision, comparison, now)
            outcome.candidate_version = self._versions.get_version(candidate.version_number)
            outcome.action = ACTION_AWAITING_APPROVAL
        else:
            outcome.candidate_version = self._schedules.approve(
                candidate.version_number, user, decision.reason
            )
            outcome.action = ACTION_APPROVED
            if self._settings.writeback_mode is WritebackMode.CONTROLLED_AUTO:
                published = self._schedules.publish(
                    candidate.version_number, user, decision.reason, auto=True
                )
                outcome.candidate_version = published.version
                outcome.action = ACTION_PUBLISHED
        self._audit.record(
            user,
            ENTITY_SCHEDULE_VERSION,
            candidate.schedule_version_id,
            "replan.decision",
            {"active_version": active.version_number if active else None},
            {
                "candidate_version": candidate.version_number,
                "action": outcome.action,
                "decision": asdict(decision),
                "comparison": comparison.summary if comparison else None,
            },
            decision.reason,
            {
                "trigger": trigger.value,
                "events": outcome.event_types,
                "candidate_version": candidate.version_number,
            },
        )
        logger.info(
            "replan.evaluated",
            candidate_version=candidate.version_number,
            action=outcome.action,
            should_replan=decision.should_replan,
            requires_approval=decision.requires_approval,
            improvement_pct=round(decision.improvement_pct, 2),
        )
        return outcome

    # ------------------------------------------------------------ internals
    def _events(
        self, active: ScheduleVersionInfo | None, current: PlanningSnapshot, alerts: Any
    ) -> list[ReplanEvent]:
        if active is None or not active.input_snapshot_id:
            return []
        try:
            previous = self._snapshot_repo.load(active.input_snapshot_id)
        except NotFoundError:
            log.warning("replan.previous_snapshot_missing", snapshot_id=active.input_snapshot_id)
            return []
        return detect_events(previous, current, alerts=alerts)

    def _persist_decision(
        self,
        generation: GenerationSummary,
        decision: ReplanDecision,
        events: list[ReplanEvent],
        comparison: ScheduleComparison | None,
        trigger: ReplanTriggerType,
    ) -> None:
        payload = {
            "trigger": trigger.value,
            "decision": asdict(decision),
            "events": [e.to_dict() for e in events[:_MAX_EVENTS_STORED]],
            "events_total": len(events),
            "comparison": comparison.summary if comparison else None,
            "changes": (
                {k: v for k, v in comparison.changes.items() if k != "entries"} if comparison else None
            ),
        }
        run = generation.run
        run.metrics = {**run.metrics, "replan": payload}
        self._runs.save(run)
        self._versions.update_details(generation.version.version_number, {"replan": payload})

    def _supersede_pending_drafts(self, keep: int, user: CurrentUser | str) -> list[int]:
        """Older replanning candidates still awaiting approval are superseded by the newest one."""
        superseded: list[int] = []
        page = self._versions.list_versions(status=ScheduleStatus.DRAFT, limit=1000)
        for info in page.items:
            if info.version_number == keep or not str(info.details.get("trigger", "")).startswith(
                TRIGGER_REPLAN
            ):
                continue
            self._versions.set_status(
                info.version_number, ScheduleStatus.SUPERSEDED, user_id=None, at=self.now()
            )
            superseded.append(info.version_number)
        if superseded:
            log.info("replan.pending_drafts_superseded", versions=superseded, kept=keep)
        return superseded

    def _raise_alert(
        self,
        candidate: ScheduleVersionInfo,
        active: ScheduleVersionInfo | None,
        decision: ReplanDecision,
        comparison: ScheduleComparison | None,
        now: datetime,
    ) -> AlertRecord:
        quality = f"{candidate.quality['score']:.0f}/100" if candidate.quality else "n/a"
        title = f"New plan v{candidate.version_number} awaiting approval"
        reason = (
            f"Replanning proposes schedule v{candidate.version_number} (quality {quality}) "
            f"to replace v{active.version_number if active else '-'}: {decision.reason}"
        )
        alert = Alert(
            alert_type=AlertType.SCHEDULE_DISRUPTION,
            severity=AlertSeverity.HIGH if decision.frozen_violations else AlertSeverity.WARNING,
            title=title,
            reason=reason,
            recommended_action=(
                f"Review the old-vs-new comparison and approve or reject schedule v{candidate.version_number}"
            ),
            raised_at=now,
            entity_ref=candidate.schedule_version_id,
            dedupe_key=f"schedule_disruption:replan:v{candidate.version_number}",
            details={
                PENDING_VERSION_KEY: candidate.version_number,
                "active_version": active.version_number if active else None,
                "decision": asdict(decision),
                "comparison": comparison.summary if comparison else None,
                "metrics": comparison.metrics if comparison else None,
            },
        )
        return self._alerts.upsert(alert, now)


__all__ = [
    "ACTION_APPROVED",
    "ACTION_AWAITING_APPROVAL",
    "ACTION_NOT_TRIGGERED",
    "ACTION_PUBLISHED",
    "ACTION_REJECTED",
    "SYSTEM_USER",
    "ReplanOutcome",
    "ReplanningService",
]
