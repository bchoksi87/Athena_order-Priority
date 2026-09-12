"""Reconciliation report grading."""

from __future__ import annotations

import pytest

from app.integration.reconciliation import ReconciliationThresholds, reconcile

pytestmark = pytest.mark.unit


def test_all_matching_is_ok() -> None:
    report = reconcile({"order": 100, "machine": 12}, {"order": 100, "machine": 12})
    assert report.status == "ok"
    assert [d.entity for d in report.deltas] == ["machine", "order"]
    assert all(d.delta == 0 and d.delta_pct == 0.0 for d in report.deltas)
    assert "reconciled" in report.summary


def test_small_absolute_drift_is_tolerated() -> None:
    report = reconcile({"order": 10}, {"order": 8})
    assert report.status == "ok"


def test_warning_and_mismatch_levels() -> None:
    report = reconcile(
        {"order": 1000, "operation": 5000, "customer": 800},
        {"order": 970, "operation": 4400, "customer": 800},
    )
    by_entity = {d.entity: d for d in report.deltas}
    assert by_entity["order"].status == "warning" and by_entity["order"].delta == -30
    assert by_entity["operation"].status == "mismatch" and by_entity["operation"].delta_pct == 12.0
    assert by_entity["customer"].status == "ok"
    assert report.status == "mismatch"
    assert "operation: connector 5000 vs stored 4400" in report.summary
    assert report.to_dict()["deltas"][0]["entity"] == "customer"


def test_entities_missing_on_one_side() -> None:
    report = reconcile({"tooling": 40}, {"tooling": 0, "calendar": 2})
    by_entity = {d.entity: d for d in report.deltas}
    assert by_entity["tooling"].status == "mismatch" and by_entity["tooling"].delta_pct == 100.0
    assert by_entity["calendar"].status == "ok"  # within absolute tolerance
    assert reconcile({"x": 0}, {"x": 50}).deltas[0].status == "mismatch"


def test_custom_thresholds() -> None:
    strict = ReconciliationThresholds(warning_pct=0.1, mismatch_pct=1.0, absolute_tolerance=0)
    assert reconcile({"order": 1000}, {"order": 999}, strict).status == "warning"
    assert reconcile({"order": 1000}, {"order": 980}, strict).status == "mismatch"
