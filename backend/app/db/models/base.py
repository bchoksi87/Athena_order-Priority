"""Shared column helpers for the ORM models."""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, String

from app.core.db import Base, TimestampMixin, UTCDateTime

#: Identifier columns (ERP ids kept verbatim, internal ULIDs are 30 chars with prefix).
ID_LENGTH = 64
CODE_LENGTH = 32
NAME_LENGTH = 255

IdString = String(ID_LENGTH)
CodeString = String(CODE_LENGTH)
NameString = String(NAME_LENGTH)

#: Mutable JSON columns hold plain ``dict``/``list`` values only.
JSONDict = JSON

JsonValue = dict[str, Any] | list[Any] | None

__all__ = [
    "CODE_LENGTH",
    "ID_LENGTH",
    "NAME_LENGTH",
    "Base",
    "CodeString",
    "IdString",
    "JSONDict",
    "JsonValue",
    "NameString",
    "TimestampMixin",
    "UTCDateTime",
]
