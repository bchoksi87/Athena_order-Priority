"""Aggregates the v1 routers under one ``APIRouter``.

Other resource routers (orders, machines, schedule, ...) are added here by
their owners: ``api_router.include_router(<module>.router)``. Keep this file
free of logic.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    alerts,
    audit,
    auth,
    customers,
    data_quality,
    health,
    locks,
    machines,
    orders,
    priority_config,
    scheduling_config,
    users,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(orders.router)
api_router.include_router(machines.router)
api_router.include_router(locks.router)
api_router.include_router(priority_config.router)
api_router.include_router(scheduling_config.router)
api_router.include_router(customers.router)
api_router.include_router(alerts.router)
api_router.include_router(audit.router)
api_router.include_router(data_quality.router)
api_router.include_router(users.router)

__all__ = ["api_router"]
