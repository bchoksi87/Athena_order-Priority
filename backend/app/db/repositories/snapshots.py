"""SnapshotRepository: compressed PlanningSnapshot storage for reproducibility."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from app.core.errors import NotFoundError
from app.core.ids import new_id
from app.db.models import InputSnapshotRow
from app.db.records import Page, SnapshotInfo
from app.db.repositories.base import DEFAULT_PAGE_SIZE, Repository
from app.db.snapshot_codec import CODEC_GZIP_JSON, decode_snapshot, encode_snapshot, payload_digest
from app.domain.snapshot import PlanningSnapshot


class SnapshotRepository(Repository):
    def save(self, snapshot: PlanningSnapshot, *, created_by: str | None = None) -> SnapshotInfo:
        """Compress and store ``snapshot``; assigns ``snapshot.snapshot_id`` when missing."""
        snapshot_id = snapshot.snapshot_id or new_id("snap")
        snapshot.snapshot_id = snapshot_id
        payload = encode_snapshot(snapshot)
        row = InputSnapshotRow(
            snapshot_id=snapshot_id,
            as_of=snapshot.as_of,
            source=snapshot.source,
            codec=CODEC_GZIP_JSON,
            payload=payload,
            size_bytes=len(payload),
            sha256=payload_digest(payload),
            summary=snapshot.summary(),
            created_by=created_by,
        )
        self._session.add(row)
        self._flush()
        return _info(row)

    def load(self, snapshot_id: str) -> PlanningSnapshot:
        row = self._session.get(InputSnapshotRow, snapshot_id)
        if row is None:
            raise NotFoundError(f"snapshot '{snapshot_id}' not found", details={"snapshot_id": snapshot_id})
        snapshot = decode_snapshot(row.payload, row.codec)
        snapshot.snapshot_id = row.snapshot_id
        return snapshot

    def get_info(self, snapshot_id: str) -> SnapshotInfo:
        row = self._session.get(InputSnapshotRow, snapshot_id)
        if row is None:
            raise NotFoundError(f"snapshot '{snapshot_id}' not found", details={"snapshot_id": snapshot_id})
        return _info(row)

    def list(self, *, offset: int = 0, limit: int = DEFAULT_PAGE_SIZE) -> Page:
        stmt = select(InputSnapshotRow).order_by(
            InputSnapshotRow.as_of.desc(), InputSnapshotRow.snapshot_id.desc()
        )
        page = self._paginate(stmt, offset, limit)
        page.items = [_info(r) for r in page.items]
        return page

    def delete_older_than(self, cutoff: datetime) -> int:
        stmt = select(InputSnapshotRow).where(InputSnapshotRow.as_of < cutoff)
        count = 0
        for row in self._session.execute(stmt).scalars():
            self._session.delete(row)
            count += 1
        self._flush()
        return count


def _info(row: InputSnapshotRow) -> SnapshotInfo:
    return SnapshotInfo(
        snapshot_id=row.snapshot_id,
        as_of=row.as_of,
        source=row.source,
        codec=row.codec,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        summary=dict(row.summary or {}),
        created_at=row.created_at,
        created_by=row.created_by,
    )


__all__ = ["SnapshotRepository"]
