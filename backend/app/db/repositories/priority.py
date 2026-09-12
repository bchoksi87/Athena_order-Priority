"""PriorityResultRepository: persisted priority-engine output per run."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import delete, func, select

from app.db.mappers import priority_result_from_row, priority_result_to_row
from app.db.models import PriorityResultRow
from app.db.repositories.base import Repository, chunked
from app.domain.results import PriorityResult


class PriorityResultRepository(Repository):
    def save_run(self, run_id: str, results: Iterable[PriorityResult]) -> int:
        """Bulk-insert the results of one run (replacing any prior rows for ``run_id``).

        ``rank`` is filled from score order (descending, order_id tiebreak) when
        the engine left it ``None``.
        """
        items = sorted(results, key=lambda r: (-r.score, r.order_id))
        self._session.execute(delete(PriorityResultRow).where(PriorityResultRow.run_id == run_id))
        for position, result in enumerate(items, start=1):
            if result.rank is None:
                result.rank = position
            self._session.add(priority_result_to_row(result, run_id))
        self._flush()
        return len(items)

    def latest_run_id(self) -> str | None:
        stmt = (
            select(PriorityResultRow.run_id)
            .order_by(PriorityResultRow.computed_at.desc(), PriorityResultRow.run_id.desc())
            .limit(1)
        )
        return self._session.execute(stmt).scalar_one_or_none()

    def results_for_run(self, run_id: str) -> dict[str, PriorityResult]:
        stmt = select(PriorityResultRow).where(PriorityResultRow.run_id == run_id)
        return {r.order_id: priority_result_from_row(r) for r in self._session.execute(stmt).scalars()}

    def ranking(
        self, run_id: str | None = None, *, limit: int | None = None, offset: int = 0
    ) -> list[PriorityResult]:
        """Results of ``run_id`` (default: latest run) ordered by rank."""
        run_id = run_id or self.latest_run_id()
        if run_id is None:
            return []
        stmt = (
            select(PriorityResultRow)
            .where(PriorityResultRow.run_id == run_id)
            .order_by(PriorityResultRow.rank, PriorityResultRow.score.desc(), PriorityResultRow.order_id)
            .offset(offset)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return [priority_result_from_row(r) for r in self._session.execute(stmt).scalars()]

    def latest_for_order(self, order_id: str) -> PriorityResult | None:
        stmt = (
            select(PriorityResultRow)
            .where(PriorityResultRow.order_id == order_id)
            .order_by(PriorityResultRow.computed_at.desc(), PriorityResultRow.run_id.desc())
            .limit(1)
        )
        row = self._session.execute(stmt).scalar_one_or_none()
        return priority_result_from_row(row) if row else None

    def latest_for_orders(
        self, order_ids: Iterable[str], *, include_breakdown: bool = True
    ) -> dict[str, PriorityResult]:
        """Latest-run result for each order (from the latest run only).

        ``include_breakdown=False`` returns results without the decoded
        factor/adjustment lists (scalar columns and explanation text only),
        which is an order of magnitude cheaper for list views.
        """
        run_id = self.latest_run_id()
        if run_id is None:
            return {}
        out: dict[str, PriorityResult] = {}
        for ids in chunked(set(order_ids)):
            stmt = select(PriorityResultRow).where(
                PriorityResultRow.run_id == run_id, PriorityResultRow.order_id.in_(ids)
            )
            for row in self._session.execute(stmt).scalars():
                out[row.order_id] = priority_result_from_row(row, include_breakdown=include_breakdown)
        return out

    def history_for_order(self, order_id: str, limit: int = 20) -> list[PriorityResult]:
        stmt = (
            select(PriorityResultRow)
            .where(PriorityResultRow.order_id == order_id)
            .order_by(PriorityResultRow.computed_at.desc())
            .limit(limit)
        )
        return [priority_result_from_row(r) for r in self._session.execute(stmt).scalars()]

    def count_for_run(self, run_id: str) -> int:
        stmt = select(func.count(PriorityResultRow.result_id)).where(PriorityResultRow.run_id == run_id)
        return int(self._session.execute(stmt).scalar_one())

    def delete_run(self, run_id: str) -> int:
        result = self._session.execute(delete(PriorityResultRow).where(PriorityResultRow.run_id == run_id))
        self._flush()
        return int(getattr(result, "rowcount", 0) or 0)


__all__ = ["PriorityResultRepository"]
