"""Required-field catalogue and connector capability assessment.

The engines need certain domain fields; a real ERP may not supply all of
them. :func:`assess_capabilities` compares a connector's declared
:class:`ConnectorCapabilities` against :data:`REQUIRED_FIELDS` and produces
a report (Available / Missing per field, engine impact, recommendation) that
feeds ``docs/ERP_INTEGRATION.md`` and the capabilities API endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import structlog

from app.integration.connector import ConnectorCapabilities

log = structlog.get_logger(__name__)

Importance = Literal["required", "recommended", "optional"]


@dataclass(frozen=True, slots=True)
class RequiredField:
    """A domain field the engines depend on, with what happens when it is missing."""

    entity: str
    field: str
    importance: Importance
    used_by: tuple[str, ...]
    impact_if_missing: str
    recommendation: str


def _req(entity: str, name: str, used_by: tuple[str, ...], impact: str, recommendation: str) -> RequiredField:
    return RequiredField(entity, name, "required", used_by, impact, recommendation)


def _rec(entity: str, name: str, used_by: tuple[str, ...], impact: str, recommendation: str) -> RequiredField:
    return RequiredField(entity, name, "recommended", used_by, impact, recommendation)


def _opt(entity: str, name: str, used_by: tuple[str, ...], impact: str, recommendation: str) -> RequiredField:
    return RequiredField(entity, name, "optional", used_by, impact, recommendation)


REQUIRED_FIELDS: tuple[RequiredField, ...] = (
    # Customers
    _req(
        "customer",
        "customer_id",
        ("priority",),
        "Orders cannot be linked to customers",
        "Expose the customer master key",
    ),
    _req(
        "customer",
        "customer_name",
        ("dashboard",),
        "Dashboards show ids instead of names",
        "Map the customer name column",
    ),
    _rec(
        "customer",
        "customer_tier",
        ("priority",),
        "Customer importance factor falls back to the standard tier",
        "Derive a tier from ERP category or revenue band",
    ),
    _rec(
        "customer",
        "customer_revenue",
        ("priority",),
        "Revenue ranking inside customer importance is disabled",
        "Provide trailing-12-month revenue",
    ),
    _opt(
        "customer",
        "customer_profitability",
        ("priority",),
        "Profitability weight inside customer importance is ignored",
        "Provide contribution margin per customer",
    ),
    _rec(
        "customer",
        "sla_hours",
        ("priority",),
        "SLA risk factor scores as 'no SLA' unless the order carries one",
        "Expose contractual turnaround per customer",
    ),
    _opt(
        "customer",
        "escalation_level",
        ("priority",),
        "Escalations do not raise priority automatically",
        "Feed CRM escalation flags",
    ),
    _opt(
        "customer",
        "historical_on_time_delivery",
        ("analytics",),
        "OTD trend per customer is unavailable",
        "Compute from shipment history",
    ),
    # Orders
    _req(
        "order",
        "order_id",
        ("priority", "scheduling"),
        "Nothing can be planned",
        "Expose order number and line number",
    ),
    _req(
        "order",
        "customer_id",
        ("priority",),
        "Customer importance and SLA factors are neutral",
        "Map the customer foreign key",
    ),
    _req(
        "order",
        "part_id",
        ("scheduling",),
        "Batching by part family is impossible",
        "Map the item/part number",
    ),
    _req("order", "quantity", ("scheduling",), "Run times cannot be computed", "Map ordered quantity"),
    _rec(
        "order",
        "completed_quantity",
        ("scheduling",),
        "Partially completed orders are scheduled in full",
        "Map produced/confirmed quantity",
    ),
    _req(
        "order",
        "requested_delivery_date",
        ("priority", "scheduling"),
        "Due-date urgency is unknown; order is flagged by data quality",
        "Map requested or promised date",
    ),
    _rec(
        "order",
        "promised_delivery_date",
        ("priority",),
        "Only the requested date drives urgency",
        "Map the confirmed/promised date",
    ),
    _rec(
        "order",
        "revised_delivery_date",
        ("priority",),
        "Renegotiated dates are ignored",
        "Map the latest agreed date",
    ),
    _req(
        "order",
        "order_status",
        ("priority", "scheduling"),
        "Closed orders may be scheduled",
        "Map ERP status codes (see codes.py)",
    ),
    _rec(
        "order",
        "order_value",
        ("priority", "analytics"),
        "Order value factor and revenue-at-risk are neutral/zero",
        "Map line value in base currency",
    ),
    _rec(
        "order",
        "estimated_margin",
        ("priority", "analytics"),
        "Margin factor and margin-at-risk are neutral/zero",
        "Map or derive cost and margin",
    ),
    _rec(
        "order",
        "material_status",
        ("constraints",),
        "Readiness derives only from material stock levels",
        "Map material availability flag",
    ),
    _rec(
        "order",
        "quality_status",
        ("constraints",),
        "Quality holds are not detected",
        "Map QC hold/rework state",
    ),
    _rec(
        "order",
        "erp_priority",
        ("priority",),
        "ERP priority adjustment is skipped",
        "Map the ERP priority code",
    ),
    _opt(
        "order",
        "lateness_penalty_per_day",
        ("priority",),
        "Delay penalty uses the configured default ratio of order value",
        "Map contractual penalties",
    ),
    _opt(
        "order",
        "drawing_approved",
        ("constraints",),
        "Orders waiting for approval look ready",
        "Map engineering release flag",
    ),
    _opt("order", "on_hold", ("constraints",), "ERP holds are not respected", "Map hold flag and reason"),
    _opt(
        "order",
        "depends_on_order_ids",
        ("priority", "scheduling"),
        "Downstream impact and assembly sequencing are unavailable",
        "Expose BOM/assembly links between orders",
    ),
    # Operations
    _req(
        "operation",
        "operation_id",
        ("scheduling",),
        "Routing cannot be represented",
        "Expose routing/job-card operations",
    ),
    _req(
        "operation",
        "order_id",
        ("scheduling",),
        "Operations cannot be attached to orders",
        "Map order and line keys on operations",
    ),
    _req(
        "operation",
        "sequence",
        ("scheduling",),
        "Operation order is unknown",
        "Map operation sequence number",
    ),
    _req(
        "operation",
        "operation_type",
        ("constraints", "scheduling"),
        "Machines cannot be matched to work",
        "Map work-centre type to a process code",
    ),
    _req(
        "operation",
        "machine_group",
        ("constraints", "scheduling"),
        "Operation cannot be placed; flagged by data quality",
        "Map work centre / machine group",
    ),
    _req(
        "operation",
        "cycle_minutes_per_unit",
        ("scheduling",),
        "Run time unknown; operation cannot be scheduled",
        "Map standard cycle time",
    ),
    _rec(
        "operation",
        "setup_minutes",
        ("scheduling",),
        "Configured default setup time is used and flagged",
        "Map standard setup time",
    ),
    _rec(
        "operation",
        "operation_status",
        ("scheduling",),
        "Completed operations may be re-scheduled",
        "Map operation confirmation status",
    ),
    _rec(
        "operation",
        "material_id",
        ("constraints",),
        "Material readiness cannot be checked per operation",
        "Map component/material requirement",
    ),
    _opt(
        "operation",
        "tooling_ids",
        ("constraints",),
        "Tooling constraints are ignored",
        "Map tool list per operation",
    ),
    _opt(
        "operation",
        "machine_cycle_minutes",
        ("scheduling",),
        "All machines assumed equally fast",
        "Provide per-machine cycle times if available",
    ),
    _opt(
        "operation",
        "setup_family",
        ("scheduling",),
        "Setup optimisation uses material only",
        "Provide fixture/setup family codes",
    ),
    # Machines
    _req(
        "machine",
        "machine_id",
        ("scheduling",),
        "No resources to schedule on",
        "Expose the machine/work-centre master",
    ),
    _req(
        "machine",
        "machine_group",
        ("constraints", "scheduling"),
        "Alternative machines cannot be derived",
        "Map work-centre grouping",
    ),
    _req(
        "machine",
        "process_type",
        ("constraints",),
        "Eligibility cannot be checked",
        "Map machine type to a process code",
    ),
    _rec(
        "machine",
        "status",
        ("constraints",),
        "Down machines receive work",
        "Map machine status / breakdown flag",
    ),
    _rec(
        "machine",
        "calendar_id",
        ("calendar",),
        "Plant default calendar is used for every machine",
        "Map shift pattern per machine",
    ),
    _rec(
        "machine",
        "maintenance_windows",
        ("calendar",),
        "Maintenance is not blocked out",
        "Feed the preventive maintenance schedule",
    ),
    _opt(
        "machine",
        "efficiency",
        ("scheduling",),
        "All machines assumed nominal speed",
        "Provide efficiency factors",
    ),
    _opt(
        "machine",
        "compatible_materials",
        ("constraints",),
        "Material/machine compatibility is not enforced",
        "Provide material compatibility matrix",
    ),
    _opt(
        "machine",
        "current_setup_family",
        ("scheduling",),
        "Setup efficiency at the start of the horizon is unknown",
        "Expose current job / setup",
    ),
    # Materials
    _req(
        "material",
        "material_id",
        ("constraints",),
        "Material readiness cannot be checked",
        "Expose the material master",
    ),
    _req(
        "material",
        "available_quantity",
        ("constraints",),
        "Every material looks unavailable",
        "Map on-hand stock",
    ),
    _rec(
        "material",
        "reserved_quantity",
        ("constraints",),
        "Allocated stock counts as free",
        "Map reservations/allocations",
    ),
    _rec(
        "material",
        "expected_receipt_date",
        ("constraints", "priority"),
        "Blocked orders have no expected release time",
        "Map open purchase-order receipt dates",
    ),
    _opt(
        "material",
        "incoming_quantity",
        ("constraints",),
        "Incoming stock is unknown",
        "Map open purchase-order quantity",
    ),
    # Tooling
    _rec(
        "tooling",
        "tooling_id",
        ("constraints",),
        "Tooling constraints are disabled",
        "Expose the tool master",
    ),
    _rec(
        "tooling", "available", ("constraints",), "Tool shortages are not detected", "Map tool availability"
    ),
    _opt(
        "tooling",
        "compatible_machine_ids",
        ("constraints",),
        "Tools assumed usable on any machine",
        "Map tool/machine compatibility",
    ),
    # Calendars
    _rec(
        "calendar",
        "calendar_id",
        ("calendar",),
        "A default two-shift calendar is assumed",
        "Expose shift calendars",
    ),
    _rec("calendar", "shifts", ("calendar",), "Working hours are assumed", "Map shift start/end/days"),
    _opt(
        "calendar",
        "holidays",
        ("calendar",),
        "Holidays are treated as working days",
        "Map the plant holiday list",
    ),
)


@dataclass(slots=True)
class FieldAssessment:
    entity: str
    field: str
    importance: Importance
    available: bool
    used_by: tuple[str, ...]
    impact_if_missing: str
    recommendation: str

    @property
    def status(self) -> str:
        return "Available" if self.available else "Missing"

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity": self.entity,
            "field": self.field,
            "importance": self.importance,
            "status": self.status,
            "used_by": list(self.used_by),
            "impact_if_missing": self.impact_if_missing,
            "recommendation": self.recommendation,
        }


@dataclass(slots=True)
class CapabilityReport:
    connector_name: str
    supports_incremental: bool
    supports_webhooks: bool
    assessments: list[FieldAssessment] = field(default_factory=list)

    @property
    def missing(self) -> list[FieldAssessment]:
        return [a for a in self.assessments if not a.available]

    @property
    def missing_required(self) -> list[FieldAssessment]:
        return [a for a in self.missing if a.importance == "required"]

    @property
    def coverage_pct(self) -> float:
        if not self.assessments:
            return 0.0
        return 100.0 * sum(1 for a in self.assessments if a.available) / len(self.assessments)

    @property
    def can_schedule(self) -> bool:
        """True when every *required* field is available."""
        return not self.missing_required

    def by_entity(self) -> dict[str, list[FieldAssessment]]:
        grouped: dict[str, list[FieldAssessment]] = {}
        for assessment in self.assessments:
            grouped.setdefault(assessment.entity, []).append(assessment)
        return grouped

    def to_dict(self) -> dict[str, Any]:
        return {
            "connector_name": self.connector_name,
            "supports_incremental": self.supports_incremental,
            "supports_webhooks": self.supports_webhooks,
            "coverage_pct": round(self.coverage_pct, 2),
            "can_schedule": self.can_schedule,
            "missing_required": [a.field for a in self.missing_required],
            "assessments": [a.to_dict() for a in self.assessments],
        }

    def to_markdown(self) -> str:
        """Table for ``docs/ERP_INTEGRATION.md`` (Required / Available / Missing)."""
        lines = [
            f"Connector: `{self.connector_name}` — coverage {self.coverage_pct:.0f}%"
            f", incremental: {'yes' if self.supports_incremental else 'no'}"
            f", webhooks: {'yes' if self.supports_webhooks else 'no'}",
            "",
            "| Entity | Field | Importance | Status | Used by | Impact if missing | Recommendation |",
            "|---|---|---|---|---|---|---|",
        ]
        for a in self.assessments:
            lines.append(
                f"| {a.entity} | `{a.field}` | {a.importance} | {a.status} | {', '.join(a.used_by)} | "
                f"{a.impact_if_missing} | {a.recommendation} |"
            )
        return "\n".join(lines)


def assess_capabilities(
    caps: ConnectorCapabilities, catalogue: tuple[RequiredField, ...] = REQUIRED_FIELDS
) -> CapabilityReport:
    """Compare declared connector fields against the engine requirements."""
    report = CapabilityReport(
        connector_name=caps.connector_name,
        supports_incremental=caps.supports_incremental,
        supports_webhooks=caps.supports_webhooks,
    )
    for required in catalogue:
        report.assessments.append(
            FieldAssessment(
                entity=required.entity,
                field=required.field,
                importance=required.importance,
                available=caps.has(required.entity, required.field),
                used_by=required.used_by,
                impact_if_missing=required.impact_if_missing,
                recommendation=required.recommendation,
            )
        )
    log.info(
        "capabilities.assessed",
        connector=caps.connector_name,
        coverage_pct=round(report.coverage_pct, 1),
        missing_required=[a.field for a in report.missing_required],
    )
    return report


__all__ = ["REQUIRED_FIELDS", "CapabilityReport", "FieldAssessment", "RequiredField", "assess_capabilities"]
