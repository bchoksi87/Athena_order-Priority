"""ExpediteService: temporary priority boosts (spec Phase 16).

Boost and duration default to / are capped by ``PriorityProfile.expedite``
of the active configuration. An expedite is time-boxed (``starts_at`` ..
``expires_at``); once it expires the normal calculation resumes. A new
expedite on an order supersedes the earlier active one (recorded in the
audit row as the previous value).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import structlog
from sqlalchemy.orm import Session

from app.core.clock import Clock, ensure_utc
from app.core.errors import ConflictError, ValidationError
from app.core.ids import new_id
from app.core.security import CurrentUser
from app.db.repositories.orders import OrderRepository
from app.db.repositories.overlays import ExpediteRepository
from app.domain.models import Expedite
from app.services.audit_service import ENTITY_ORDER, AuditService
from app.services.base import Service, actor_id, require_reason
from app.services.snapshot_service import SnapshotService

log = structlog.get_logger(__name__)


class ExpediteService(Service):
    def __init__(
        self,
        session: Session,
        clock: Clock,
        audit: AuditService,
        snapshots: SnapshotService | None = None,
    ) -> None:
        super().__init__(session, clock)
        self._audit = audit
        self._snapshots = snapshots or SnapshotService(session, clock)
        self._expedites = ExpediteRepository(session)
        self._orders = OrderRepository(session)

    def get(self, expedite_id: str) -> Expedite:
        return self._expedites.get(expedite_id)

    def list_active(self, order_id: str | None = None) -> list[Expedite]:
        now = self.now()
        if order_id is not None:
            return [e for e in self._expedites.list_for_order(order_id) if e.is_active_at(now)]
        return [e for e in self._expedites.list_active(now) if e.is_active_at(now)]

    def expedite(
        self,
        order_id: str,
        user: CurrentUser | str,
        reason: str,
        *,
        boost_points: float | None = None,
        duration_hours: float | None = None,
        starts_at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> Expedite:
        reason = require_reason(reason)
        now = self.now()
        order = self._orders.get(order_id)
        if not order.is_open:
            raise ConflictError(
                f"order '{order_id}' is closed ({order.order_status.value}) and cannot be expedited",
                details={"order_status": order.order_status.value},
            )
        rules = self._snapshots.active_config().priority_profile.expedite
        boost = float(boost_points) if boost_points is not None else rules.default_boost_points
        if boost <= 0 or boost > rules.max_boost_points:
            raise ValidationError(
                f"boost_points must be in (0, {rules.max_boost_points:g}]",
                details={"boost_points": boost, "max_boost_points": rules.max_boost_points},
            )
        start = ensure_utc(starts_at) if starts_at is not None else now
        if start < now:
            raise ValidationError(
                "starts_at must not be in the past", details={"starts_at": start.isoformat()}
            )
        if expires_at is not None:
            end = ensure_utc(expires_at)
            if duration_hours is not None:
                raise ValidationError("give either duration_hours or expires_at, not both")
        else:
            hours = float(duration_hours) if duration_hours is not None else rules.default_duration_hours
            if hours <= 0:
                raise ValidationError("duration_hours must be positive", details={"duration_hours": hours})
            end = start + timedelta(hours=hours)
        duration = (end - start).total_seconds() / 3600.0
        if duration <= 0:
            raise ValidationError(
                "expires_at must be after starts_at",
                details={"starts_at": start.isoformat(), "expires_at": end.isoformat()},
            )
        if duration > rules.max_duration_hours:
            raise ValidationError(
                f"expedite duration {duration:g} h exceeds the maximum of {rules.max_duration_hours:g} h",
                details={"duration_hours": duration, "max_duration_hours": rules.max_duration_hours},
            )

        previous = [e for e in self._expedites.list_for_order(order_id) if e.is_active_at(now)]
        for old in previous:
            self._expedites.deactivate(old.expedite_id, released_by=actor_id(user), at=now)
        expedite = self._expedites.add(
            Expedite(
                expedite_id=new_id("exp"),
                order_id=order_id,
                created_by=actor_id(user),
                created_at=now,
                reason=reason,
                boost_points=boost,
                starts_at=start,
                expires_at=end,
            )
        )
        self._audit.record(
            user,
            ENTITY_ORDER,
            order_id,
            "expedite.create",
            {"active_expedites": previous},
            {"expedite": expedite, "boost_points": boost, "duration_hours": duration},
            reason,
            {
                "expedite_id": expedite.expedite_id,
                "superseded_expedite_ids": [e.expedite_id for e in previous],
            },
        )
        log.info("expedite.created", expedite_id=expedite.expedite_id, order_id=order_id, boost=boost)
        return expedite

    def cancel(self, expedite_id: str, user: CurrentUser | str, reason: str) -> Expedite:
        reason = require_reason(reason)
        current = self._expedites.get(expedite_id)
        if not current.active:
            raise ConflictError(f"expedite '{expedite_id}' is already cancelled")
        updated = self._expedites.deactivate(expedite_id, released_by=actor_id(user), at=self.now())
        self._audit.record(
            user,
            ENTITY_ORDER,
            current.order_id,
            "expedite.cancel",
            current,
            updated,
            reason,
            {"expedite_id": expedite_id},
        )
        return updated


__all__ = ["ExpediteService"]
