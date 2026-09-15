#!/usr/bin/env python
"""Capture the dataset behind the frontend DEMO MODE (``frontend/src/demo/demo-data.json``).

The script builds a dedicated PostgreSQL database ``ppse_demo`` (created when missing,
dropped and recreated with ``--reset``), migrates and seeds it, synchronises the SMALL
synthetic plant (seed 42) through the mock ERP connector, starts the real API with
``uvicorn`` on a private port and then drives the API the way the control tower would:

* plan history: generate v1 -> approve v1 -> publish v1 (read-only writeback receipt),
  generate two more DRAFT versions, evaluate one replan;
* every read endpoint the UI calls (orders, explanations, machine options, machines,
  schedule views for every version and every plant day of the horizon, analytics for
  every dimension / period / horizon / window the UI offers, configuration, customers,
  alerts, data quality, audit, users, sync, health, metrics, one ``/auth/me`` per role);
* one what-if simulation preset per scenario kind using the demo plant's real ids;
* the priority-profile preview for the "due date 25 -> 40" candidate.

Everything is written as ONE pretty-printed JSON file (keys sorted). The script is
idempotent: re-running it against an existing ``ppse_demo`` reuses the plan history it
finds (``--reset`` starts from scratch, which is the recommended way to refresh the demo
after a backend change because the synthetic plant is anchored to the current day).

Usage (from ``backend/``, virtualenv activated)::

    python scripts/capture_demo_data.py [--reset] [--port 8010] [--output PATH]
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.db.seed import DEV_USERS  # noqa: E402

DEFAULT_OUTPUT = BACKEND_DIR.parent / "frontend" / "src" / "demo" / "demo-data.json"
DB_NAME = "ppse_demo"
DEFAULT_DB_HOST = "127.0.0.1"
DEFAULT_DB_PORT = 5432
DEFAULT_DB_USER = "postgres"
DEFAULT_DB_PASSWORD = "postgres"
API_PREFIX = "/api/v1"
SCALE = "small"
SEED = 42

CAPACITY_DIMENSIONS = ("machine", "machine_group", "process", "department")
CAPACITY_PERIODS = ("day", "week")
CAPACITY_HORIZONS = (None, 7, 14, 28, 56, 84, 120)  # None = the scheduling horizon (the UI default)
OTD_WINDOWS = (7, 14, 30, 60, 90)
MAX_PAGE = 500


def log(message: str) -> None:
    print(f"[capture] {message}", flush=True)


# ------------------------------------------------------------------ database


def admin_dsn(args: argparse.Namespace) -> str:
    return f"postgresql://{args.db_user}:{args.db_password}@{args.db_host}:{args.db_port}/postgres"


def sqlalchemy_url(args: argparse.Namespace) -> str:
    return f"postgresql+psycopg://{args.db_user}:{args.db_password}@{args.db_host}:{args.db_port}/{DB_NAME}"


def ensure_database(args: argparse.Namespace) -> bool:
    """Create ``ppse_demo`` when missing (drop it first with ``--reset``); returns True when created."""
    import psycopg

    with psycopg.connect(admin_dsn(args), autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DB_NAME,)).fetchone()
        if exists and args.reset:
            log(f"dropping database {DB_NAME}")
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
                (DB_NAME,),
            )
            conn.execute(f'DROP DATABASE "{DB_NAME}"')
            exists = None
        if not exists:
            log(f"creating database {DB_NAME}")
            conn.execute(f'CREATE DATABASE "{DB_NAME}"')
            return True
    log(f"database {DB_NAME} present")
    return False


def backend_env(args: argparse.Namespace) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "PPSE_ENVIRONMENT": "dev",
            "PPSE_DATABASE_URL": sqlalchemy_url(args),
            "PPSE_ERP_CONNECTOR": "mock",
            "PPSE_SYNTHETIC_SCALE": SCALE,
            "PPSE_SYNTHETIC_SEED": str(SEED),
            "PPSE_WRITEBACK_MODE": "read_only",
            "PPSE_BACKGROUND_JOBS_ENABLED": "false",
            "PPSE_SEED_ON_STARTUP": "true",
            "PPSE_LOG_LEVEL": "WARNING",
            "PPSE_LOG_JSON": "false",
            "PYTHONUNBUFFERED": "1",
        }
    )
    return env


def run_cli(args: argparse.Namespace, *cli_args: str) -> None:
    command = [sys.executable, "-m", "app.cli", "--database-url", sqlalchemy_url(args), *cli_args]
    log("cli " + " ".join(cli_args))
    subprocess.run(command, cwd=BACKEND_DIR, env=backend_env(args), check=True)


# ----------------------------------------------------------------- api client


class Api:
    """Tiny synchronous client: bearer token, JSON envelopes, non-2xx raised as RuntimeError."""

    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.token: str | None = None
        self.http = httpx.Client(timeout=600.0)
        self.requests = 0

    def close(self) -> None:
        self.http.close()

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def call(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any | None = None,
        allow: tuple[int, ...] = (),
    ) -> Any:
        self.requests += 1
        response = self.http.request(
            method,
            f"{self.base}{API_PREFIX}{path}",
            params={k: v for k, v in (params or {}).items() if v is not None},
            json=json_body,
            headers=self._headers(),
        )
        if response.status_code >= 400 and response.status_code not in allow:
            raise RuntimeError(f"{method} {path} -> {response.status_code}: {response.text[:500]}")
        if response.status_code == 204 or not response.content:
            return None
        payload = response.json()
        if response.status_code in allow and response.status_code >= 400:
            return {"_error": {"status": response.status_code, **(payload if isinstance(payload, dict) else {})}}
        return payload

    def get(self, path: str, params: dict[str, Any] | None = None, allow: tuple[int, ...] = ()) -> Any:
        return self.call("GET", path, params=params, allow=allow)

    def post(self, path: str, body: Any | None = None, allow: tuple[int, ...] = ()) -> Any:
        return self.call("POST", path, json_body=body, allow=allow)

    def put(self, path: str, body: Any) -> Any:
        return self.call("PUT", path, json_body=body)

    def login(self, username: str, password: str) -> dict[str, Any]:
        token = self.post("/auth/login", {"username": username, "password": password})
        self.token = token["access_token"]
        return token

    def pages(self, path: str, params: dict[str, Any] | None = None, page_size: int = MAX_PAGE) -> list[Any]:
        """Every item of a page-number list endpoint."""
        items: list[Any] = []
        page = 1
        while True:
            payload = self.get(path, {**(params or {}), "page": page, "page_size": page_size})
            items.extend(payload["items"])
            if not payload.get("has_more"):
                return items
            page += 1


def start_uvicorn(args: argparse.Namespace) -> subprocess.Popen[bytes]:
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:create_app",
        "--factory",
        "--host",
        "127.0.0.1",
        "--port",
        str(args.port),
        "--log-level",
        "warning",
    ]
    log(f"starting uvicorn on 127.0.0.1:{args.port}")
    log_file = open(BACKEND_DIR / "scripts" / ".capture_uvicorn.log", "wb")  # noqa: SIM115
    return subprocess.Popen(command, cwd=BACKEND_DIR, env=backend_env(args), stdout=log_file, stderr=subprocess.STDOUT)


def wait_for_api(api: Api, process: subprocess.Popen[bytes], timeout: float = 120.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError("uvicorn exited before the API became ready (see scripts/.capture_uvicorn.log)")
        try:
            health = api.http.get(f"{api.base}{API_PREFIX}/health", timeout=5.0)
            if health.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise RuntimeError("timed out waiting for the API")


def stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()


# --------------------------------------------------------------- helpers


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_iso(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)


def day_range(start: datetime, end: datetime) -> Iterator[str]:
    """Every calendar day (UTC) touched by [start, end)."""
    day = start.date()
    last = (end - timedelta(seconds=1)).date()
    while day <= last:
        yield day.isoformat()
        day += timedelta(days=1)


def capture_step(name: str, fn: Callable[[], Any]) -> Any:
    started = time.perf_counter()
    result = fn()
    log(f"{name} ({time.perf_counter() - started:.1f}s)")
    return result


# ------------------------------------------------------------ plan history


def ensure_plan_history(api: Api, out: dict[str, Any]) -> None:
    """v1 published + two newer drafts + one replan evaluation (only what is missing)."""
    generate_responses: dict[str, Any] = {}
    versions = api.pages("/schedule/versions", page_size=100)
    published = [v for v in versions if v["status"] == "published"]
    if not published:
        log("generating v1 (baseline) -> approve -> publish")
        generated = api.post("/schedule/generate", {"note": "Demo baseline plan"})
        number = generated["version"]["version_number"]
        generate_responses[str(number)] = generated
        api.post("/schedule/approve", {"version": number, "reason": "Baseline plan reviewed for the demo"})
        out["schedule"]["publish_response"] = api.post(
            "/schedule/publish", {"version": number, "reason": "Released to the shop floor (demo)"}
        )
        versions = api.pages("/schedule/versions", page_size=100)
        published = [v for v in versions if v["status"] == "published"]
    active = max(published, key=lambda v: v["version_number"])
    drafts = [v for v in versions if v["status"] == "draft" and v["version_number"] > active["version_number"]]
    notes = ["Re-plan after material receipts (demo draft)", "Re-plan with overtime allowance (demo draft)"]
    while len(drafts) < 2:
        note = notes[len(drafts)] if len(drafts) < len(notes) else f"Demo draft {len(drafts) + 1}"
        log(f"generating draft {len(drafts) + 1}")
        generated = api.post("/schedule/generate", {"note": note})
        generate_responses[str(generated["version"]["version_number"])] = generated
        versions = api.pages("/schedule/versions", page_size=100)
        drafts = [v for v in versions if v["status"] == "draft" and v["version_number"] > active["version_number"]]
    replan_versions = [v for v in versions if str(v.get("trigger") or "").startswith("replan")]
    if not replan_versions:
        log("evaluating one replan")
        out["schedule"]["replan"] = api.post(
            "/schedule/replan", {"trigger": "manual", "reason": "Evaluated from the control tower (demo capture)"}
        )
    else:
        log("replan candidate already present; evaluating again for the captured outcome")
        out["schedule"]["replan"] = api.post(
            "/schedule/replan", {"trigger": "manual", "reason": "Evaluated from the control tower (demo capture)"}
        )
    out["schedule"]["generate_responses"] = generate_responses


# ------------------------------------------------------------------ capture


def capture_orders(api: Api, out: dict[str, Any]) -> list[dict[str, Any]]:
    all_orders = api.pages("/orders", {"sort": "rank", "order": "asc", "open_only": "false"})
    open_orders = api.pages("/orders", {"sort": "rank", "order": "asc"})
    out["orders"]["list"] = open_orders
    out["orders"]["list_all"] = all_orders
    detail: dict[str, Any] = {}
    explanation: dict[str, Any] = {}
    machines: dict[str, Any] = {}
    overrides: dict[str, Any] = {}
    for index, row in enumerate(all_orders, start=1):
        order_id = row["order"]["order_id"]
        detail[order_id] = api.get(f"/orders/{order_id}")
        explanation[order_id] = api.get(f"/orders/{order_id}/explanation", allow=(404,))
        machines[order_id] = api.get(f"/orders/{order_id}/machines", allow=(404, 409))
        overrides[order_id] = api.get(f"/orders/{order_id}/overrides")
        if index % 50 == 0:
            log(f"  orders {index}/{len(all_orders)}")
    out["orders"]["detail"] = detail
    out["orders"]["explanation"] = explanation
    out["orders"]["machines"] = machines
    out["orders"]["overrides"] = overrides
    out["overrides"] = api.get("/overrides")
    out["expedites"] = api.get("/expedites")
    return all_orders


def capture_machines(api: Api, out: dict[str, Any], horizon_start: str, horizon_end: str) -> list[dict[str, Any]]:
    machines = api.get("/machines")
    out["machines"]["list"] = machines
    out["machines"]["detail"] = {m["machine"]["machine_id"]: api.get(f"/machines/{m['machine']['machine_id']}") for m in machines}
    out["machines"]["schedule"] = {
        m["machine"]["machine_id"]: api.get(
            f"/machines/{m['machine']['machine_id']}/schedule", {"start": horizon_start, "end": horizon_end}
        )
        for m in machines
    }
    return machines


def capture_schedule(api: Api, out: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    sched = out["schedule"]
    plan = api.get("/schedule", {"page": 1, "page_size": MAX_PAGE})
    if plan["version"] is not None:
        entries: list[Any] = list(plan["entries"]["items"])
        page = 2
        while plan["entries"]["has_more"] and (page - 1) * MAX_PAGE < plan["entries"]["total"]:
            more = api.get("/schedule", {"page": page, "page_size": MAX_PAGE})
            entries.extend(more["entries"]["items"])
            if not more["entries"]["has_more"]:
                break
            page += 1
        plan["entries"] = {
            "items": entries,
            "total": len(entries),
            "page": 1,
            "page_size": max(len(entries), 1),
            "pages": 1,
            "has_more": False,
        }
    sched["plan"] = plan
    versions = api.pages("/schedule/versions", page_size=100)
    sched["versions"] = versions
    sched["version_detail"] = {}
    sched["version_entries"] = {}
    sched["gantt"] = {}
    sched["days"] = {}
    sched["runs"] = {}
    for version in versions:
        number = version["version_number"]
        key = str(number)
        detail = api.get(f"/schedule/versions/{number}")
        sched["version_detail"][key] = detail
        sched["version_entries"][key] = api.pages(f"/schedule/versions/{number}/entries", page_size=MAX_PAGE)
        sched["gantt"][key] = api.get(
            "/schedule/gantt",
            {"version": number, "start": version["horizon_start"], "end": version["horizon_end"]},
        )
        days: dict[str, Any] = {}
        for day in day_range(parse_iso(version["horizon_start"]), parse_iso(version["horizon_end"])):
            days[day] = api.get(f"/schedule/{day}", {"version": number})
        sched["days"][key] = days
        if version.get("run_id"):
            sched["runs"][version["run_id"]] = api.get(f"/schedule/runs/{version['run_id']}", allow=(404,))
        log(f"  schedule v{number}: {len(sched['version_entries'][key])} entries, {len(days)} days")
    numbers = sorted(v["version_number"] for v in versions)
    sched["compare"] = {}
    for a in numbers:
        for b in numbers:
            if a < b:
                sched["compare"][f"{a}-{b}"] = api.get("/schedule/compare", {"a": a, "b": b})
    sched["locks"] = api.get("/schedule/locks")
    return plan, versions


def capture_analytics(api: Api, out: dict[str, Any]) -> None:
    analytics = out["analytics"]
    analytics["kpis"] = api.get("/analytics/kpis")
    analytics["bottlenecks"] = api.get("/analytics/bottlenecks")
    analytics["schedule_quality"] = api.get("/analytics/schedule-quality")
    capacity: dict[str, Any] = {}
    for dimension in CAPACITY_DIMENSIONS:
        for period in CAPACITY_PERIODS:
            for horizon in CAPACITY_HORIZONS:
                key = f"{dimension}|{period}|{horizon if horizon is not None else 'default'}"
                capacity[key] = api.get(
                    "/analytics/capacity", {"dimension": dimension, "period": period, "horizon_days": horizon}
                )
    analytics["capacity"] = capacity
    analytics["on_time_delivery"] = {str(w): api.get("/analytics/on-time-delivery", {"window_days": w}) for w in OTD_WINDOWS}


def capture_configuration(api: Api, out: dict[str, Any]) -> dict[str, Any]:
    cfg = out["configuration"]
    priority = api.get("/priority/configuration")
    cfg["priority"] = priority
    cfg["priority_versions"] = api.get("/priority/configuration/versions", {"limit": 100})
    cfg["priority_version_detail"] = {
        str(v["version"]): api.get(f"/priority/configuration/versions/{v['version']}") for v in cfg["priority_versions"]
    }
    cfg["scheduling"] = api.get("/scheduling/configuration")
    cfg["scheduling_versions"] = api.get("/scheduling/configuration/versions", {"limit": 100})
    cfg["scheduling_version_detail"] = {
        str(v["version"]): api.get(f"/scheduling/configuration/versions/{v['version']}")
        for v in cfg["scheduling_versions"]
    }
    candidate = json.loads(json.dumps(priority["profile"]))
    for weight in candidate["weights"]:
        if weight["key"] == "due_date_urgency":
            weight["weight"] = 40
    preview_request = {"profile": candidate, "top_n": 50}
    cfg["preview"] = {"request": preview_request, "response": api.post("/priority/configuration/preview", preview_request)}
    return priority


def capture_customers(api: Api, out: dict[str, Any]) -> list[dict[str, Any]]:
    customers = api.pages("/customers", page_size=MAX_PAGE)
    out["customers"]["list"] = customers
    out["customers"]["detail"] = {c["customer_id"]: api.get(f"/customers/{c['customer_id']}") for c in customers}
    out["customers"]["rules"] = {
        c["customer_id"]: api.get(f"/customers/{c['customer_id']}/rules", allow=(404,)) for c in customers
    }
    return customers


def capture_system(api: Api, out: dict[str, Any]) -> None:
    out["alerts"]["list"] = api.pages("/alerts", page_size=MAX_PAGE)
    out["alerts"]["summary"] = api.get("/alerts/summary")
    out["data_quality"]["summary"] = api.get("/data-quality")
    out["data_quality"]["issues"] = api.pages("/data-quality/issues", page_size=MAX_PAGE)
    out["data_quality"]["run_response"] = api.post("/data-quality/run", {})
    out["users"] = api.get("/users")
    out["health"] = api.get("/health")
    out["metrics"] = api.get("/metrics")
    runs = api.pages("/sync/runs", page_size=100)
    out["sync"]["runs"] = runs
    out["sync"]["run_detail"] = {r["run_id"]: api.get(f"/sync/runs/{r['run_id']}") for r in runs}
    out["sync"]["status"] = api.get("/sync/status")
    out["sync"]["capabilities"] = api.get("/sync/capabilities")
    out["simulations"]["scenario_types"] = api.get("/simulation/scenario-types")
    out["audit"] = api.pages("/audit", page_size=MAX_PAGE)


# ----------------------------------------------------------- simulations


def pick_simulation_inputs(
    orders: list[dict[str, Any]],
    machines: list[dict[str, Any]],
    customers: list[dict[str, Any]],
    bottlenecks: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    open_orders = [o for o in orders if o["order"]["order_status"] not in ("completed", "packed", "shipped", "cancelled")]
    ranked = sorted(open_orders, key=lambda o: (o["priority"] or {}).get("rank") or 10**9)

    cnc = [m for m in machines if m["machine"]["process_type"] == "cnc_machining"]
    busiest = max(cnc or machines, key=lambda m: (m["load"]["utilization_pct"] or 0.0, m["load"]["scheduled_hours"]))
    five_axis = [m for m in machines if "CNC5" in m["machine"]["machine_group"].upper() or "5" in m["machine"]["machine_type"]]
    template = (five_axis or cnc or machines)[0]

    tomorrow = now + timedelta(hours=24)
    with_due = [o for o in open_orders if o["order"]["due_date"]]
    due_tomorrow = min(with_due, key=lambda o: abs((parse_iso(o["order"]["due_date"]) - tomorrow).total_seconds()))

    groups = sorted({m["machine"]["machine_group"] for m in machines})
    current = bottlenecks.get("current") or {}
    bottleneck_group = current.get("resource_id") if current.get("resource_type") == "machine_group" else None
    if bottleneck_group not in groups:
        bottleneck_group = busiest["machine"]["machine_group"]

    waiting = [o for o in open_orders if (o["priority"] or {}).get("readiness") == "waiting_material" and o["order"]["required_material_id"]]
    material = (waiting or [o for o in open_orders if o["order"]["required_material_id"]])[0]["order"]["required_material_id"]

    strategic = [c for c in customers if c["strategic_customer_flag"] or c["effective_tier"] == "strategic"]
    customer = (strategic or customers)[0]

    saturday = now.date() + timedelta(days=(5 - now.weekday()) % 7 or 7)
    monday = now.date() + timedelta(days=(0 - now.weekday()) % 7 or 7)
    top_ids = [o["order"]["order_id"] for o in ranked[:3]]
    mid_ids = [o["order"]["order_id"] for o in ranked[10:12]] or top_ids[:2]
    return {
        "busiest_machine": busiest["machine"]["machine_id"],
        "template_machine": template["machine"]["machine_id"],
        "due_tomorrow_order": due_tomorrow["order"]["order_id"],
        "bottleneck_group": bottleneck_group,
        "material": material,
        "customer": customer["customer_id"],
        "saturday": saturday.isoformat(),
        "monday": monday.isoformat(),
        "top_ids": top_ids,
        "mid_ids": mid_ids,
        "tomorrow": iso(tomorrow),
    }


def simulation_presets(inputs: dict[str, Any], profile: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    weights = {w["key"]: w["weight"] for w in profile["weights"]}
    due_weight = weights.get("due_date_urgency", 25)
    return [
        {
            "kind": "machine_down",
            "label": f"{inputs['busiest_machine']} down for 8 hours",
            "scenario": {"kind": "machine_down", "machine_id": inputs["busiest_machine"], "duration_hours": 8, "reason": "spindle failure"},
        },
        {
            "kind": "urgent_orders",
            "label": f"3 urgent orders like {inputs['due_tomorrow_order']} due tomorrow",
            "scenario": {
                "kind": "urgent_orders",
                "orders": [{"clone_of": inputs["due_tomorrow_order"], "due": inputs["tomorrow"]} for _ in range(3)],
            },
        },
        {
            "kind": "add_machine",
            "label": f"Add MC-CNC5-NEW cloned from {inputs['template_machine']}",
            "scenario": {"kind": "add_machine", "clone_of_machine_id": inputs["template_machine"], "new_machine_id": "MC-CNC5-NEW", "name": "5-axis (new)"},
        },
        {
            "kind": "extra_working_day",
            "label": f"{inputs['saturday']} becomes a working day",
            "scenario": {"kind": "extra_working_day", "day": inputs["saturday"]},
        },
        {
            "kind": "extra_shift",
            "label": f"Extra evening shift on {inputs['monday']}",
            "scenario": {"kind": "extra_shift", "day": inputs["monday"], "start": "18:00:00", "end": "22:00:00", "name": "extra shift"},
        },
        {
            "kind": "outsource",
            "label": f"Outsource 500 pieces from {inputs['bottleneck_group']}",
            "scenario": {"kind": "outsource", "machine_group": inputs["bottleneck_group"], "quantity": 500, "supplier": "external supplier"},
        },
        {
            "kind": "material_delay",
            "label": f"{inputs['material']} arrives 2 days late",
            "scenario": {"kind": "material_delay", "material_id": inputs["material"], "delay_days": 2},
        },
        {
            "kind": "material_arrival",
            "label": f"{inputs['material']} received in 4 hours",
            "scenario": {"kind": "material_arrival", "material_id": inputs["material"], "arrives_at": iso(now + timedelta(hours=4))},
        },
        {
            "kind": "prioritize_customer",
            "label": f"Prioritise {inputs['customer']} (+20 pts)",
            "scenario": {"kind": "prioritize_customer", "customer_id": inputs["customer"], "boost_points": 20},
        },
        {
            "kind": "weight_change",
            "label": f"Due date urgency weight {due_weight:g} -> 40",
            "scenario": {"kind": "weight_change", "weights": {"due_date_urgency": 40}},
        },
        {
            "kind": "due_date_change",
            "label": f"{inputs['top_ids'][0]} must be completed tomorrow",
            "scenario": {"kind": "due_date_change", "order_id": inputs["top_ids"][0], "new_due": inputs["tomorrow"]},
        },
        {
            "kind": "hold_orders",
            "label": f"Hold {', '.join(inputs['mid_ids'])}",
            "scenario": {"kind": "hold_orders", "order_ids": inputs["mid_ids"], "reason": "simulated hold"},
        },
        {
            "kind": "expedite_orders",
            "label": f"Expedite {', '.join(inputs['top_ids'][1:3])}",
            "scenario": {"kind": "expedite_orders", "order_ids": inputs["top_ids"][1:3], "boost_points": 30, "hours": 8, "reason": "simulated expedite"},
        },
    ]


def capture_simulations(api: Api, out: dict[str, Any], presets: list[dict[str, Any]]) -> None:
    captured: list[dict[str, Any]] = []
    for preset in presets:
        request = {"scenarios": [preset["scenario"]], "top_n": 50, "note": preset["label"]}
        started = time.perf_counter()
        response = api.post("/schedule/simulate", request, allow=(422, 409))
        log(f"  simulate {preset['kind']} ({time.perf_counter() - started:.1f}s)")
        captured.append({"kind": preset["kind"], "label": preset["label"], "request": request, "response": response})
    out["simulations"]["presets"] = captured


# ------------------------------------------------------------------ slimming
#
# The raw capture is ~25 MB because the API repeats the same facts in many views. The demo
# backend derives those views the way the services do, so the shipped file keeps one copy of
# each fact and the removed copies go to a verification file that the frontend tests compare
# against (frontend/src/demo/demoDerivations.test.ts):
#
# * order list rows        = detail.order + detail.priority + schedule placement (order_query_service)
# * explanations/breakdown = scoring port of the engine (explanation_lines / render_explanation)
# * gantt, day views, machine schedules, "upcoming", order placements = version entries + masters
# * capacity rows          = the 120-day report sliced to the requested horizon (rows are packed
#                            "key|period_start|period_end|required|available"; gap and utilisation
#                            are recomputed like engines/analytics/capacity.py)
# * entries of v3+         = the newest draft plan again (the drafts were generated seconds apart
#                            from the same snapshot; only per-entry score decimals differ)


def pack_capacity_row(row: dict[str, Any]) -> str:
    return "|".join(
        [
            str(row["key"]),
            str(row["period_start"]),
            str(row["period_end"]),
            repr(float(row["required_hours"])),
            repr(float(row["available_hours"])),
        ]
    )


def _entries_equivalent(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> bool:
    """Same placements (ids, machines, times, locks); only score decimals / reasons may differ."""
    if len(a) != len(b):
        return False
    ignore = {"priority_score", "placement_reason"}
    for x, y in zip(a, b, strict=True):
        if {k: v for k, v in x.items() if k not in ignore} != {k: v for k, v in y.items() if k not in ignore}:
            return False
    return True


def slim_dataset(raw: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    data = json.loads(json.dumps(raw))
    verify: dict[str, Any] = {"captured_at": raw["meta"]["captured_at"]}

    orders = data["orders"]
    verify["orders_list"] = orders.pop("list")
    verify["orders_list_all"] = orders.pop("list_all")
    verify["explanations"] = orders.pop("explanation")
    verify["order_overrides"] = orders.pop("overrides")
    verify["order_breakdown"] = {}
    verify["order_schedule"] = {}
    verify["priority_explanation"] = {}
    for order_id, detail in orders["detail"].items():
        verify["order_breakdown"][order_id] = detail.pop("breakdown")
        verify["order_schedule"][order_id] = detail.pop("schedule")
        if detail.get("priority"):
            verify["priority_explanation"][order_id] = detail["priority"].pop("explanation")
        for factor in detail.get("factors", []):
            factor.pop("details", None)

    machines = data["machines"]
    verify["machine_schedule"] = machines.pop("schedule")
    verify["machine_upcoming"] = {mid: d.pop("upcoming") for mid, d in machines["detail"].items()}

    sched = data["schedule"]
    verify["plan_entries"] = sched["plan"].pop("entries")
    verify["gantt"] = sched.pop("gantt")
    days = sched.pop("days")
    verify["days"] = days
    sample_version = next(iter(days))
    sample_day, sample = next(iter(sorted(days[sample_version].items())))
    midnight_utc = datetime.fromisoformat(f"{sample_day}T00:00:00+00:00")
    sched["day_meta"] = {
        "timezone": sample["timezone"],
        "offset_minutes": round((parse_iso(sample["start"]) - midnight_utc).total_seconds() / 60),
    }
    for number, response in sched.get("generate_responses", {}).items():
        verify.setdefault("generate_versions", {})[number] = response.pop("version")
    entries = sched["version_entries"]
    numbers = sorted(entries, key=int)
    verify["version_entries"] = json.loads(json.dumps(entries))
    kept: dict[str, list[dict[str, Any]]] = {}
    for number in numbers:
        alias = next((k for k, v in kept.items() if _entries_equivalent(v, entries[number])), None)
        if alias is None:
            kept[number] = entries[number]
        else:
            entries[number] = {"same_as": alias}

    capacity = data["analytics"]["capacity"]
    verify["capacity"] = json.loads(json.dumps(capacity))
    reports: dict[str, Any] = {}
    rows: dict[str, Any] = {}
    for key, report in capacity.items():
        dimension, period, horizon = key.split("|")
        if horizon == "120":
            rows[f"{dimension}|{period}"] = [pack_capacity_row(r) for r in report["rows"]]
        reports[key] = {k: v for k, v in report.items() if k != "rows"}
    data["analytics"]["capacity"] = {"reports": reports, "rows": rows}
    return data, verify


# ---------------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Capture the PPSE demo dataset")
    parser.add_argument("--reset", action="store_true", help="drop and recreate the ppse_demo database first")
    parser.add_argument("--port", type=int, default=8010, help="port for the private uvicorn instance")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="where to write demo-data.json")
    parser.add_argument(
        "--raw-output",
        type=Path,
        default=BACKEND_DIR / "scripts" / ".demo-raw.json",
        help="where to keep the unslimmed capture (re-slim later with --from-raw)",
    )
    parser.add_argument(
        "--verify-output",
        type=Path,
        default=DEFAULT_OUTPUT.parent / ".demo-verify.json",
        help="where to write the derived views the frontend tests compare against",
    )
    parser.add_argument("--from-raw", type=Path, default=None, help="skip the capture and re-slim this raw file")
    parser.add_argument("--db-host", default=os.environ.get("PPSE_DEMO_DB_HOST", DEFAULT_DB_HOST))
    parser.add_argument("--db-port", type=int, default=int(os.environ.get("PPSE_DEMO_DB_PORT", DEFAULT_DB_PORT)))
    parser.add_argument("--db-user", default=os.environ.get("PPSE_DEMO_DB_USER", DEFAULT_DB_USER))
    parser.add_argument("--db-password", default=os.environ.get("PPSE_DEMO_DB_PASSWORD", DEFAULT_DB_PASSWORD))
    parser.add_argument("--skip-simulations", action="store_true", help="skip the what-if presets (faster)")
    return parser


def write_json(path: Path, payload: Any) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1, sort_keys=True, ensure_ascii=False)
        handle.write("\n")
    return path.stat().st_size


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    started = time.perf_counter()
    if args.from_raw is not None:
        raw = json.loads(args.from_raw.read_text(encoding="utf-8"))
    else:
        raw = capture(args)
        size = write_json(args.raw_output, raw)
        log(f"wrote raw capture {args.raw_output} ({size / 1_048_576:.2f} MB)")
    data, verify = slim_dataset(raw)
    verify_size = write_json(args.verify_output, verify)
    size = write_json(args.output, data)
    log(f"wrote verification views {args.verify_output} ({verify_size / 1_048_576:.2f} MB)")
    log(f"wrote {args.output} ({size / 1_048_576:.2f} MB, {time.perf_counter() - started:.0f}s)")
    return 0


def capture(args: argparse.Namespace) -> dict[str, Any]:
    started = time.perf_counter()
    ensure_database(args)
    run_cli(args, "migrate")
    run_cli(args, "seed")
    run_cli(args, "sync", "--mode", "full", "--scale", SCALE, "--seed", str(SEED))

    process = start_uvicorn(args)
    api = Api(f"http://127.0.0.1:{args.port}")
    try:
        wait_for_api(api, process)
        admin = next(u for u in DEV_USERS if u.username == "admin")
        api.login(admin.username, admin.password)
        now = datetime.now(tz=UTC)
        out: dict[str, Any] = {
            "meta": {},
            "credentials": [
                {"username": u.username, "password": u.password, "role": u.role.value, "display_name": u.display_name}
                for u in DEV_USERS
            ],
            "auth": {"me": {}},
            "orders": {},
            "machines": {},
            "schedule": {},
            "analytics": {},
            "simulations": {},
            "configuration": {},
            "customers": {},
            "alerts": {},
            "data_quality": {},
            "sync": {},
        }
        capture_step("plan history", lambda: ensure_plan_history(api, out))
        plan, versions = capture_step("schedule", lambda: capture_schedule(api, out))
        active = plan["version"]
        horizon_start = active["horizon_start"] if active else iso(now)
        horizon_end = active["horizon_end"] if active else iso(now + timedelta(days=14))
        orders = capture_step("orders", lambda: capture_orders(api, out))
        machines = capture_step("machines", lambda: capture_machines(api, out, horizon_start, horizon_end))
        capture_step("analytics", lambda: capture_analytics(api, out))
        priority = capture_step("configuration", lambda: capture_configuration(api, out))
        customers = capture_step("customers", lambda: capture_customers(api, out))
        if not args.skip_simulations:
            inputs = pick_simulation_inputs(orders, machines, customers, out["analytics"]["bottlenecks"], now)
            out["simulations"]["inputs"] = inputs
            capture_step("simulations", lambda: capture_simulations(api, out, simulation_presets(inputs, priority["profile"], now)))
        capture_step("system", lambda: capture_system(api, out))
        # /auth/me for every seeded role (the audit trail and users list are captured above).
        for user in DEV_USERS:
            token = api.login(user.username, user.password)
            out["auth"]["me"][user.role.value] = api.get("/auth/me")
            out["auth"].setdefault("expires_in", token["expires_in"])
        api.login(admin.username, admin.password)
        out["meta"] = {
            "captured_at": iso(now),
            "api_version": out["health"].get("version"),
            "plant": {"connector": "mock", "scale": SCALE, "seed": SEED},
            "horizon_start": horizon_start,
            "horizon_end": horizon_end,
            "active_version": active["version_number"] if active else None,
            "versions": [{"version_number": v["version_number"], "status": v["status"]} for v in versions],
            "orders": len(orders),
            "machines": len(machines),
            "customers": len(customers),
            "requests": api.requests,
        }
    finally:
        api.close()
        stop_process(process)
    log(f"captured {api.requests} API requests in {time.perf_counter() - started:.0f}s")
    return out


if __name__ == "__main__":
    sys.exit(main())
