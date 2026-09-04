"""HTTP request and response models for API v1."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class HealthResponse(BaseModel):
    """Readiness response for clients and deployment probes."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = "ok"


class InfoResponse(BaseModel):
    """Public build and runtime information."""

    model_config = ConfigDict(extra="forbid")

    name: str
    version: str
    environment: str
