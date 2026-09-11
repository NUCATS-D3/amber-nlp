"""Shared provenance and data-handling enums."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Zone(StrEnum):
    local = "local"
    institution = "institution"
    external_baa = "external_baa"
    external = "external"


class Sensitivity(StrEnum):
    synthetic = "synthetic"
    deidentified = "deidentified"
    limited = "limited"
    phi = "phi"


class Provenance(BaseModel):
    """The producer and policy context responsible for a domain node."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    producer: str = Field(min_length=1)
    model: str | None = None
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    run_id: str | None = None
    trace_id: str | None = None
    zone: Zone
    sensitivity: Sensitivity
    created_at: datetime
    version: str = Field(min_length=1)
