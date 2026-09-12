"""ScheduleService / ReplanningService decision paths on the in-memory sample plant."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session

from app.core.clock import FrozenClock
from app.core.config import Settings
from app.core.db import SQLITE_MEMORY_URL
from app.core.errors import ConflictError, ValidationError
from app.db.repositories import AlertRepository, ConfigRepository, MachineRepository, ScheduleRepository
from app.db.repositories.schedule import OptimizationRunRepository
from app.db.seed import seed_default_config
from app.domain.config import ReplanningConfig, SystemConfig
from app.domain.enums import AlertType, MachineStatus, ReplanTriggerType, ScheduleStatus, WritebackMode
from app.services.base import Pagination
from app.services.replanning_service import (
    ACTION_APPROVED,
    ACTION_AWAITING_APPROVAL,
    ACTION_NOT_TRIGGERED,
    ACTION_PUBLISHED,
    ACTION_REJECTED,
    ReplanningService,
)
from app.services.schedule_service import ScheduleService
from tests.conftest import SampleData, load_sample

pytestmark = pytest.mark.unit

USER = "usr_test"


def _settings(mode: WritebackMode = WritebackMode.READ_ONLY) -> Settings:
    return Settings(
        environment="test",
        database_url=SQLITE_MEMORY_URL,
        jwt_secret="unit-test-secret-that-is-long-enough",
        writeback_mode=mode,
        _env_file=None,  # type: ignore[call-arg]
    )


@pytest.fixture
def plant(session: Session, sample: SampleData) -> Iterator[Session]:
    load_sample(session, sample)
    seed_default_config(session)
    session.flush()
    yield session


def _set_replanning(session: Session, **overrides: object) -> None:
    repo = ConfigRepository(session)
    config = repo.get_active()
    updated = config.model_copy(update={"replanning": ReplanningConfig(**overrides)}, deep=True)
    repo.save_new_version(SystemConfig.model_validate(updated.model_dump(mode="json")), reason="test")


# ------------------------------------------------------------ schedule service


def test_workflow_transitions_are_enforced(plant: Session, frozen_clock: FrozenClock) -> None:
    service = ScheduleService(plant, frozen_clock, _settings())
    first = service.generate(USER, note="baseline")
    assert first.version.status is ScheduleStatus.DRAFT and first.entries > 0
    assert first.priority_results == len(first.result.priorities) == 3  # open orders of the sample
    with pytest.raises(ConflictError, match="approve it first"):
        service.publish(first.version.version_number, USER, "too early")
    with pytest.raises(ValidationError):
        service.approve(first.version.version_number, USER, "  ")
    approved = service.approve(first.version.version_number, USER, "ok")
    assert approved.status is ScheduleStatus.APPROVED and approved.approved_by == USER
    with pytest.raises(ConflictError, match="only a draft"):
        service.approve(first.version.version_number, USER, "again")

    second = service.generate(USER)
    assert service.approve(second.version.version_number, USER, "newer").status is ScheduleStatus.APPROVED
    assert service.get_version(first.version.version_number).status is ScheduleStatus.SUPERSEDED

    published = service.publish(second.version.version_number, USER, "go")
    assert published.version.status is ScheduleStatus.PUBLISHED
    assert (
        published.receipt.status == "skipped_read_only" and published.receipt.mode is WritebackMode.READ_ONLY
    )
    assert published.version.details["writeback_receipt"]["status"] == "skipped_read_only"
    assert service.current().status == "published"
    with pytest.raises(ConflictError):
        service.reject(second.version.version_number, USER, "cannot reject a published plan")

    third = service.generate(USER)
    rejected = service.reject(third.version.version_number, USER, "no")
    assert rejected.status is ScheduleStatus.REJECTED and rejected.details["rejection_reason"] == "no"
    assert service.current().version.version_number == second.version.version_number  # type: ignore[union-attr]
    assert service.list_versions(Pagination()).total == 3
    details = service.run_details(second.run.run_id)
    assert details.version is not None and details.priority_results == 3


def test_publish_modes(plant: Session, frozen_clock: FrozenClock) -> None:
    approval = ScheduleService(plant, frozen_clock, _settings(WritebackMode.APPROVAL))
    version = approval.generate(USER).version.version_number
    approval.approve(version, USER, "ok")
    outcome = approval.publish(version, USER, "send to ERP")
    assert outcome.receipt.status == "published" and outcome.receipt.mode is WritebackMode.APPROVAL
    assert outcome.receipt.entries_published == outcome.version.entry_count > 0
    later = approval.generate(USER).version.version_number
    approval.approve(later, USER, "ok")
    with pytest.raises(ConflictError, match="controlled_auto"):
        approval.publish(later, USER, "auto", auto=True)  # auto-publish needs CONTROLLED_AUTO

    controlled = ScheduleService(plant, frozen_clock, _settings(WritebackMode.CONTROLLED_AUTO))
    manual = controlled.generate(USER).version.version_number
    controlled.approve(manual, USER, "ok")
    receipt = controlled.publish(manual, USER, "a person publishes").receipt
    assert receipt.mode is WritebackMode.APPROVAL and receipt.details["configured_mode"] == "controlled_auto"


# ---------------------------------------------------------- replanning service


def test_replan_without_active_plan_awaits_approval(plant: Session, frozen_clock: FrozenClock) -> None:
    service = ReplanningService(plant, frozen_clock, _settings())
    outcome = service.evaluate(ReplanTriggerType.MANUAL, USER, reason="planner asked")
    assert outcome.triggered and outcome.action == ACTION_AWAITING_APPROVAL
    assert outcome.active_version is None and outcome.comparison is None
    assert outcome.candidate_version is not None and outcome.candidate_version.status is ScheduleStatus.DRAFT
    assert (
        outcome.decision is not None and outcome.decision.should_replan and outcome.decision.requires_approval
    )
    assert outcome.event_types == ["manual"] and outcome.events[0].message == "planner asked"
    assert outcome.alert is not None and outcome.alert.alert_type is AlertType.SCHEDULE_DISRUPTION
    assert outcome.alert.details["pending_version"] == outcome.candidate_version.version_number
    run = OptimizationRunRepository(plant).get(outcome.candidate_version.run_id or "")
    assert run.kind == "replan" and run.metrics["replan"]["decision"]["should_replan"] is True
    assert run.metrics["replan"]["events"][0]["type"] == "manual"


def test_identical_candidate_is_rejected(plant: Session, frozen_clock: FrozenClock) -> None:
    schedules = ScheduleService(plant, frozen_clock, _settings())
    baseline = schedules.generate(USER)
    schedules.approve(baseline.version.version_number, USER, "ok")
    service = ReplanningService(plant, frozen_clock, _settings(), schedules)
    outcome = service.evaluate(ReplanTriggerType.SCHEDULED)
    assert outcome.triggered and outcome.action == ACTION_REJECTED
    assert (
        outcome.candidate_version is not None and outcome.candidate_version.status is ScheduleStatus.REJECTED
    )
    assert "identical" in outcome.reason and outcome.comparison is not None
    assert outcome.comparison.moved_orders == 0
    assert outcome.active_version is not None
    assert outcome.active_version.version_number == baseline.version.version_number
    assert schedules.current().version.version_number == baseline.version.version_number  # type: ignore[union-attr]
    assert AlertRepository(plant).list_active(alert_type=AlertType.SCHEDULE_DISRUPTION).total == 0


def test_replanning_disabled_or_untriggered_does_nothing(plant: Session, frozen_clock: FrozenClock) -> None:
    _set_replanning(plant, enabled=False)
    service = ReplanningService(plant, frozen_clock, _settings())
    outcome = service.evaluate(ReplanTriggerType.SCHEDULED)
    assert not outcome.triggered and outcome.action == ACTION_NOT_TRIGGERED
    assert outcome.reason == "replanning disabled" and outcome.candidate_version is None
    assert ScheduleRepository(plant).count_versions() == 0

    _set_replanning(plant, enabled=True, trigger_on=["machine_down"])
    outcome = service.evaluate(ReplanTriggerType.CONFIG_CHANGE)
    assert not outcome.triggered and outcome.reason == "no triggering events"
    assert ScheduleRepository(plant).count_versions() == 0


def test_hard_event_replans_and_auto_publishes_under_controlled_auto(
    plant: Session, frozen_clock: FrozenClock
) -> None:
    _set_replanning(plant, require_approval=False)
    settings = _settings(WritebackMode.CONTROLLED_AUTO)
    schedules = ScheduleService(plant, frozen_clock, settings)
    baseline = schedules.generate(USER)
    schedules.approve(baseline.version.version_number, USER, "ok")
    schedules.publish(baseline.version.version_number, USER, "go")
    machine_id = next(iter({e.machine_id for e in baseline.result.schedule.entries}))
    MachineRepository(plant).set_status(machine_id, MachineStatus.DOWN)

    outcome = ReplanningService(plant, frozen_clock, settings, schedules).evaluate(
        ReplanTriggerType.SCHEDULED
    )
    assert outcome.triggered and "machine_down" in outcome.event_types
    assert outcome.decision is not None and outcome.decision.should_replan
    assert "infeasible" in outcome.decision.reason
    assert outcome.action == ACTION_PUBLISHED
    candidate = outcome.candidate_version
    assert candidate is not None and candidate.status is ScheduleStatus.PUBLISHED
    assert candidate.details["writeback_receipt"]["mode"] == "controlled_auto"
    assert candidate.details["writeback_receipt"]["status"] == "published"
    assert schedules.get_version(baseline.version.version_number).status is ScheduleStatus.SUPERSEDED
    assert (
        outcome.comparison is not None
        and outcome.comparison.a.version_number == baseline.version.version_number
    )


def test_auto_approve_without_publish_in_read_only_mode(plant: Session, frozen_clock: FrozenClock) -> None:
    _set_replanning(plant, require_approval=False, significant_change_orders=0)
    schedules = ScheduleService(plant, frozen_clock, _settings())
    baseline = schedules.generate(USER)
    schedules.approve(baseline.version.version_number, USER, "ok")
    machine_id = next(iter({e.machine_id for e in baseline.result.schedule.entries}))
    MachineRepository(plant).set_status(machine_id, MachineStatus.DOWN)
    outcome = ReplanningService(plant, frozen_clock, _settings(), schedules).evaluate(
        ReplanTriggerType.SCHEDULED
    )
    assert outcome.action == ACTION_APPROVED
    assert (
        outcome.candidate_version is not None and outcome.candidate_version.status is ScheduleStatus.APPROVED
    )
    assert schedules.get_version(baseline.version.version_number).status is ScheduleStatus.SUPERSEDED
