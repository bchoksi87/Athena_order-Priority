"""Shared service plumbing: pagination, mandatory reasons, actor resolution.

Application services (contract §3 ``app/services``) orchestrate engines,
repositories and the audit trail. They receive their collaborators through
the constructor and never touch FastAPI; :mod:`app.api.deps` wires them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Generic, TypeVar

from sqlalchemy.orm import Session

from app.core.clock import Clock, ensure_utc
from app.core.errors import ValidationError
from app.core.security import CurrentUser

T = TypeVar("T")

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 500
MAX_REASON_LENGTH = 2000


@dataclass(slots=True, frozen=True)
class Pagination:
    """1-based page selection used by every list endpoint."""

    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE

    def __post_init__(self) -> None:
        if self.page < 1:
            raise ValidationError("page must be >= 1", details={"page": self.page})
        if self.page_size < 1 or self.page_size > MAX_PAGE_SIZE:
            raise ValidationError(
                f"page_size must be in 1..{MAX_PAGE_SIZE}", details={"page_size": self.page_size}
            )

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size


@dataclass(slots=True)
class PagedResult(Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int

    @property
    def pages(self) -> int:
        return (self.total + self.page_size - 1) // self.page_size if self.page_size else 0

    @property
    def has_more(self) -> bool:
        return self.page * self.page_size < self.total

    @classmethod
    def slice(cls, items: list[T], pagination: Pagination) -> PagedResult[T]:
        """Paginate an already filtered/sorted in-memory list."""
        start = pagination.offset
        return cls(
            items=items[start : start + pagination.page_size],
            total=len(items),
            page=pagination.page,
            page_size=pagination.page_size,
        )


def require_reason(reason: str | None) -> str:
    """Every mutating action carries a non-empty reason (spec Phase 9/22)."""
    text = (reason or "").strip()
    if not text:
        raise ValidationError("reason is required", details={"field": "reason"})
    if len(text) > MAX_REASON_LENGTH:
        raise ValidationError(
            f"reason must be at most {MAX_REASON_LENGTH} characters", details={"field": "reason"}
        )
    return text


def actor_id(user: CurrentUser | str) -> str:
    """The user id recorded on overlays and audit rows."""
    if isinstance(user, CurrentUser):
        return user.user_id
    if not user:
        raise ValidationError("user is required")
    return user


def ensure_future(value: datetime | None, now: datetime, field_name: str) -> datetime | None:
    """Normalise an optional expiry to UTC and reject instants that are not after ``now``."""
    if value is None:
        return None
    value = ensure_utc(value)
    if value <= now:
        raise ValidationError(
            f"{field_name} must be in the future",
            details={field_name: value.isoformat(), "now": now.isoformat()},
        )
    return value


class Service:
    """Base class holding the unit-of-work session and the injected clock."""

    def __init__(self, session: Session, clock: Clock) -> None:
        self._session = session
        self._clock = clock

    @property
    def session(self) -> Session:
        return self._session

    @property
    def clock(self) -> Clock:
        return self._clock

    def now(self) -> datetime:
        return ensure_utc(self._clock.now())


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "MAX_REASON_LENGTH",
    "PagedResult",
    "Pagination",
    "Service",
    "actor_id",
    "ensure_future",
    "require_reason",
]
