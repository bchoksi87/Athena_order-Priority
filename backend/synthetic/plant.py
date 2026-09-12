"""Plant structure for the synthetic generator: machine groups, routes, process timing, calendars.

Reference data only (see :mod:`synthetic.catalog` for names and materials).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from typing import Literal

from app.domain.enums import ProcessType

# ---------------------------------------------------------------------------
# Machine groups
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MachineGroupSpec:
    group: str
    machine_type: str
    model_name: str
    process_type: ProcessType
    extra_processes: tuple[ProcessType, ...]
    material_classes: tuple[str, ...]  # "*" = any
    calendar: Literal["default", "printers"]
    capacity_hours_per_day: float
    location: str


MACHINE_GROUPS: dict[str, MachineGroupSpec] = {
    "CNC3": MachineGroupSpec(
        "CNC3",
        "CNC 3-axis VMC",
        "VMC-850",
        ProcessType.CNC_MACHINING,
        (),
        ("aluminium", "steel", "titanium", "polymer", "copper_alloy", "superalloy"),
        "default",
        16.0,
        "Bay A",
    ),
    "CNC5": MachineGroupSpec(
        "CNC5",
        "CNC 5-axis",
        "5AX-U600",
        ProcessType.CNC_MACHINING,
        (),
        ("aluminium", "steel", "titanium", "superalloy"),
        "default",
        16.0,
        "Bay A",
    ),
    "LATHE": MachineGroupSpec(
        "LATHE",
        "CNC lathe",
        "TL-250",
        ProcessType.CNC_MACHINING,
        (),
        ("aluminium", "steel", "copper_alloy", "polymer"),
        "default",
        16.0,
        "Bay B",
    ),
    "AM_SLA": MachineGroupSpec(
        "AM_SLA",
        "3D printer SLA",
        "SLA-300",
        ProcessType.ADDITIVE_3D_PRINTING,
        (),
        ("resin",),
        "printers",
        24.0,
        "AM Lab",
    ),
    "AM_MJF": MachineGroupSpec(
        "AM_MJF",
        "3D printer MJF",
        "MJF-4200",
        ProcessType.ADDITIVE_3D_PRINTING,
        (),
        ("polymer_powder",),
        "printers",
        24.0,
        "AM Lab",
    ),
    "AM_FDM": MachineGroupSpec(
        "AM_FDM",
        "3D printer FDM",
        "FDM-F450",
        ProcessType.ADDITIVE_3D_PRINTING,
        (),
        ("filament",),
        "printers",
        24.0,
        "AM Lab",
    ),
    "AM_DMLS": MachineGroupSpec(
        "AM_DMLS",
        "3D printer DMLS",
        "DMLS-M290",
        ProcessType.ADDITIVE_3D_PRINTING,
        (),
        ("metal_powder",),
        "printers",
        24.0,
        "AM Lab",
    ),
    "DEBURR": MachineGroupSpec(
        "DEBURR",
        "Deburring station",
        "Deburr Cell",
        ProcessType.DEBURRING,
        (ProcessType.FINISHING,),
        ("*",),
        "default",
        16.0,
        "Bay C",
    ),
    "CMM": MachineGroupSpec(
        "CMM",
        "Inspection CMM",
        "CMM-Bridge 7.10.7",
        ProcessType.INSPECTION,
        (),
        ("*",),
        "default",
        16.0,
        "Quality Lab",
    ),
    "SURF": MachineGroupSpec(
        "SURF",
        "Surface treatment line",
        "Anodising Line",
        ProcessType.SURFACE_TREATMENT,
        (ProcessType.HEAT_TREATMENT,),
        ("*",),
        "default",
        16.0,
        "Bay D",
    ),
    "AMPOST": MachineGroupSpec(
        "AMPOST",
        "AM post-processing cell",
        "AM Post Cell",
        ProcessType.SUPPORT_REMOVAL,
        (ProcessType.FINISHING,),
        ("*",),
        "default",
        16.0,
        "AM Lab",
    ),
    "ASSY": MachineGroupSpec(
        "ASSY",
        "Assembly bench",
        "Assembly Bench",
        ProcessType.ASSEMBLY,
        (),
        ("*",),
        "default",
        16.0,
        "Bay E",
    ),
    "PACK": MachineGroupSpec(
        "PACK",
        "Packing station",
        "Packing Station",
        ProcessType.PACKING,
        (),
        ("*",),
        "default",
        16.0,
        "Dispatch",
    ),
}

#: Groups whose machines execute a given process (first = preferred).
GROUPS_BY_PROCESS: dict[ProcessType, tuple[str, ...]] = {
    ProcessType.CNC_MACHINING: ("CNC3", "CNC5", "LATHE"),
    ProcessType.ADDITIVE_3D_PRINTING: ("AM_SLA", "AM_MJF", "AM_FDM", "AM_DMLS"),
    ProcessType.SUPPORT_REMOVAL: ("AMPOST",),
    ProcessType.FINISHING: ("AMPOST", "DEBURR"),
    ProcessType.DEBURRING: ("DEBURR",),
    ProcessType.INSPECTION: ("CMM",),
    ProcessType.SURFACE_TREATMENT: ("SURF",),
    ProcessType.HEAT_TREATMENT: ("SURF",),
    ProcessType.ASSEMBLY: ("ASSY",),
    ProcessType.PACKING: ("PACK",),
    ProcessType.OTHER: ("ASSY",),
}

#: Additive technology -> printer group and material class.
AM_TECHNOLOGIES: dict[str, tuple[str, str]] = {
    "SLA": ("AM_SLA", "resin"),
    "MJF": ("AM_MJF", "polymer_powder"),
    "FDM": ("AM_FDM", "filament"),
    "DMLS": ("AM_DMLS", "metal_powder"),
}


# ---------------------------------------------------------------------------
# Routes and process timing
# ---------------------------------------------------------------------------

CNC_ROUTES: tuple[tuple[ProcessType, ...], ...] = (
    (ProcessType.CNC_MACHINING, ProcessType.DEBURRING, ProcessType.INSPECTION, ProcessType.PACKING),
    (
        ProcessType.CNC_MACHINING,
        ProcessType.DEBURRING,
        ProcessType.INSPECTION,
        ProcessType.SURFACE_TREATMENT,
        ProcessType.PACKING,
    ),
    (
        ProcessType.CNC_MACHINING,
        ProcessType.DEBURRING,
        ProcessType.INSPECTION,
        ProcessType.SURFACE_TREATMENT,
        ProcessType.ASSEMBLY,
        ProcessType.PACKING,
    ),
)

AM_ROUTES: tuple[tuple[ProcessType, ...], ...] = (
    (
        ProcessType.ADDITIVE_3D_PRINTING,
        ProcessType.SUPPORT_REMOVAL,
        ProcessType.FINISHING,
        ProcessType.INSPECTION,
    ),
    (
        ProcessType.ADDITIVE_3D_PRINTING,
        ProcessType.SUPPORT_REMOVAL,
        ProcessType.FINISHING,
        ProcessType.INSPECTION,
        ProcessType.PACKING,
    ),
)

ASSEMBLY_ROUTE: tuple[ProcessType, ...] = (ProcessType.ASSEMBLY, ProcessType.INSPECTION, ProcessType.PACKING)


@dataclass(frozen=True, slots=True)
class ProcessTiming:
    setup_min: float
    setup_max: float
    cycle_min: float
    cycle_max: float


PROCESS_TIMING: dict[ProcessType, ProcessTiming] = {
    ProcessType.CNC_MACHINING: ProcessTiming(20.0, 120.0, 2.0, 45.0),
    ProcessType.ADDITIVE_3D_PRINTING: ProcessTiming(15.0, 45.0, 10.0, 180.0),
    ProcessType.SUPPORT_REMOVAL: ProcessTiming(5.0, 10.0, 3.0, 15.0),
    ProcessType.DEBURRING: ProcessTiming(5.0, 15.0, 1.0, 8.0),
    ProcessType.FINISHING: ProcessTiming(5.0, 20.0, 3.0, 20.0),
    ProcessType.HEAT_TREATMENT: ProcessTiming(30.0, 60.0, 5.0, 20.0),
    ProcessType.SURFACE_TREATMENT: ProcessTiming(15.0, 45.0, 2.0, 10.0),
    ProcessType.INSPECTION: ProcessTiming(10.0, 30.0, 1.0, 10.0),
    ProcessType.ASSEMBLY: ProcessTiming(10.0, 30.0, 5.0, 40.0),
    ProcessType.PACKING: ProcessTiming(5.0, 10.0, 0.5, 3.0),
    ProcessType.OTHER: ProcessTiming(10.0, 30.0, 1.0, 10.0),
}


# ---------------------------------------------------------------------------
# Calendars
# ---------------------------------------------------------------------------

PLANT_TIMEZONE = "Asia/Kolkata"

DEFAULT_CALENDAR_ID = "CAL-2SHIFT"
PRINTER_CALENDAR_ID = "CAL-3SHIFT"

TWO_SHIFT_TIMES: tuple[tuple[str, time, time, tuple[int, ...]], ...] = (
    ("Shift A", time(6, 0), time(14, 0), (0, 1, 2, 3, 4)),
    ("Shift B", time(14, 0), time(22, 0), (0, 1, 2, 3, 4)),
)

THREE_SHIFT_TIMES: tuple[tuple[str, time, time, tuple[int, ...]], ...] = (
    ("Shift A", time(6, 0), time(14, 0), (0, 1, 2, 3, 4, 5)),
    ("Shift B", time(14, 0), time(22, 0), (0, 1, 2, 3, 4, 5)),
    ("Shift C", time(22, 0), time(6, 0), (0, 1, 2, 3, 4, 5)),
)

HOLIDAYS_2026: tuple[date, ...] = (
    date(2026, 1, 26),
    date(2026, 3, 4),
    date(2026, 8, 15),
    date(2026, 10, 2),
    date(2026, 11, 8),
    date(2026, 11, 9),
    date(2026, 12, 25),
)


__all__ = [
    "AM_ROUTES",
    "AM_TECHNOLOGIES",
    "ASSEMBLY_ROUTE",
    "CNC_ROUTES",
    "DEFAULT_CALENDAR_ID",
    "GROUPS_BY_PROCESS",
    "HOLIDAYS_2026",
    "MACHINE_GROUPS",
    "PLANT_TIMEZONE",
    "PRINTER_CALENDAR_ID",
    "PROCESS_TIMING",
    "THREE_SHIFT_TIMES",
    "TWO_SHIFT_TIMES",
    "MachineGroupSpec",
    "ProcessTiming",
]
