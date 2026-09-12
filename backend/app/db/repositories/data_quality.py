"""DataQualityRepository: issues detected per data-quality run."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import delete, func, select

from app.core.ids import new_id
from app.db.models import DataQualityIssueRow
from app.db.records import DataQualityIssueRecord, DataQualitySummary, Page
from app.db.repositories.base import DEFAULT_PAGE_SIZE, Repository
from app.domain.enums import DataQualityCode, DataQualitySeverity
from app.domain.results import DataQualityIssue


class DataQualityRepository(Repository):
    def replace_run(self, run_id: str, issues: Iterable[DataQualityIssue], detected_at: datetime) -> int:
        """Store the issues of ``run_id``, replacing any earlier rows for that run."""
        self._session.execute(delete(DataQualityIssueRow).where(DataQualityIssueRow.run_id == run_id))
        count = 0
        for issue in issues:
            self._session.add(
                DataQualityIssueRow(
                    issue_id=new_id("dq"),
                    run_id=run_id,
                    detected_at=detected_at,
                    code=issue.code.value,
                    severity=issue.severity.value,
                    entity_type=issue.entity_type,
                    entity_id=issue.entity_id,
                    message=issue.message,
                    field_name=issue.field_name,
                    recommendation=issue.recommendation,
                    details=dict(issue.details),
                )
            )
            count += 1
        self._flush()
        return count

    def latest_run_id(self) -> str | None:
        stmt = (
            select(DataQualityIssueRow.run_id)
            .order_by(DataQualityIssueRow.detected_at.desc(), DataQualityIssueRow.run_id.desc())
            .limit(1)
        )
        return self._session.execute(stmt).scalar_one_or_none()

    def list_issues(
        self,
        run_id: str | None = None,
        *,
        severity: DataQualitySeverity | None = None,
        code: DataQualityCode | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        offset: int = 0,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> Page:
        run_id = run_id or self.latest_run_id()
        if run_id is None:
            return Page(items=[], total=0, offset=offset, limit=limit)
        stmt = select(DataQualityIssueRow).where(DataQualityIssueRow.run_id == run_id)
        if severity is not None:
            stmt = stmt.where(DataQualityIssueRow.severity == severity.value)
        if code is not None:
            stmt = stmt.where(DataQualityIssueRow.code == code.value)
        if entity_type:
            stmt = stmt.where(DataQualityIssueRow.entity_type == entity_type)
        if entity_id:
            stmt = stmt.where(DataQualityIssueRow.entity_id == entity_id)
        stmt = stmt.order_by(
            DataQualityIssueRow.severity,
            DataQualityIssueRow.code,
            DataQualityIssueRow.entity_id,
            DataQualityIssueRow.issue_id,
        )
        page = self._paginate(stmt, offset, limit)
        page.items = [_record(r) for r in page.items]
        return page

    def issues_for_entity(
        self, entity_type: str, entity_id: str, run_id: str | None = None
    ) -> list[DataQualityIssue]:
        run_id = run_id or self.latest_run_id()
        if run_id is None:
            return []
        stmt = (
            select(DataQualityIssueRow)
            .where(
                DataQualityIssueRow.run_id == run_id,
                DataQualityIssueRow.entity_type == entity_type,
                DataQualityIssueRow.entity_id == entity_id,
            )
            .order_by(DataQualityIssueRow.severity, DataQualityIssueRow.code)
        )
        return [_domain(r) for r in self._session.execute(stmt).scalars()]

    def summary(self, run_id: str | None = None) -> DataQualitySummary:
        """Counts by severity/code/entity type plus number of entities with blocking issues."""
        run_id = run_id or self.latest_run_id()
        if run_id is None:
            return DataQualitySummary(None, None, 0, {}, {}, {}, 0)
        stmt = select(
            DataQualityIssueRow.severity,
            DataQualityIssueRow.code,
            DataQualityIssueRow.entity_type,
            DataQualityIssueRow.entity_id,
            DataQualityIssueRow.detected_at,
        ).where(DataQualityIssueRow.run_id == run_id)
        by_severity: Counter[str] = Counter()
        by_code: Counter[str] = Counter()
        by_entity_type: Counter[str] = Counter()
        blocked: set[tuple[str, str]] = set()
        detected_at: datetime | None = None
        total = 0
        for severity, code, entity_type, entity_id, at in self._session.execute(stmt).all():
            total += 1
            by_severity[severity] += 1
            by_code[code] += 1
            by_entity_type[entity_type] += 1
            detected_at = detected_at or at
            if severity == DataQualitySeverity.BLOCKING.value:
                blocked.add((entity_type, entity_id))
        return DataQualitySummary(
            run_id=run_id,
            detected_at=detected_at,
            total=total,
            by_severity=dict(sorted(by_severity.items())),
            by_code=dict(by_code.most_common()),
            by_entity_type=dict(sorted(by_entity_type.items())),
            blocked_entities=len(blocked),
        )

    def count(self, run_id: str) -> int:
        stmt = select(func.count(DataQualityIssueRow.issue_id)).where(DataQualityIssueRow.run_id == run_id)
        return int(self._session.execute(stmt).scalar_one())


def _record(row: DataQualityIssueRow) -> DataQualityIssueRecord:
    return DataQualityIssueRecord(
        issue_id=row.issue_id,
        run_id=row.run_id,
        detected_at=row.detected_at,
        code=row.code,
        severity=row.severity,
        entity_type=row.entity_type,
        entity_id=row.entity_id,
        message=row.message,
        field_name=row.field_name,
        recommendation=row.recommendation,
        details=dict(row.details or {}),
    )


def _domain(row: DataQualityIssueRow) -> DataQualityIssue:
    return DataQualityIssue(
        code=DataQualityCode(row.code),
        severity=DataQualitySeverity(row.severity),
        entity_type=row.entity_type,
        entity_id=row.entity_id,
        message=row.message,
        field_name=row.field_name,
        recommendation=row.recommendation,
        details=dict(row.details or {}),
    )


__all__ = ["DataQualityRepository"]
