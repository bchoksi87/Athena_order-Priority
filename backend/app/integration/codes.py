"""ERP status-code vocabularies shared by the mock connector and the normalizer.

Real ERPs use terse codes ("REL", "WIP", "HLD"). The normalizer translates
them into domain enums through these maps; the mock connector uses the inverse
maps so that its raw records look like a genuine ERP export. Codes are matched
case-insensitively after stripping whitespace.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TypeVar

from app.domain.enums import (
    CustomerTier,
    MachineStatus,
    MaterialStatus,
    OperationStatus,
    OrderStatus,
    PaymentRisk,
    ProcessType,
    QualityStatus,
    ShippingStatus,
)

E = TypeVar("E", bound=StrEnum)

ORDER_STATUS_CODES: dict[str, OrderStatus] = {
    "NEW": OrderStatus.NEW,
    "REL": OrderStatus.RELEASED,
    "PLN": OrderStatus.PLANNED,
    "SCH": OrderStatus.SCHEDULED,
    "MTW": OrderStatus.MATERIAL_WAITING,
    "TLW": OrderStatus.TOOLING_WAITING,
    "WIP": OrderStatus.IN_PRODUCTION,
    "PCM": OrderStatus.PARTIALLY_COMPLETED,
    "QIN": OrderStatus.QUALITY_INSPECTION,
    "RWK": OrderStatus.REWORK,
    "CMP": OrderStatus.COMPLETED,
    "PKD": OrderStatus.PACKED,
    "SHP": OrderStatus.SHIPPED,
    "HLD": OrderStatus.ON_HOLD,
    "CAN": OrderStatus.CANCELLED,
}

OPERATION_STATUS_CODES: dict[str, OperationStatus] = {
    "PND": OperationStatus.PENDING,
    "RDY": OperationStatus.READY,
    "SCH": OperationStatus.SCHEDULED,
    "WIP": OperationStatus.IN_PROGRESS,
    "CMP": OperationStatus.COMPLETED,
    "HLD": OperationStatus.ON_HOLD,
    "RWK": OperationStatus.REWORK,
    "CAN": OperationStatus.CANCELLED,
}

MACHINE_STATUS_CODES: dict[str, MachineStatus] = {
    "AVL": MachineStatus.AVAILABLE,
    "RUN": MachineStatus.RUNNING,
    "DWN": MachineStatus.DOWN,
    "MNT": MachineStatus.MAINTENANCE,
    "OFF": MachineStatus.OFFLINE,
}

MATERIAL_STATUS_CODES: dict[str, MaterialStatus] = {
    "AVL": MaterialStatus.AVAILABLE,
    "PRT": MaterialStatus.PARTIAL,
    "UNA": MaterialStatus.UNAVAILABLE,
    "ORD": MaterialStatus.ON_ORDER,
    "UNK": MaterialStatus.UNKNOWN,
}

QUALITY_STATUS_CODES: dict[str, QualityStatus] = {
    "NON": QualityStatus.NONE,
    "PND": QualityStatus.PENDING,
    "PAS": QualityStatus.PASSED,
    "FAL": QualityStatus.FAILED,
    "RWK": QualityStatus.REWORK,
    "HLD": QualityStatus.HOLD,
}

SHIPPING_STATUS_CODES: dict[str, ShippingStatus] = {
    "NS": ShippingStatus.NOT_SHIPPED,
    "PS": ShippingStatus.PARTIAL,
    "SH": ShippingStatus.SHIPPED,
}

CUSTOMER_TIER_CODES: dict[str, CustomerTier] = {
    "A": CustomerTier.STRATEGIC,
    "B": CustomerTier.KEY,
    "C": CustomerTier.STANDARD,
    "D": CustomerTier.LOW,
}

PAYMENT_RISK_CODES: dict[str, PaymentRisk] = {
    "L": PaymentRisk.LOW,
    "M": PaymentRisk.MEDIUM,
    "H": PaymentRisk.HIGH,
    "U": PaymentRisk.UNKNOWN,
}

PROCESS_TYPE_CODES: dict[str, ProcessType] = {
    "CNC": ProcessType.CNC_MACHINING,
    "AM": ProcessType.ADDITIVE_3D_PRINTING,
    "SUP": ProcessType.SUPPORT_REMOVAL,
    "DEB": ProcessType.DEBURRING,
    "FIN": ProcessType.FINISHING,
    "HT": ProcessType.HEAT_TREATMENT,
    "SRF": ProcessType.SURFACE_TREATMENT,
    "INS": ProcessType.INSPECTION,
    "ASM": ProcessType.ASSEMBLY,
    "PCK": ProcessType.PACKING,
    "OTH": ProcessType.OTHER,
}


def invert(codes: dict[str, E]) -> dict[E, str]:
    """Enum -> ERP code (first code wins when several map to one member)."""
    out: dict[E, str] = {}
    for code, member in codes.items():
        out.setdefault(member, code)
    return out


def compose_order_id(order_no: str, line_no: int) -> str:
    """Internal order-line id convention shared by the generator and normalizer."""
    return f"{order_no}-{line_no:02d}"


#: Separator used for multi-valued fields in flat ERP exports ("T1;T2").
LIST_SEPARATOR = ";"

__all__ = [
    "CUSTOMER_TIER_CODES",
    "LIST_SEPARATOR",
    "MACHINE_STATUS_CODES",
    "MATERIAL_STATUS_CODES",
    "OPERATION_STATUS_CODES",
    "ORDER_STATUS_CODES",
    "PAYMENT_RISK_CODES",
    "PROCESS_TYPE_CODES",
    "QUALITY_STATUS_CODES",
    "SHIPPING_STATUS_CODES",
    "compose_order_id",
    "invert",
]
