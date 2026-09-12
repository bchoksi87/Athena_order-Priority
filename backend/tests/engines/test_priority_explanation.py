"""Explanation text is rendered from the computed numbers and re-sums to the score."""

from __future__ import annotations

import re

import pytest

from app.core.clock import FrozenClock
from app.domain.config import PriorityProfile
from app.domain.enums import OverrideType, ProcessType
from app.engines.priority import PriorityEngine, default_factors, explanation_lines, render_explanation
from app.engines.priority.explanation import format_points
from tests.engines.factories import (
    NOW,
    at,
    make_calendar_spec,
    make_expedite,
    make_machine,
    make_order_with_routing,
    make_override,
    make_snapshot,
)

LINE = re.compile(r"^([+-]?\d+(?:\.\d)?) — (.+?): (.+)$")


def _snapshot(**kw):
    o1, ops1 = make_order_with_routing(
        "R3D-10482", due_in_days=0.75, order_value=25_000, erp_priority=2, received_date=at(days=-9)
    )
    o2, ops2 = make_order_with_routing(
        "O2", due_in_days=6, order_value=500, on_hold=True, hold_reason="drawing"
    )
    o3, ops3 = make_order_with_routing("O3", due_in_days=30, order_value=None)
    return make_snapshot(
        orders=[o1, o2, o3],
        operations=ops1 + ops2 + ops3,
        machines=[
            make_machine("M1", calendar_id="CAL1"),
            make_machine("D1", ProcessType.DEBURRING, "DEBURRING", calendar_id="CAL1"),
        ],
        calendars=[make_calendar_spec("CAL1")],
        default_calendar_id="CAL1",
        **kw,
    )


@pytest.fixture
def results():
    engine = PriorityEngine(default_factors(), FrozenClock(NOW))
    snapshot = _snapshot(
        expedites=[make_expedite("R3D-10482", 60)],
        overrides=[make_override("O3", OverrideType.DECREASE_PRIORITY, value=200)],
    )
    return engine.evaluate(snapshot, PriorityProfile(blocked_order_cap=20.0))


def test_format_points() -> None:
    assert format_points(28.0) == "+28"
    assert format_points(-3.0) == "-3"
    assert format_points(27.44) == "+27.4"
    assert format_points(0.01) == "0"
    assert format_points(-0.26) == "-0.3"


def test_explanation_layout_matches_spec(results) -> None:
    text = results["R3D-10482"].explanation
    lines = text.split("\n")
    assert lines[0] == "ORDER #R3D-10482"
    assert lines[1] == "Priority: 100"
    assert lines[2] == "Why?"
    assert lines[3].startswith("+") and "Due Date Urgency: Due in 18 hours" in lines[3]
    assert any(line.startswith("+5 — ERP priority: ERP priority 2") for line in lines)
    assert any("— Aging: Waiting 9 days (4 beyond 5)" in line for line in lines)
    assert any("— Expedite: Expedited by manager" in line for line in lines)
    assert any(line.startswith("-") and "Cap: Score limited to 0..100" in line for line in lines)
    assert "(not weighted)" in text  # zero-weight factors are still explained
    assert lines[-1] == "Total: 100"


@pytest.mark.parametrize("order_id", ["R3D-10482", "O2", "O3"])
def test_lines_re_sum_to_the_score(results, order_id: str) -> None:
    result = results[order_id]
    structured = explanation_lines(result)
    assert sum(line["points"] for line in structured) == pytest.approx(result.score, abs=1e-9)
    parsed = [LINE.match(line) for line in result.explanation.split("\n")[3:] if " — " in line]
    assert all(parsed) and len(parsed) == len(structured)
    displayed = sum(float(m.group(1)) for m in parsed)
    assert displayed == pytest.approx(result.score, abs=0.05 * len(parsed) + 0.5)
    labels = [m.group(2) for m in parsed]
    assert labels[: len(result.factors)] == [f.name for f in result.factors]


def test_blocked_cap_and_negative_clamp_lines(results) -> None:
    blocked = results["O2"]
    assert blocked.blocked and blocked.score == 20.0
    kinds = [line["key"] for line in explanation_lines(blocked)]
    assert "blocked_cap" in kinds
    assert blocked.explanation.split("\n")[-1].startswith("Blocked: order on hold: drawing")
    floor = results["O3"]
    assert floor.score == 0.0
    cap = [line for line in explanation_lines(floor) if line["key"] == "clamp"][0]
    assert cap["points"] > 0 and "raw total -" in cap["reason"]


def test_render_is_pure_function_of_result(results) -> None:
    result = results["R3D-10482"]
    assert render_explanation(result) == result.explanation
    result.factors[0].points += 5.0
    assert "Cap" in render_explanation(result)  # re-rendered from the (now inconsistent) lists
