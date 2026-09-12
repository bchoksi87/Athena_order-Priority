"""``/schedule``: generation persistence, frozen window, workflow state machine, views, comparison, roles."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db.repositories import AlertRepository, DataQualityRepository, PriorityResultRepository
from app.db.repositories.schedule import OptimizationRunRepository, ScheduleRepository
from app.domain.enums import ROLE_RANK, Role
from tests.api.conftest import API, Seeded
from tests.conftest import NOW

FROZEN_END = (NOW + timedelta(minutes=30)).isoformat()


def _generate(seeded: Seeded, note: str | None = None, role: Role = Role.PLANNER) -> dict[str, Any]:
    response = seeded.post("/schedule/generate", {"note": note} if note else None, role=role)
    assert response.status_code == 201, response.text
    return response.json()


def _entry_key(e: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (e["operation_id"], e["machine_id"], e["setup_start"], e["start"], e["end"])


# ----------------------------------------------------------------- generate


def test_generate_persists_version_run_results_and_views(seeded: Seeded) -> None:
    body = _generate(seeded, note="first plan")
    version = body["version"]
    assert version["version_number"] == seeded.schedule_version + 1
    assert version["status"] == "draft" and version["label"] == "first plan"
    assert version["generated_by"] == seeded.users[Role.PLANNER].user_id
    assert version["algorithm"] == "rule_based" and version["algorithm_version"] == "1.0.0"
    assert version["profile_id"] == "PriorityProfile-A" and version["config_version"] == 1
    assert version["trigger"] == "manual" and version["previous_version"] == seeded.schedule_version
    assert version["input_snapshot_id"] and version["run_id"] == body["run"]["run_id"]
    assert version["entry_count"] == body["entries"] > 0
    assert version["quality"]["score"] == version["quality_score"] is not None
    assert set(version["quality"]["components"]) == {
        "on_time_delivery",
        "lateness",
        "utilization",
        "setup_efficiency",
        "at_risk",
    }
    analytics = version["analytics"]
    assert analytics["kpis"]["total_open_orders"] == len(seeded.results)
    assert analytics["capacity"]["dimension"] == "machine_group" and analytics["capacity"]["totals"]
    assert isinstance(analytics["bottlenecks"], list) and analytics["data_quality"]["rules_run"]
    assert analytics["alerts"]["total"] == body["alerts"] > 0

    run = body["run"]
    assert run["kind"] == "schedule" and run["status"] == "completed"
    assert run["orders_considered"] == body["priority_results"] == len(seeded.results)
    assert run["orders_scheduled"] == version["metrics"]["scheduled_orders"] > 0
    assert run["quality_score"] == version["quality_score"] and run["objective_score"] is not None
    assert run["metrics"]["timings"]["total"] > 0 and run["input_snapshot_id"] == version["input_snapshot_id"]
    assert run["triggered_by"] == seeded.users[Role.PLANNER].user_id
    assert {"total", "persist", "schedule", "priority"} <= set(body["timings"])
    assert body["snapshot"]["summary"]["orders"] == len(seeded.results)

    # persistence: priority results, data-quality issues, alerts and the audit row
    session = seeded.session
    assert PriorityResultRepository(session).count_for_run(run["run_id"]) == body["priority_results"]
    assert DataQualityRepository(session).count(run["run_id"]) == body["data_quality_issues"] > 0
    assert AlertRepository(session).list_active(limit=1).total >= body["alerts"]
    assert ScheduleRepository(session).entry_count(version["version_number"]) == body["entries"]
    stored = OptimizationRunRepository(session).get(run["run_id"])
    assert stored.status == "completed" and stored.metrics["trigger"] == "manual"
    entries = seeded.audit(action="schedule.generated")
    assert len(entries) == 1
    assert entries[0]["user_id"] == seeded.users[Role.PLANNER].user_id
    assert entries[0]["new_value"]["version"] == version["version_number"]
    assert entries[0]["previous_value"]["version"] == seeded.schedule_version
    assert entries[0]["reason"] == "first plan"

    # the new draft is the active plan; GET /schedule pages its entries
    plan = seeded.get("/schedule", page_size=5)
    assert plan["status"] == "draft" and plan["version"]["version_number"] == version["version_number"]
    assert plan["entries"]["total"] == body["entries"] and len(plan["entries"]["items"]) == 5
    first = plan["entries"]["items"][0]
    by_machine = seeded.get("/schedule", machine_id=first["machine_id"], page_size=500)["entries"]
    assert by_machine["total"] > 0 and all(
        e["machine_id"] == first["machine_id"] for e in by_machine["items"]
    )
    by_order = seeded.get(
        f"/schedule/versions/{version['version_number']}/entries", order_id=first["order_id"]
    )
    assert by_order["total"] >= 1 and all(e["order_id"] == first["order_id"] for e in by_order["items"])

    # run details and version listing
    details = seeded.get(f"/schedule/runs/{run['run_id']}")
    assert details["run"]["run_id"] == run["run_id"]
    assert details["version"]["version_number"] == version["version_number"]
    assert details["priority_results"] == body["priority_results"]
    assert details["data_quality_issues"] == body["data_quality_issues"]
    assert details["snapshot"]["snapshot_id"] == version["input_snapshot_id"]
    versions = seeded.get("/schedule/versions")
    assert [v["version_number"] for v in versions["items"]] == [
        version["version_number"],
        seeded.schedule_version,
    ]
    assert seeded.get("/schedule/versions", status="approved")["total"] == 0
    assert seeded.get(f"/schedule/versions/{version['version_number']}")["writeback_receipt"] is None
    assert (
        seeded.client.get(f"{API}/schedule/versions/999", headers=seeded.headers(Role.ADMIN)).status_code
        == 404
    )

    # second generation: entries inside the frozen window are reproduced exactly
    second = _generate(seeded)["version"]
    assert second["version_number"] == version["version_number"] + 1
    assert second["previous_version"] == version["version_number"]
    frozen = seeded.get(
        f"/schedule/versions/{version['version_number']}/entries", end=FROZEN_END, page_size=500
    )
    assert frozen["total"] > 0
    after = seeded.get(
        f"/schedule/versions/{second['version_number']}/entries", end=FROZEN_END, page_size=500
    )
    after_keys = {_entry_key(e) for e in after["items"]}
    for entry in frozen["items"]:
        assert _entry_key(entry) in after_keys, entry

    # comparison: metric pairs, quality pair and change counts
    comparison = seeded.get("/schedule/compare", a=version["version_number"], b=second["version_number"])
    assert comparison["a"]["version_number"] == version["version_number"]
    assert comparison["b"]["version_number"] == second["version_number"]
    assert {"on_time_pct", "avg_lateness_hours", "overall_utilization_pct", "total_setup_hours"} <= set(
        comparison["metrics"]
    )
    pair = comparison["metrics"]["on_time_pct"]
    assert pair["label"] == "On-time delivery" and pair["delta"] == pytest.approx(
        pair["after"] - pair["before"]
    )
    assert comparison["quality"]["before"] == version["quality_score"]
    assert comparison["summary"].startswith("Schedule quality:")
    changes = comparison["changes"]
    assert changes["moved_entries"] + changes["added_entries"] + changes["removed_entries"] >= 0
    assert comparison["moved_orders"] == len(changes["changed_orders"])

    # day view and Gantt shapes
    day = seeded.get(f"/schedule/{NOW.date().isoformat()}")
    assert (
        day["date"] == NOW.date().isoformat() and day["version"]["version_number"] == second["version_number"]
    )
    assert day["entries"] > 0 and day["timezone"]
    busy = [m for m in day["machines"] if m["blocks"]]
    assert busy, "expected at least one machine with work on the day"
    block = busy[0]["blocks"][0]
    assert {"entry", "order_id", "customer_name", "part_id", "order_status", "locked", "late"} <= set(block)
    assert block["setup_start"] <= block["start"] <= block["end"]
    assert block["entry"]["machine_id"] == busy[0]["machine_id"]

    gantt = seeded.get("/schedule/gantt", version=version["version_number"])
    assert gantt["version"]["version_number"] == version["version_number"]
    assert gantt["axis_start"] < gantt["axis_end"] and gantt["entries"] > 0
    starts = [b["setup_start"] for r in gantt["rows"] for b in r["blocks"]]
    assert all(gantt["axis_start"] <= s <= gantt["axis_end"] for s in starts)
    group = busy[0]["machine_group"]
    grouped = seeded.get("/schedule/gantt", machine_group=group)
    assert grouped["rows"] and all(r["machine_group"] == group for r in grouped["rows"])
    for row in grouped["rows"]:
        block_starts = [b["setup_start"] for b in row["blocks"]]
        assert block_starts == sorted(block_starts)
    only = seeded.get("/schedule/gantt", machine_id=busy[0]["machine_id"])
    assert [r["machine_id"] for r in only["rows"]] == [busy[0]["machine_id"]]
    bad = seeded.client.get(
        f"{API}/schedule/gantt",
        headers=seeded.headers(Role.ADMIN),
        params={"start": NOW.isoformat(), "end": (NOW - timedelta(hours=1)).isoformat()},
    )
    assert bad.status_code == 422 and bad.json()["error"] == "validation_error"
    assert (
        seeded.client.get(f"{API}/schedule/not-a-date", headers=seeded.headers(Role.ADMIN)).status_code == 422
    )
    assert seeded.get("/schedule/locks") == []  # the lock router stays reachable next to /{date}


# ------------------------------------------------------------- state machine


def test_workflow_state_machine_and_read_only_publish(seeded: Seeded) -> None:
    v = _generate(seeded)["version"]["version_number"]
    manager = seeded.users[Role.PRODUCTION_MANAGER].user_id

    denied = seeded.post("/schedule/approve", {"version": v, "reason": "go"}, role=Role.PLANNER)
    assert denied.status_code == 403
    early = seeded.post("/schedule/publish", {"version": v, "reason": "go"}, role=Role.PRODUCTION_MANAGER)
    assert early.status_code == 409 and "approve it first" in early.json()["message"]
    blank = seeded.post("/schedule/approve", {"version": v, "reason": "   "}, role=Role.PRODUCTION_MANAGER)
    assert blank.status_code == 422

    approved = seeded.post(
        "/schedule/approve", {"version": v, "reason": "looks good"}, role=Role.PRODUCTION_MANAGER
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved" and approved.json()["approved_by"] == manager
    again = seeded.post("/schedule/approve", {"version": v, "reason": "twice"}, role=Role.PRODUCTION_MANAGER)
    assert again.status_code == 409 and again.json()["error"] == "conflict"

    published = seeded.post(
        "/schedule/publish", {"version": v, "reason": "release"}, role=Role.PRODUCTION_MANAGER
    )
    assert published.status_code == 200, published.text
    body = published.json()
    assert body["version"]["status"] == "published" and body["version"]["published_by"] == manager
    receipt = body["receipt"]
    assert receipt["status"] == "skipped_read_only" and receipt["mode"] == "read_only"
    assert receipt["entries_published"] == 0 and receipt["approved_by"] == manager
    assert body["version"]["writeback_receipt"]["receipt_id"] == receipt["receipt_id"]
    assert seeded.get(f"/schedule/versions/{v}")["writeback_receipt"]["status"] == "skipped_read_only"
    assert seeded.get("/schedule")["status"] == "published"
    assert seeded.get("/schedule")["version"]["version_number"] == v
    assert seeded.post("/schedule/publish", {"version": v, "reason": "x"}, role=Role.ADMIN).status_code == 409
    assert seeded.post("/schedule/reject", {"version": v, "reason": "x"}, role=Role.ADMIN).status_code == 409

    # a newer draft does not displace the published plan until it is published itself
    v2 = _generate(seeded)["version"]["version_number"]
    assert seeded.get("/schedule")["version"]["version_number"] == v
    assert (
        seeded.post("/schedule/approve", {"version": v2, "reason": "ok"}, role=Role.ADMIN).status_code == 200
    )
    assert seeded.get("/schedule")["version"]["version_number"] == v
    second = seeded.post("/schedule/publish", {"version": v2, "reason": "switch"}, role=Role.ADMIN)
    assert second.status_code == 200 and second.json()["superseded"] == 1
    assert seeded.get("/schedule")["version"]["version_number"] == v2
    assert seeded.get(f"/schedule/versions/{v}")["status"] == "superseded"
    assert seeded.get(f"/schedule/versions/{v}")["superseded_at"] is not None

    # rejecting a draft
    v3 = _generate(seeded)["version"]["version_number"]
    rejected = seeded.post(
        "/schedule/reject", {"version": v3, "reason": "not needed"}, role=Role.PRODUCTION_MANAGER
    )
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
    assert rejected.json()["details"]["rejection_reason"] == "not needed"
    assert (
        seeded.post("/schedule/reject", {"version": v3, "reason": "again"}, role=Role.ADMIN).status_code
        == 409
    )
    assert seeded.get("/schedule")["version"]["version_number"] == v2
    assert seeded.get("/schedule/versions", status="rejected")["total"] == 1

    actions = {e["action"] for e in seeded.audit(entity_type="schedule_version")}
    assert {"schedule.generated", "schedule.approved", "schedule.published", "schedule.rejected"} <= actions
    published_rows = seeded.audit(action="schedule.published")
    assert len(published_rows) == 2 and published_rows[-1]["reason"] == "release"
    assert published_rows[-1]["previous_value"] == {"status": "approved"}
    assert published_rows[-1]["new_value"]["receipt"]["status"] == "skipped_read_only"
    assert published_rows[-1]["details"]["writeback_mode"] == "read_only"


# ------------------------------------------------------------------- roles

ENDPOINTS: list[tuple[str, str, dict[str, Any] | None, Role, bool]] = [
    ("GET", "/schedule", None, Role.OPERATOR, True),
    ("GET", "/schedule/versions", None, Role.OPERATOR, True),
    ("GET", "/schedule/versions/1", None, Role.OPERATOR, True),
    ("GET", "/schedule/versions/1/entries", None, Role.OPERATOR, True),
    ("GET", "/schedule/gantt", None, Role.OPERATOR, True),
    ("GET", "/schedule/compare?a=1&b=2", None, Role.OPERATOR, True),
    ("GET", "/schedule/runs/NOPE", None, Role.OPERATOR, True),
    ("GET", "/schedule/2026-09-11", None, Role.OPERATOR, True),
    ("POST", "/schedule/generate", None, Role.PLANNER, False),
    (
        "POST",
        "/schedule/simulate",
        {"scenarios": [{"kind": "machine_down", "machine_id": "NOPE", "duration_hours": 1}]},
        Role.PLANNER,
        False,
    ),
    ("POST", "/schedule/replan", None, Role.PLANNER, False),
    ("POST", "/schedule/approve", {"version": 1, "reason": "r"}, Role.PRODUCTION_MANAGER, False),
    ("POST", "/schedule/publish", {"version": 1, "reason": "r"}, Role.PRODUCTION_MANAGER, False),
    ("POST", "/schedule/reject", {"version": 1, "reason": "r"}, Role.PRODUCTION_MANAGER, False),
    ("GET", "/simulation/scenario-types", None, Role.OPERATOR, True),
    ("GET", "/analytics/kpis", None, Role.EXECUTIVE, True),
    ("GET", "/analytics/capacity", None, Role.EXECUTIVE, True),
    ("GET", "/analytics/bottlenecks", None, Role.EXECUTIVE, True),
    ("GET", "/analytics/on-time-delivery", None, Role.EXECUTIVE, True),
    ("GET", "/analytics/schedule-quality", None, Role.EXECUTIVE, True),
    ("GET", "/sync/runs", None, Role.ADMIN, False),
    ("GET", "/sync/runs/NOPE", None, Role.ADMIN, False),
    ("GET", "/sync/capabilities", None, Role.ADMIN, False),
    ("GET", "/sync/status", None, Role.ADMIN, False),
]


def _allowed(role: Role, minimum: Role, executive_reads: bool) -> bool:
    if role is Role.EXECUTIVE:
        return executive_reads
    return ROLE_RANK[role] >= ROLE_RANK[minimum]


@pytest.mark.parametrize(("method", "path", "body", "minimum", "executive_reads"), ENDPOINTS)
def test_role_matrix(
    app_client: TestClient,
    auth_headers: Any,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    minimum: Role,
    executive_reads: bool,
) -> None:
    """Guards run before handlers: an unseeded database (404s / empty plans) proves the matrix."""
    for role in Role:
        response = app_client.request(method, f"{API}{path}", headers=auth_headers(role), json=body)
        if _allowed(role, minimum, executive_reads):
            assert response.status_code != 403, (role, method, path, response.text)
            assert response.status_code < 500, (role, method, path, response.text)
        else:
            assert response.status_code == 403, (role, method, path, response.text)
            assert response.json()["error"] == "forbidden"
    anonymous = app_client.request(method, f"{API}{path}", json=body)
    assert anonymous.status_code == 401


def test_sync_run_requires_admin(app_client: TestClient, auth_headers: Any) -> None:
    for role in (Role.EXECUTIVE, Role.OPERATOR, Role.SUPERVISOR, Role.PLANNER, Role.PRODUCTION_MANAGER):
        response = app_client.post(f"{API}/sync/run", headers=auth_headers(role), json={"mode": "full"})
        assert response.status_code == 403, role
