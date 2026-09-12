"""Configuration, priority results, schedule versions/entries, optimization runs and overlays."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from app.core.errors import ConfigurationError, ConflictError, NotFoundError
from app.db.records import OptimizationRunRecord
from app.db.repositories import (
    ConfigRepository,
    ExpediteRepository,
    LockRepository,
    OptimizationRunRepository,
    OverrideRepository,
    PriorityResultRepository,
    ScheduleRepository,
)
from app.domain.config import FactorWeight, SystemConfig
from app.domain.enums import LockType, OverrideType, ReadinessState, RiskLevel, ScheduleStatus
from app.domain.models import Expedite, PriorityOverride, ScheduleLock, TimeWindow
from app.domain.results import (
    FactorScore,
    PriorityAdjustment,
    PriorityResult,
    ScheduleEntry,
    ScheduleMetrics,
    ScheduleQuality,
    ScheduleResult,
    UnscheduledItem,
)
from tests.conftest import NOW

pytestmark = pytest.mark.integration


# ------------------------------------------------------------------- config


def test_config_versions(db_session: Session) -> None:
    repo = ConfigRepository(db_session)
    assert not repo.has_active()
    assert repo.get_active_info() is None
    with pytest.raises(ConfigurationError):
        repo.get_active()

    v1 = repo.save_new_version(SystemConfig(), created_by="usr_admin", reason="seed")
    assert v1.version == 1 and v1.is_active
    assert v1.profile_id == "PriorityProfile-A" and v1.scheduling_config_id == "SchedulingConfig-A"
    active = repo.get_active()
    assert active.priority_profile.version == 1 and active.scheduling.version == 1

    changed = SystemConfig()
    changed.priority_profile.weights = [FactorWeight(key="due_date_urgency", weight=100)]
    changed.scheduling.horizon_days = 21
    v2 = repo.save_new_version(changed, created_by="usr_admin", reason="tighten")
    assert v2.version == 2 and v2.is_active
    assert repo.get_active().scheduling.horizon_days == 21
    assert repo.get_active().priority_profile.version == 2
    assert repo.get_version(1).scheduling.horizon_days == 14
    versions = repo.list_versions()
    assert [v.version for v in versions] == [2, 1]
    assert [v.is_active for v in versions] == [True, False]
    assert repo.next_version() == 3
    with pytest.raises(NotFoundError):
        repo.get_version(9)

    draft = repo.save_new_version(SystemConfig(), activate=False)
    assert draft.version == 3 and not draft.is_active
    assert repo.get_active_info() is not None and repo.get_active_info().version == 2

    rolled = repo.activate_version(1)
    assert rolled.version == 1 and rolled.is_active
    assert repo.get_active().scheduling.horizon_days == 14
    assert [v.is_active for v in repo.list_versions()] == [False, False, True]
    with pytest.raises(NotFoundError):
        repo.activate_version(42)


# ----------------------------------------------------------------- priority


def _result(order_id: str, score: float, rank: int | None = None, at=NOW) -> PriorityResult:
    return PriorityResult(
        order_id=order_id,
        score=score,
        base_score=score - 5,
        factors=[FactorScore("due_date_urgency", "Due", "bonus", score, 1.0, score, "because")],
        adjustments=[PriorityAdjustment("aging", 5.0, "aged 6 days")],
        readiness=ReadinessState.READY,
        blocked=False,
        blocking_reasons=[],
        risk_level=RiskLevel.MEDIUM,
        explanation=f"{order_id} scored {score}",
        profile_id="PriorityProfile-A",
        profile_version=1,
        computed_at=at,
        rank=rank,
    )


def test_priority_results(db_session: Session) -> None:
    repo = PriorityResultRepository(db_session)
    assert repo.latest_run_id() is None
    assert repo.ranking() == []
    assert repo.latest_for_orders(["ORD-1"]) == {}
    run1 = [_result("ORD-1", 80), _result("ORD-2", 95), _result("ORD-3", 80)]
    assert repo.save_run("run-1", run1) == 3
    ranked = repo.ranking("run-1")
    assert [(r.order_id, r.rank) for r in ranked] == [("ORD-2", 1), ("ORD-1", 2), ("ORD-3", 3)]
    assert ranked[0] == run1[1]
    assert repo.count_for_run("run-1") == 3

    later = NOW + timedelta(hours=1)
    repo.save_run("run-2", [_result("ORD-1", 70, at=later), _result("ORD-2", 60, rank=5, at=later)])
    assert repo.latest_run_id() == "run-2"
    assert [r.order_id for r in repo.ranking()] == ["ORD-1", "ORD-2"]
    assert [r.order_id for r in repo.ranking(limit=1, offset=1)] == ["ORD-2"]
    assert repo.ranking()[1].rank == 5  # engine-provided rank is kept
    latest = repo.latest_for_order("ORD-1")
    assert latest is not None and latest.score == 70
    assert repo.latest_for_order("ORD-3").score == 80  # type: ignore[union-attr]
    assert repo.latest_for_order("nope") is None
    assert set(repo.latest_for_orders(["ORD-1", "ORD-2", "ORD-3"])) == {"ORD-1", "ORD-2"}
    assert [r.score for r in repo.history_for_order("ORD-1")] == [70, 80]
    assert repo.results_for_run("run-1").keys() == {"ORD-1", "ORD-2", "ORD-3"}

    # saving the same run again replaces its rows
    repo.save_run("run-1", [_result("ORD-9", 10)])
    assert repo.results_for_run("run-1").keys() == {"ORD-9"}
    assert repo.delete_run("run-1") == 1
    assert repo.results_for_run("run-1") == {}


# ----------------------------------------------------------------- schedule


def _entry(
    entry_id: str, machine: str, order: str, seq: int, offset_hours: float, locked: bool = False
) -> ScheduleEntry:
    setup_start = NOW + timedelta(hours=offset_hours)
    return ScheduleEntry(
        entry_id=entry_id,
        machine_id=machine,
        order_id=order,
        operation_id=f"{order}-10",
        sequence_on_machine=seq,
        setup_start=setup_start,
        start=setup_start + timedelta(minutes=15),
        end=setup_start + timedelta(hours=2),
        setup_minutes=15,
        run_minutes=105,
        quantity=10,
        priority_score=90 - seq,
        placement_reason="next highest priority",
        locked=locked,
    )


def _schedule_result(entries: list[ScheduleEntry]) -> ScheduleResult:
    return ScheduleResult(
        algorithm="rule_based",
        algorithm_version="1.0.0",
        profile_id="PriorityProfile-A",
        profile_version=1,
        config_version=1,
        generated_at=NOW,
        horizon_start=NOW,
        horizon_end=NOW + timedelta(days=14),
        entries=entries,
        unscheduled=[UnscheduledItem("ORD-3", None, "on_hold", "order on hold", ReadinessState.ON_HOLD)],
        metrics=ScheduleMetrics(scheduled_orders=len({e.order_id for e in entries}), on_time_pct=100.0),
        quality=ScheduleQuality(88.0, {"on_time_delivery": 100.0}, {"on_time_delivery": 1.0}, "good"),
        warnings=["setup times defaulted for 1 operation"],
    )


def test_schedule_versions_and_entries(db_session: Session) -> None:
    repo = ScheduleRepository(db_session)
    assert repo.next_version_number() == 1
    assert repo.get_latest() is None and repo.get_current() is None
    assert repo.get_entries() == []

    entries = [
        _entry("e1", "CNC-01", "ORD-1", 1, 0, locked=True),
        _entry("e2", "CNC-01", "ORD-2", 2, 2),
        _entry("e3", "CNC-02", "ORD-3", 1, 30),
    ]
    v1 = repo.create_version(_schedule_result(entries), generated_by="usr_planner", label="morning run")
    assert v1.version_number == 1 and v1.status is ScheduleStatus.DRAFT
    assert v1.entry_count == 3 and v1.label == "morning run"
    assert v1.metrics["scheduled_orders"] == 3 and v1.quality is not None and v1.quality["score"] == 88.0
    assert v1.unscheduled[0]["order_id"] == "ORD-3" and v1.warnings
    assert repo.get_version(1) == v1
    assert repo.get_version_by_id(v1.schedule_version_id) == v1
    assert repo.entry_count(1) == 3
    assert repo.get_entries(1) == entries
    assert repo.get_entries(1, machine_id="CNC-02") == [entries[2]]
    assert repo.get_entries(1, order_id="ORD-2") == [entries[1]]
    assert [e.entry_id for e in repo.get_entries(1, start_from=NOW + timedelta(hours=1))] == ["e2", "e3"]
    assert [e.entry_id for e in repo.get_entries(1, start_to=NOW + timedelta(hours=1))] == ["e1"]
    day = (NOW, NOW + timedelta(hours=24))
    assert [e.entry_id for e in repo.get_entries(1, overlapping=day)] == ["e1", "e2"]
    assert repo.get_entries(schedule_version_id=v1.schedule_version_id) == entries

    # a second version is the new current DRAFT; the first stays as history
    v2 = repo.create_version(_schedule_result(entries[:1]), generated_by="usr_planner")
    assert v2.version_number == 2
    assert repo.get_current() == v2
    assert repo.get_entries() == entries[:1]
    page = repo.list_versions(limit=1)
    assert page.total == 2 and page.items[0].version_number == 2 and page.has_more

    # status lifecycle with transition enforcement
    approved = repo.set_status(2, ScheduleStatus.APPROVED, user_id="usr_manager", at=NOW + timedelta(hours=1))
    assert approved.status is ScheduleStatus.APPROVED and approved.approved_by == "usr_manager"
    assert repo.get_current() == approved
    with pytest.raises(ConflictError):
        repo.set_status(2, ScheduleStatus.DRAFT, user_id="u", at=NOW)
    published = repo.set_status(
        2, ScheduleStatus.PUBLISHED, user_id="usr_manager", at=NOW + timedelta(hours=2)
    )
    assert published.published_at == NOW + timedelta(hours=2)
    assert repo.get_latest(ScheduleStatus.PUBLISHED) == published
    assert repo.get_latest(ScheduleStatus.DRAFT).version_number == 1  # type: ignore[union-attr]
    assert repo.set_status(2, ScheduleStatus.PUBLISHED, user_id="u", at=NOW) == published  # idempotent

    v3 = repo.create_version(_schedule_result(entries), generated_by="usr_planner")
    repo.set_status(3, ScheduleStatus.APPROVED, user_id="u", at=NOW)
    repo.set_status(3, ScheduleStatus.PUBLISHED, user_id="u", at=NOW + timedelta(hours=3))
    assert repo.supersede_others(3, at=NOW + timedelta(hours=3)) == 1
    assert repo.get_version(2).status is ScheduleStatus.SUPERSEDED
    assert repo.get_version(2).superseded_at == NOW + timedelta(hours=3)
    assert repo.get_current().version_number == 3  # type: ignore[union-attr]
    forced = repo.set_status(3, ScheduleStatus.DRAFT, user_id="u", at=NOW, enforce_transition=False)
    assert forced.status is ScheduleStatus.DRAFT
    with pytest.raises(NotFoundError):
        repo.get_version(99)
    with pytest.raises(NotFoundError):
        repo.get_version_by_id("nope")
    assert v3.version_number == 3


def test_optimization_runs(db_session: Session) -> None:
    repo = OptimizationRunRepository(db_session)
    record = OptimizationRunRecord(
        run_id="run-1",
        kind="schedule",
        status="running",
        started_at=NOW,
        algorithm="rule_based",
        algorithm_version="1.0.0",
        profile_id="PriorityProfile-A",
        profile_version=1,
        config_version=1,
        triggered_by="usr_planner",
        trigger_reason="manual",
    )
    started = repo.start(record)
    assert started == record and started.duration_seconds is None
    record.status = "completed"
    record.finished_at = NOW + timedelta(seconds=42)
    record.orders_considered, record.orders_scheduled, record.orders_blocked = 10, 8, 2
    record.quality_score = 88.0
    record.metrics = {"on_time_pct": 100.0}
    record.warnings = ["w"]
    finished = repo.save(record)
    assert finished.duration_seconds == 42 and finished.orders_scheduled == 8
    assert repo.get("run-1") == finished
    repo.start(OptimizationRunRecord("run-2", "priority", "completed", NOW + timedelta(minutes=1)))
    assert repo.latest().run_id == "run-2"  # type: ignore[union-attr]
    assert repo.latest(kind="schedule").run_id == "run-1"  # type: ignore[union-attr]
    assert repo.latest(status="failed") is None
    assert [r.run_id for r in repo.list().items] == ["run-2", "run-1"]
    assert repo.list(kind="priority").total == 1
    with pytest.raises(NotFoundError):
        repo.get("nope")
    with pytest.raises(NotFoundError):
        repo.save(OptimizationRunRecord("ghost", "schedule", "x", NOW))


# ----------------------------------------------------------------- overlays


def test_locks(db_session: Session) -> None:
    repo = LockRepository(db_session)
    live = ScheduleLock("L1", LockType.ORDER, "usr_p", NOW, "keep", order_id="ORD-1")
    windowed = ScheduleLock(
        "L2",
        LockType.TIME_SLOT,
        "usr_p",
        NOW,
        "slot",
        machine_id="M",
        window=TimeWindow(NOW, NOW + timedelta(hours=1)),
    )
    expired = ScheduleLock(
        "L3",
        LockType.MACHINE,
        "usr_p",
        NOW - timedelta(days=1),
        "old",
        machine_id="M",
        window=TimeWindow(NOW - timedelta(days=1), NOW - timedelta(hours=1)),
    )
    for lock in (live, windowed, expired):
        assert repo.add(lock) == lock
    assert repo.get("L1") == live
    assert [lock.lock_id for lock in repo.list_active()] == ["L3", "L1", "L2"]
    assert [lock.lock_id for lock in repo.list_active(NOW)] == ["L1", "L2"]
    assert [lock.lock_id for lock in repo.list_active(NOW + timedelta(hours=2))] == ["L1"]
    assert [lock.lock_id for lock in repo.list_for_order("ORD-1")] == ["L1"]
    released = repo.release("L1", released_by="usr_m", at=NOW)
    assert not released.active
    assert repo.list_for_order("ORD-1") == []
    assert [lock.lock_id for lock in repo.list_for_order("ORD-1", active_only=False)] == ["L1"]
    with pytest.raises(NotFoundError):
        repo.release("nope", released_by="u", at=NOW)


def test_overrides(db_session: Session) -> None:
    repo = OverrideRepository(db_session)
    o1 = PriorityOverride("O1", "ORD-1", OverrideType.INCREASE_PRIORITY, "usr_m", NOW, "call", value=10)
    o2 = PriorityOverride(
        "O2",
        "ORD-1",
        OverrideType.FORCE_NEXT,
        "usr_m",
        NOW + timedelta(minutes=1),
        "urgent",
        expires_at=NOW + timedelta(hours=4),
    )
    o3 = PriorityOverride(
        "O3", "ORD-2", OverrideType.HOLD_ORDER, "usr_m", NOW, "hold", expires_at=NOW - timedelta(hours=1)
    )
    for o in (o1, o2, o3):
        repo.add(o)
    assert repo.get("O2") == o2
    assert [o.override_id for o in repo.list_active()] == ["O1", "O3", "O2"]
    assert [o.override_id for o in repo.list_active(NOW)] == ["O1", "O2"]
    assert [o.override_id for o in repo.list_for_order("ORD-1")] == ["O1", "O2"]
    assert [o.override_id for o in repo.list_for_order("ORD-1", override_type=OverrideType.FORCE_NEXT)] == [
        "O2"
    ]
    assert not repo.deactivate("O2", released_by="usr_m", at=NOW).active
    assert repo.deactivate_for_order("ORD-1", released_by="usr_m", at=NOW) == 1
    assert repo.list_for_order("ORD-1") == []
    assert (
        repo.deactivate_for_order("ORD-2", released_by="u", at=NOW, override_type=OverrideType.FORCE_NEXT)
        == 0
    )
    with pytest.raises(NotFoundError):
        repo.get("nope")


def test_expedites(db_session: Session) -> None:
    repo = ExpediteRepository(db_session)
    e1 = Expedite("E1", "ORD-1", "usr_m", NOW, "rush", 30, NOW, NOW + timedelta(hours=4))
    e2 = Expedite(
        "E2",
        "ORD-1",
        "usr_m",
        NOW - timedelta(days=1),
        "old",
        20,
        NOW - timedelta(days=1),
        NOW - timedelta(hours=20),
    )
    repo.add(e1)
    repo.add(e2)
    assert repo.get("E1") == e1
    assert [e.expedite_id for e in repo.list_active()] == ["E2", "E1"]
    assert [e.expedite_id for e in repo.list_active(NOW)] == ["E1"]
    assert [e.expedite_id for e in repo.list_for_order("ORD-1")] == ["E2", "E1"]
    assert not repo.deactivate("E1", released_by="u", at=NOW).active
    assert repo.list_active(NOW) == []
    with pytest.raises(NotFoundError):
        repo.deactivate("nope", released_by="u", at=NOW)
