"""AuditService: the single writer of ``audit_log`` rows (contract §10).

Every override, lock, expedite, configuration change, acknowledgement and
user administration action goes through :meth:`AuditService.record` with the
acting user, the timestamp from the injected clock, the previous and new
values (JSON) and the mandatory reason. Read access (``query``) backs
``GET /audit`` and the per-order audit trail.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import structlog
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.security import CurrentUser
from app.db.records import AuditEntry
from app.db.repositories.audit import AuditFilters, AuditRepository
from app.db.snapshot_codec import to_jsonable
from app.services.base import PagedResult, Pagination, Service, actor_id

log = structlog.get_logger(__name__)

#: Entity types written by the services in this package.
ENTITY_ORDER = "order"
ENTITY_LOCK = "schedule_lock"
ENTITY_CONFIG = "system_config"
ENTITY_CUSTOMER_RULE = "customer_rule"
ENTITY_ALERT = "alert"
ENTITY_USER = "user"
ENTITY_DATA_QUALITY = "data_quality_run"


class AuditService(Service):
    def __init__(self, session: Session, clock: Clock, *, request_id: str | None = None) -> None:
        super().__init__(session, clock)
        self._repo = AuditRepository(session)
        self._request_id = request_id

    def record(
        self,
        user: CurrentUser | str,
        entity_type: str,
        entity_id: str,
        action: str,
        previous: Any,
        new: Any,
        reason: str | None,
        details: dict[str, Any] | None = None,
    ) -> AuditEntry:
        """Append one audit row; ``previous``/``new`` may be any domain value (JSON-encoded)."""
        user_id = actor_id(user)
        entry = self._repo.append(
            user_id=user_id,
            timestamp=self.now(),
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            previous_value=to_jsonable(previous) if previous is not None else None,
            new_value=to_jsonable(new) if new is not None else None,
            reason=reason,
            request_id=self._request_id,
            details=details,
        )
        log.info(
            "audit.recorded",
            audit_id=entry.audit_id,
            user_id=user_id,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
        )
        return entry

    def query(self, filters: AuditFilters | None, pagination: Pagination) -> PagedResult[AuditEntry]:
        page = self._repo.query(filters, offset=pagination.offset, limit=pagination.limit)
        return PagedResult(
            items=list(page.items), total=page.total, page=pagination.page, page_size=pagination.page_size
        )

    def for_entity(self, entity_type: str, entity_id: str, limit: int = 100) -> list[AuditEntry]:
        return self._repo.for_entity(entity_type, entity_id, limit=limit)

    def for_entities(self, refs: Iterable[tuple[str, str]], limit: int = 100) -> list[AuditEntry]:
        """Merged, newest-first trail of several entities (an order plus its locks, say)."""
        seen: set[str] = set()
        merged: list[AuditEntry] = []
        for entity_type, entity_id in refs:
            for entry in self._repo.for_entity(entity_type, entity_id, limit=limit):
                if entry.audit_id not in seen:
                    seen.add(entry.audit_id)
                    merged.append(entry)
        merged.sort(key=lambda e: (e.timestamp, e.audit_id), reverse=True)
        return merged[:limit]


# ------------------------------------------------------------------ diffing


def _flatten(value: Any, prefix: str, out: dict[str, Any]) -> None:
    if isinstance(value, dict):
        if not value:
            out[prefix] = {}
        for key, item in value.items():
            _flatten(item, f"{prefix}.{key}" if prefix else str(key), out)
    elif isinstance(value, list):
        if not value:
            out[prefix] = []
        for index, item in enumerate(value):
            _flatten(item, f"{prefix}[{index}]", out)
    else:
        out[prefix] = value


def flatten_json(value: Any) -> dict[str, Any]:
    """``{"a.b[0].c": leaf}`` view of a JSON document (deterministic key order)."""
    out: dict[str, Any] = {}
    _flatten(to_jsonable(value), "", out)
    return dict(sorted(out.items()))


def json_diff(previous: Any, new: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Only the leaves that differ: ``(previous_changed, new_changed)`` keyed by dotted path.

    Keys absent on one side are reported as ``None`` on that side so removals
    and additions are visible in the audit row.
    """
    before, after = flatten_json(previous), flatten_json(new)
    changed_previous: dict[str, Any] = {}
    changed_new: dict[str, Any] = {}
    for key in sorted(set(before) | set(after)):
        old, cur = before.get(key), after.get(key)
        if old != cur or (key in before) != (key in after):
            changed_previous[key] = old
            changed_new[key] = cur
    return changed_previous, changed_new


__all__ = [
    "ENTITY_ALERT",
    "ENTITY_CONFIG",
    "ENTITY_CUSTOMER_RULE",
    "ENTITY_DATA_QUALITY",
    "ENTITY_LOCK",
    "ENTITY_ORDER",
    "ENTITY_USER",
    "AuditService",
    "flatten_json",
    "json_diff",
]
