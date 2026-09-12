"""``/simulation``: scenario catalogue for the what-if form builder (spec Phase 7).

The simulation itself is ``POST /schedule/simulate`` (see :mod:`app.api.v1.schedule`).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import SimulationServiceDep, require_read_access
from app.api.schemas.simulation import ScenarioTypesResponse
from app.core.security import CurrentUser
from app.domain.enums import Role

router = APIRouter(prefix="/simulation", tags=["simulation"])

Reader = Annotated[CurrentUser, Depends(require_read_access(Role.OPERATOR))]


@router.get(
    "/scenario-types",
    response_model=ScenarioTypesResponse,
    response_model_by_alias=True,
    summary="JSON schema of every what-if scenario kind",
)
def scenario_types(_user: Reader, service: SimulationServiceDep) -> ScenarioTypesResponse:
    return ScenarioTypesResponse.from_domain(service.list_scenario_types())


__all__ = ["router"]
