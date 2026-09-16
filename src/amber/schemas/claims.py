"""Guarded claim records for the evidence graph boundary."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from amber.ids import validate_ulid
from amber.schemas._immutable import freeze_json
from amber.schemas.provenance import Provenance

_CLAIM_MINT_CONTEXT = object()
_CLAIM_GUARD_MESSAGE = "Claim may only be minted from a validated claim graph"
_StrictString = Annotated[str, Field(strict=True)]
_Confidence = Annotated[float, Field(allow_inf_nan=False, ge=0.0, le=1.0)]


def _validated_provenance(value: Any) -> Provenance:
    if isinstance(value, Provenance):
        value = value.model_dump()
    return Provenance.model_validate(value)


class _ClaimRecord(BaseModel):
    """Locally validated claim fields without graph-acceptance semantics."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: _StrictString
    source_id: _StrictString
    patient_id: _StrictString
    task: _StrictString
    schema_ref: _StrictString
    value: dict[str, Any]
    effective_datetime: datetime | None
    confidence: _Confidence | None
    status: Literal["proposed", "verified", "rejected", "gold"] = "proposed"
    provenance: Provenance

    @field_validator("claim_id")
    @classmethod
    def require_canonical_claim_id(cls, value: str) -> str:
        return validate_ulid(value)

    @field_validator("source_id", "patient_id", "task", "schema_ref")
    @classmethod
    def require_nonblank_identifier(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("identifiers must not be blank")
        return value

    @field_validator("value")
    @classmethod
    def freeze_value(cls, value: dict[str, Any]) -> dict[str, Any]:
        return freeze_json(value)

    @field_validator("provenance", mode="before")
    @classmethod
    def revalidate_provenance(cls, value: Any) -> Provenance:
        return _validated_provenance(value)


class Claim(_ClaimRecord):
    """A claim admitted only after complete graph validation."""

    @model_validator(mode="before")
    @classmethod
    def require_validated_graph(cls, data: Any, info: ValidationInfo) -> Any:
        context = info.context or {}
        if context.get("claim_minter") is not _CLAIM_MINT_CONTEXT:
            raise ValueError(_CLAIM_GUARD_MESSAGE)
        return data

    @classmethod
    def model_construct(  # type: ignore[override]
        cls, _fields_set: set[str] | None = None, **values: Any
    ) -> Self:
        raise TypeError(_CLAIM_GUARD_MESSAGE)


def _mint_claim(data: Mapping[str, Any]) -> Claim:
    """Mint a Claim after the graph boundary has validated its complete support closure."""
    return Claim.model_validate(dict(data), context={"claim_minter": _CLAIM_MINT_CONTEXT})
