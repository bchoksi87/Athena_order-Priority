"""Aggregates the v1 routers under one ``APIRouter``.

Other resource routers (orders, machines, schedule, ...) are added here by
their owners: ``api_router.include_router(<module>.router)``. Keep this file
free of logic.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import auth, health

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)

__all__ = ["api_router"]
