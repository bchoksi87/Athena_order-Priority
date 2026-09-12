"""WritebackService: hand an approved schedule to the ERP gateway (spec Phase 26).

Gateway selection follows ``settings.writeback_mode``:

* ``READ_ONLY`` (default) → :class:`ReadOnlyWritebackGateway`: nothing leaves
  the system, a ``skipped_read_only`` receipt is recorded and the version still
  becomes the active plan *inside* PPSE;
* ``APPROVAL`` / ``WRITEBACK`` / ``CONTROLLED_AUTO`` → :class:`MockWritebackGateway`
  **until a real ERP gateway exists**: the ERP/MES is an unknown external system
  (contract §1), so the transport is a stand-in that enforces the mode rules and
  records what would have been sent. Replace :meth:`WritebackService.gateway`
  once the ERP exposes a scheduling endpoint.

``CONTROLLED_AUTO`` publishes only from the replanning worker (``auto=True``,
under the replanning rules); a *manual* publish while that mode is configured
is sent as ``APPROVAL`` (a named user approved it) and the receipt says so.
The receipt is stored in the schedule version's ``details`` JSON.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import structlog
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.config import Settings
from app.core.errors import ConflictError, IntegrationError
from app.core.security import CurrentUser
from app.db.records import ScheduleVersionInfo
from app.db.repositories.schedule import ScheduleRepository
from app.domain.enums import WritebackMode
from app.domain.results import ScheduleEntry, ScheduleResult
from app.integration.writeback import (
    AutoPublishRule,
    MockWritebackGateway,
    ReadOnlyWritebackGateway,
    WritebackGateway,
    WritebackReceipt,
)
from app.services.base import Service, actor_id

log = structlog.get_logger(__name__)

RECEIPT_KEY = "writeback_receipt"


def _accept_all(schedule: ScheduleResult) -> tuple[bool, str]:
    """Auto-publish rule for CONTROLLED_AUTO: the replanning engine already decided."""
    return True, f"replanning rules accepted the schedule ({len(schedule.entries)} entries)"


class WritebackService(Service):
    def __init__(
        self,
        session: Session,
        clock: Clock,
        settings: Settings,
        *,
        gateway: WritebackGateway | None = None,
        auto_rule: AutoPublishRule | None = None,
    ) -> None:
        super().__init__(session, clock)
        self._settings = settings
        self._gateway = gateway
        self._auto_rule = auto_rule or _accept_all
        self._versions = ScheduleRepository(session)

    @property
    def mode(self) -> WritebackMode:
        return self._settings.writeback_mode

    @property
    def gateway(self) -> WritebackGateway:
        """The configured gateway (see module docstring for the mock stand-in)."""
        if self._gateway is None:
            if self.mode is WritebackMode.READ_ONLY:
                self._gateway = ReadOnlyWritebackGateway(self._clock)
            else:
                self._gateway = MockWritebackGateway(self._clock, auto_rule=self._auto_rule)
        return self._gateway

    def effective_mode(self, *, auto: bool) -> WritebackMode:
        """Mode sent to the gateway: CONTROLLED_AUTO only for the worker, APPROVAL for a person."""
        if self.mode is WritebackMode.CONTROLLED_AUTO and not auto:
            return WritebackMode.APPROVAL
        return self.mode

    def publish(
        self,
        version: ScheduleVersionInfo,
        entries: Sequence[ScheduleEntry],
        user: CurrentUser | str,
        *,
        auto: bool = False,
    ) -> WritebackReceipt:
        """Send ``version`` through the gateway and store the receipt on the version.

        Raises :class:`ConflictError` when the worker asks for an automatic publish
        while the configured mode is not ``CONTROLLED_AUTO`` and
        :class:`IntegrationError` when the gateway fails or its rules reject the plan.
        """
        if auto and self.mode is not WritebackMode.CONTROLLED_AUTO:
            raise ConflictError(
                "automatic publishing requires writeback mode 'controlled_auto' "
                f"(configured: {self.mode.value})",
                details={"writeback_mode": self.mode.value},
            )
        mode = self.effective_mode(auto=auto)
        schedule = schedule_result_from_version(version, entries)
        receipt = self.gateway.publish(schedule, mode, approved_by=actor_id(user))
        receipt.details.setdefault("configured_mode", self.mode.value)
        receipt.details.setdefault("version_number", version.version_number)
        self._versions.update_details(version.version_number, {RECEIPT_KEY: receipt.to_dict()})
        log.info(
            "writeback.receipt",
            schedule_version=version.version_number,
            mode=mode.value,
            status=receipt.status,
            entries=receipt.entries_published,
            user_id=actor_id(user),
        )
        if receipt.status in ("failed", "rejected_by_rules"):
            raise IntegrationError(
                f"schedule v{version.version_number} was not published: {receipt.message}",
                details={"receipt": receipt.to_dict()},
            )
        return receipt


def schedule_result_from_version(
    version: ScheduleVersionInfo, entries: Sequence[ScheduleEntry]
) -> ScheduleResult:
    """Rebuild the engine-shaped :class:`ScheduleResult` of a stored version (metrics + quality + entries)."""
    from app.db.snapshot_codec import decode_dataclass
    from app.domain.results import ScheduleMetrics, ScheduleQuality, UnscheduledItem

    metrics = decode_dataclass(version.metrics, ScheduleMetrics) if version.metrics else ScheduleMetrics()
    quality = decode_dataclass(version.quality, ScheduleQuality) if version.quality else None
    unscheduled: list[UnscheduledItem] = []
    for item in version.unscheduled:
        try:
            unscheduled.append(decode_dataclass(item, UnscheduledItem))
        except (TypeError, ValueError, KeyError):  # tolerate older/partial rows
            continue
    return ScheduleResult(
        algorithm=version.algorithm,
        algorithm_version=version.algorithm_version,
        profile_id=version.profile_id,
        profile_version=version.profile_version,
        config_version=version.config_version,
        generated_at=version.generated_at,
        horizon_start=version.horizon_start,
        horizon_end=version.horizon_end,
        entries=list(entries),
        unscheduled=unscheduled,
        metrics=metrics,
        quality=quality,
        warnings=list(version.warnings),
        run_id=version.run_id,
    )


def receipt_of(version: ScheduleVersionInfo) -> dict[str, Any] | None:
    receipt = version.details.get(RECEIPT_KEY)
    return dict(receipt) if isinstance(receipt, dict) else None


__all__ = ["RECEIPT_KEY", "WritebackService", "receipt_of", "schedule_result_from_version"]
