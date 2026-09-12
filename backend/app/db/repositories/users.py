"""UserRepository: local accounts for JWT authentication."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.ids import new_id
from app.core.security import hash_password, verify_password
from app.db.models import UserRow
from app.db.records import UserRecord
from app.db.repositories.base import Repository
from app.domain.enums import Role


class UserRepository(Repository):
    def get_by_username(self, username: str) -> UserRecord | None:
        row = self._row_by_username(username)
        return _record(row) if row else None

    def get(self, user_id: str) -> UserRecord:
        row = self._session.get(UserRow, user_id)
        if row is None:
            raise NotFoundError(f"user '{user_id}' not found", details={"user_id": user_id})
        return _record(row)

    def create(
        self,
        *,
        username: str,
        password: str,
        role: Role,
        display_name: str,
        email: str | None = None,
        user_id: str | None = None,
    ) -> UserRecord:
        username = username.strip().lower()
        if not username:
            raise ValidationError("username must not be empty")
        if self._row_by_username(username) is not None:
            raise ConflictError(f"username '{username}' already exists", details={"username": username})
        row = UserRow(
            user_id=user_id or new_id("usr"),
            username=username,
            password_hash=hash_password(password),
            role=role.value,
            display_name=display_name,
            email=email,
            active=True,
        )
        self._session.add(row)
        self._flush()
        return _record(row)

    def list(self, active_only: bool = False) -> list[UserRecord]:
        stmt = select(UserRow).order_by(UserRow.username)
        if active_only:
            stmt = stmt.where(UserRow.active.is_(True))
        return [_record(r) for r in self._session.execute(stmt).scalars()]

    def authenticate(self, username: str, password: str) -> UserRecord | None:
        """Return the user when the credentials match an active account, else ``None``."""
        row = self._row_by_username(username.strip().lower())
        if row is None or not row.active:
            return None
        if not verify_password(password, row.password_hash):
            return None
        return _record(row)

    def set_password(self, user_id: str, password: str) -> None:
        row = self._session.get(UserRow, user_id)
        if row is None:
            raise NotFoundError(f"user '{user_id}' not found", details={"user_id": user_id})
        row.password_hash = hash_password(password)
        self._flush()

    def set_active(self, user_id: str, active: bool) -> UserRecord:
        row = self._session.get(UserRow, user_id)
        if row is None:
            raise NotFoundError(f"user '{user_id}' not found", details={"user_id": user_id})
        row.active = active
        self._flush()
        return _record(row)

    def record_login(self, user_id: str, at: datetime) -> None:
        row = self._session.get(UserRow, user_id)
        if row is not None:
            row.last_login_at = at
            self._flush()

    def _row_by_username(self, username: str) -> UserRow | None:
        stmt = select(UserRow).where(UserRow.username == username.strip().lower())
        return self._session.execute(stmt).scalar_one_or_none()


def _record(row: UserRow) -> UserRecord:
    return UserRecord(
        user_id=row.user_id,
        username=row.username,
        role=Role(row.role),
        display_name=row.display_name,
        email=row.email,
        active=row.active,
        last_login_at=row.last_login_at,
        created_at=row.created_at,
    )


__all__ = ["UserRepository"]
