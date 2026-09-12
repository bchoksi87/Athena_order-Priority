"""Overrides, expedites, hold/release, move and locks: behaviour plus the audit trail."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from app.domain.enums import Role
from tests.api.conftest import Seeded


def _audit_for_order(seeded: Seeded, order_id: str, action: str) -> list[dict[str, Any]]:
    return [e for e in seeded.audit(entity_type="order", entity_id=order_id) if e["action"] == action]


def _assert_audited(entry: dict[str, Any], seeded: Seeded, role: Role, reason: str) -> None:
    assert entry["user_id"] == seeded.users[role].user_id
    assert entry["timestamp"] == "2026-09-11T08:00:00Z"
    assert entry["reason"] == reason
    assert "previous_value" in entry and "new_value" in entry
    assert entry["new_value"] is not None


# --------------------------------------------------------------- priority overrides


@pytest.mark.parametrize(
    ("kind", "value", "override_type", "action"),
    [
        ("increase", 15, "increase_priority", "override.increase_priority"),
        ("decrease", 10, "decrease_priority", "override.decrease_priority"),
        ("set", 77, "set_priority", "override.set_priority"),
    ],
)
def test_override_priority_creates_override_and_audit(
    seeded: Seeded, kind: str, value: float, override_type: str, action: str
) -> None:
    response = seeded.post(
        f"/orders/{seeded.order_id}/override-priority",
        {"type": kind, "value": value, "reason": "customer escalation"},
        role=Role.PRODUCTION_MANAGER,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["override_type"] == override_type and body["value"] == value and body["active"] is True
    assert body["created_by"] == seeded.users[Role.PRODUCTION_MANAGER].user_id

    entries = _audit_for_order(seeded, seeded.order_id, action)
    assert len(entries) == 1
    _assert_audited(entries[0], seeded, Role.PRODUCTION_MANAGER, "customer escalation")
    previous, new = entries[0]["previous_value"], entries[0]["new_value"]
    assert previous["stored_score"] == seeded.results[seeded.order_id].score
    assert new["override"]["override_id"] == body["override_id"]
    assert "projected_score" in new
    if kind == "set":
        assert new["projected_score"] == value

    active = seeded.get(f"/orders/{seeded.order_id}/overrides")
    assert [o["override_id"] for o in active] == [body["override_id"]]
    detail = seeded.get(f"/orders/{seeded.order_id}")
    assert detail["overrides"][0]["override_id"] == body["override_id"]
    assert any(a["action"] == action for a in detail["audit"])


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/override-priority", {"type": "increase", "value": 5}),
        ("/override-priority", {"type": "increase", "value": 5, "reason": ""}),
        ("/override-priority", {"type": "increase", "value": 5, "reason": "   "}),
        ("/expedite", {}),
        ("/hold", {"reason": ""}),
        ("/release", {}),
        ("/force-next", {"reason": " "}),
        ("/move", {"target_machine_id": "M"}),
    ],
)
def test_reason_is_mandatory(seeded: Seeded, path: str, body: dict[str, Any]) -> None:
    response = seeded.post(f"/orders/{seeded.order_id}{path}", body)
    assert response.status_code == 422, response.text
    assert response.json()["error"] == "validation_error"
    assert seeded.get(f"/orders/{seeded.order_id}/overrides") == []


def test_override_bounds(seeded: Seeded) -> None:
    assert (
        seeded.post(
            f"/orders/{seeded.order_id}/override-priority", {"type": "set", "value": 101, "reason": "r"}
        ).status_code
        == 422
    )
    assert (
        seeded.post(
            f"/orders/{seeded.order_id}/override-priority", {"type": "increase", "value": 0, "reason": "r"}
        ).status_code
        == 422
    )
    past = (seeded.clock.now() - timedelta(hours=1)).isoformat()
    response = seeded.post(
        f"/orders/{seeded.order_id}/override-priority",
        {"type": "increase", "value": 5, "reason": "r", "expires_at": past},
    )
    assert response.status_code == 422 and "expires_at" in response.json()["message"]


def test_override_on_closed_order_is_409(seeded: Seeded) -> None:
    response = seeded.post(
        f"/orders/{seeded.closed_order_id}/override-priority", {"type": "set", "value": 50, "reason": "r"}
    )
    assert response.status_code == 409
    assert response.json()["error"] == "conflict"
    assert seeded.post("/orders/NOPE/force-next", {"reason": "r"}).status_code == 404


def test_force_next_supersedes_previous(seeded: Seeded) -> None:
    first = seeded.post(
        f"/orders/{seeded.order_id}/force-next", {"reason": "first"}, role=Role.PRODUCTION_MANAGER
    ).json()
    second = seeded.post(
        f"/orders/{seeded.order_id}/force-next", {"reason": "second"}, role=Role.PRODUCTION_MANAGER
    ).json()
    active = seeded.get(f"/orders/{seeded.order_id}/overrides")
    assert [o["override_id"] for o in active] == [second["override_id"]]
    entries = _audit_for_order(seeded, seeded.order_id, "override.force_next")
    assert len(entries) == 2
    newest = entries[0]
    assert newest["details"]["superseded_override_ids"] == [first["override_id"]]
    assert newest["new_value"]["forced_next"] is True and newest["new_value"]["projected_score"] == 100


def test_cancel_override(seeded: Seeded) -> None:
    created = seeded.post(
        f"/orders/{seeded.order_id}/override-priority", {"type": "increase", "value": 5, "reason": "r"}
    ).json()
    response = seeded.delete(
        f"/overrides/{created['override_id']}", {"reason": "mistake"}, role=Role.PRODUCTION_MANAGER
    )
    assert response.status_code == 200 and response.json()["active"] is False
    assert seeded.get(f"/orders/{seeded.order_id}/overrides") == []
    entry = _audit_for_order(seeded, seeded.order_id, "override.cancel")[0]
    _assert_audited(entry, seeded, Role.PRODUCTION_MANAGER, "mistake")
    assert entry["previous_value"]["active"] is True and entry["new_value"]["active"] is False
    # cancelling twice conflicts; the reason may also travel as a query parameter
    assert seeded.delete(f"/overrides/{created['override_id']}", {"reason": "again"}).status_code == 409
    assert seeded.delete(f"/overrides/{created['override_id']}").status_code == 422
    other = seeded.post(
        f"/orders/{seeded.order_id}/override-priority", {"type": "increase", "value": 5, "reason": "r"}
    ).json()
    assert (
        seeded.client.request(
            "DELETE",
            f"/api/v1/overrides/{other['override_id']}?reason=query",
            headers=seeded.headers(Role.ADMIN),
        ).status_code
        == 200
    )


# ------------------------------------------------------------------- hold / release


def test_hold_and_release(seeded: Seeded) -> None:
    hold = seeded.post(f"/orders/{seeded.order_id}/hold", {"reason": "awaiting drawing"}, role=Role.PLANNER)
    assert hold.status_code == 201 and hold.json()["override_type"] == "hold_order"
    detail = seeded.get(f"/orders/{seeded.order_id}")
    assert detail["order"]["on_hold"] is True
    assert "awaiting drawing" in detail["order"]["hold_reason"]
    listed = seeded.get("/orders", on_hold="true", page_size=500)
    assert seeded.order_id in {i["order"]["order_id"] for i in listed["items"]}
    # the machine-options view sees the hold as a readiness blocker but still lists machines
    assert (
        seeded.post(f"/orders/{seeded.order_id}/hold", {"reason": "again"}, role=Role.PLANNER).status_code
        == 409
    )

    entry = _audit_for_order(seeded, seeded.order_id, "override.hold")[0]
    _assert_audited(entry, seeded, Role.PLANNER, "awaiting drawing")
    assert entry["previous_value"] == {"on_hold": False, "hold_reason": None}
    assert entry["new_value"]["on_hold"] is True

    release = seeded.post(
        f"/orders/{seeded.order_id}/release", {"reason": "drawing approved"}, role=Role.PLANNER
    )
    assert release.status_code == 201 and release.json()["override_type"] == "release_hold"
    assert seeded.get(f"/orders/{seeded.order_id}")["order"]["on_hold"] is False
    entry = _audit_for_order(seeded, seeded.order_id, "override.release")[0]
    assert entry["previous_value"]["on_hold"] is True and entry["new_value"]["on_hold"] is False
    assert entry["previous_value"]["hold_override_id"] == hold.json()["override_id"]
    assert (
        seeded.post(f"/orders/{seeded.order_id}/release", {"reason": "twice"}, role=Role.PLANNER).status_code
        == 409
    )


def test_erp_hold_cannot_be_released_locally(seeded: Seeded) -> None:
    response = seeded.post(f"/orders/{seeded.held_order_id}/release", {"reason": "r"}, role=Role.PLANNER)
    assert response.status_code == 409
    assert "ERP" in response.json()["message"]
    assert (
        seeded.post(f"/orders/{seeded.held_order_id}/hold", {"reason": "r"}, role=Role.PLANNER).status_code
        == 409
    )


# ------------------------------------------------------------------------ move


def test_move_order_creates_override_and_lock(seeded: Seeded) -> None:
    options = seeded.get(f"/orders/{seeded.order_id}/machines")
    target = options["eligible"][-1]["machine_id"]
    response = seeded.post(
        f"/orders/{seeded.order_id}/move",
        {"target_machine_id": target, "reason": "balance load"},
        role=Role.PRODUCTION_MANAGER,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert (
        body["override"]["override_type"] == "move_order" and body["override"]["target_machine_id"] == target
    )
    assert body["lock"]["lock_type"] == "order" and body["lock"]["machine_id"] == target
    assert body["lock"]["order_id"] == seeded.order_id and body["lock"]["window"] is None
    locks = seeded.get("/schedule/locks", order_id=seeded.order_id)
    assert [lk["lock_id"] for lk in locks] == [body["lock"]["lock_id"]]
    after = seeded.get(f"/orders/{seeded.order_id}/machines")
    assert after["pinned_machine_id"] == target and after["pinned_by"].startswith("lock:")

    entry = _audit_for_order(seeded, seeded.order_id, "override.move")[0]
    _assert_audited(entry, seeded, Role.PRODUCTION_MANAGER, "balance load")
    assert entry["previous_value"]["scheduled_machine_id"] == options["scheduled_machine_id"]
    assert entry["new_value"]["target_machine_id"] == target
    assert entry["details"]["lock_id"] == body["lock"]["lock_id"]

    cancel = seeded.delete(
        f"/overrides/{body['override']['override_id']}", {"reason": "undo"}, role=Role.PRODUCTION_MANAGER
    )
    assert cancel.status_code == 200
    assert seeded.get("/schedule/locks", order_id=seeded.order_id) == []
    assert _audit_for_order(seeded, seeded.order_id, "override.cancel")[0]["details"][
        "released_lock_ids"
    ] == [body["lock"]["lock_id"]]


def test_move_order_with_time_creates_time_slot(seeded: Seeded) -> None:
    options = seeded.get(f"/orders/{seeded.order_id}/machines")
    target = options["eligible"][0]["machine_id"]
    start = seeded.clock.now() + timedelta(hours=3)
    body = seeded.post(
        f"/orders/{seeded.order_id}/move",
        {"target_machine_id": target, "start_at": start.isoformat(), "reason": "customer visit"},
        role=Role.PRODUCTION_MANAGER,
    ).json()
    assert body["lock"]["lock_type"] == "time_slot"
    assert body["lock"]["window"]["start"] == "2026-09-11T11:00:00Z"
    assert body["lock"]["window"]["end"] > body["lock"]["window"]["start"]


def test_move_to_ineligible_machine_is_422(seeded: Seeded) -> None:
    options = seeded.get(f"/orders/{seeded.order_id}/machines")
    rejected = next(iter(options["rejected"]), None)
    if rejected is None:
        pytest.skip("every machine is eligible for this order")
    response = seeded.post(f"/orders/{seeded.order_id}/move", {"target_machine_id": rejected, "reason": "r"})
    assert response.status_code == 422
    assert response.json()["details"]["violations"]
    assert (
        seeded.post(
            f"/orders/{seeded.order_id}/move", {"target_machine_id": "NOPE", "reason": "r"}
        ).status_code
        == 404
    )


def test_lock_machine_assignment(seeded: Seeded) -> None:
    target = seeded.get(f"/orders/{seeded.order_id}/machines")["eligible"][0]["machine_id"]
    body = seeded.post(
        f"/orders/{seeded.order_id}/lock-machine", {"machine_id": target, "reason": "fixture mounted"}
    ).json()
    assert body["override_type"] == "lock_machine_assignment" and body["target_machine_id"] == target
    assert seeded.get(f"/orders/{seeded.order_id}/machines")["pinned_by"] == f"override:{body['override_id']}"
    assert _audit_for_order(seeded, seeded.order_id, "override.lock_machine_assignment")


# --------------------------------------------------------------------- expedites


def test_expedite_defaults_from_profile(seeded: Seeded) -> None:
    response = seeded.post(
        f"/orders/{seeded.order_id}/expedite", {"reason": "VIP"}, role=Role.PRODUCTION_MANAGER
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["boost_points"] == 30.0  # ExpediteConfig.default_boost_points
    assert body["starts_at"] == "2026-09-11T08:00:00Z" and body["expires_at"] == "2026-09-11T12:00:00Z"
    assert seeded.get("/expedites", order_id=seeded.order_id)[0]["expedite_id"] == body["expedite_id"]
    entry = [
        e
        for e in seeded.audit(entity_type="order", entity_id=seeded.order_id)
        if e["action"] == "expedite.create"
    ][0]
    _assert_audited(entry, seeded, Role.PRODUCTION_MANAGER, "VIP")
    assert entry["previous_value"] == {"active_expedites": []}
    assert entry["new_value"]["boost_points"] == 30.0 and entry["new_value"]["duration_hours"] == 4.0

    replaced = seeded.post(
        f"/orders/{seeded.order_id}/expedite", {"reason": "more", "boost_points": 45, "duration_hours": 8}
    ).json()
    active = seeded.get("/expedites", order_id=seeded.order_id)
    assert [e["expedite_id"] for e in active] == [replaced["expedite_id"]]
    entry = [
        e
        for e in seeded.audit(entity_type="order", entity_id=seeded.order_id)
        if e["action"] == "expedite.create"
    ][0]
    assert entry["previous_value"]["active_expedites"][0]["expedite_id"] == body["expedite_id"]
    assert entry["details"]["superseded_expedite_ids"] == [body["expedite_id"]]


def test_expedite_limits(seeded: Seeded) -> None:
    assert (
        seeded.post(f"/orders/{seeded.order_id}/expedite", {"reason": "r", "boost_points": 61}).status_code
        == 422
    )
    assert (
        seeded.post(f"/orders/{seeded.order_id}/expedite", {"reason": "r", "duration_hours": 73}).status_code
        == 422
    )
    both = {
        "reason": "r",
        "duration_hours": 2,
        "expires_at": (seeded.clock.now() + timedelta(hours=2)).isoformat(),
    }
    assert seeded.post(f"/orders/{seeded.order_id}/expedite", both).status_code == 422
    assert seeded.post(f"/orders/{seeded.closed_order_id}/expedite", {"reason": "r"}).status_code == 409
    explicit = seeded.post(
        f"/orders/{seeded.order_id}/expedite",
        {"reason": "r", "expires_at": (seeded.clock.now() + timedelta(hours=6)).isoformat()},
    ).json()
    assert explicit["expires_at"] == "2026-09-11T14:00:00Z"


def test_cancel_expedite(seeded: Seeded) -> None:
    created = seeded.post(f"/orders/{seeded.order_id}/expedite", {"reason": "r"}).json()
    response = seeded.delete(
        f"/expedites/{created['expedite_id']}", {"reason": "resolved"}, role=Role.PRODUCTION_MANAGER
    )
    assert response.status_code == 200 and response.json()["active"] is False
    assert seeded.get("/expedites", order_id=seeded.order_id) == []
    entry = [
        e
        for e in seeded.audit(entity_type="order", entity_id=seeded.order_id)
        if e["action"] == "expedite.cancel"
    ][0]
    _assert_audited(entry, seeded, Role.PRODUCTION_MANAGER, "resolved")
    assert entry["previous_value"]["active"] is True and entry["new_value"]["active"] is False
    assert seeded.delete(f"/expedites/{created['expedite_id']}", {"reason": "again"}).status_code == 409
    assert seeded.delete("/expedites/NOPE", {"reason": "r"}).status_code == 404


# ------------------------------------------------------------------------- locks


def test_machine_lock_defaults_to_lock_window(seeded: Seeded) -> None:
    response = seeded.post(
        "/schedule/lock",
        {"lock_type": "machine", "machine_id": seeded.machine_id, "reason": "next 4 hours"},
        role=Role.PRODUCTION_MANAGER,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["window"]["start"] == "2026-09-11T08:00:00Z"
    assert body["window"]["end"] == "2026-09-11T12:00:00Z"  # SchedulingConfig.lock_window_minutes = 240
    listed = seeded.get("/schedule/locks", machine_id=seeded.machine_id)
    assert [lk["lock_id"] for lk in listed] == [body["lock_id"]]
    assert seeded.get(f"/machines/{seeded.machine_id}")["locks"][0]["lock_id"] == body["lock_id"]

    entry = [e for e in seeded.audit(entity_type="schedule_lock", entity_id=body["lock_id"])][0]
    _assert_audited(entry, seeded, Role.PRODUCTION_MANAGER, "next 4 hours")
    assert entry["action"] == "lock.create" and entry["previous_value"] is None
    assert entry["new_value"]["lock_id"] == body["lock_id"]

    overlapping = seeded.post(
        "/schedule/lock",
        {
            "lock_type": "time_slot",
            "machine_id": seeded.machine_id,
            "window_start": "2026-09-11T10:00:00Z",
            "window_end": "2026-09-11T11:00:00Z",
            "reason": "r",
        },
    )
    assert overlapping.status_code == 409
    assert overlapping.json()["details"]["conflicting_lock_id"] == body["lock_id"]
    later = seeded.post(
        "/schedule/lock",
        {
            "lock_type": "time_slot",
            "machine_id": seeded.machine_id,
            "window_start": "2026-09-11T12:00:00Z",
            "reason": "r",
        },
    )
    assert later.status_code == 201 and later.json()["window"]["end"] == "2026-09-11T16:00:00Z"


def test_unlock(seeded: Seeded) -> None:
    lock = seeded.post(
        "/schedule/lock", {"lock_type": "machine", "machine_id": seeded.machine_id, "reason": "r"}
    ).json()
    response = seeded.post(
        "/schedule/unlock", {"lock_id": lock["lock_id"], "reason": "done"}, role=Role.PRODUCTION_MANAGER
    )
    assert response.status_code == 200 and response.json()["active"] is False
    assert seeded.get("/schedule/locks", machine_id=seeded.machine_id) == []
    entries = seeded.audit(entity_type="schedule_lock", entity_id=lock["lock_id"])
    assert [e["action"] for e in entries] == ["lock.release", "lock.create"]
    assert entries[0]["previous_value"]["active"] is True and entries[0]["new_value"]["active"] is False
    assert seeded.post("/schedule/unlock", {"lock_id": lock["lock_id"], "reason": "twice"}).status_code == 409
    assert seeded.post("/schedule/unlock", {"lock_id": "NOPE", "reason": "r"}).status_code == 404


def test_order_and_sequence_locks(seeded: Seeded) -> None:
    order_lock = seeded.post(
        "/schedule/lock", {"lock_type": "order", "order_id": seeded.order_id, "reason": "keep"}
    )
    assert order_lock.status_code == 201 and order_lock.json()["window"] is None
    assert (
        seeded.post(
            "/schedule/lock", {"lock_type": "order", "order_id": seeded.order_id, "reason": "dup"}
        ).status_code
        == 409
    )
    assert seeded.post("/schedule/lock", {"lock_type": "order", "reason": "no order"}).status_code == 422
    assert (
        seeded.post(
            "/schedule/lock", {"lock_type": "order", "order_id": seeded.closed_order_id, "reason": "r"}
        ).status_code
        == 409
    )

    ids = [i["order"]["order_id"] for i in seeded.get("/orders", readiness="ready", page_size=3)["items"]]
    sequence = seeded.post(
        "/schedule/lock", {"lock_type": "sequence", "sequence_order_ids": ids, "reason": "run in order"}
    )
    assert sequence.status_code == 201 and sequence.json()["sequence_order_ids"] == ids
    assert (
        seeded.post(
            "/schedule/lock", {"lock_type": "sequence", "sequence_order_ids": ids[:1], "reason": "r"}
        ).status_code
        == 422
    )
    clash = seeded.post(
        "/schedule/lock", {"lock_type": "sequence", "sequence_order_ids": [ids[0], "X"], "reason": "r"}
    )
    assert clash.status_code in (404, 409)
    assert seeded.get("/schedule/locks", lock_type="sequence")[0]["lock_id"] == sequence.json()["lock_id"]
    assert seeded.get("/schedule/locks", order_id=ids[1])[0]["lock_id"] == sequence.json()["lock_id"]

    bad_window = seeded.post(
        "/schedule/lock",
        {
            "lock_type": "machine",
            "machine_id": seeded.machine_id,
            "window_start": "2026-09-11T12:00:00Z",
            "window_end": "2026-09-11T11:00:00Z",
            "reason": "r",
        },
    )
    assert bad_window.status_code == 422
