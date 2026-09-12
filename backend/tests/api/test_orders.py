"""``/orders``: pagination, filters, sorting, detail, explanation and machine options."""

from __future__ import annotations

from datetime import timedelta

from app.domain.enums import ReadinessState, RiskLevel, Role
from tests.api.conftest import Seeded


def test_list_pagination_envelope(seeded: Seeded) -> None:
    page1 = seeded.get("/orders", page=1, page_size=10)
    assert set(page1) == {"items", "total", "page", "page_size", "pages", "has_more"}
    assert page1["page"] == 1 and page1["page_size"] == 10 and len(page1["items"]) == 10
    assert page1["total"] == len(seeded.results)  # open orders only by default
    assert page1["has_more"] is True
    page2 = seeded.get("/orders", page=2, page_size=10)
    ids1 = {i["order"]["order_id"] for i in page1["items"]}
    ids2 = {i["order"]["order_id"] for i in page2["items"]}
    assert not ids1 & ids2
    last = seeded.get("/orders", page=page1["pages"], page_size=10)
    assert last["has_more"] is False


def test_list_joins_priority_and_schedule(seeded: Seeded) -> None:
    items = seeded.get("/orders", page_size=50)["items"]
    scored = [i for i in items if i["priority"] is not None]
    assert scored, "expected priority results joined"
    first = scored[0]
    assert {"score", "rank", "risk_level", "readiness", "explanation", "blocked"} <= set(first["priority"])
    assert first["priority"]["explanation"].startswith("ORDER #")
    scheduled = [i for i in items if i["schedule"] is not None]
    assert scheduled, "expected schedule placements joined"
    placement = scheduled[0]["schedule"]
    assert placement["version_number"] == seeded.schedule_version
    assert placement["status"] == "draft"
    assert placement["machine_id"] and placement["start"] and placement["expected_completion"]


def test_default_sort_is_priority_descending(seeded: Seeded) -> None:
    items = seeded.get("/orders", page_size=100)["items"]
    scores = [i["priority"]["score"] for i in items if i["priority"]]
    assert scores == sorted(scores, reverse=True)


def test_sort_by_rank_and_due_date(seeded: Seeded) -> None:
    by_rank = seeded.get("/orders", sort="rank", page_size=30)["items"]
    ranks = [i["priority"]["rank"] for i in by_rank]
    assert ranks == sorted(ranks) and ranks[0] == 1
    by_due = seeded.get("/orders", sort="due_date", order="asc", page_size=30)["items"]
    dues = [i["order"]["due_date"] for i in by_due if i["order"]["due_date"]]
    assert dues == sorted(dues)
    by_value = seeded.get("/orders", sort="order_value", page_size=30)["items"]
    values = [i["order"]["order_value"] or 0 for i in by_value]
    assert values == sorted(values, reverse=True)


def test_invalid_sort_is_422(seeded: Seeded) -> None:
    response = seeded.client.get("/api/v1/orders", headers=seeded.headers(Role.ADMIN), params={"sort": "nope"})
    assert response.status_code == 422
    assert response.json()["error"] == "validation_error"


def test_filters(seeded: Seeded) -> None:
    by_customer = seeded.get("/orders", customer_id=seeded.customer_id, page_size=200)
    assert by_customer["total"] > 0
    assert all(i["order"]["customer_id"] == seeded.customer_id for i in by_customer["items"])

    critical = seeded.get("/orders", risk="critical", page_size=200)
    expected = sum(1 for r in seeded.results.values() if r.risk_level is RiskLevel.CRITICAL)
    assert critical["total"] == expected
    assert all(i["priority"]["risk_level"] == "critical" for i in critical["items"])

    ready = seeded.get("/orders", readiness="ready", page_size=500)
    assert ready["total"] == sum(1 for r in seeded.results.values() if r.readiness is ReadinessState.READY)

    held = seeded.get("/orders", on_hold="true", page_size=200)
    assert seeded.held_order_id in {i["order"]["order_id"] for i in held["items"]}
    assert all(i["order"]["on_hold"] for i in held["items"])

    search = seeded.get("/orders", search=seeded.order_id)
    assert search["total"] >= 1 and search["items"][0]["order"]["order_id"] == seeded.order_id

    status = seeded.get("/orders", status=["released", "new"], page_size=500)
    assert status["total"] > 0
    assert {i["order"]["order_status"] for i in status["items"]} <= {"released", "new"}

    everything = seeded.get("/orders", open_only="false", page_size=1)
    assert everything["total"] == len(seeded.dataset.orders)

    now = seeded.clock.now()
    due = seeded.get("/orders", due_from=now.isoformat(), due_to=(now + timedelta(days=3)).isoformat(), page_size=500)
    assert due["total"] > 0
    for item in due["items"]:
        assert now.isoformat().replace("+00:00", "Z") <= item["order"]["due_date"]


def test_filter_by_scheduled_machine(seeded: Seeded) -> None:
    placed = next(i for i in seeded.get("/orders", page_size=100)["items"] if i["schedule"])
    machine_id = placed["schedule"]["machine_id"]
    result = seeded.get("/orders", machine_id=machine_id, page_size=500)
    assert placed["order"]["order_id"] in {i["order"]["order_id"] for i in result["items"]}
    for item in result["items"]:
        assert (item["schedule"] and item["schedule"]["machine_id"] == machine_id) or item["order"][
            "required_machine_id"
        ] == machine_id


def test_order_detail_shape(seeded: Seeded) -> None:
    detail = seeded.get(f"/orders/{seeded.order_id}")
    expected_keys = {
        "order",
        "customer",
        "priority",
        "breakdown",
        "factors",
        "adjustments",
        "schedule",
        "operations",
        "materials",
        "tooling",
        "production_minutes",
        "dependencies",
        "dependents",
        "overrides",
        "expedites",
        "locks",
        "audit",
        "data_quality_issues",
    }
    assert expected_keys <= set(detail)
    assert detail["order"]["order_id"] == seeded.order_id
    assert detail["customer"]["customer_id"] == seeded.customer_id
    assert detail["order"]["order_value"] is not None and detail["order"]["quantity"] > 0
    assert detail["priority"]["score"] == seeded.results[seeded.order_id].score
    assert detail["breakdown"] and detail["factors"]
    assert detail["schedule"]["machine_id"] and detail["schedule"]["entries"]
    assert detail["operations"] and detail["operations"][0]["operation_status"]
    assert detail["production_minutes"] is not None and detail["production_minutes"] > 0
    assert detail["schedule"]["expected_lateness_hours"] is not None
    assert detail["order"]["on_hold"] is False


def test_order_detail_404(seeded: Seeded) -> None:
    response = seeded.client.get("/api/v1/orders/NOPE", headers=seeded.headers(Role.OPERATOR))
    assert response.status_code == 404
    assert response.json() == {"error": "not_found", "message": "order 'NOPE' not found", "details": {"order_id": "NOPE"}}


def test_explanation_lines_resum_to_score(seeded: Seeded) -> None:
    body = seeded.get(f"/orders/{seeded.order_id}/explanation")
    assert body["order_id"] == seeded.order_id
    assert body["explanation"].startswith(f"ORDER #{seeded.order_id}")
    assert "Why?" in body["explanation"]
    assert body["lines"] and {"kind", "key", "label", "points", "reason"} <= set(body["lines"][0])
    assert abs(sum(line["points"] for line in body["lines"]) - body["score"]) < 0.05
    keys = [line["key"] for line in body["lines"] if line["kind"] == "factor"]
    assert "due_date_urgency" in keys and "customer_importance" in keys


def test_explanation_without_result_is_404(seeded: Seeded) -> None:
    response = seeded.client.get(
        f"/api/v1/orders/{seeded.closed_order_id}/explanation", headers=seeded.headers(Role.OPERATOR)
    )
    assert response.status_code == 404
    assert "no priority result" in response.json()["message"]


def test_machine_options(seeded: Seeded) -> None:
    body = seeded.get(f"/orders/{seeded.order_id}/machines")
    assert body["order_id"] == seeded.order_id and body["source"] == "live"
    assert body["eligible"], "a ready order must have at least one eligible machine"
    first = body["eligible"][0]
    assert first["rank"] == 1 and first["recommended"] is True
    assert body["recommended_machine_id"] == first["machine_id"]
    assert body["scheduled_machine_id"] is not None
    assert isinstance(body["rejected"], dict)
    for reasons in body["rejected"].values():
        assert reasons and all(isinstance(r, str) for r in reasons)


def test_machine_options_for_closed_order_is_409(seeded: Seeded) -> None:
    response = seeded.client.get(
        f"/api/v1/orders/{seeded.closed_order_id}/machines", headers=seeded.headers(Role.OPERATOR)
    )
    assert response.status_code == 409


def test_machines_list_detail_and_schedule(seeded: Seeded) -> None:
    machines = seeded.get("/machines")
    assert len(machines) == len(seeded.dataset.machines)
    item = next(m for m in machines if m["load"]["scheduled_entries"] > 0)
    assert item["load"]["version_number"] == seeded.schedule_version
    assert item["load"]["utilization_pct"] is not None
    assert item["load"]["scheduled_hours"] > 0 and item["load"]["next_free"]

    group = machines[0]["machine"]["machine_group"]
    assert all(m["machine"]["machine_group"] == group for m in seeded.get("/machines", machine_group=group))

    detail = seeded.get(f"/machines/{item['machine']['machine_id']}")
    assert detail["calendar"]["summary"].startswith("Calendar ")
    assert detail["calendar_source"] in ("machine", "default", "fallback_24x7")
    assert detail["calendar"]["shifts"]
    assert isinstance(detail["downtime"], list)
    assert detail["machine"]["compatible_materials"]

    schedule = seeded.get(f"/machines/{item['machine']['machine_id']}/schedule")
    assert schedule["version_number"] == seeded.schedule_version
    assert schedule["entries"], "expected entries in the next 24 h"
    assert schedule["entries"] == sorted(schedule["entries"], key=lambda e: e["setup_start"])
    week = seeded.get(
        f"/machines/{item['machine']['machine_id']}/schedule",
        start=seeded.clock.now().isoformat(),
        end=(seeded.clock.now() + timedelta(days=7)).isoformat(),
    )
    assert len(week["entries"]) >= len(schedule["entries"])

    bad = seeded.client.get(
        f"/api/v1/machines/{item['machine']['machine_id']}/schedule",
        headers=seeded.headers(Role.OPERATOR),
        params={"start": seeded.clock.now().isoformat(), "end": seeded.clock.now().isoformat()},
    )
    assert bad.status_code == 422
    assert seeded.client.get("/api/v1/machines/NOPE", headers=seeded.headers(Role.OPERATOR)).status_code == 404
