"""Shared provenance and data-handling enums."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from amber.ids import parse_producer
from amber.schemas._immutable import freeze_json
from amber.schemas._wire import Timestamp


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
    destination: str = Field(strict=True, min_length=1)
    policy_version: str = Field(strict=True, min_length=1)
    restriction_ids: tuple[str, ...] = ()
    zone: Zone
    sensitivity: Sensitivity
    created_at: Timestamp
    version: str = Field(min_length=1)

    @field_validator("producer")
    @classmethod
    def require_producer_reference(cls, value: str) -> str:
        parse_producer(value)
        return value

    @field_validator("destination", "policy_version")
    @classmethod
    def require_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be blank")
        return value

    @field_validator("restriction_ids")
    @classmethod
    def require_distinct_restrictions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not isinstance(item, str) or not item.strip() for item in value):
            raise ValueError("restriction IDs must be non-blank strings")
        if len(value) != len(set(value)):
            raise ValueError("restriction IDs must be distinct")
        return value

    @field_validator("prompt_versions")
    @classmethod
    def freeze_prompt_versions(cls, value: dict[str, str]) -> dict[str, str]:
        return freeze_json(value)

    @model_validator(mode="after")
    def require_local_zone_for_local_destination(self) -> Provenance:
        if self.destination.startswith("local:") and self.zone is not Zone.local:
            raise ValueError("a local: destination sends nothing anywhere and must be zone local")
        return self
