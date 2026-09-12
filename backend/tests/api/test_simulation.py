"""``POST /schedule/simulate`` and ``GET /simulation/scenario-types`` (spec Phase 7)."""

from __future__ import annotations

from app.domain.enums import Role
from app.engines.simulation.scenarios import SCENARIO_KINDS
from tests.api.conftest import API, Seeded


def test_machine_down_simulation_returns_diff_without_creating_a_version(seeded: Seeded) -> None:
    versions_before = seeded.get("/schedule/versions")["total"]
    scenario = {
        "kind": "machine_down",
        "machine_id": seeded.machine_id,
        "duration_hours": 8,
        "label": "spindle",
    }
    response = seeded.post(
        "/schedule/simulate", {"scenarios": [scenario], "note": "what if", "top_n": 5}, role=Role.PLANNER
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["simulation_id"].startswith("sim_") and body["note"] == "what if"
    assert body["baseline_version"] == seeded.schedule_version
    assert len(body["scenarios"]) == 1
    record = body["scenarios"][0]
    assert record["kind"] == "machine_down" and record["label"] == "spindle"
    assert seeded.machine_id in record["affected_machine_ids"]
    assert record["parameters"]["machine_id"] == seeded.machine_id
    assert record["description"].startswith(f"Machine {seeded.machine_id} down")

    diff = body["diff"]
    assert diff["orders_affected"] >= 0 and diff["summary"].endswith("%") or "affected" in diff["summary"]
    assert body["summary"] == diff["summary"]
    assert diff["late_orders_before"] >= 0 and diff["late_orders_after"] >= 0
    assert isinstance(diff["bottlenecks_before"], list) and isinstance(diff["bottlenecks_after"], list)
    assert body["baseline"]["entries"] > 0 and body["baseline"]["quality"]["score"] >= 0
    assert body["scenario"]["metrics"]["scheduled_orders"] >= 0
    assert body["comparison"]["on_time_pct"]["label"] == "On-time delivery"
    assert body["comparison"]["quality_score"]["before"] == body["baseline"]["quality"]["score"]
    assert body["comparison_summary"].startswith("Schedule quality:")
    affected = body["affected_orders"]
    assert len(affected) <= 5 and len(affected) == min(5, diff["orders_affected"])
    for item in affected:
        assert item["order_id"] == item["delta"]["order_id"] and item["customer_id"]
        assert "baseline_late" in item["delta"] and "scenario_late" in item["delta"]

    assert seeded.get("/schedule/versions")["total"] == versions_before  # nothing persisted
    entries = seeded.audit(action="simulation.run")
    assert len(entries) == 1 and entries[0]["entity_id"] == body["simulation_id"]
    assert entries[0]["new_value"]["scenarios"][0]["kind"] == "machine_down"
    assert entries[0]["details"]["scenario_kinds"] == ["machine_down"]
    assert entries[0]["reason"] == "what if"

    invalid = seeded.post(
        "/schedule/simulate", {"scenarios": [{"kind": "machine_down", "machine_id": seeded.machine_id}]}
    )
    assert invalid.status_code == 422  # needs exactly one of end / duration_hours
    unknown = seeded.post("/schedule/simulate", {"scenarios": [{"kind": "teleport"}]})
    assert unknown.status_code == 422
    empty = seeded.post("/schedule/simulate", {"scenarios": []})
    assert empty.status_code == 422


def test_scenario_types_expose_json_schema(seeded: Seeded) -> None:
    body = seeded.get("/simulation/scenario-types", role=Role.PLANNER)
    kinds = [k["kind"] for k in body["kinds"]]
    assert kinds == list(SCENARIO_KINDS)
    machine_down = next(k for k in body["kinds"] if k["kind"] == "machine_down")
    assert machine_down["title"] and "machine_id" in machine_down["schema"]["properties"]
    assert machine_down["schema"]["properties"]["kind"]["const"] == "machine_down"
    assert "$defs" in body["schema"] and "oneOf" in body["schema"]
    response = seeded.client.get(f"{API}/simulation/scenario-types", headers=seeded.headers(Role.EXECUTIVE))
    assert response.status_code == 200
