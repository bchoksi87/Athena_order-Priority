"""Customer rules CRUD, alert inbox and audit query filters."""

from __future__ import annotations

from datetime import timedelta

from app.db.repositories import AlertRepository
from app.domain.enums import AlertSeverity, AlertType, Role
from app.domain.results import Alert
from tests.api.conftest import Seeded

# ------------------------------------------------------------------ customers


def test_customer_rules_crud(seeded: Seeded) -> None:
    cid = seeded.customer_id
    missing = seeded.client.get(f"/api/v1/customers/{cid}/rules", headers=seeded.headers(Role.PLANNER))
    assert missing.status_code == 404

    created = seeded.put(
        f"/customers/{cid}/rules",
        {
            "sla_hours": 24,
            "tier_override": "strategic",
            "priority_boost_points": 5,
            "notes": "key account",
            "reason": "contract",
        },
        role=Role.PRODUCTION_MANAGER,
    )
    assert created.status_code == 200, created.text
    assert created.json() == {
        "customer_id": cid,
        "sla_hours": 24.0,
        "tier_override": "strategic",
        "priority_boost_points": 5.0,
        "notes": "key account",
        "active": True,
    }
    assert seeded.get(f"/customers/{cid}/rules", role=Role.PLANNER)["sla_hours"] == 24.0
    customer = seeded.get(f"/customers/{cid}")
    assert customer["rule"]["sla_hours"] == 24.0
    assert customer["effective_tier"] == "strategic" and customer["effective_sla_hours"] == 24.0

    entries = seeded.audit(entity_type="customer_rule", entity_id=cid)
    assert [e["action"] for e in entries] == ["customer_rule.create"]
    assert entries[0]["previous_value"] is None and entries[0]["new_value"]["sla_hours"] == 24.0
    assert (
        entries[0]["reason"] == "contract"
        and entries[0]["user_id"] == seeded.users[Role.PRODUCTION_MANAGER].user_id
    )

    updated = seeded.put(
        f"/customers/{cid}/rules", {"sla_hours": 48, "reason": "renegotiated"}, role=Role.PRODUCTION_MANAGER
    )
    assert (
        updated.status_code == 200
        and updated.json()["sla_hours"] == 48.0
        and updated.json()["tier_override"] is None
    )
    entries = seeded.audit(entity_type="customer_rule", entity_id=cid)
    assert entries[0]["action"] == "customer_rule.update"
    assert entries[0]["previous_value"]["sla_hours"] == 24.0 and entries[0]["new_value"]["sla_hours"] == 48.0

    listed = seeded.get("/customers", search=cid, role=Role.PLANNER)
    assert listed["total"] == 1 and listed["items"][0]["rule"]["sla_hours"] == 48.0

    deleted = seeded.delete(f"/customers/{cid}/rules", {"reason": "expired"}, role=Role.PRODUCTION_MANAGER)
    assert deleted.status_code == 200 and deleted.json()["sla_hours"] == 48.0
    assert (
        seeded.client.get(f"/api/v1/customers/{cid}/rules", headers=seeded.headers(Role.PLANNER)).status_code
        == 404
    )
    assert seeded.delete(f"/customers/{cid}/rules", {"reason": "again"}).status_code == 404
    assert seeded.audit(entity_type="customer_rule", entity_id=cid)[0]["action"] == "customer_rule.delete"
    assert seeded.audit(entity_type="customer_rule", entity_id=cid)[0]["new_value"] is None


def test_customer_rule_validation(seeded: Seeded) -> None:
    cid = seeded.customer_id
    assert seeded.put(f"/customers/{cid}/rules", {"sla_hours": 0, "reason": "r"}).status_code == 422
    assert (
        seeded.put(f"/customers/{cid}/rules", {"priority_boost_points": 500, "reason": "r"}).status_code
        == 422
    )
    assert seeded.put(f"/customers/{cid}/rules", {"sla_hours": 24}).status_code == 422
    assert seeded.put("/customers/NOPE/rules", {"sla_hours": 24, "reason": "r"}).status_code == 404
    assert seeded.audit(entity_type="customer_rule") == []


def test_customer_list_pagination(seeded: Seeded) -> None:
    page = seeded.get("/customers", page=1, page_size=5, role=Role.EXECUTIVE)
    assert page["total"] == len(seeded.dataset.customers) and len(page["items"]) == 5
    assert page["has_more"] is True
    names = [c["customer_name"] for c in page["items"]]
    assert names == sorted(names)


# --------------------------------------------------------------------- alerts


def _raise_alerts(seeded: Seeded) -> list[str]:
    repo = AlertRepository(seeded.session)
    now = seeded.clock.now()
    alerts = [
        Alert(
            AlertType.ORDER_OVERDUE,
            AlertSeverity.CRITICAL,
            "Overdue",
            "3 days late",
            "Expedite",
            now,
            order_id=seeded.order_id,
        ),
        Alert(
            AlertType.MACHINE_DOWNTIME,
            AlertSeverity.HIGH,
            "Down",
            "spindle",
            "Reassign",
            now - timedelta(hours=1),
            machine_id=seeded.machine_id,
        ),
        Alert(
            AlertType.MATERIAL_SHORTAGE,
            AlertSeverity.WARNING,
            "Short",
            "no stock",
            "Order",
            now - timedelta(hours=2),
            order_id=seeded.closed_order_id,
        ),
    ]
    ids = [repo.upsert(a, now).alert_id for a in alerts]
    seeded.session.flush()
    return ids


def test_alerts_list_filter_summary_and_acknowledge(seeded: Seeded) -> None:
    ids = _raise_alerts(seeded)
    listed = seeded.get("/alerts", role=Role.SUPERVISOR)
    assert listed["total"] == 3 and [a["alert_id"] for a in listed["items"]] == ids  # newest first
    assert listed["items"][0]["severity"] == "critical" and listed["items"][0]["acknowledged"] is False
    assert seeded.get("/alerts", severity="high")["items"][0]["machine_id"] == seeded.machine_id
    assert seeded.get("/alerts", alert_type="material_shortage")["total"] == 1
    assert seeded.get("/alerts", order_id=seeded.order_id)["total"] == 1
    assert seeded.get("/alerts", machine_id=seeded.machine_id)["total"] == 1

    summary = seeded.get("/alerts/summary", role=Role.EXECUTIVE)
    assert summary == {
        "total_active": 3,
        "unacknowledged": 3,
        "by_severity": {"info": 0, "warning": 1, "high": 1, "critical": 1},
    }

    response = seeded.post(
        f"/alerts/{ids[0]}/acknowledge", {"note": "called the customer"}, role=Role.SUPERVISOR
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["acknowledged"] is True and body["acknowledged_by"] == seeded.users[Role.SUPERVISOR].user_id
    assert body["acknowledged_at"] == "2026-09-11T08:00:00Z"
    assert seeded.get("/alerts/summary")["unacknowledged"] == 2
    assert seeded.get("/alerts", acknowledged="false")["total"] == 2
    acked = seeded.get("/alerts", acknowledged="true")
    assert acked["total"] == 1 and acked["items"][0]["alert_id"] == ids[0]
    assert seeded.get(f"/alerts/{ids[0]}")["acknowledged"] is True

    entry = seeded.audit(entity_type="alert", entity_id=ids[0])[0]
    assert entry["action"] == "alert.acknowledge" and entry["reason"] == "called the customer"
    assert entry["previous_value"]["acknowledged"] is False and entry["new_value"]["acknowledged"] is True
    assert entry["details"]["order_id"] == seeded.order_id

    assert seeded.post(f"/alerts/{ids[0]}/acknowledge", None, role=Role.SUPERVISOR).status_code == 409
    assert seeded.post("/alerts/NOPE/acknowledge", None).status_code == 404
    # no note is fine: the audit reason falls back to "acknowledged"
    assert seeded.post(f"/alerts/{ids[1]}/acknowledge", None, role=Role.SUPERVISOR).status_code == 200
    assert seeded.audit(entity_type="alert", entity_id=ids[1])[0]["reason"] == "acknowledged"


def test_order_detail_lists_alert_acknowledgement_in_audit(seeded: Seeded) -> None:
    ids = _raise_alerts(seeded)
    seeded.post(f"/alerts/{ids[0]}/acknowledge", {"note": "n"}, role=Role.SUPERVISOR)
    assert seeded.get("/alerts", order_id=seeded.order_id)["items"][0]["acknowledged"] is True


# ---------------------------------------------------------------------- audit


def test_audit_query_filters_and_pagination(seeded: Seeded) -> None:
    seeded.post(f"/orders/{seeded.order_id}/hold", {"reason": "h"}, role=Role.PLANNER)
    seeded.post(f"/orders/{seeded.order_id}/release", {"reason": "r"}, role=Role.PLANNER)
    seeded.post(f"/orders/{seeded.order_id}/expedite", {"reason": "e"}, role=Role.PRODUCTION_MANAGER)
    lock = seeded.post(
        "/schedule/lock",
        {"lock_type": "machine", "machine_id": seeded.machine_id, "reason": "l"},
        role=Role.PRODUCTION_MANAGER,
    ).json()

    everything = seeded.get("/audit", role=Role.PRODUCTION_MANAGER)
    assert everything["total"] == 4 and set(everything) == {
        "items",
        "total",
        "page",
        "page_size",
        "pages",
        "has_more",
    }
    actions = {e["action"] for e in everything["items"]}
    assert actions == {"override.hold", "override.release", "expedite.create", "lock.create"}

    order_trail = seeded.get("/audit", entity_type="order", entity_id=seeded.order_id)
    assert order_trail["total"] == 3
    assert [e["action"] for e in order_trail["items"]] == [
        "expedite.create",
        "override.release",
        "override.hold",
    ]

    planner = seeded.get("/audit", user=seeded.users[Role.PLANNER].user_id)
    assert planner["total"] == 2 and all(
        e["user_id"] == seeded.users[Role.PLANNER].user_id for e in planner["items"]
    )
    manager = seeded.get("/audit", user=seeded.users[Role.PRODUCTION_MANAGER].user_id, action="lock.create")
    assert manager["total"] == 1 and manager["items"][0]["entity_id"] == lock["lock_id"]
    assert seeded.get("/audit", entity_type="schedule_lock")["total"] == 1

    now = seeded.clock.now()
    assert seeded.get("/audit", **{"from": (now + timedelta(minutes=1)).isoformat()})["total"] == 0
    assert (
        seeded.get("/audit", **{"from": now.isoformat(), "to": (now + timedelta(minutes=1)).isoformat()})[
            "total"
        ]
        == 4
    )
    assert seeded.get("/audit", to=now.isoformat())["total"] == 0

    page = seeded.get("/audit", page=2, page_size=3)
    assert page["total"] == 4 and len(page["items"]) == 1 and page["has_more"] is False
    for entry in everything["items"]:
        assert entry["request_id"], "audit rows carry the request id"
        assert entry["timestamp"] == "2026-09-11T08:00:00Z"

    assert seeded.client.get("/api/v1/audit", headers=seeded.headers(Role.PLANNER)).status_code == 403
    assert seeded.client.get("/api/v1/audit", headers=seeded.headers(Role.EXECUTIVE)).status_code == 403
