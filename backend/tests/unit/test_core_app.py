"""create_app(): health, metrics, auth endpoints, error mapping and role guards."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.deps import require_min_role, require_read_access
from app.core.errors import ConflictError, NotFoundError
from app.core.security import CurrentUser
from app.domain.enums import Role

pytestmark = pytest.mark.unit

PREFIX = "/api/v1"


def test_health_reports_db_and_version(app_client: TestClient) -> None:
    response = app_client.get(f"{PREFIX}/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok" and body["database"] == "ok"
    assert body["environment"] == "test"
    assert body["time"].startswith("2026-09-11T08:00:00")
    assert response.headers["x-request-id"].startswith("req_")


def test_metrics_count_requests(app_client: TestClient) -> None:
    app_client.get(f"{PREFIX}/health")
    app_client.get(f"{PREFIX}/health")
    body = app_client.get(f"{PREFIX}/metrics").json()
    assert body["requests_total"] == 2  # the /metrics call itself is counted after its response
    assert body["by_status"] == {"2xx": 2}
    assert body["by_path"] == {f"GET {PREFIX}/health": 2}


def test_login_returns_token_and_role(app_client: TestClient, dev_passwords: dict[str, str]) -> None:
    response = app_client.post(
        f"{PREFIX}/auth/login", json={"username": "planner", "password": dev_passwords["planner"]}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["role"] == "planner"
    assert body["user"]["username"] == "planner"
    assert body["expires_in"] == 3600

    me = app_client.get(f"{PREFIX}/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json()["role"] == "planner"


def test_login_is_case_insensitive_on_username(app_client: TestClient, dev_passwords: dict[str, str]) -> None:
    response = app_client.post(
        f"{PREFIX}/auth/login", json={"username": "ADMIN", "password": dev_passwords["admin"]}
    )
    assert response.status_code == 200


def test_login_rejects_bad_credentials(app_client: TestClient) -> None:
    response = app_client.post(f"{PREFIX}/auth/login", json={"username": "planner", "password": "nope"})
    assert response.status_code == 401
    assert response.json() == {
        "error": "unauthenticated",
        "message": "invalid username or password",
        "details": {},
    }
    unknown = app_client.post(f"{PREFIX}/auth/login", json={"username": "ghost", "password": "x"})
    assert unknown.status_code == 401


def test_validation_errors_use_error_envelope(app_client: TestClient) -> None:
    response = app_client.post(f"{PREFIX}/auth/login", json={"username": "planner"})
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "validation_error"
    assert body["details"]["errors"]


def test_me_requires_bearer(app_client: TestClient) -> None:
    assert app_client.get(f"{PREFIX}/auth/me").status_code == 401
    assert app_client.get(f"{PREFIX}/auth/me", headers={"Authorization": "Basic abc"}).status_code == 401
    assert app_client.get(f"{PREFIX}/auth/me", headers={"Authorization": "Bearer nope"}).status_code == 401


@pytest.mark.parametrize("role", list(Role))
def test_auth_headers_fixture_works_for_every_role(
    app_client: TestClient, auth_headers: Callable[[Role], dict[str, str]], role: Role
) -> None:
    me = app_client.get(f"{PREFIX}/auth/me", headers=auth_headers(role))
    assert me.status_code == 200
    assert me.json()["role"] == role.value


def _guarded_app(app: FastAPI) -> FastAPI:
    @app.get("/write")
    def write(
        user: Annotated[CurrentUser, Depends(require_min_role(Role.PRODUCTION_MANAGER))],
    ) -> dict[str, str]:
        return {"user": user.username}

    @app.get("/read")
    def read(user: Annotated[CurrentUser, Depends(require_read_access(Role.PLANNER))]) -> dict[str, str]:
        return {"user": user.username}

    @app.get("/missing")
    def missing() -> None:
        raise NotFoundError("order 'X' not found", details={"order_id": "X"})

    @app.get("/conflict")
    def conflict() -> None:
        raise ConflictError("already approved")

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("unexpected")

    return app


@pytest.mark.parametrize(
    ("role", "write_status", "read_status"),
    [
        (Role.ADMIN, 200, 200),
        (Role.PRODUCTION_MANAGER, 200, 200),
        (Role.PLANNER, 403, 200),
        (Role.SUPERVISOR, 403, 403),
        (Role.OPERATOR, 403, 403),
        (Role.EXECUTIVE, 403, 200),
    ],
)
def test_role_guards(
    app: FastAPI,
    seeded_users: dict[Role, object],
    auth_headers: Callable[[Role], dict[str, str]],
    role: Role,
    write_status: int,
    read_status: int,
) -> None:
    _guarded_app(app)
    with TestClient(app) as client:
        assert client.get("/write", headers=auth_headers(role)).status_code == write_status
        assert client.get("/read", headers=auth_headers(role)).status_code == read_status
        assert client.get("/write").status_code == 401


def test_app_errors_map_to_json(app: FastAPI) -> None:
    _guarded_app(app)
    with TestClient(app, raise_server_exceptions=False) as client:
        missing = client.get("/missing")
        assert missing.status_code == 404
        assert missing.json() == {
            "error": "not_found",
            "message": "order 'X' not found",
            "details": {"order_id": "X"},
        }
        assert client.get("/conflict").status_code == 409
        boom = client.get("/boom")
        assert boom.status_code == 500
        assert boom.json()["error"] == "internal_error"
        assert boom.headers["x-request-id"]
