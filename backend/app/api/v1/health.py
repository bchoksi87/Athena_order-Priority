"""Public operational endpoints: ``GET /health`` and ``GET /metrics``."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, ConfigDict

from app.api.deps import ClockDep, SettingsDep
from app.core.db import ping

router = APIRouter(tags=["health"])


@dataclass
class MetricsRegistry:
    """Minimal in-process counters (placeholder until a metrics backend is wired).

    Held on ``app.state.metrics``; :class:`RequestIdMiddleware` calls ``observe``
    for every HTTP request. Not module-level state: one registry per app instance.
    """

    requests_total: int = 0
    errors_total: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    by_path: dict[str, int] = field(default_factory=dict)
    counters: dict[str, float] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock, repr=False)

    def observe(self, method: str, path: str, status_code: int) -> None:
        with self._lock:
            self.requests_total += 1
            if status_code >= 500:
                self.errors_total += 1
            bucket = f"{status_code // 100}xx"
            self.by_status[bucket] = self.by_status.get(bucket, 0) + 1
            key = f"{method} {path}"
            self.by_path[key] = self.by_path.get(key, 0) + 1

    def increment(self, name: str, amount: float = 1.0) -> None:
        with self._lock:
            self.counters[name] = self.counters.get(name, 0.0) + amount

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "requests_total": self.requests_total,
                "errors_total": self.errors_total,
                "by_status": dict(self.by_status),
                "by_path": dict(sorted(self.by_path.items())),
                "counters": dict(sorted(self.counters.items())),
            }


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str
    database: str
    version: str
    environment: str
    time: str


@router.get("/health", response_model=HealthResponse, summary="Liveness/readiness probe")
def health(request: Request, response: Response, settings: SettingsDep, clock: ClockDep) -> HealthResponse:
    engine = getattr(request.app.state, "engine", None)
    db_ok = engine is not None and ping(engine)
    if not db_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status="ok" if db_ok else "degraded",
        database="ok" if db_ok else "unavailable",
        version=settings.app_version,
        environment=settings.environment,
        time=clock.now().isoformat(),
    )


@router.get("/metrics", summary="Simple JSON counters")
def metrics(request: Request) -> dict[str, Any]:
    registry: MetricsRegistry | None = getattr(request.app.state, "metrics", None)
    return registry.snapshot() if registry else MetricsRegistry().snapshot()


__all__ = ["MetricsRegistry", "router"]
