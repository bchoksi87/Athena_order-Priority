"""UserService: administrator account management (spec Phase 27/28).

Passwords never reach the audit trail; only the fact that they were reset.
"""

from __future__ import annotations

import structlog
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.errors import ConflictError, ValidationError
from app.core.security import CurrentUser
from app.db.records import UserRecord
from app.db.repositories.users import UserRepository
from app.domain.enums import Role
from app.services.audit_service import ENTITY_USER, AuditService
from app.services.base import Service, actor_id, require_reason

log = structlog.get_logger(__name__)

MIN_PASSWORD_LENGTH = 8


class UserService(Service):
    def __init__(self, session: Session, clock: Clock, audit: AuditService) -> None:
        super().__init__(session, clock)
        self._audit = audit
        self._users = UserRepository(session)

    def list(self, *, active_only: bool = False) -> list[UserRecord]:
        return self._users.list(active_only=active_only)

    def get(self, user_id: str) -> UserRecord:
        return self._users.get(user_id)

    def create(
        self,
        actor: CurrentUser | str,
        *,
        username: str,
        password: str,
        role: Role,
        display_name: str,
        email: str | None = None,
        reason: str | None = None,
    ) -> UserRecord:
        _check_password(password)
        record = self._users.create(
            username=username, password=password, role=role, display_name=display_name, email=email
        )
        self._audit.record(
            actor,
            ENTITY_USER,
            record.user_id,
            "user.create",
            None,
            _public(record),
            reason or "user created by administrator",
            {"username": record.username, "role": role.value},
        )
        log.info("user.created", user_id=record.user_id, role=role.value, by=actor_id(actor))
        return record

    def set_active(self, user_id: str, active: bool, actor: CurrentUser | str, reason: str) -> UserRecord:
        reason = require_reason(reason)
        current = self._users.get(user_id)
        if not active and current.user_id == actor_id(actor):
            raise ConflictError("you cannot deactivate your own account")
        if current.active == active:
            raise ConflictError(
                f"user '{user_id}' is already {'active' if active else 'inactive'}",
                details={"active": active},
            )
        updated = self._users.set_active(user_id, active)
        self._audit.record(
            actor,
            ENTITY_USER,
            user_id,
            "user.activate" if active else "user.deactivate",
            _public(current),
            _public(updated),
            reason,
            {"username": current.username},
        )
        return updated

    def reset_password(
        self, user_id: str, new_password: str, actor: CurrentUser | str, reason: str
    ) -> UserRecord:
        reason = require_reason(reason)
        _check_password(new_password)
        current = self._users.get(user_id)
        self._users.set_password(user_id, new_password)
        self._audit.record(
            actor,
            ENTITY_USER,
            user_id,
            "user.reset_password",
            {"password": "***"},
            {"password": "*** (reset)"},
            reason,
            {"username": current.username},
        )
        log.info("user.password_reset", user_id=user_id, by=actor_id(actor))
        return current


def _check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValidationError(
            f"password must be at least {MIN_PASSWORD_LENGTH} characters", details={"field": "password"}
        )


def _public(record: UserRecord) -> dict[str, object]:
    return {
        "user_id": record.user_id,
        "username": record.username,
        "role": record.role.value,
        "display_name": record.display_name,
        "email": record.email,
        "active": record.active,
    }


__all__ = ["MIN_PASSWORD_LENGTH", "UserService"]
