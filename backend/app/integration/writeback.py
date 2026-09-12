"""ERP writeback gateway (spec Phase 26).

Modes escalate from READ_ONLY (default: nothing leaves the system) through
APPROVAL (a named planner approved the schedule) and WRITEBACK to
CONTROLLED_AUTO (published automatically when configured rules pass). The
gateway enforces those preconditions; the transport is pluggable.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

import structlog

from app.core.clock import Clock
from app.core.errors import ConfigurationError, ValidationError
from app.core.ids import new_id
from app.domain.enums import WritebackMode
from app.domain.results import ScheduleResult

log = structlog.get_logger(__name__)

WritebackStatus = Literal["published", "skipped_read_only", "rejected_by_rules", "failed"]

#: Decides whether CONTROLLED_AUTO may publish a given schedule. Returns
#: ``(allowed, reason)`` so the receipt can explain a refusal.
AutoPublishRule = Callable[[ScheduleResult], tuple[bool, str]]


@dataclass(slots=True)
class WritebackReceipt:
    receipt_id: str
    mode: WritebackMode
    status: WritebackStatus
    attempted_at: datetime
    entries_published: int = 0
    approved_by: str | None = None
    message: str = ""
    run_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def published(self) -> bool:
        return self.status == "published"

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "mode": self.mode.value,
            "status": self.status,
            "attempted_at": self.attempted_at.isoformat(),
            "entries_published": self.entries_published,
            "approved_by": self.approved_by,
            "message": self.message,
            "run_id": self.run_id,
            "details": dict(self.details),
        }


class WritebackGateway(Protocol):
    def publish(
        self, schedule: ScheduleResult, mode: WritebackMode, approved_by: str | None = None
    ) -> WritebackReceipt: ...


def check_publish_preconditions(
    mode: WritebackMode, approved_by: str | None, auto_rule: AutoPublishRule | None
) -> None:
    """Raise unless the mode's preconditions hold (shared by every gateway)."""
    if mode == WritebackMode.APPROVAL and not approved_by:
        raise ValidationError(
            "APPROVAL writeback mode requires an approving user", details={"mode": mode.value}
        )
    if mode == WritebackMode.CONTROLLED_AUTO and auto_rule is None:
        raise ConfigurationError(
            "CONTROLLED_AUTO writeback mode requires configured auto-publish rules",
            details={"mode": mode.value},
        )


class ReadOnlyWritebackGateway:
    """Never publishes; records the intent and returns a ``skipped_read_only`` receipt."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    def publish(
        self, schedule: ScheduleResult, mode: WritebackMode, approved_by: str | None = None
    ) -> WritebackReceipt:
        now = self._clock.now()
        log.info(
            "writeback.skipped_read_only",
            requested_mode=mode.value,
            run_id=schedule.run_id,
            entries=len(schedule.entries),
            approved_by=approved_by,
        )
        return WritebackReceipt(
            receipt_id=new_id("wb"),
            mode=mode,
            status="skipped_read_only",
            attempted_at=now,
            approved_by=approved_by,
            message="writeback gateway is read-only; schedule was not sent to the ERP",
            run_id=schedule.run_id,
            details={"requested_mode": mode.value, "entries": len(schedule.entries)},
        )


@dataclass(slots=True)
class PublishedSchedule:
    receipt: WritebackReceipt
    schedule: ScheduleResult


class MockWritebackGateway:
    """In-memory gateway for tests: enforces mode rules and records what was published."""

    def __init__(self, clock: Clock, auto_rule: AutoPublishRule | None = None) -> None:
        self._clock = clock
        self._auto_rule = auto_rule
        self.published: list[PublishedSchedule] = []
        self.receipts: list[WritebackReceipt] = []

    def publish(
        self, schedule: ScheduleResult, mode: WritebackMode, approved_by: str | None = None
    ) -> WritebackReceipt:
        now = self._clock.now()
        if mode == WritebackMode.READ_ONLY:
            receipt = WritebackReceipt(
                receipt_id=new_id("wb"),
                mode=mode,
                status="skipped_read_only",
                attempted_at=now,
                approved_by=approved_by,
                message="READ_ONLY mode: nothing published",
                run_id=schedule.run_id,
            )
            self.receipts.append(receipt)
            log.info("writeback.mock.skipped", run_id=schedule.run_id)
            return receipt
        check_publish_preconditions(mode, approved_by, self._auto_rule)
        if mode == WritebackMode.CONTROLLED_AUTO and self._auto_rule is not None:
            allowed, reason = self._auto_rule(schedule)
            if not allowed:
                receipt = WritebackReceipt(
                    receipt_id=new_id("wb"),
                    mode=mode,
                    status="rejected_by_rules",
                    attempted_at=now,
                    message=f"auto-publish rules rejected the schedule: {reason}",
                    run_id=schedule.run_id,
                    details={"reason": reason},
                )
                self.receipts.append(receipt)
                log.warning("writeback.mock.rejected", run_id=schedule.run_id, reason=reason)
                return receipt
        receipt = WritebackReceipt(
            receipt_id=new_id("wb"),
            mode=mode,
            status="published",
            attempted_at=now,
            entries_published=len(schedule.entries),
            approved_by=approved_by,
            message=f"published {len(schedule.entries)} schedule entries ({mode.value})",
            run_id=schedule.run_id,
        )
        self.receipts.append(receipt)
        self.published.append(PublishedSchedule(receipt=receipt, schedule=schedule))
        log.info(
            "writeback.mock.published",
            run_id=schedule.run_id,
            mode=mode.value,
            entries=len(schedule.entries),
            approved_by=approved_by,
        )
        return receipt


__all__ = [
    "AutoPublishRule",
    "MockWritebackGateway",
    "PublishedSchedule",
    "ReadOnlyWritebackGateway",
    "WritebackGateway",
    "WritebackReceipt",
    "WritebackStatus",
    "check_publish_preconditions",
]
