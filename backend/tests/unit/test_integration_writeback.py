"""Writeback gateway mode rules (spec Phase 26)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.clock import FrozenClock
from app.core.errors import ConfigurationError, ValidationError
from app.domain.enums import WritebackMode
from app.domain.results import ScheduleEntry, ScheduleResult
from app.integration.writeback import MockWritebackGateway, ReadOnlyWritebackGateway, WritebackGateway

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 14, 4, 0, tzinfo=UTC)


def make_schedule(entries: int = 2) -> ScheduleResult:
    result = ScheduleResult(
        algorithm="rule_based",
        algorithm_version="1.0.0",
        profile_id="PriorityProfile-A",
        profile_version=1,
        config_version=1,
        generated_at=NOW,
        horizon_start=NOW,
        horizon_end=NOW + timedelta(days=14),
        run_id="run_1",
    )
    for i in range(entries):
        start = NOW + timedelta(hours=i)
        result.entries.append(
            ScheduleEntry(
                entry_id=f"e{i}",
                machine_id="MC-CNC3-01",
                order_id=f"SO-{i}",
                operation_id=f"OP-{i}",
                sequence_on_machine=i,
                setup_start=start,
                start=start,
                end=start + timedelta(minutes=30),
                setup_minutes=0.0,
                run_minutes=30.0,
                quantity=1.0,
                priority_score=50.0,
                placement_reason="test",
            )
        )
    return result


def test_read_only_gateway_never_publishes() -> None:
    gateway: WritebackGateway = ReadOnlyWritebackGateway(FrozenClock(NOW))
    receipt = gateway.publish(make_schedule(), WritebackMode.WRITEBACK, approved_by="planner")
    assert receipt.status == "skipped_read_only" and not receipt.published
    assert receipt.entries_published == 0 and receipt.attempted_at == NOW and receipt.run_id == "run_1"
    assert receipt.to_dict()["mode"] == "writeback"


def test_mock_gateway_read_only_mode() -> None:
    gateway = MockWritebackGateway(FrozenClock(NOW))
    receipt = gateway.publish(make_schedule(), WritebackMode.READ_ONLY)
    assert receipt.status == "skipped_read_only"
    assert gateway.published == [] and gateway.receipts == [receipt]


def test_approval_mode_requires_approver() -> None:
    gateway = MockWritebackGateway(FrozenClock(NOW))
    with pytest.raises(ValidationError):
        gateway.publish(make_schedule(), WritebackMode.APPROVAL)
    assert gateway.published == []
    receipt = gateway.publish(make_schedule(3), WritebackMode.APPROVAL, approved_by="pm@plant")
    assert receipt.published and receipt.entries_published == 3 and receipt.approved_by == "pm@plant"
    assert len(gateway.published) == 1 and gateway.published[0].schedule.run_id == "run_1"


def test_writeback_mode_publishes() -> None:
    gateway = MockWritebackGateway(FrozenClock(NOW))
    receipt = gateway.publish(make_schedule(1), WritebackMode.WRITEBACK)
    assert receipt.published and receipt.mode == WritebackMode.WRITEBACK


def test_controlled_auto_requires_rules_and_honours_them() -> None:
    with pytest.raises(ConfigurationError):
        MockWritebackGateway(FrozenClock(NOW)).publish(make_schedule(), WritebackMode.CONTROLLED_AUTO)

    def rule(schedule: ScheduleResult) -> tuple[bool, str]:
        ok = len(schedule.entries) <= 2
        return ok, "ok" if ok else "too many changes"

    gateway = MockWritebackGateway(FrozenClock(NOW), auto_rule=rule)
    accepted = gateway.publish(make_schedule(2), WritebackMode.CONTROLLED_AUTO)
    rejected = gateway.publish(make_schedule(5), WritebackMode.CONTROLLED_AUTO)
    assert accepted.published
    assert rejected.status == "rejected_by_rules" and rejected.details["reason"] == "too many changes"
    assert len(gateway.published) == 1 and len(gateway.receipts) == 2
