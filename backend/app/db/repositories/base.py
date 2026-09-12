"""Shared repository plumbing."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any, TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.core.errors import ValidationError
from app.db.records import Page

MAX_PAGE_SIZE = 1000
DEFAULT_PAGE_SIZE = 100
#: Bulk ``IN (...)`` lookups are chunked to keep statements portable.
IN_CHUNK = 500

RowT = TypeVar("RowT")


class Repository:
    """Base class: holds the session and offers query helpers."""

    def __init__(self, session: Session) -> None:
        self._session = session

    @property
    def session(self) -> Session:
        return self._session

    def _flush(self) -> None:
        self._session.flush()

    def _rows_by_ids(
        self, model: type[RowT], column: InstrumentedAttribute[str], ids: Iterable[str]
    ) -> dict[str, RowT]:
        """Bulk load rows keyed by ``column`` (chunked ``IN`` queries)."""
        out: dict[str, RowT] = {}
        for batch in chunked(set(ids)):
            for row in self._session.execute(select(model).where(column.in_(batch))).scalars():
                out[getattr(row, column.key)] = row
        return out

    def _count(self, stmt: Select[Any]) -> int:
        count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
        return int(self._session.execute(count_stmt).scalar_one())

    def _paginate(self, stmt: Select[Any], offset: int, limit: int) -> Page:
        """Run ``stmt`` with offset/limit and count the unbounded total."""
        offset, limit = validate_paging(offset, limit)
        total = self._count(stmt)
        rows = list(self._session.execute(stmt.offset(offset).limit(limit)).scalars().all())
        return Page(items=rows, total=total, offset=offset, limit=limit)


def validate_paging(offset: int, limit: int) -> tuple[int, int]:
    if offset < 0:
        raise ValidationError("offset must be >= 0", details={"offset": offset})
    if limit <= 0 or limit > MAX_PAGE_SIZE:
        raise ValidationError(f"limit must be in 1..{MAX_PAGE_SIZE}", details={"limit": limit})
    return offset, limit


def chunked(values: Iterable[Any], size: int = IN_CHUNK) -> Iterable[Sequence[Any]]:
    batch: list[Any] = []
    for value in values:
        batch.append(value)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


__all__ = ["DEFAULT_PAGE_SIZE", "IN_CHUNK", "MAX_PAGE_SIZE", "Repository", "chunked", "validate_paging"]
