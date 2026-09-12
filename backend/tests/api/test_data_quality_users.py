"""Data quality runs/dashboard and administrator user management."""

from __future__ import annotations

from app.domain.enums import Role
from tests.api.conftest import Seeded

# --------------------------------------------------------------- data quality


def test_data_quality_run_and_summary(seeded: Seeded) -> None:
    before = seeded.get("/data-quality", role=Role.PLANNER)
    assert before["run_id"] is None and before["total_issues"] == 0
    assert before["dashboard"]["headline"] == "All open orders pass the blocking data quality checks"
    assert before["dashboard"]["open_orders"] == len(seeded.results)

    response = seeded.post("/data-quality/run", None, role=Role.PLANNER)
    assert response.status_code == 200, response.text
    run = response.json()
    assert run["run_id"] and run["total_issues"] > 0
    assert run["by_severity"]["blocking"] > 0
    dashboard = run["dashboard"]
    assert dashboard["unschedulable_orders"] > 0
    assert dashboard["headline"].startswith(
        f"{dashboard['unschedulable_orders']} orders cannot be scheduled because: "
    )
    assert sum(r["orders"] for r in dashboard["reasons"]) == dashboard["unschedulable_orders"]
    for reason in dashboard["reasons"]:
        assert f"{reason['orders']} {reason['label']}" in dashboard["headline"]
    assert run["engine_summary"]["rules_run"]

    summary = seeded.get("/data-quality", role=Role.EXECUTIVE)
    assert summary["run_id"] == run["run_id"]
    assert summary["total_issues"] == run["total_issues"] and summary["by_code"] == run["by_code"]
    assert summary["dashboard"]["headline"] == dashboard["headline"]
    assert summary["dashboard"]["reasons"] == dashboard["reasons"]
    assert summary["engine_summary"] == {}

    issues = seeded.get("/data-quality/issues", page_size=10)
    assert issues["total"] == run["total_issues"] and len(issues["items"]) == 10
    blocking = seeded.get("/data-quality/issues", severity="blocking", page_size=500)
    assert blocking["total"] == run["by_severity"]["blocking"]
    assert all(i["severity"] == "blocking" for i in blocking["items"])
    code, count = next(iter(run["by_code"].items()))
    assert seeded.get("/data-quality/issues", code=code, page_size=500)["total"] == count
    first = blocking["items"][0]
    assert (
        seeded.get("/data-quality/issues", entity_type=first["entity_type"], entity_id=first["entity_id"])[
            "total"
        ]
        >= 1
    )

    entry = seeded.audit(entity_type="data_quality_run", entity_id=run["run_id"])[0]
    assert entry["action"] == "data_quality.run" and entry["user_id"] == seeded.users[Role.PLANNER].user_id
    assert entry["new_value"]["issues"] == run["total_issues"]

    second = seeded.post("/data-quality/run", None, role=Role.ADMIN).json()
    assert second["run_id"] != run["run_id"] and second["total_issues"] == run["total_issues"]
    assert seeded.get("/data-quality/issues", page_size=1)["total"] == run["total_issues"]  # latest run only

    order_issue = next((i for i in blocking["items"] if i["entity_type"] == "order"), None)
    if order_issue is not None:
        detail = seeded.get(f"/orders/{order_issue['entity_id']}")
        assert any(i["code"] == order_issue["code"] for i in detail["data_quality_issues"])


# ---------------------------------------------------------------------- users


def test_user_admin_lifecycle(seeded: Seeded) -> None:
    users = seeded.get("/users", role=Role.ADMIN)
    assert {u["username"] for u in users} == {
        "admin",
        "manager",
        "planner",
        "supervisor",
        "operator",
        "executive",
    }
    assert "password" not in users[0] and "password_hash" not in users[0]

    response = seeded.post(
        "/users",
        {
            "username": "NewPlanner",
            "password": "s3cret-pass",
            "role": "planner",
            "display_name": "New Planner",
            "email": "np@example.com",
            "reason": "joined",
        },
        role=Role.ADMIN,
    )
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["username"] == "newplanner" and created["role"] == "planner" and created["active"] is True
    assert seeded.get(f"/users/{created['user_id']}")["email"] == "np@example.com"

    login = seeded.client.post(
        "/api/v1/auth/login", json={"username": "newplanner", "password": "s3cret-pass"}
    )
    assert login.status_code == 200 and login.json()["role"] == "planner"

    entry = seeded.audit(entity_type="user", entity_id=created["user_id"])[0]
    assert entry["action"] == "user.create" and entry["reason"] == "joined"
    assert entry["new_value"]["username"] == "newplanner" and "password" not in entry["new_value"]

    assert (
        seeded.post(
            "/users",
            {"username": "newplanner", "password": "s3cret-pass", "role": "planner", "display_name": "Dup"},
        ).status_code
        == 409
    )
    assert (
        seeded.post(
            "/users", {"username": "short", "password": "short", "role": "planner", "display_name": "S"}
        ).status_code
        == 422
    )
    assert (
        seeded.post(
            "/users", {"username": "x", "password": "s3cret-pass", "role": "boss", "display_name": "S"}
        ).status_code
        == 422
    )

    reset = seeded.post(
        f"/users/{created['user_id']}/reset-password",
        {"password": "another-pass", "reason": "forgot"},
        role=Role.ADMIN,
    )
    assert reset.status_code == 200
    assert (
        seeded.client.post(
            "/api/v1/auth/login", json={"username": "newplanner", "password": "s3cret-pass"}
        ).status_code
        == 401
    )
    assert (
        seeded.client.post(
            "/api/v1/auth/login", json={"username": "newplanner", "password": "another-pass"}
        ).status_code
        == 200
    )
    entry = seeded.audit(entity_type="user", entity_id=created["user_id"], action="user.reset_password")[0]
    assert entry["reason"] == "forgot" and "another-pass" not in str(entry)

    deactivated = seeded.patch(
        f"/users/{created['user_id']}", {"active": False, "reason": "left the company"}, role=Role.ADMIN
    )
    assert deactivated.status_code == 200 and deactivated.json()["active"] is False
    assert (
        seeded.client.post(
            "/api/v1/auth/login", json={"username": "newplanner", "password": "another-pass"}
        ).status_code
        == 401
    )
    assert seeded.get("/users", active_only="true") and all(
        u["active"] for u in seeded.get("/users", active_only="true")
    )
    entry = seeded.audit(entity_type="user", entity_id=created["user_id"], action="user.deactivate")[0]
    assert entry["previous_value"]["active"] is True and entry["new_value"]["active"] is False
    assert (
        seeded.patch(f"/users/{created['user_id']}", {"active": False, "reason": "again"}).status_code == 409
    )
    assert seeded.patch(f"/users/{created['user_id']}", {"active": True, "reason": "back"}).status_code == 200
    assert seeded.patch(f"/users/{created['user_id']}", {"active": True}).status_code == 422

    admin_id = seeded.users[Role.ADMIN].user_id
    assert seeded.patch(f"/users/{admin_id}", {"active": False, "reason": "oops"}).status_code == 409
    assert seeded.patch("/users/NOPE", {"active": False, "reason": "r"}).status_code == 404
    assert (
        seeded.client.get("/api/v1/users", headers=seeded.headers(Role.PRODUCTION_MANAGER)).status_code == 403
    )
