"""Application factory. ``create_app()`` wires settings, logging, database,
middleware, error handlers and routers — no business logic lives here."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.engine import Engine

from app.api.router import api_router
from app.api.v1.health import MetricsRegistry
from app.core.clock import Clock, SystemClock
from app.core.config import Settings, get_settings
from app.core.db import create_engine_from_url, create_session_factory, session_scope
from app.core.errors import AppError
from app.core.logging import RequestIdMiddleware, configure_logging

log = structlog.get_logger(__name__)


def _seed_dev_data(app: FastAPI, settings: Settings) -> None:
    from app.db.seed import seed_all

    try:
        with session_scope(app.state.session_factory) as session:
            seed_all(session, environment=settings.environment)
    except Exception as exc:  # seeding must never block start-up (e.g. migrations not applied yet)
        log.warning("seed_skipped", reason=str(exc))


def _start_background_jobs(app: FastAPI, settings: Settings, clock: Clock) -> Any:
    """In-process APScheduler loop (dev); a dedicated worker container runs it in prod."""
    from app.workers.scheduler import build_scheduler, start_scheduler

    scheduler = build_scheduler(settings, app.state.session_factory, clock)
    start_scheduler(scheduler)
    app.state.scheduler = scheduler
    return scheduler


def _build_lifespan(settings: Settings, engine: Engine | None, clock: Clock) -> Any:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings.log_level, settings.log_json)
        if settings.is_prod and settings.uses_default_secret:
            log.warning("insecure_jwt_secret", environment=settings.environment)
        owns_engine = engine is None
        app_engine = engine or create_engine_from_url(
            settings.effective_database_url, echo=settings.database_echo
        )
        app.state.settings = settings
        app.state.clock = clock
        app.state.engine = app_engine
        app.state.session_factory = create_session_factory(app_engine)
        app.state.metrics = MetricsRegistry()
        if settings.is_dev and settings.seed_on_startup:
            _seed_dev_data(app, settings)
        scheduler = _start_background_jobs(app, settings, clock) if settings.background_jobs_enabled else None
        app.state.scheduler = scheduler
        log.info(
            "app_started",
            environment=settings.environment,
            version=settings.app_version,
            background_jobs=settings.background_jobs_enabled,
        )
        try:
            yield
        finally:
            if scheduler is not None:
                from app.workers.scheduler import shutdown_scheduler

                shutdown_scheduler(scheduler, wait=False)
            if owns_engine:
                app_engine.dispose()
            log.info("app_stopped")

    return lifespan


def _app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
    if exc.status_code >= 500:
        log.error("app_error", code=exc.code, message=exc.message, details=exc.details)
    else:
        log.info("app_error", code=exc.code, message=exc.message)
    return JSONResponse(status_code=exc.status_code, content=exc.to_dict())


def _validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": "validation_error",
            "message": "request validation failed",
            "details": {"errors": jsonable_encoder(exc.errors())},
        },
    )


def _unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Runs in Starlette's outermost error middleware, so the request id is re-attached here."""
    log.exception("unhandled_error", error=str(exc))
    request_id = getattr(request.state, "request_id", None)
    return JSONResponse(
        status_code=500,
        content={"error": "internal_error", "message": "internal server error", "details": {}},
        headers={"X-Request-ID": request_id} if request_id else None,
    )


def create_app(
    settings: Settings | None = None,
    *,
    engine: Engine | None = None,
    clock: Clock | None = None,
) -> FastAPI:
    """Build the FastAPI application.

    ``engine`` and ``clock`` are injectable for tests (an in-memory SQLite engine,
    a :class:`FrozenClock`); by default the engine is created from the settings
    at start-up and disposed at shutdown.
    """
    settings = settings or get_settings()
    clock = clock or SystemClock()
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=_build_lifespan(settings, engine, clock),
        docs_url=f"{settings.api_prefix}/docs",
        openapi_url=f"{settings.api_prefix}/openapi.json",
        redoc_url=None,
    )
    # State is populated in the lifespan; expose settings/clock early for dependencies.
    app.state.settings = settings
    app.state.clock = clock

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(RequestIdMiddleware)

    app.add_exception_handler(AppError, _app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _unhandled_error_handler)

    app.include_router(api_router, prefix=settings.api_prefix)
    return app


__all__ = ["create_app"]
