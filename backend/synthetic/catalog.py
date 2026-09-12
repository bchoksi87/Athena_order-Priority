"""Static catalogues used by the synthetic data generator.

Everything here is *immutable reference data* (names, templates, plausible
ranges) rather than business rules. The generator combines these catalogues
with a seeded ``random.Random`` to produce deterministic datasets.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.domain.enums import CustomerTier
from synthetic.plant import (
    AM_ROUTE_WEIGHTS,
    AM_ROUTES,
    AM_TECHNOLOGIES,
    ASSEMBLY_ROUTE,
    CNC_ROUTE_WEIGHTS,
    CNC_ROUTES,
    DEFAULT_CALENDAR_ID,
    FIVE_AXIS_CYCLE_FACTOR,
    GROUPS_BY_PROCESS,
    HOLIDAYS_2026,
    MACHINE_GROUPS,
    PLANT_TIMEZONE,
    PRINTER_CALENDAR_ID,
    PROCESS_TIMING,
    THREE_SHIFT_TIMES,
    TWO_SHIFT_TIMES,
    MachineGroupSpec,
    ProcessTiming,
)

Scale = Literal["small", "medium", "large"]


# ---------------------------------------------------------------------------
# Scale profiles
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScaleProfile:
    """Entity volumes for one generation scale."""

    name: Scale
    customers: int
    orders: int
    materials: int
    tooling: int
    machines_per_group: dict[str, int]
    #: multiplier on lot sizes: a 12-machine shop with 300 lines a month runs
    #: fewer, larger lots than a 5,000-line job shop (see ``synthetic.generator``)
    lot_multiplier: float = 1.0


# Machine counts per group and scale. Sized against the open order book so that
# the plant runs at ~85-90 % of its 30-day calendar capacity with the 5-axis
# cell and the CMM room as the bottlenecks (see the calibration notes in
# ``synthetic.generator``). Large is medium x4 (20,000 vs 5,000 lines).
_SMALL_MACHINES = {  # no AM post cell: the deburr bay strips supports
    "CNC3": 2,
    "CNC5": 2,
    "LATHE": 2,
    "AM_SLA": 1,
    "AM_FDM": 1,
    "DEBURR": 2,
    "CMM": 1,
    "SURF": 1,
    "ASSY": 1,
    "PACK": 1,
}

_MEDIUM_MACHINES = {
    "CNC3": 14,
    "CNC5": 4,
    "LATHE": 6,
    "AM_SLA": 2,
    "AM_MJF": 2,
    "AM_FDM": 2,
    "AM_DMLS": 2,
    "DEBURR": 6,
    "CMM": 5,
    "SURF": 3,
    "AMPOST": 3,
    "ASSY": 3,
    "PACK": 3,
}

_LARGE_MACHINES = {group: count * 4 for group, count in _MEDIUM_MACHINES.items()}

SCALES: dict[str, ScaleProfile] = {
    "small": ScaleProfile("small", 80, 300, 20, 16, _SMALL_MACHINES, lot_multiplier=6.0),
    "medium": ScaleProfile("medium", 800, 5_000, 33, 40, _MEDIUM_MACHINES),
    "large": ScaleProfile("large", 800, 20_000, 33, 40, _LARGE_MACHINES),
}


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MaterialTemplate:
    code: str
    name: str
    material_class: str
    grade: str
    unit: str
    unit_cost: float  # INR per unit, drives order value


MATERIALS: tuple[MaterialTemplate, ...] = (
    MaterialTemplate("AL6061", "Aluminium 6061-T6 plate", "aluminium", "6061-T6", "kg", 420.0),
    MaterialTemplate("AL7075", "Aluminium 7075-T6 plate", "aluminium", "7075-T6", "kg", 690.0),
    MaterialTemplate("AL2024", "Aluminium 2024-T3 bar", "aluminium", "2024-T3", "kg", 640.0),
    MaterialTemplate("AL5083", "Aluminium 5083 plate", "aluminium", "5083-H111", "kg", 460.0),
    MaterialTemplate("SS304", "Stainless steel 304 bar", "steel", "304", "kg", 310.0),
    MaterialTemplate("SS316L", "Stainless steel 316L bar", "steel", "316L", "kg", 380.0),
    MaterialTemplate("SS17-4PH", "Stainless 17-4 PH round", "steel", "17-4PH H900", "kg", 520.0),
    MaterialTemplate("EN8", "Mild steel EN8 bar", "steel", "EN8", "kg", 95.0),
    MaterialTemplate("EN24", "Alloy steel EN24 bar", "steel", "EN24T", "kg", 140.0),
    MaterialTemplate("42CRMO4", "Alloy steel 42CrMo4 bar", "steel", "42CrMo4 QT", "kg", 160.0),
    MaterialTemplate("D2", "Tool steel D2 flat", "steel", "D2", "kg", 480.0),
    MaterialTemplate("TI64", "Titanium Ti-6Al-4V bar", "titanium", "Grade 5", "kg", 3_900.0),
    MaterialTemplate("TI-GR2", "Titanium Grade 2 sheet", "titanium", "Grade 2", "kg", 2_600.0),
    MaterialTemplate("BRASS360", "Brass C360 bar", "copper_alloy", "C36000", "kg", 620.0),
    MaterialTemplate("CU110", "Copper C110 bar", "copper_alloy", "C11000", "kg", 880.0),
    MaterialTemplate("IN718", "Inconel 718 bar", "superalloy", "718", "kg", 6_200.0),
    MaterialTemplate("POM", "Delrin POM rod", "polymer", "POM-C", "kg", 350.0),
    MaterialTemplate("PA6", "Nylon 6 rod", "polymer", "PA6", "kg", 290.0),
    MaterialTemplate("PEEK", "PEEK rod", "polymer", "PEEK 450G", "kg", 9_800.0),
    MaterialTemplate("PTFE", "PTFE rod", "polymer", "virgin", "kg", 1_100.0),
    MaterialTemplate("RS-GREY", "SLA resin standard grey", "resin", "Standard", "L", 4_200.0),
    MaterialTemplate("RS-TOUGH", "SLA resin tough (ABS-like)", "resin", "Tough 2000", "L", 6_800.0),
    MaterialTemplate("RS-HT", "SLA resin high-temp", "resin", "HT", "L", 8_900.0),
    MaterialTemplate("RS-CAST", "SLA resin castable", "resin", "Castable Wax", "L", 12_000.0),
    MaterialTemplate("PA12-PWD", "PA12 powder", "polymer_powder", "PA12", "kg", 5_400.0),
    MaterialTemplate("PA12GB-PWD", "PA12 glass-bead powder", "polymer_powder", "PA12-GB", "kg", 6_100.0),
    MaterialTemplate("ALSI10MG-PWD", "AlSi10Mg powder", "metal_powder", "AlSi10Mg", "kg", 9_500.0),
    MaterialTemplate("TI64-PWD", "Ti64 powder", "metal_powder", "Ti-6Al-4V ELI", "kg", 32_000.0),
    MaterialTemplate("316L-PWD", "316L powder", "metal_powder", "316L", "kg", 7_800.0),
    MaterialTemplate("PLA-FIL", "PLA filament", "filament", "PLA", "kg", 1_600.0),
    MaterialTemplate("ABS-FIL", "ABS filament", "filament", "ABS", "kg", 1_900.0),
    MaterialTemplate("PETG-FIL", "PETG filament", "filament", "PETG", "kg", 2_100.0),
    MaterialTemplate("ULTEM-FIL", "ULTEM 9085 filament", "filament", "9085", "kg", 21_000.0),
)

SUPPLIERS: tuple[str, ...] = (
    "Hindalco Metals Trading",
    "Bharat Alloys",
    "Sunrise Steel Stockists",
    "PolyChem Supplies",
    "AM Materials India",
    "Titan Specialty Metals",
    "Deccan Engineering Stores",
)


# ---------------------------------------------------------------------------
# Tooling
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ToolingTemplate:
    code: str
    name: str
    groups: tuple[str, ...]
    setup_minutes: float
    expected_life: float  # minutes of cutting


TOOLING: tuple[ToolingTemplate, ...] = (
    ToolingTemplate("EM06", "End mill 6 mm carbide", ("CNC3", "CNC5"), 8.0, 900.0),
    ToolingTemplate("EM10", "End mill 10 mm carbide", ("CNC3", "CNC5"), 8.0, 1_200.0),
    ToolingTemplate("EM16", "End mill 16 mm carbide", ("CNC3", "CNC5"), 10.0, 1_500.0),
    ToolingTemplate("BM04", "Ball mill 4 mm", ("CNC5",), 8.0, 700.0),
    ToolingTemplate("BM08", "Ball mill 8 mm", ("CNC5", "CNC3"), 8.0, 900.0),
    ToolingTemplate("FM63", "Face mill 63 mm", ("CNC3", "CNC5"), 12.0, 2_400.0),
    ToolingTemplate("DR05", "Drill 5 mm", ("CNC3", "CNC5", "LATHE"), 5.0, 800.0),
    ToolingTemplate("DR08", "Drill 8.5 mm", ("CNC3", "CNC5", "LATHE"), 5.0, 800.0),
    ToolingTemplate("TP-M6", "Tap M6", ("CNC3", "CNC5", "LATHE"), 5.0, 500.0),
    ToolingTemplate("TP-M10", "Tap M10", ("CNC3", "CNC5", "LATHE"), 5.0, 500.0),
    ToolingTemplate("BB20", "Boring bar 20 mm", ("LATHE",), 10.0, 1_800.0),
    ToolingTemplate("TI-CNMG", "Turning insert CNMG", ("LATHE",), 6.0, 600.0),
    ToolingTemplate("TI-DNMG", "Turning insert DNMG", ("LATHE",), 6.0, 600.0),
    ToolingTemplate("GRV-3", "Grooving tool 3 mm", ("LATHE",), 8.0, 700.0),
    ToolingTemplate("VISE-150", "Precision vise 150 mm", ("CNC3",), 20.0, 90_000.0),
    ToolingTemplate("VISE-200", "Precision vise 200 mm", ("CNC3",), 20.0, 90_000.0),
    ToolingTemplate("FIX-5AX", "5-axis dovetail fixture", ("CNC5",), 25.0, 90_000.0),
    ToolingTemplate("CHK-250", "3-jaw chuck 250 mm", ("LATHE",), 30.0, 90_000.0),
    ToolingTemplate("COL-ER32", "Collet set ER32", ("CNC3", "CNC5"), 10.0, 60_000.0),
    ToolingTemplate("PROBE", "Touch probe", ("CNC3", "CNC5"), 15.0, 60_000.0),
)

TOOL_SIZES: tuple[str, ...] = ("A", "B")


# ---------------------------------------------------------------------------
# Customers and parts
# ---------------------------------------------------------------------------

CUSTOMER_PREFIXES: tuple[str, ...] = (
    "Apex",
    "Nova",
    "Shakti",
    "Vertex",
    "Orion",
    "Indus",
    "Kaveri",
    "Meridian",
    "Zenith",
    "Aurora",
    "Sahyadri",
    "Trident",
    "Lumen",
    "Everest",
    "Pinnacle",
    "Quantum",
    "Helix",
    "Vimana",
    "Garuda",
    "Arjun",
    "Surya",
    "Chandra",
    "Prithvi",
    "Vayu",
    "Agni",
    "Neel",
    "Ashoka",
    "Kalinga",
    "Chola",
    "Maurya",
    "Delta",
    "Sigma",
    "Omega",
    "Kappa",
    "Falcon",
    "Kestrel",
    "Osprey",
    "Heron",
    "Ibis",
    "Sparrow",
    "Granite",
    "Basalt",
    "Cobalt",
    "Argon",
    "Neon",
    "Krypton",
    "Tungsten",
    "Vanadium",
    "Niobium",
    "Radiant",
)

CUSTOMER_SUFFIXES: tuple[str, ...] = (
    "Aerospace",
    "Precision Engineering",
    "Motors",
    "Medical Devices",
    "Defence Systems",
    "Robotics",
    "Tooling",
    "Automation",
    "Components",
    "Hydraulics",
    "Electronics",
    "Energy Systems",
)

CUSTOMER_FORMS: tuple[str, ...] = ("Pvt Ltd", "Ltd", "LLP", "Industries")

CUSTOMER_CATEGORIES: tuple[str, ...] = (
    "aerospace",
    "automotive",
    "medical",
    "defence",
    "industrial",
    "energy",
    "consumer",
)

ACCOUNT_MANAGERS: tuple[str, ...] = (
    "R. Iyer",
    "S. Mehta",
    "P. Nair",
    "A. Deshpande",
    "K. Reddy",
    "M. Chopra",
    "V. Bhatt",
    "N. Rao",
)

TIER_SHARES: tuple[tuple[CustomerTier, float], ...] = (
    (CustomerTier.STRATEGIC, 0.05),
    (CustomerTier.KEY, 0.15),
    (CustomerTier.STANDARD, 0.65),
    (CustomerTier.LOW, 0.15),
)

PART_FAMILIES: tuple[str, ...] = (
    "Bracket",
    "Housing",
    "Manifold",
    "Shaft",
    "Flange",
    "Impeller",
    "Gear",
    "Bushing",
    "Adapter",
    "Plate",
    "Nozzle",
    "Clamp",
    "Cover",
    "Spacer",
    "Lever",
    "Valve Body",
    "Enclosure",
    "Fixture",
    "Coupling",
    "Insert",
)

SURFACE_FINISHES: tuple[str, ...] = (
    "as-machined",
    "anodised-clear",
    "anodised-black",
    "powder-coat",
    "passivated",
    "bead-blast",
    "polished",
)


__all__ = [
    "ACCOUNT_MANAGERS",
    "AM_ROUTES",
    "AM_ROUTE_WEIGHTS",
    "AM_TECHNOLOGIES",
    "ASSEMBLY_ROUTE",
    "CNC_ROUTES",
    "CNC_ROUTE_WEIGHTS",
    "CUSTOMER_CATEGORIES",
    "CUSTOMER_FORMS",
    "CUSTOMER_PREFIXES",
    "CUSTOMER_SUFFIXES",
    "DEFAULT_CALENDAR_ID",
    "FIVE_AXIS_CYCLE_FACTOR",
    "GROUPS_BY_PROCESS",
    "HOLIDAYS_2026",
    "MACHINE_GROUPS",
    "MATERIALS",
    "PART_FAMILIES",
    "PLANT_TIMEZONE",
    "PRINTER_CALENDAR_ID",
    "PROCESS_TIMING",
    "SCALES",
    "SUPPLIERS",
    "SURFACE_FINISHES",
    "THREE_SHIFT_TIMES",
    "TIER_SHARES",
    "TOOLING",
    "TOOL_SIZES",
    "TWO_SHIFT_TIMES",
    "MachineGroupSpec",
    "MaterialTemplate",
    "ProcessTiming",
    "Scale",
    "ScaleProfile",
    "ToolingTemplate",
]
