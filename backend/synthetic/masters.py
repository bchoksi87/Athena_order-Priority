"""Master-data builders for the synthetic generator (customers, machines, materials, tooling, calendars).

Each builder is a pure function of a seeded ``random.Random`` plus the scale
profile, so the output is fully deterministic.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from app.domain.enums import CustomerTier, MachineStatus, PaymentRisk, ProcessType
from app.domain.models import CalendarSpec, Customer, Machine, Material, Shift, TimeWindow, Tooling
from synthetic import catalog
from synthetic.catalog import MachineGroupSpec, ScaleProfile

#: Approximate customer revenue bands (INR / year) per tier, used for sampling.
_TIER_REVENUE_BANDS: dict[CustomerTier, tuple[float, float]] = {
    CustomerTier.STRATEGIC: (40_000_000.0, 250_000_000.0),
    CustomerTier.KEY: (8_000_000.0, 60_000_000.0),
    CustomerTier.STANDARD: (500_000.0, 12_000_000.0),
    CustomerTier.LOW: (50_000.0, 1_500_000.0),
}

_TIER_PRIORITY: dict[CustomerTier, int] = {
    CustomerTier.STRATEGIC: 1,
    CustomerTier.KEY: 2,
    CustomerTier.STANDARD: 3,
    CustomerTier.LOW: 4,
}


def _pick_tier(rng: random.Random) -> CustomerTier:
    roll = rng.random()
    cumulative = 0.0
    for tier, share in catalog.TIER_SHARES:
        cumulative += share
        if roll < cumulative:
            return tier
    return CustomerTier.STANDARD


def _unique_customer_names(rng: random.Random, count: int) -> list[str]:
    combos = [(p, s) for p in catalog.CUSTOMER_PREFIXES for s in catalog.CUSTOMER_SUFFIXES]
    if count > len(combos) * len(catalog.CUSTOMER_FORMS):
        raise ValueError(f"cannot build {count} unique customer names")
    picked = rng.sample(combos, min(count, len(combos)))
    names = [f"{p} {s} {rng.choice(catalog.CUSTOMER_FORMS)}" for p, s in picked]
    while len(names) < count:  # extremely large scales: vary the legal form
        p, s = rng.choice(combos)
        candidate = f"{p} {s} {rng.choice(catalog.CUSTOMER_FORMS)}"
        if candidate not in names:
            names.append(candidate)
    return names


def build_customers(rng: random.Random, profile: ScaleProfile) -> list[Customer]:
    """Create customers with a realistic tier mix, revenue and service attributes."""
    names = _unique_customer_names(rng, profile.customers)
    customers: list[Customer] = []
    for index, name in enumerate(names, start=1):
        tier = _pick_tier(rng)
        low, high = _TIER_REVENUE_BANDS[tier]
        revenue = rng.uniform(low, high)
        strategic = tier == CustomerTier.STRATEGIC or (tier == CustomerTier.KEY and rng.random() < 0.15)
        has_sla = rng.random() < {CustomerTier.STRATEGIC: 0.8, CustomerTier.KEY: 0.5}.get(tier, 0.1)
        escalation = 0
        if rng.random() < 0.08:
            escalation = rng.choice((1, 1, 2, 3))
        customers.append(
            Customer(
                customer_id=f"CUST-{index:04d}",
                customer_name=name,
                customer_category=rng.choice(catalog.CUSTOMER_CATEGORIES),
                customer_tier=tier,
                customer_priority=_TIER_PRIORITY[tier],
                strategic_customer_flag=strategic,
                annual_revenue=round(revenue * rng.uniform(4.0, 30.0), 2),
                customer_revenue=round(revenue, 2),
                customer_profitability=round(rng.uniform(0.08, 0.42), 3),
                customer_service_level=round(rng.uniform(0.85, 0.99), 3),
                sla_hours=float(rng.choice((48, 72, 96, 120, 168))) if has_sla else None,
                escalation_level=escalation,
                historical_on_time_delivery=round(rng.uniform(0.7, 0.99), 3),
                payment_risk=rng.choice(
                    (PaymentRisk.LOW, PaymentRisk.LOW, PaymentRisk.LOW, PaymentRisk.MEDIUM, PaymentRisk.HIGH)
                ),
                preferred_delivery_expectation=rng.choice(
                    ("standard", "standard", "express", "just-in-time")
                ),
                account_manager=rng.choice(catalog.ACCOUNT_MANAGERS),
                active=rng.random() > 0.02,
                external_ref=f"C{index:05d}",
            )
        )
    return customers


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------


def build_materials(rng: random.Random, profile: ScaleProfile, as_of: datetime) -> list[Material]:
    """Create materials, including a few shortages (zero stock with an incoming receipt)."""
    templates = list(catalog.MATERIALS[: profile.materials])
    if profile.materials < len(catalog.MATERIALS):
        # Ensure every material class is represented at small scales.
        seen = {t.material_class for t in templates}
        for template in catalog.MATERIALS[profile.materials :]:
            if template.material_class not in seen:
                templates.append(template)
                seen.add(template.material_class)
    materials: list[Material] = []
    shortage_count = max(1, round(len(templates) * 0.12))
    shortage_codes = {t.code for t in rng.sample(templates, shortage_count)}
    for template in templates:
        available = round(rng.uniform(80.0, 900.0), 1)
        reserved = round(available * rng.uniform(0.0, 0.45), 1)
        incoming = 0.0
        receipt: datetime | None = None
        if template.code in shortage_codes:
            available = 0.0
            reserved = 0.0
            incoming = round(rng.uniform(100.0, 600.0), 1)
            receipt = as_of + timedelta(days=rng.randint(1, 12), hours=rng.randint(0, 9))
        elif rng.random() < 0.3:
            incoming = round(rng.uniform(50.0, 400.0), 1)
            receipt = as_of + timedelta(days=rng.randint(2, 20))
        materials.append(
            Material(
                material_id=f"MAT-{template.code}",
                material_name=template.name,
                material_type=template.material_class,
                grade=template.grade,
                supplier=rng.choice(catalog.SUPPLIERS),
                unit=template.unit,
                available_quantity=available,
                reserved_quantity=reserved,
                incoming_quantity=incoming,
                expected_receipt_date=receipt,
                minimum_stock=round(rng.uniform(20.0, 120.0), 1),
                attributes={"unit_cost": template.unit_cost},
            )
        )
    return materials


# ---------------------------------------------------------------------------
# Machines
# ---------------------------------------------------------------------------


def _downtime_windows(
    rng: random.Random, as_of: datetime, spec: MachineGroupSpec
) -> tuple[list[TimeWindow], list[TimeWindow]]:
    maintenance: list[TimeWindow] = []
    planned: list[TimeWindow] = []
    if rng.random() < 0.35:
        start = as_of + timedelta(days=rng.randint(1, 12), hours=rng.choice((0, 2, 4, 8)))
        maintenance.append(
            TimeWindow(start, start + timedelta(hours=rng.choice((2, 4, 8))), "preventive maintenance")
        )
    if rng.random() < 0.15:
        start = as_of + timedelta(days=rng.randint(2, 20))
        planned.append(TimeWindow(start, start + timedelta(hours=rng.choice((4, 8, 16))), "planned downtime"))
    return maintenance, planned


def build_machines(
    rng: random.Random,
    profile: ScaleProfile,
    as_of: datetime,
    materials: list[Material],
) -> list[Machine]:
    """Create machines per group; a couple are DOWN, some have maintenance windows."""
    machines: list[Machine] = []
    by_class: dict[str, list[str]] = {}
    for material in materials:
        by_class.setdefault(material.material_type, []).append(material.material_id)
    all_material_ids = {m.material_id for m in materials}

    down_budget = 2 if sum(profile.machines_per_group.values()) >= 20 else 1
    for group, count in profile.machines_per_group.items():
        spec = catalog.MACHINE_GROUPS[group]
        compatible: set[str] = set()
        if "*" in spec.material_classes:
            compatible = set(all_material_ids)
        else:
            for material_class in spec.material_classes:
                compatible.update(by_class.get(material_class, ()))
        for index in range(1, count + 1):
            machine_id = f"MC-{group}-{index:02d}"
            status = MachineStatus.AVAILABLE
            unplanned: list[TimeWindow] = []
            if down_budget > 0 and group in ("CNC3", "AM_FDM", "LATHE") and index == count:
                status = MachineStatus.DOWN
                down_budget -= 1
                unplanned.append(
                    TimeWindow(
                        as_of - timedelta(hours=3), as_of + timedelta(hours=rng.randint(6, 30)), "breakdown"
                    )
                )
            elif rng.random() < 0.05:
                status = MachineStatus.MAINTENANCE
                unplanned.append(TimeWindow(as_of, as_of + timedelta(hours=rng.randint(2, 8)), "maintenance"))
            elif rng.random() < 0.4:
                status = MachineStatus.RUNNING
            maintenance, planned = _downtime_windows(rng, as_of, spec)
            current_material = rng.choice(sorted(compatible)) if compatible and rng.random() < 0.7 else None
            machines.append(
                Machine(
                    machine_id=machine_id,
                    machine_name=f"{spec.model_name} #{index}",
                    machine_type=spec.machine_type,
                    process_type=spec.process_type,
                    machine_group=group,
                    location=spec.location,
                    status=status,
                    calendar_id=(
                        catalog.PRINTER_CALENDAR_ID
                        if spec.calendar == "printers"
                        else catalog.DEFAULT_CALENDAR_ID
                    ),
                    efficiency=round(rng.uniform(0.82, 1.1), 2),
                    utilization=round(rng.uniform(0.45, 0.92), 2),
                    capacity_hours_per_day=spec.capacity_hours_per_day,
                    maintenance_windows=maintenance,
                    planned_downtime=planned,
                    unplanned_downtime=unplanned,
                    compatible_materials=compatible,
                    compatible_processes=set(spec.extra_processes),
                    max_part_size_mm=_part_envelope(rng, spec.process_type),
                    current_material_id=current_material,
                    current_setup_family=(
                        f"{rng.choice(catalog.PART_FAMILIES)}-{rng.randint(1, 6)}"
                        if rng.random() < 0.6
                        else None
                    ),
                    available_from=None,
                    preferred_rank=index - 1,
                    attributes={"model": spec.model_name},
                )
            )
    return machines


def _part_envelope(rng: random.Random, process: ProcessType) -> tuple[float, float, float]:
    if process == ProcessType.CNC_MACHINING:
        return (float(rng.choice((500, 800, 1000))), float(rng.choice((400, 500, 600))), 500.0)
    if process == ProcessType.ADDITIVE_3D_PRINTING:
        return (float(rng.choice((145, 250, 300, 380))), float(rng.choice((145, 250, 300))), 300.0)
    return (1200.0, 800.0, 800.0)


# ---------------------------------------------------------------------------
# Tooling
# ---------------------------------------------------------------------------


def build_tooling(rng: random.Random, profile: ScaleProfile, machines: list[Machine]) -> list[Tooling]:
    """Create tooling with machine-group compatibility; a few tools are unavailable."""
    tools: list[Tooling] = []
    machines_by_group: dict[str, list[str]] = {}
    for machine in machines:
        machines_by_group.setdefault(machine.machine_group, []).append(machine.machine_id)
    templates = list(catalog.TOOLING)
    index = 0
    while len(tools) < profile.tooling:
        template = templates[index % len(templates)]
        size = catalog.TOOL_SIZES[index // len(templates) % len(catalog.TOOL_SIZES)]
        index += 1
        compatible: set[str] = set()
        for group in template.groups:
            compatible.update(machines_by_group.get(group, ()))
        if not compatible:
            continue
        usage = round(template.expected_life * rng.uniform(0.0, 0.95), 1)
        unavailable = rng.random() < 0.08
        tools.append(
            Tooling(
                tooling_id=f"TL-{template.code}-{size}",
                tooling_name=f"{template.name} ({size})",
                available=not unavailable,
                available_from=None,
                compatible_machine_ids=compatible,
                setup_minutes=template.setup_minutes,
                expected_life=template.expected_life,
                current_usage=usage,
                maintenance_status="regrind" if unavailable else "ok",
                attributes={"template": template.code},
            )
        )
        if index > profile.tooling * 4:  # safety valve when no group has machines
            break
    return tools


# ---------------------------------------------------------------------------
# Calendars
# ---------------------------------------------------------------------------


def build_calendars() -> list[CalendarSpec]:
    """Two calendars: default two-shift weekdays and a three-shift printer calendar."""
    two_shift = CalendarSpec(
        calendar_id=catalog.DEFAULT_CALENDAR_ID,
        name="Two-shift weekday calendar",
        timezone=catalog.PLANT_TIMEZONE,
        shifts=[Shift(name, start, end, days) for name, start, end, days in catalog.TWO_SHIFT_TIMES],
        holidays=list(catalog.HOLIDAYS_2026),
    )
    three_shift = CalendarSpec(
        calendar_id=catalog.PRINTER_CALENDAR_ID,
        name="Three-shift printer calendar",
        timezone=catalog.PLANT_TIMEZONE,
        shifts=[Shift(name, start, end, days) for name, start, end, days in catalog.THREE_SHIFT_TIMES],
        holidays=list(catalog.HOLIDAYS_2026),
    )
    return [two_shift, three_shift]


__all__ = ["build_calendars", "build_customers", "build_machines", "build_materials", "build_tooling"]
