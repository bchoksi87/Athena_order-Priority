"""Repositories for machines (+downtime), materials, tooling and calendars."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import select

from app.core.errors import NotFoundError, ValidationError
from app.core.ids import new_id
from app.db.mappers import (
    calendar_from_row,
    calendar_to_row,
    machine_from_row,
    machine_to_row,
    material_from_row,
    material_to_row,
    tooling_from_row,
    tooling_to_row,
)
from app.db.models import (
    DOWNTIME_KINDS,
    CalendarSpecRow,
    MachineDowntimeRow,
    MachineRow,
    MaterialRow,
    ToolingRow,
)
from app.db.repositories.base import Repository, chunked
from app.domain.enums import MachineStatus, ProcessType
from app.domain.models import CalendarSpec, Machine, Material, TimeWindow, Tooling


class MachineRepository(Repository):
    def upsert(self, machines: Iterable[Machine], synced_at: datetime | None = None) -> int:
        """Insert/update machines; downtime windows are replaced wholesale."""
        items = list(machines)
        if not items:
            return 0
        existing = self._rows_by_ids(MachineRow, MachineRow.machine_id, [m.machine_id for m in items])
        for machine in items:
            row = existing.get(machine.machine_id)
            if row is not None:
                row.downtime.clear()
            row = machine_to_row(machine, row)
            row.synced_at = synced_at
            if machine.machine_id not in existing:
                self._session.add(row)
        self._flush()
        return len(items)

    def get(self, machine_id: str) -> Machine:
        row = self._session.get(MachineRow, machine_id)
        if row is None:
            raise NotFoundError(f"machine '{machine_id}' not found", details={"machine_id": machine_id})
        return machine_from_row(row)

    def list_all(
        self,
        *,
        machine_group: str | None = None,
        process_type: ProcessType | None = None,
        status: MachineStatus | None = None,
    ) -> list[Machine]:
        """All machines with downtime, loaded in two queries (no per-machine lazy load)."""
        stmt = select(MachineRow).order_by(
            MachineRow.machine_group, MachineRow.preferred_rank, MachineRow.machine_id
        )
        if machine_group:
            stmt = stmt.where(MachineRow.machine_group == machine_group)
        if process_type:
            stmt = stmt.where(MachineRow.process_type == process_type.value)
        if status:
            stmt = stmt.where(MachineRow.status == status.value)
        rows = list(self._session.execute(stmt).scalars())
        downtime = self._downtime_by_machine([r.machine_id for r in rows])
        return [machine_from_row(r, downtime.get(r.machine_id, [])) for r in rows]

    def _downtime_by_machine(self, machine_ids: Iterable[str]) -> dict[str, list[MachineDowntimeRow]]:
        out: dict[str, list[MachineDowntimeRow]] = defaultdict(list)
        for ids in chunked(set(machine_ids)):
            stmt = select(MachineDowntimeRow).where(MachineDowntimeRow.machine_id.in_(ids))
            for row in self._session.execute(stmt).scalars():
                out[row.machine_id].append(row)
        return dict(out)

    def set_status(self, machine_id: str, status: MachineStatus) -> Machine:
        row = self._session.get(MachineRow, machine_id)
        if row is None:
            raise NotFoundError(f"machine '{machine_id}' not found", details={"machine_id": machine_id})
        row.status = status.value
        self._flush()
        return machine_from_row(row)

    def add_downtime(self, machine_id: str, kind: str, window: TimeWindow) -> Machine:
        """Append a downtime window (``kind`` is one of ``DOWNTIME_KINDS``)."""
        if kind not in DOWNTIME_KINDS:
            raise ValidationError(
                f"unknown downtime kind '{kind}'", details={"allowed": list(DOWNTIME_KINDS)}
            )
        row = self._session.get(MachineRow, machine_id)
        if row is None:
            raise NotFoundError(f"machine '{machine_id}' not found", details={"machine_id": machine_id})
        row.downtime.append(
            MachineDowntimeRow(
                downtime_id=new_id("dt"),
                machine_id=machine_id,
                kind=kind,
                start=window.start,
                end=window.end,
                reason=window.reason,
            )
        )
        self._flush()
        return machine_from_row(row)

    def groups(self) -> list[str]:
        stmt = select(MachineRow.machine_group).distinct().order_by(MachineRow.machine_group)
        return [g for (g,) in self._session.execute(stmt).all()]


class MaterialRepository(Repository):
    def upsert(self, materials: Iterable[Material], synced_at: datetime | None = None) -> int:
        items = list(materials)
        if not items:
            return 0
        existing = self._rows_by_ids(MaterialRow, MaterialRow.material_id, [m.material_id for m in items])
        for material in items:
            row = material_to_row(material, existing.get(material.material_id))
            row.synced_at = synced_at
            if material.material_id not in existing:
                self._session.add(row)
        self._flush()
        return len(items)

    def get(self, material_id: str) -> Material:
        row = self._session.get(MaterialRow, material_id)
        if row is None:
            raise NotFoundError(f"material '{material_id}' not found", details={"material_id": material_id})
        return material_from_row(row)

    def list_all(self) -> list[Material]:
        stmt = select(MaterialRow).order_by(MaterialRow.material_id)
        return [material_from_row(r) for r in self._session.execute(stmt).scalars()]


class ToolingRepository(Repository):
    def upsert(self, tooling: Iterable[Tooling], synced_at: datetime | None = None) -> int:
        items = list(tooling)
        if not items:
            return 0
        existing = self._rows_by_ids(ToolingRow, ToolingRow.tooling_id, [t.tooling_id for t in items])
        for tool in items:
            row = tooling_to_row(tool, existing.get(tool.tooling_id))
            row.synced_at = synced_at
            if tool.tooling_id not in existing:
                self._session.add(row)
        self._flush()
        return len(items)

    def get(self, tooling_id: str) -> Tooling:
        row = self._session.get(ToolingRow, tooling_id)
        if row is None:
            raise NotFoundError(f"tooling '{tooling_id}' not found", details={"tooling_id": tooling_id})
        return tooling_from_row(row)

    def list_all(self) -> list[Tooling]:
        stmt = select(ToolingRow).order_by(ToolingRow.tooling_id)
        return [tooling_from_row(r) for r in self._session.execute(stmt).scalars()]


class CalendarRepository(Repository):
    def upsert(self, calendars: Iterable[CalendarSpec]) -> int:
        items = list(calendars)
        if not items:
            return 0
        existing = self._rows_by_ids(
            CalendarSpecRow, CalendarSpecRow.calendar_id, [c.calendar_id for c in items]
        )
        for spec in items:
            row = calendar_to_row(spec, existing.get(spec.calendar_id))
            if spec.calendar_id not in existing:
                self._session.add(row)
        self._flush()
        return len(items)

    def get(self, calendar_id: str) -> CalendarSpec:
        row = self._session.get(CalendarSpecRow, calendar_id)
        if row is None:
            raise NotFoundError(f"calendar '{calendar_id}' not found", details={"calendar_id": calendar_id})
        return calendar_from_row(row)

    def list_all(self) -> list[CalendarSpec]:
        stmt = select(CalendarSpecRow).order_by(CalendarSpecRow.calendar_id)
        return [calendar_from_row(r) for r in self._session.execute(stmt).scalars()]

    def get_default_id(self) -> str | None:
        stmt = select(CalendarSpecRow.calendar_id).where(CalendarSpecRow.is_default.is_(True)).limit(1)
        return self._session.execute(stmt).scalar_one_or_none()

    def set_default(self, calendar_id: str) -> None:
        target = self._session.get(CalendarSpecRow, calendar_id)
        if target is None:
            raise NotFoundError(f"calendar '{calendar_id}' not found", details={"calendar_id": calendar_id})
        for row in self._session.execute(
            select(CalendarSpecRow).where(CalendarSpecRow.is_default.is_(True))
        ).scalars():
            row.is_default = False
        target.is_default = True
        self._flush()


__all__ = ["CalendarRepository", "MachineRepository", "MaterialRepository", "ToolingRepository"]
