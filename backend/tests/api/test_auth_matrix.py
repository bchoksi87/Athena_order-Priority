"""Role matrix (DESIGN_CONTRACT §9): every endpoint refuses roles below its minimum with 403."""

from __future__ import annotations

from typing import Any

import pytest

from app.domain.enums import ROLE_RANK, Role
from tests.api.conftest import API, Seeded

ALL_ROLES = list(Role)

# (method, path, body, minimum role, executive may read)
ENDPOINTS: list[tuple[str, str, dict[str, Any] | None, Role, bool]] = [
    ("GET", "/orders", None, Role.OPERATOR, True),
    ("GET", "/orders/NOPE", None, Role.OPERATOR, True),
    ("GET", "/orders/NOPE/explanation", None, Role.OPERATOR, True),
    ("GET", "/orders/NOPE/machines", None, Role.OPERATOR, True),
    ("POST", "/orders/NOPE/expedite", {"reason": "r"}, Role.PRODUCTION_MANAGER, False),
    ("POST", "/orders/NOPE/hold", {"reason": "r"}, Role.PLANNER, False),
    ("POST", "/orders/NOPE/release", {"reason": "r"}, Role.PLANNER, False),
    ("POST", "/orders/NOPE/override-priority", {"type": "set", "value": 50, "reason": "r"}, Role.PRODUCTION_MANAGER, False),
    ("POST", "/orders/NOPE/force-next", {"reason": "r"}, Role.PRODUCTION_MANAGER, False),
    ("POST", "/orders/NOPE/move", {"target_machine_id": "M", "reason": "r"}, Role.PRODUCTION_MANAGER, False),
    ("DELETE", "/overrides/NOPE", {"reason": "r"}, Role.PRODUCTION_MANAGER, False),
    ("DELETE", "/expedites/NOPE", {"reason": "r"}, Role.PRODUCTION_MANAGER, False),
    ("GET", "/machines", None, Role.OPERATOR, True),
    ("GET", "/machines/NOPE", None, Role.OPERATOR, True),
    ("GET", "/machines/NOPE/schedule", None, Role.OPERATOR, True),
    ("POST", "/schedule/lock", {"lock_type": "machine", "machine_id": "NOPE", "reason": "r"}, Role.PRODUCTION_MANAGER, False),
    ("POST", "/schedule/unlock", {"lock_id": "NOPE", "reason": "r"}, Role.PRODUCTION_MANAGER, False),
    ("GET", "/schedule/locks", None, Role.OPERATOR, True),
    ("GET", "/priority/configuration", None, Role.PLANNER, True),
    ("PUT", "/priority/configuration", {"profile": {}, "reason": "r"}, Role.ADMIN, False),
    ("GET", "/priority/configuration/versions", None, Role.PLANNER, True),
    ("GET", "/priority/configuration/versions/999", None, Role.PLANNER, True),
    ("POST", "/priority/configuration/versions/999/activate", {"reason": "r"}, Role.ADMIN, False),
    ("POST", "/priority/configuration/preview", {"profile": {}}, Role.PLANNER, False),
    ("GET", "/scheduling/configuration", None, Role.PLANNER, True),
    ("PUT", "/scheduling/configuration", {"scheduling": {"horizon_days": 3}, "reason": "r"}, Role.ADMIN, False),
    ("GET", "/customers", None, Role.PLANNER, True),
    ("GET", "/customers/NOPE/rules", None, Role.PLANNER, True),
    ("PUT", "/customers/NOPE/rules", {"sla_hours": 24, "reason": "r"}, Role.PRODUCTION_MANAGER, False),
    ("DELETE", "/customers/NOPE/rules", {"reason": "r"}, Role.PRODUCTION_MANAGER, False),
    ("GET", "/alerts", None, Role.SUPERVISOR, True),
    ("GET", "/alerts/summary", None, Role.SUPERVISOR, True),
    ("POST", "/alerts/NOPE/acknowledge", None, Role.SUPERVISOR, False),
    ("GET", "/audit", None, Role.PRODUCTION_MANAGER, False),
    ("GET", "/data-quality", None, Role.PLANNER, True),
    ("GET", "/data-quality/issues", None, Role.PLANNER, True),
    ("POST", "/data-quality/run", None, Role.PLANNER, False),
    ("GET", "/users", None, Role.ADMIN, False),
    ("POST", "/users", {"username": "x", "password": "password123", "role": "planner", "display_name": "X"}, Role.ADMIN, False),
    ("PATCH", "/users/NOPE", {"active": False, "reason": "r"}, Role.ADMIN, False),
    ("POST", "/users/NOPE/reset-password", {"password": "password123", "reason": "r"}, Role.ADMIN, False),
]


def _allowed(role: Role, minimum: Role, executive_reads: bool) -> bool:
    if role is Role.EXECUTIVE:
        return executive_reads
    return ROLE_RANK[role] >= ROLE_RANK[minimum]


@pytest.mark.parametrize(("method", "path", "body", "minimum", "executive_reads"), ENDPOINTS)
def test_role_matrix(
    seeded: Seeded, method: str, path: str, body: dict[str, Any] | None, minimum: Role, executive_reads: bool
) -> None:
    for role in ALL_ROLES:
        response = seeded.client.request(method, f"{API}{path}", headers=seeded.headers(role), json=body)
        if _allowed(role, minimum, executive_reads):
            assert response.status_code != 403, (role, method, path, response.text)
            assert response.status_code < 500, (role, method, path, response.text)
        else:
            assert response.status_code == 403, (role, method, path, response.text)
            assert response.json()["error"] == "forbidden"


@pytest.mark.parametrize(("method", "path", "body", "minimum", "executive_reads"), ENDPOINTS)
def test_missing_token_is_401(
    seeded: Seeded, method: str, path: str, body: dict[str, Any] | None, minimum: Role, executive_reads: bool
) -> None:
    response = seeded.client.request(method, f"{API}{path}", json=body)
    assert response.status_code == 401
    assert response.json()["error"] == "unauthenticated"


def test_executive_is_read_only_everywhere(seeded: Seeded) -> None:
    writes = [(m, p, b) for m, p, b, _min, _x in ENDPOINTS if m != "GET"]
    for method, path, body in writes:
        response = seeded.client.request(method, f"{API}{path}", headers=seeded.headers(Role.EXECUTIVE), json=body)
        assert response.status_code == 403, (method, path)
    assert seeded.get("/orders", role=Role.EXECUTIVE)["total"] > 0
