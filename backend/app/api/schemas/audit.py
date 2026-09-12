"""Audit trail schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.db.records import AuditEntry


class AuditEntryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    audit_id: str
    user_id: str
    timestamp: datetime
    entity_type: str
    entity_id: str
    action: str
    previous_value: dict[str, Any] | list[Any] | None = None
    new_value: dict[str, Any] | list[Any] | None = None
    reason: str | None = None
    request_id: str | None = None
    details: dict[str, Any] = {}

    @classmethod
    def from_record(cls, entry: AuditEntry) -> AuditEntryResponse:
        return cls(
            audit_id=entry.audit_id,
            user_id=entry.user_id,
            timestamp=entry.timestamp,
            entity_type=entry.entity_type,
            entity_id=entry.entity_id,
            action=entry.action,
            previous_value=entry.previous_value,
            new_value=entry.new_value,
            reason=entry.reason,
            request_id=entry.request_id,
            details=dict(entry.details),
        )


__all__ = ["AuditEntryResponse"]
