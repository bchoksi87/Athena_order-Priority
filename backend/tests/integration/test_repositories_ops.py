"""Alerts, audit log, data-quality issues, sync runs, snapshots and users."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.db.records import SyncRunRecord
from app.db.repositories import (
    AlertRepository,
    AuditFilters,
    AuditRepository,
    DataQualityRepository,
    SnapshotRepository,
    SyncRunRepository,
    UserRepository,
)
from app.domain.enums import (
    AlertSeverity,
    AlertType,
    DataQualityCode,
    DataQualitySeverity,
    Role,
    SyncMode,
)
from app.domain.models import Order
from app.domain.results import Alert, DataQualityIssue
from tests.conftest import NOW, SampleData

pytestmark = pytest.mark.integration


# ------------------------------------------------------------------- alerts


def _alert(order_id: str = "ORD-1", severity: AlertSeverity = AlertSeverity.WARNING, key: str = "") -> Alert:
    return Alert(
        alert_type=AlertType.ORDER_LIKELY_LATE,
        severity=severity,
        title=f"{order_id} likely late",
        reason="projected completion after due date",
        recommended_action="expedite or renegotiate",
        raised_at=NOW,
        order_id=order_id,
        dedupe_key=key,
        details={"lateness_hours": 3.5},
    )


def test_alert_dedupe_ack_and_resolve(db_session: Session) -> None:
    repo = AlertRepository(db_session)
    first = repo.upsert(_alert())
    assert first.occurrences == 1 and first.active and not first.acknowledged
    assert first.dedupe_key == "order_likely_late|ORD-1||"
    again = repo.upsert(_alert(severity=AlertSeverity.HIGH), now=NOW + timedelta(hours=1))
    assert again.alert_id == first.alert_id
    assert again.occurrences == 2 and again.severity is AlertSeverity.HIGH
    assert again.raised_at == NOW and again.last_seen_at == NOW + timedelta(hours=1)
    other = repo.upsert(_alert("ORD-2", key="custom-key"))
    assert other.dedupe_key == "custom-key"

    page = repo.list_active()
    assert page.total == 2
    assert repo.list_active(severity=AlertSeverity.HIGH).total == 1
    assert repo.list_active(order_id="ORD-2").total == 1
    assert repo.list_active(alert_type=AlertType.MACHINE_DOWNTIME).total == 0
    assert repo.active_counts_by_severity() == {"high": 1, "warning": 1}

    acked = repo.acknowledge(first.alert_id, user_id="usr_sup", at=NOW + timedelta(hours=2))
    assert acked.acknowledged and acked.acknowledged_by == "usr_sup"
    assert repo.list_active(unacknowledged_only=True).total == 1
    assert repo.get(first.alert_id).acknowledged_at == NOW + timedelta(hours=2)

    resolved = repo.resolve(first.alert_id, at=NOW + timedelta(hours=3))
    assert not resolved.active and resolved.resolved_at == NOW + timedelta(hours=3)
    assert repo.list_active().total == 1
    # re-occurrence re-activates and clears the acknowledgement
    back = repo.upsert(_alert(), now=NOW + timedelta(hours=4))
    assert back.active and not back.acknowledged and back.occurrences == 3
    assert repo.resolve_missing(["custom-key"], at=NOW + timedelta(hours=5)) == 1
    assert [a.dedupe_key for a in repo.list_active().items] == ["custom-key"]
    assert len(repo.upsert_many([_alert("ORD-7"), _alert("ORD-8")])) == 2
    with pytest.raises(NotFoundError):
        repo.acknowledge("nope", user_id="u", at=NOW)


# -------------------------------------------------------------------- audit


def test_audit_append_and_query(db_session: Session, sample: SampleData) -> None:
    repo = AuditRepository(db_session)
    before = Order("ORD-1", "CUST-A", "P", quantity=1)
    entry = repo.append(
        user_id="usr_manager",
        timestamp=NOW,
        entity_type="order",
        entity_id="ORD-1",
        action="override_priority",
        previous_value=before,
        new_value={"score": 95},
        reason="customer escalation",
        request_id="req_1",
    )
    assert entry.previous_value is not None and entry.previous_value["order_id"] == "ORD-1"
    assert entry.new_value == {"score": 95}
    repo.append(
        user_id="usr_planner",
        timestamp=NOW + timedelta(minutes=1),
        entity_type="schedule",
        entity_id="3",
        action="approve",
        new_value=["a", "b"],
    )
    repo.append(
        user_id="usr_planner",
        timestamp=NOW + timedelta(minutes=2),
        entity_type="order",
        entity_id="ORD-1",
        action="hold",
        previous_value="scalar",
    )

    all_entries = repo.query()
    assert all_entries.total == 3
    assert [e.action for e in all_entries.items] == ["hold", "approve", "override_priority"]  # newest first
    assert all_entries.items[0].previous_value == {"value": "scalar"}
    assert repo.query(AuditFilters(user_id="usr_planner")).total == 2
    assert repo.query(AuditFilters(entity_type="order", entity_id="ORD-1")).total == 2
    assert repo.query(AuditFilters(action="approve")).items[0].new_value == ["a", "b"]
    assert repo.query(AuditFilters(since=NOW + timedelta(minutes=1))).total == 2
    assert repo.query(AuditFilters(until=NOW + timedelta(minutes=1))).total == 1
    assert [e.action for e in repo.for_entity("order", "ORD-1")] == ["hold", "override_priority"]
    page = repo.query(limit=2)
    assert len(page.items) == 2 and page.has_more


# ------------------------------------------------------------- data quality


def _issue(code: DataQualityCode, severity: DataQualitySeverity, entity_id: str) -> DataQualityIssue:
    return DataQualityIssue(
        code,
        severity,
        "order",
        entity_id,
        f"{code.value} on {entity_id}",
        field_name="x",
        recommendation="fix it",
        details={"k": 1},
    )


def test_data_quality_runs(db_session: Session) -> None:
    repo = DataQualityRepository(db_session)
    empty = repo.summary()
    assert empty.total == 0 and empty.run_id is None
    assert repo.list_issues().total == 0
    issues = [
        _issue(DataQualityCode.MISSING_CYCLE_TIME, DataQualitySeverity.BLOCKING, "ORD-1"),
        _issue(DataQualityCode.MISSING_DUE_DATE, DataQualitySeverity.BLOCKING, "ORD-1"),
        _issue(DataQualityCode.MISSING_SETUP_TIME, DataQualitySeverity.WARNING, "ORD-2"),
        _issue(DataQualityCode.MISSING_CYCLE_TIME, DataQualitySeverity.BLOCKING, "ORD-3"),
    ]
    assert repo.replace_run("dq-1", issues, NOW) == 4
    summary = repo.summary()
    assert summary.run_id == "dq-1" and summary.total == 4 and summary.detected_at == NOW
    assert summary.by_severity == {"blocking": 3, "warning": 1}
    assert summary.by_code == {"missing_cycle_time": 2, "missing_due_date": 1, "missing_setup_time": 1}
    assert summary.by_entity_type == {"order": 4}
    assert summary.blocked_entities == 2
    assert repo.list_issues(severity=DataQualitySeverity.WARNING).total == 1
    assert repo.list_issues(code=DataQualityCode.MISSING_CYCLE_TIME).total == 2
    assert repo.list_issues(entity_id="ORD-1").total == 2
    assert repo.issues_for_entity("order", "ORD-1") == issues[:2]
    assert repo.issues_for_entity("order", "ORD-9") == []
    record = repo.list_issues().items[0]
    assert record.run_id == "dq-1" and record.details == {"k": 1} and record.recommendation == "fix it"

    assert repo.replace_run("dq-2", issues[:1], NOW + timedelta(hours=1)) == 1
    assert repo.latest_run_id() == "dq-2"
    assert repo.summary().total == 1
    assert repo.summary("dq-1").total == 4
    assert repo.count("dq-1") == 4
    assert repo.replace_run("dq-1", [], NOW) == 0
    assert repo.count("dq-1") == 0


# ---------------------------------------------------------------- sync runs


def test_sync_runs(db_session: Session) -> None:
    repo = SyncRunRepository(db_session)
    record = SyncRunRecord(
        "sync-1", SyncMode.FULL, "running", NOW, connector="mock", triggered_by="usr_admin"
    )
    assert repo.start(record) == record
    record.status = "completed"
    record.finished_at = NOW + timedelta(seconds=5)
    record.records_fetched = {"orders": 10, "machines": 3}
    record.records_upserted = {"orders": 10, "machines": 3}
    record.issues_count = 2
    assert repo.save(record) == record
    assert repo.get("sync-1").records_fetched == {"orders": 10, "machines": 3}
    repo.start(
        SyncRunRecord(
            "sync-2", SyncMode.INCREMENTAL, "failed", NOW + timedelta(hours=1), error_message="boom"
        )
    )
    assert repo.latest().run_id == "sync-2"  # type: ignore[union-attr]
    assert repo.latest_successful_started_at().run_id == "sync-1"  # type: ignore[union-attr]
    assert [r.run_id for r in repo.list().items] == ["sync-2", "sync-1"]
    with pytest.raises(NotFoundError):
        repo.get("nope")
    with pytest.raises(NotFoundError):
        repo.save(SyncRunRecord("ghost", SyncMode.FULL, "x", NOW))


# ---------------------------------------------------------------- snapshots


def test_snapshot_save_and_load(db_session: Session, sample: SampleData) -> None:
    repo = SnapshotRepository(db_session)
    snapshot = sample.snapshot()
    info = repo.save(snapshot, created_by="usr_planner")
    assert snapshot.snapshot_id == info.snapshot_id
    assert info.codec == "gzip+json/v1" and info.size_bytes > 0 and len(info.sha256) == 64
    assert info.summary == snapshot.summary()
    loaded = repo.load(info.snapshot_id)
    assert loaded.snapshot_id == info.snapshot_id
    assert loaded.orders == snapshot.orders
    assert loaded.machines == snapshot.machines
    assert loaded.calendars == snapshot.calendars
    assert loaded.locks == snapshot.locks
    assert repo.get_info(info.snapshot_id).sha256 == info.sha256
    second = sample.snapshot(NOW + timedelta(hours=1))
    repo.save(second)
    assert [s.snapshot_id for s in repo.list().items] == [second.snapshot_id, info.snapshot_id]
    assert repo.delete_older_than(NOW + timedelta(minutes=30)) == 1
    assert repo.list().total == 1
    with pytest.raises(NotFoundError):
        repo.load("nope")
    with pytest.raises(NotFoundError):
        repo.get_info("nope")


# -------------------------------------------------------------------- users


def test_user_repository(db_session: Session) -> None:
    repo = UserRepository(db_session)
    assert repo.get_by_username("alice") is None
    alice = repo.create(
        username="  Alice ", password="pw-1234", role=Role.PLANNER, display_name="Alice", email="a@x.io"
    )
    assert alice.username == "alice" and alice.role is Role.PLANNER and alice.active
    assert repo.get_by_username("ALICE") == alice
    assert repo.get(alice.user_id) == alice
    assert repo.authenticate("alice", "pw-1234") == alice
    assert repo.authenticate("alice", "nope") is None
    assert repo.authenticate("bob", "pw-1234") is None
    with pytest.raises(ConflictError):
        repo.create(username="alice", password="x", role=Role.ADMIN, display_name="Dup")
    with pytest.raises(ValidationError):
        repo.create(username="   ", password="x", role=Role.ADMIN, display_name="Blank")
    bob = repo.create(
        username="bob", password="pw", role=Role.OPERATOR, display_name="Bob", user_id="usr_bob"
    )
    assert bob.user_id == "usr_bob"
    assert [u.username for u in repo.list()] == ["alice", "bob"]
    repo.set_password(alice.user_id, "new-pw")
    assert repo.authenticate("alice", "new-pw") is not None
    assert not repo.set_active(alice.user_id, False).active
    assert repo.authenticate("alice", "new-pw") is None
    assert [u.username for u in repo.list(active_only=True)] == ["bob"]
    repo.record_login("usr_bob", NOW)
    assert repo.get("usr_bob").last_login_at == NOW
    with pytest.raises(NotFoundError):
        repo.get("nope")
    with pytest.raises(NotFoundError):
        repo.set_password("nope", "x")
