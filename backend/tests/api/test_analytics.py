"""``/analytics``: KPIs, capacity, bottlenecks, OTD and schedule quality, consistent with the active plan."""

from __future__ import annotations

from app.domain.enums import Role
from app.engines.analytics.common import DIMENSIONS
from tests.api.conftest import API, Seeded


def test_analytics_views_follow_the_active_plan(seeded: Seeded) -> None:
    generated = seeded.post("/schedule/generate", {"note": "analytics"}, role=Role.PLANNER)
    assert generated.status_code == 201, generated.text
    version = generated.json()["version"]
    v = version["version_number"]
    stored_kpis = version["analytics"]["kpis"]

    kpis = seeded.get("/analytics/kpis", role=Role.EXECUTIVE)
    assert kpis["source"] == "stored" and kpis["version_number"] == v and kpis["version_status"] == "draft"
    assert kpis["kpis"] == stored_kpis
    assert kpis["kpis"]["total_open_orders"] == len(seeded.results)
    assert kpis["kpis"]["scheduled_orders"] + kpis["kpis"]["unscheduled_orders"] == len(seeded.results)
    assert (
        kpis["kpis"]["blocked_total"]
        >= kpis["kpis"]["blocked_by_material"] + kpis["kpis"]["blocked_by_tooling"]
    )

    bottlenecks = seeded.get("/analytics/bottlenecks", role=Role.EXECUTIVE)
    assert bottlenecks["source"] == "stored" and bottlenecks["version_number"] == v
    assert bottlenecks["items"] == version["analytics"]["bottlenecks"]
    if bottlenecks["items"]:
        assert bottlenecks["current"] == bottlenecks["items"][0]
        assert {
            "resource_type",
            "utilization_pct",
            "orders_waiting",
            "capacity_shortfall_hours",
            "revenue_at_risk",
        } <= set(bottlenecks["current"])
    else:
        assert bottlenecks["current"] is None

    capacity = seeded.get("/analytics/capacity", role=Role.EXECUTIVE)
    assert capacity["dimension"] == "machine_group" and capacity["period"] == "week"
    assert capacity["rows"] and capacity["totals"]
    for total in capacity["totals"]:
        assert total["gap_hours"] == total["available_hours"] - total["required_hours"]
    stored_totals = {t["key"]: t["required_hours"] for t in version["analytics"]["capacity"]["totals"]}
    assert {t["key"] for t in capacity["totals"]} == set(stored_totals)
    for dimension in DIMENSIONS:
        by_dim = seeded.get("/analytics/capacity", dimension=dimension, period="day", horizon_days=3)
        assert by_dim["dimension"] == dimension and by_dim["period"] == "day"
        assert all(r["period_start"] < r["period_end"] for r in by_dim["rows"])
    bad = seeded.client.get(
        f"{API}/analytics/capacity", headers=seeded.headers(Role.ADMIN), params={"dimension": "colour"}
    )
    assert bad.status_code == 422

    otd = seeded.get("/analytics/on-time-delivery", role=Role.EXECUTIVE, window_days=30)
    assert otd["window_days"] == 30 and otd["historical"]["key"] == "historical"
    assert otd["projected"]["total"] == otd["projected"]["on_time"] + otd["projected"]["late"]
    assert otd["summary"].startswith("On-time delivery:")
    assert all(p["day"] for p in otd["trend"])

    quality = seeded.get("/analytics/schedule-quality", role=Role.EXECUTIVE)
    assert quality["version"]["version_number"] == v
    assert quality["quality"]["score"] == version["quality_score"]
    assert quality["metrics"]["scheduled_orders"] == version["metrics"]["scheduled_orders"]
    assert quality["comparison"] is None  # the active plan is the newest draft

    # publish, then a newer draft: the quality view compares active vs newest draft
    assert seeded.post("/schedule/approve", {"version": v, "reason": "ok"}).status_code == 200
    assert seeded.post("/schedule/publish", {"version": v, "reason": "go"}).status_code == 200
    draft = seeded.post("/schedule/generate", None, role=Role.PLANNER).json()["version"]["version_number"]
    quality = seeded.get("/analytics/schedule-quality")
    assert quality["version"]["version_number"] == v and quality["version"]["status"] == "published"
    assert quality["newest_draft"]["version_number"] == draft
    assert quality["comparison"]["a"]["version_number"] == v
    assert quality["comparison"]["b"]["version_number"] == draft
    assert "on_time_pct" in quality["comparison"]["metrics"]

    # a newer sync makes the stored analytics stale: KPIs are recomputed live
    seeded.clock.advance(minutes=5)
    synced = seeded.post("/sync/run", {"mode": "incremental"}, role=Role.ADMIN)
    assert synced.status_code == 200, synced.text
    live = seeded.get("/analytics/kpis")
    assert live["source"] == "live" and live["version_number"] == v
    assert live["as_of"].startswith("2026-09-11T08:05:00")
    assert live["kpis"]["total_open_orders"] == len(seeded.results)
    assert seeded.get("/analytics/bottlenecks")["source"] == "live"
