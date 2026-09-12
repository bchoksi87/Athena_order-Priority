"""Shared API schemas (errors, pagination envelopes, generic messages)."""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

T = TypeVar("T")


class ErrorResponse(BaseModel):
    """Shape produced for every :class:`app.core.errors.AppError` (``error.to_dict()``)."""

    model_config = ConfigDict(extra="forbid")
    error: str = Field(description="Machine-readable error code")
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class MessageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str


class PageMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    total: int
    offset: int
    limit: int
    has_more: bool


class PagedResponse(BaseModel, Generic[T]):
    model_config = ConfigDict(extra="forbid")
    items: list[T]
    meta: PageMeta

    @classmethod
    def build(cls, items: list[T], *, total: int, offset: int, limit: int) -> PagedResponse[T]:
        return cls(
            items=items,
            meta=PageMeta(total=total, offset=offset, limit=limit, has_more=offset + len(items) < total),
        )


class PagingParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=1000)


__all__ = ["ErrorResponse", "MessageResponse", "PageMeta", "PagedResponse", "PagingParams"]


class PageResponse(BaseModel, Generic[T]):
    """Page-number pagination envelope used by the resource list endpoints."""

    model_config = ConfigDict(extra="forbid")
    items: list[T]
    total: int
    page: int
    page_size: int
    pages: int
    has_more: bool

    @classmethod
    def build(cls, items: list[T], *, total: int, page: int, page_size: int) -> PageResponse[T]:
        pages = (total + page_size - 1) // page_size if page_size else 0
        return cls(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            pages=pages,
            has_more=page * page_size < total,
        )


class ReasonBody(BaseModel):
    """Base for every mutating request: the reason is mandatory and non-blank."""

    model_config = ConfigDict(extra="forbid")
    reason: str = Field(
        min_length=1,
        max_length=2000,
        pattern=r"\S",
        description="Why this action is taken (audited); must not be blank",
    )

    @field_validator("reason")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


__all__ = [*__all__, "PageResponse", "ReasonBody"]
