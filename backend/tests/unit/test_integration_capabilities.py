"""Capability catalogue and assessment report."""

from __future__ import annotations

import pytest

from app.core.clock import FrozenClock
from app.integration.capabilities import REQUIRED_FIELDS, assess_capabilities
from app.integration.connector import ConnectorCapabilities
from app.integration.field_maps import target_fields
from app.integration.mock_connector import MockERPConnector
from synthetic.generator import SyntheticDataGenerator

pytestmark = pytest.mark.unit


def test_catalogue_is_well_formed() -> None:
    keys = [(f.entity, f.field) for f in REQUIRED_FIELDS]
    assert len(keys) == len(set(keys))
    for required in REQUIRED_FIELDS:
        assert required.field in target_fields(required.entity), (required.entity, required.field)
        assert required.used_by and required.impact_if_missing and required.recommendation


def test_mock_connector_covers_everything() -> None:
    dataset = SyntheticDataGenerator(seed=1, scale="small").generate()
    connector = MockERPConnector(dataset, FrozenClock(dataset.as_of))
    report = assess_capabilities(connector.capabilities())
    assert report.coverage_pct == 100.0 and report.can_schedule
    assert report.missing == [] and report.supports_incremental
    assert set(report.by_entity()) == {f.entity for f in REQUIRED_FIELDS}


def test_missing_fields_are_reported_with_impact() -> None:
    caps = ConnectorCapabilities(
        connector_name="legacy",
        fields={
            "customer": ["customer_id", "customer_name"],
            "order": ["order_id", "customer_id", "part_id", "quantity", "order_status"],
            "operation": ["operation_id", "order_id", "sequence", "operation_type", "machine_group"],
            "machine": ["machine_id", "machine_group", "process_type"],
            "material": ["material_id"],
        },
    )
    report = assess_capabilities(caps)
    assert not report.can_schedule
    missing_required = {(a.entity, a.field) for a in report.missing_required}
    assert ("order", "requested_delivery_date") in missing_required
    assert ("operation", "cycle_minutes_per_unit") in missing_required
    assert ("material", "available_quantity") in missing_required
    assert ("tooling", "tooling_id") not in missing_required  # recommended only
    due = next(a for a in report.assessments if a.field == "requested_delivery_date")
    assert due.status == "Missing" and "urgency" in due.impact_if_missing
    assert 0 < report.coverage_pct < 50
    payload = report.to_dict()
    assert payload["connector_name"] == "legacy" and payload["missing_required"]
    markdown = report.to_markdown()
    assert "| order | `requested_delivery_date` | required | Missing |" in markdown
