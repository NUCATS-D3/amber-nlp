"""Operational endpoints."""

from fastapi import APIRouter

from amber.api.deps import AmberDep
from amber.api.v1.models import HealthResponse, InfoResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Report API readiness without touching external providers."""

    return HealthResponse()


@router.get("/info", response_model=InfoResponse)
def info(amber: AmberDep) -> InfoResponse:
    """Report build and runtime metadata."""

    return InfoResponse.model_validate(amber.info().model_dump())
