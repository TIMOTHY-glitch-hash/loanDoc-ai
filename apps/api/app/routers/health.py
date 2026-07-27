"""Liveness endpoint.

Also doubles as capability discovery: the frontend reads ``agentEnabled`` to
decide whether to advertise live extraction.
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.config import Settings, get_settings
from app.schemas import HealthResponse

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse, response_model_by_alias=True)
def read_health(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
    return HealthResponse(
        version=settings.api_version,
        agent_enabled=settings.agent_enabled,
    )
