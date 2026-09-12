"""``/sync``: run, history, capabilities report and status (admin)."""

from __future__ import annotations

from app.domain.enums import Role
from tests.api.conftest import API, Seeded


def test_sync_run_history_capabilities_and_status(seeded: Seeded) -> None:
    before = seeded.get("/sync/runs", role=Role.ADMIN)
    assert before["total"] >= 1  # the fixture loaded the dataset through the sync service
    assert before["items"][0]["status"] == "completed"

    response = seeded.post("/sync/run", {"mode": "incremental"}, role=Role.ADMIN)
    assert response.status_code == 200, response.text
    run = response.json()
    assert run["run_id"].startswith("sync_") and run["status"] == "completed"
    assert run["mode"] == "incremental" and run["connector"] == "mock"
    assert run["triggered_by"] == seeded.users[Role.ADMIN].user_id
    assert run["watermark_source"] == before["items"][0]["run_id"]
    assert set(run["records_fetched"]) >= {"customer", "order", "operation", "machine"}
    assert run["reconciliation"]["status"] in ("ok", "warning", "mismatch")
    assert run["issues_count"] == len(run["issues"]) or run["issues_truncated"]

    runs = seeded.get("/sync/runs", role=Role.ADMIN, page_size=10)
    assert runs["total"] == before["total"] + 1 and runs["items"][0]["run_id"] == run["run_id"]
    assert runs["items"][0]["issues"] == []  # listing omits issue rows

    one = seeded.get(f"/sync/runs/{run['run_id']}", role=Role.ADMIN)
    assert one["run_id"] == run["run_id"] and one["status"] == "completed"
    assert one["records_upserted"] == run["records_upserted"]
    missing = seeded.client.get(f"{API}/sync/runs/sync_nope", headers=seeded.headers(Role.ADMIN))
    assert missing.status_code == 404

    capabilities = seeded.get("/sync/capabilities", role=Role.ADMIN)
    assert capabilities["connector_name"] == "mock" and capabilities["supports_incremental"] is True
    assert capabilities["can_schedule"] is True and capabilities["missing_required"] == []
    assert capabilities["coverage_pct"] == 100.0
    assert capabilities["assessments"] and {a["status"] for a in capabilities["assessments"]} == {"Available"}
    first = capabilities["assessments"][0]
    assert {"entity", "field", "importance", "used_by", "impact_if_missing", "recommendation"} <= set(first)

    status = seeded.get("/sync/status", role=Role.ADMIN)
    assert status["connector"] == "mock" and status["health"]["healthy"] is True
    assert (
        status["last_run"]["run_id"] == run["run_id"] and status["last_completed"]["run_id"] == run["run_id"]
    )
    assert status["watermark"] == run["started_at"] and status["runs_total"] == runs["total"]

    entries = seeded.audit(action="sync.run")
    assert entries and entries[0]["entity_id"] == run["run_id"]
    assert entries[0]["user_id"] == seeded.users[Role.ADMIN].user_id
    assert entries[0]["new_value"]["status"] == "completed"

    invalid = seeded.post("/sync/run", {"mode": "sideways"}, role=Role.ADMIN)
    assert invalid.status_code == 422
