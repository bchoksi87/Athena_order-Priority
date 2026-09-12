"""Resource scenarios: machines, calendars and materials (spec Phase 7).

* ``machine_down``      — "CNC-07 goes down for 8 hours": an unplanned downtime
  window is added to the machine; the calendar removes it and the scheduler
  routes around it (status is left as the ERP reported it: the window alone
  models the outage, so the machine is eligible again afterwards).
* ``add_machine``       — clone an existing machine as a fresh, idle one.
* ``extra_working_day`` — a date (Saturday) runs the calendar's shifts.
* ``extra_shift``       — an additional shift window on one date, expressed in
  the calendar's local time and stored as an absolute overtime window.
* ``material_delay``    — the incoming material arrives later (or earlier).
* ``material_arrival``  — the material is (or will be) received at a date.

Calendar scenarios mutate the *specs* in the snapshot, so every machine using
the spec (or ``all`` calendars) sees the change once the simulation engine
rebuilds calendars.
"""

from __future__ import annotations

import copy
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, model_validator

from app.core.clock import ensure_utc
from app.core.errors import ValidationError
from app.domain.enums import MachineStatus, MaterialStatus
from app.domain.models import CalendarSpec, Machine, Order, TimeWindow
from app.domain.snapshot import PlanningSnapshot
from app.engines.simulation.base import (
    ScenarioBase,
    ScenarioEffect,
    add_note,
    fmt_dt,
    require_machine_id,
    require_material_id,
)

MINUTES_PER_HOUR = 60.0
ALL_CALENDARS = "all"


# ----------------------------------------------------------------- machines


class MachineDownScenario(ScenarioBase):
    """Machine unavailable for a window (``start`` defaults to the snapshot instant)."""

    kind: Literal["machine_down"] = "machine_down"
    machine_id: str
    start: datetime | None = None
    end: datetime | None = None
    duration_hours: float | None = Field(default=None, gt=0)
    reason: str = "simulated breakdown"

    @model_validator(mode="after")
    def _end_or_duration(self) -> MachineDownScenario:
        if (self.end is None) == (self.duration_hours is None):
            raise ValueError("machine_down needs exactly one of 'end' or 'duration_hours'")
        return self

    def window(self, as_of: datetime) -> TimeWindow:
        start = ensure_utc(self.start) if self.start is not None else ensure_utc(as_of)
        if self.end is not None:
            end = ensure_utc(self.end)
        else:
            end = start + timedelta(hours=float(self.duration_hours or 0.0))
        if end <= start:
            raise ValidationError(
                "machine_down: window end must be after start",
                details={"start": start.isoformat(), "end": end.isoformat()},
            )
        return TimeWindow(start, end, self.reason)

    def _mutate(self, snapshot: PlanningSnapshot, effect: ScenarioEffect) -> None:
        require_machine_id(snapshot, self.machine_id, self.kind)
        machine = snapshot.machines[self.machine_id]
        window = self.window(snapshot.as_of)
        machine.unplanned_downtime.append(window)
        add_note(machine.attributes, f"down {fmt_dt(window.start)} → {fmt_dt(window.end)}: {self.reason}")
        effect.touch_machines(self.machine_id)
        effect.note(
            f"{self.machine_id} unavailable {fmt_dt(window.start)} → {fmt_dt(window.end)} "
            f"({window.minutes / MINUTES_PER_HOUR:g} h)"
        )

    def describe(self) -> str:
        if self.duration_hours is not None:
            when = "now" if self.start is None else fmt_dt(self.start)
            return f"Machine {self.machine_id} down for {self.duration_hours:g} h from {when}"
        start = "now" if self.start is None else fmt_dt(self.start)
        end = fmt_dt(self.end) if self.end is not None else "?"
        return f"Machine {self.machine_id} down from {start} until {end}"


class AddMachineScenario(ScenarioBase):
    """Add a machine cloned from an existing one (capabilities, group, calendar)."""

    kind: Literal["add_machine"] = "add_machine"
    clone_of_machine_id: str
    new_machine_id: str
    name: str | None = None
    calendar_id: str | None = None
    available_from: datetime | None = None

    def _mutate(self, snapshot: PlanningSnapshot, effect: ScenarioEffect) -> None:
        require_machine_id(snapshot, self.clone_of_machine_id, self.kind)
        if self.new_machine_id in snapshot.machines:
            raise ValidationError(
                f"add_machine: machine {self.new_machine_id!r} already exists",
                details={"machine_id": self.new_machine_id},
            )
        if self.calendar_id is not None and self.calendar_id not in snapshot.calendars:
            raise ValidationError(
                f"add_machine: unknown calendar {self.calendar_id!r}",
                details={"calendar_id": self.calendar_id},
            )
        source = snapshot.machines[self.clone_of_machine_id]
        machine: Machine = copy.deepcopy(source)
        machine.machine_id = self.new_machine_id
        machine.machine_name = self.name or f"{source.machine_name} (simulated)"
        machine.status = MachineStatus.AVAILABLE
        machine.maintenance_windows = []
        machine.planned_downtime = []
        machine.unplanned_downtime = []
        machine.tooling_configuration = set()
        machine.current_material_id = None
        machine.current_setup_family = None
        machine.utilization = None
        machine.available_from = ensure_utc(self.available_from) if self.available_from else None
        if self.calendar_id is not None:
            machine.calendar_id = self.calendar_id
        add_note(machine.attributes, f"simulated machine cloned from {self.clone_of_machine_id}")
        snapshot.machines[self.new_machine_id] = machine
        # Tool and material releases are keyed by machine id on the tool / material side:
        # whatever is released for the source machine is released for its clone.
        released_tools = [
            t.tooling_id
            for t in snapshot.tooling.values()
            if self.clone_of_machine_id in t.compatible_machine_ids
        ]
        for tooling_id in released_tools:
            snapshot.tooling[tooling_id].compatible_machine_ids.add(self.new_machine_id)
        released_materials = [
            m.material_id
            for m in snapshot.materials.values()
            if self.clone_of_machine_id in m.compatible_machine_ids
        ]
        for material_id in released_materials:
            snapshot.materials[material_id].compatible_machine_ids.add(self.new_machine_id)
        if released_tools or released_materials:
            effect.note(
                f"{self.new_machine_id} inherits {len(released_tools)} tool and "
                f"{len(released_materials)} material release(s) from {self.clone_of_machine_id}"
            )
        effect.touch_machines(self.new_machine_id)
        effect.note(f"Added machine {self.new_machine_id} (clone of {self.clone_of_machine_id})")

    def describe(self) -> str:
        return f"Add machine {self.new_machine_id} like {self.clone_of_machine_id}"


# ---------------------------------------------------------------- calendars


def _target_specs(snapshot: PlanningSnapshot, calendar_id: str, scenario: str) -> list[CalendarSpec]:
    if calendar_id == ALL_CALENDARS:
        return [snapshot.calendars[k] for k in sorted(snapshot.calendars)]
    spec = snapshot.calendars.get(calendar_id)
    if spec is None:
        raise ValidationError(
            f"{scenario}: unknown calendar {calendar_id!r}", details={"calendar_id": calendar_id}
        )
    return [spec]


def _local_window(spec: CalendarSpec, day: date, start: time, end: time, reason: str) -> TimeWindow:
    """Absolute UTC window for ``start``-``end`` local to ``spec.timezone`` on ``day``."""
    try:
        tz = ZoneInfo(spec.timezone or "UTC")
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValidationError(
            f"calendar {spec.calendar_id!r} has unknown timezone {spec.timezone!r}"
        ) from exc
    start_local = datetime.combine(day, start, tzinfo=tz)
    end_day = day + timedelta(days=1) if end <= start else day
    end_local = datetime.combine(end_day, end, tzinfo=tz)
    return TimeWindow(start_local.astimezone(UTC), end_local.astimezone(UTC), reason)


class ExtraWorkingDayScenario(ScenarioBase):
    """``day`` becomes a working day: every shift of the calendar runs (holidays overridden)."""

    kind: Literal["extra_working_day"] = "extra_working_day"
    day: date
    calendar_id: str = ALL_CALENDARS

    def _mutate(self, snapshot: PlanningSnapshot, effect: ScenarioEffect) -> None:
        for spec in _target_specs(snapshot, self.calendar_id, self.kind):
            if self.day not in spec.extra_working_days:
                spec.extra_working_days.append(self.day)
                spec.extra_working_days.sort()
            effect.note(f"{spec.calendar_id}: {self.day.isoformat()} added as working day")
        effect.touch_machines(
            *sorted(m.machine_id for m in snapshot.machines.values() if self._uses(snapshot, m))
        )

    def _uses(self, snapshot: PlanningSnapshot, machine: Machine) -> bool:
        if self.calendar_id == ALL_CALENDARS:
            return True
        return (machine.calendar_id or snapshot.default_calendar_id) == self.calendar_id

    def describe(self) -> str:
        scope = "all calendars" if self.calendar_id == ALL_CALENDARS else f"calendar {self.calendar_id}"
        return f"{self.day.strftime('%A %Y-%m-%d')} becomes a working day ({scope})"


class ExtraShiftScenario(ScenarioBase):
    """An additional shift (local ``start``-``end``) on ``day`` for one or all calendars."""

    kind: Literal["extra_shift"] = "extra_shift"
    day: date
    start: time
    end: time
    calendar_id: str = ALL_CALENDARS
    name: str = "extra shift"

    def _mutate(self, snapshot: PlanningSnapshot, effect: ScenarioEffect) -> None:
        for spec in _target_specs(snapshot, self.calendar_id, self.kind):
            window = _local_window(spec, self.day, self.start, self.end, self.name)
            spec.overtime_windows.append(window)
            effect.note(
                f"{spec.calendar_id}: {self.name} {fmt_dt(window.start)} → {fmt_dt(window.end)} UTC "
                f"({window.minutes / MINUTES_PER_HOUR:g} h)"
            )
        effect.touch_machines(
            *sorted(
                m.machine_id
                for m in snapshot.machines.values()
                if self.calendar_id == ALL_CALENDARS
                or (m.calendar_id or snapshot.default_calendar_id) == self.calendar_id
            )
        )

    def describe(self) -> str:
        scope = "all calendars" if self.calendar_id == ALL_CALENDARS else f"calendar {self.calendar_id}"
        return (
            f"Extra shift {self.start.strftime('%H:%M')}-{self.end.strftime('%H:%M')} on "
            f"{self.day.isoformat()} ({scope})"
        )


# ---------------------------------------------------------------- materials


def _orders_using_material(snapshot: PlanningSnapshot, material_id: str) -> list[Order]:
    """Open orders whose order-level or any pending operation material is ``material_id``."""
    hits: list[Order] = []
    for order in snapshot.open_orders():
        if order.required_material_id == material_id:
            hits.append(order)
            continue
        if any(op.material_id == material_id for op in snapshot.pending_operations_for_order(order.order_id)):
            hits.append(order)
    return hits


class MaterialDelayScenario(ScenarioBase):
    """Incoming material arrives ``delay_days`` later (or at ``new_expected_receipt_date``).

    Orders already marked ``AVAILABLE`` by the ERP keep their stock unless
    ``affects_allocated_stock`` is set (models "the stock we counted on *is*
    the delayed shipment"); every other order using the material is put
    ``ON_ORDER`` so readiness reports WAITING_MATERIAL until the new date.
    """

    kind: Literal["material_delay"] = "material_delay"
    material_id: str
    delay_days: float | None = Field(default=None, gt=0)
    new_expected_receipt_date: datetime | None = None
    affects_allocated_stock: bool = False

    @model_validator(mode="after")
    def _one_of(self) -> MaterialDelayScenario:
        if (self.delay_days is None) == (self.new_expected_receipt_date is None):
            raise ValueError(
                "material_delay needs exactly one of 'delay_days' or 'new_expected_receipt_date'"
            )
        return self

    def _mutate(self, snapshot: PlanningSnapshot, effect: ScenarioEffect) -> None:
        require_material_id(snapshot, self.material_id, self.kind)
        material = snapshot.materials[self.material_id]
        previous = ensure_utc(material.expected_receipt_date) if material.expected_receipt_date else None
        if self.new_expected_receipt_date is not None:
            new_date = ensure_utc(self.new_expected_receipt_date)
        else:
            base = previous if previous is not None else ensure_utc(snapshot.as_of)
            new_date = base + timedelta(days=float(self.delay_days or 0.0))
        material.expected_receipt_date = new_date
        if self.affects_allocated_stock:
            material.incoming_quantity += material.available_quantity
            material.available_quantity = 0.0
            material.reserved_quantity = 0.0
        touched: list[str] = []
        for order in _orders_using_material(snapshot, self.material_id):
            allocated = order.material_status == MaterialStatus.AVAILABLE
            if allocated and not self.affects_allocated_stock:
                continue  # the ERP says this order already holds its material
            order.material_status = MaterialStatus.ON_ORDER
            add_note(order.attributes, f"material {self.material_id} expected {fmt_dt(new_date)}")
            touched.append(order.order_id)
        effect.touch_orders(*touched)
        was = fmt_dt(previous) if previous is not None else "unknown"
        effect.note(
            f"Material {self.material_id} expected {was} → {fmt_dt(new_date)}; "
            f"{len(touched)} order(s) waiting"
        )

    def describe(self) -> str:
        if self.delay_days is not None:
            return f"Material {self.material_id} arrives {self.delay_days:g} day(s) late"
        when = fmt_dt(self.new_expected_receipt_date) if self.new_expected_receipt_date else "?"
        return f"Material {self.material_id} expected on {when}"


class MaterialArrivalScenario(ScenarioBase):
    """Material received at ``arrives_at`` (in the past or now → available immediately)."""

    kind: Literal["material_arrival"] = "material_arrival"
    material_id: str
    arrives_at: datetime | None = None
    quantity: float | None = Field(default=None, ge=0)

    def _mutate(self, snapshot: PlanningSnapshot, effect: ScenarioEffect) -> None:
        require_material_id(snapshot, self.material_id, self.kind)
        material = snapshot.materials[self.material_id]
        now = ensure_utc(snapshot.as_of)
        arrives = ensure_utc(self.arrives_at) if self.arrives_at is not None else now
        qty = self.quantity if self.quantity is not None else material.incoming_quantity
        touched: list[str] = []
        if arrives <= now:
            material.available_quantity += qty
            material.incoming_quantity = max(0.0, material.incoming_quantity - qty)
            material.expected_receipt_date = None
            for order in _orders_using_material(snapshot, self.material_id):
                if order.material_status != MaterialStatus.AVAILABLE:
                    order.material_status = MaterialStatus.AVAILABLE
                    add_note(
                        order.attributes, f"material {self.material_id} received ({qty:g} {material.unit})"
                    )
                    touched.append(order.order_id)
            effect.note(f"Material {self.material_id}: {qty:g} {material.unit} received now")
        else:
            material.expected_receipt_date = arrives
            if self.quantity is not None:
                material.incoming_quantity = max(material.incoming_quantity, self.quantity)
            for order in _orders_using_material(snapshot, self.material_id):
                if order.material_status in (MaterialStatus.UNAVAILABLE, MaterialStatus.ON_ORDER):
                    touched.append(order.order_id)
            effect.note(f"Material {self.material_id} expected {fmt_dt(arrives)} ({qty:g} {material.unit})")
        effect.touch_orders(*touched)

    def describe(self) -> str:
        when = "now" if self.arrives_at is None else fmt_dt(self.arrives_at)
        return f"Material {self.material_id} arrives {when}"


__all__ = [
    "ALL_CALENDARS",
    "AddMachineScenario",
    "ExtraShiftScenario",
    "ExtraWorkingDayScenario",
    "MachineDownScenario",
    "MaterialArrivalScenario",
    "MaterialDelayScenario",
]
