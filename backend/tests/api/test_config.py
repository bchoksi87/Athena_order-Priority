"""Priority / scheduling configuration: versions, audit diffs, preview and rollback."""

from __future__ import annotations

from typing import Any

from app.domain.enums import Role
from tests.api.conftest import Seeded


def _profile_with(seeded: Seeded, **weights: float) -> dict[str, Any]:
    profile = seeded.get("/priority/configuration")["profile"]
    for weight in profile["weights"]:
        if weight["key"] in weights:
            weight["weight"] = weights[weight["key"]]
    return profile


def test_get_priority_configuration(seeded: Seeded) -> None:
    body = seeded.get("/priority/configuration", role=Role.PLANNER)
    assert body["version"]["version"] == 1 and body["version"]["is_active"] is True
    assert body["profile"]["profile_id"] == "PriorityProfile-A"
    assert abs(sum(body["weights_pct"].values()) - 100.0) < 1e-6
    assert body["weights_pct"]["due_date_urgency"] == 25.0


def test_put_creates_version_with_audit_diff(seeded: Seeded) -> None:
    profile = _profile_with(seeded, due_date_urgency=40)
    response = seeded.put(
        "/priority/configuration", {"profile": profile, "reason": "due dates matter more"}, role=Role.ADMIN
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["version"]["version"] == 2 and body["version"]["is_active"] is True
    assert body["previous_version"] == 1
    assert body["config"]["priority_profile"]["version"] == 2
    assert body["config"]["scheduling"]["version"] == 2
    changed = body["changed_new"]
    assert changed["priority_profile.weights[0].weight"] == 40.0
    assert body["changed_previous"]["priority_profile.weights[0].weight"] == 25.0
    assert set(changed) == {
        "priority_profile.version",
        "priority_profile.weights[0].weight",
        "scheduling.version",
    }

    assert seeded.get("/priority/configuration")["weights_pct"]["due_date_urgency"] > 25.0
    versions = seeded.get("/priority/configuration/versions")
    assert [v["version"] for v in versions] == [2, 1]
    assert versions[0]["reason"] == "due dates matter more"
    assert versions[0]["created_by"] == seeded.users[Role.ADMIN].user_id

    entries = seeded.audit(entity_type="system_config", action="config.update_priority_profile")
    assert len(entries) == 1
    entry = entries[0]
    assert entry["reason"] == "due dates matter more" and entry["user_id"] == seeded.users[Role.ADMIN].user_id
    assert entry["previous_value"]["version"] == 1 and entry["new_value"]["version"] == 2
    assert entry["previous_value"]["changes"]["priority_profile.weights[0].weight"] == 25.0
    assert entry["new_value"]["changes"]["priority_profile.weights[0].weight"] == 40.0
    assert "priority_profile.name" not in entry["new_value"]["changes"]  # only changed keys
    assert entry["details"]["changed_keys"] == sorted(changed)


def test_put_unchanged_and_invalid(seeded: Seeded) -> None:
    profile = seeded.get("/priority/configuration")["profile"]
    same = seeded.put("/priority/configuration", {"profile": profile, "reason": "no-op"})
    assert same.status_code == 409
    profile["weights"].append({"key": "due_date_urgency", "weight": 5})
    duplicate = seeded.put("/priority/configuration", {"profile": profile, "reason": "dup"})
    assert duplicate.status_code == 422
    zero = _profile_with(
        seeded,
        **{
            k: 0
            for k in (
                "due_date_urgency",
                "sla_risk",
                "customer_importance",
                "order_value",
                "margin",
                "delay_penalty",
                "production_readiness",
                "machine_availability",
                "setup_efficiency",
            )
        },
    )
    assert seeded.put("/priority/configuration", {"profile": zero, "reason": "all zero"}).status_code == 422
    assert seeded.put("/priority/configuration", {"profile": profile, "reason": ""}).status_code == 422
    assert (
        seeded.put(
            "/priority/configuration",
            {"profile": _profile_with(seeded, due_date_urgency=30), "reason": "r"},
            role=Role.PRODUCTION_MANAGER,
        ).status_code
        == 403
    )
    assert (
        seeded.get("/priority/configuration/versions")
        and len(seeded.get("/priority/configuration/versions")) == 1
    )


def test_preview_sentence_and_deltas(seeded: Seeded) -> None:
    profile = _profile_with(seeded, due_date_urgency=40)
    response = seeded.post(
        "/priority/configuration/preview", {"profile": profile, "top_n": 50}, role=Role.PLANNER
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary"].startswith("Changing Due Date Urgency weight from 25% to 40% would move ")
    assert "into the top 50" in body["summary"]
    assert body["top_n"] == 50 and body["orders_evaluated"] == len(seeded.results)
    assert body["weight_changes"] == [{"key": "due_date_urgency", "previous_pct": 25.0, "new_pct": 40.0}]
    assert len(body["top_n_before"]) == 50 and len(body["top_n_after"]) == 50
    assert set(body["entered_top_n"]) <= set(body["top_n_after"])
    assert set(body["left_top_n"]) <= set(body["top_n_before"])
    assert len(body["entered_top_n"]) == len(body["left_top_n"])
    assert f"move {len(body['entered_top_n'])} order(s) into the top 50" in body["summary"]
    for move in body["biggest_moves"]:
        assert move["rank_delta"] != 0
    # preview never stores anything
    assert [v["version"] for v in seeded.get("/priority/configuration/versions")] == [1]
    assert seeded.audit(entity_type="system_config") == []


def test_rollback_activate_version(seeded: Seeded) -> None:
    seeded.put(
        "/priority/configuration", {"profile": _profile_with(seeded, due_date_urgency=40), "reason": "v2"}
    )
    seeded.put(
        "/priority/configuration", {"profile": _profile_with(seeded, due_date_urgency=35), "reason": "v3"}
    )
    assert seeded.get("/priority/configuration")["version"]["version"] == 3

    response = seeded.post(
        "/priority/configuration/versions/1/activate", {"reason": "rollback: too aggressive"}, role=Role.ADMIN
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["version"]["version"] == 1 and body["previous_version"] == 3
    assert body["changed_previous"]["priority_profile.weights[0].weight"] == 35.0
    assert body["changed_new"]["priority_profile.weights[0].weight"] == 25.0
    active = seeded.get("/priority/configuration")
    assert active["version"]["version"] == 1 and active["weights_pct"]["due_date_urgency"] == 25.0
    versions = {v["version"]: v["is_active"] for v in seeded.get("/priority/configuration/versions")}
    assert versions == {3: False, 2: False, 1: True}

    entry = seeded.audit(entity_type="system_config", action="config.activate_version")[0]
    assert entry["previous_value"]["version"] == 3 and entry["new_value"]["version"] == 1
    assert entry["reason"] == "rollback: too aggressive"
    assert seeded.post("/priority/configuration/versions/1/activate", {"reason": "again"}).status_code == 409
    assert seeded.post("/priority/configuration/versions/99/activate", {"reason": "r"}).status_code == 404

    version3 = seeded.get("/priority/configuration/versions/3")
    assert (
        version3["version"]["version"] == 3
        and version3["config"]["priority_profile"]["weights"][0]["weight"] == 35.0
    )
    assert (
        seeded.client.get(
            "/api/v1/priority/configuration/versions/99", headers=seeded.headers(Role.ADMIN)
        ).status_code
        == 404
    )


def test_scheduling_configuration(seeded: Seeded) -> None:
    body = seeded.get("/scheduling/configuration", role=Role.PLANNER)
    assert body["scheduling"]["lock_window_minutes"] == 240.0
    assert {"replanning", "alerts", "data_quality"} <= set(body)

    scheduling = dict(body["scheduling"], horizon_days=21, lock_window_minutes=120)
    response = seeded.put(
        "/scheduling/configuration", {"scheduling": scheduling, "reason": "longer horizon"}, role=Role.ADMIN
    )
    assert response.status_code == 200, response.text
    assert response.json()["version"]["version"] == 2
    assert response.json()["changed_new"]["scheduling.horizon_days"] == 21
    assert response.json()["changed_previous"]["scheduling.lock_window_minutes"] == 240.0
    assert seeded.get("/scheduling/configuration")["scheduling"]["horizon_days"] == 21

    # the new lock window is used by the lock service defaults
    lock = seeded.post(
        "/schedule/lock", {"lock_type": "machine", "machine_id": seeded.machine_id, "reason": "r"}
    ).json()
    assert lock["window"]["end"] == "2026-09-11T10:00:00Z"

    alerts = dict(body["alerts"], likely_late_slack_hours=12)
    response = seeded.put("/scheduling/configuration", {"alerts": alerts, "reason": "later alerts"})
    assert response.status_code == 200 and response.json()["version"]["version"] == 3
    assert seeded.audit(entity_type="system_config", action="config.update_alerts")
    assert seeded.put("/scheduling/configuration", {"reason": "nothing"}).status_code == 422
    assert (
        seeded.put(
            "/scheduling/configuration", {"scheduling": dict(scheduling, horizon_days="x"), "reason": "r"}
        ).status_code
        == 422
    )
    assert (
        seeded.put(
            "/scheduling/configuration", {"scheduling": scheduling, "reason": "r"}, role=Role.PLANNER
        ).status_code
        == 403
    )
    assert [v["version"] for v in seeded.get("/scheduling/configuration/versions")] == [3, 2, 1]
    assert seeded.get("/scheduling/configuration/versions/2")["config"]["scheduling"]["horizon_days"] == 21
