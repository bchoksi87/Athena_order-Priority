"""SnapshotService: the one way services obtain engine input and active rules.

``load_snapshot`` delegates to :class:`app.db.snapshot_builder.DbSnapshotBuilder`
(open orders + operations, resources, calendars, active locks / overrides /
expedites and customer rules). Planner holds are represented as active
``HOLD_ORDER`` overrides in the snapshot; the constraint engine's readiness
pass already turns them into an ``ON_HOLD`` blocker (a later ``RELEASE_HOLD``
supersedes), so the order rows synced from the ERP are never rewritten.
:func:`effective_hold` exposes the same rule to the query services so list and
detail views agree with the engines.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.clock import Clock, ensure_utc
from app.db.records import ConfigVersionInfo
from app.db.repositories.config import ConfigRepository
from app.db.snapshot_builder import DbSnapshotBuilder
from app.domain.config import SystemConfig
from app.domain.enums import OverrideType
from app.domain.models import Order, PriorityOverride
from app.domain.snapshot import PlanningSnapshot
from app.services.base import Service


class SnapshotService(Service):
    def __init__(self, session: Session, clock: Clock) -> None:
        super().__init__(session, clock)
        self._config = ConfigRepository(session)

    def load_snapshot(
        self, as_of: datetime | None = None, *, include_closed_orders: bool = False
    ) -> PlanningSnapshot:
        """Build the :class:`PlanningSnapshot` as of ``as_of`` (default: the clock's now)."""
        at = ensure_utc(as_of) if as_of is not None else self.now()
        return DbSnapshotBuilder(self._session).build(at, include_closed_orders=include_closed_orders)

    def active_config(self) -> SystemConfig:
        return self._config.get_active()

    def active_config_info(self) -> ConfigVersionInfo | None:
        return self._config.get_active_info()


def latest_hold_decision(
    order_id: str, overrides: Iterable[PriorityOverride], now: datetime
) -> PriorityOverride | None:
    """The most recent active HOLD_ORDER / RELEASE_HOLD override of ``order_id``."""
    latest: PriorityOverride | None = None
    for override in overrides:
        if override.order_id != order_id or not override.is_active_at(now):
            continue
        if override.override_type not in (OverrideType.HOLD_ORDER, OverrideType.RELEASE_HOLD):
            continue
        if latest is None or (ensure_utc(override.created_at), override.override_id) > (
            ensure_utc(latest.created_at),
            latest.override_id,
        ):
            latest = override
    return latest


def effective_hold(
    order: Order, overrides: Iterable[PriorityOverride], now: datetime
) -> tuple[bool, str | None, PriorityOverride | None]:
    """``(on_hold, reason, hold_override)`` combining the ERP flag with planner holds.

    Mirrors ``app.engines.constraints.readiness``: an ERP hold always wins; otherwise
    the latest planner decision (HOLD_ORDER vs RELEASE_HOLD) applies.
    """
    if order.on_hold:
        return True, order.hold_reason, None
    decision = latest_hold_decision(order.order_id, overrides, now)
    if decision is not None and decision.override_type is OverrideType.HOLD_ORDER:
        return True, f"held by {decision.created_by}: {decision.reason}", decision
    return False, None, None


__all__ = ["SnapshotService", "effective_hold", "latest_hold_decision"]
