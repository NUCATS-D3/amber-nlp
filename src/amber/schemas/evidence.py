"""Evidence value objects.

Inclusions require a private validation context so ordinary model construction cannot mint
verified evidence. Grounding and, later, the explicit human annotation path own that context.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from amber.ids import inclusion_id as make_inclusion_id
from amber.ids import validate_ulid
from amber.schemas._immutable import freeze_json
from amber.schemas.provenance import Provenance

_INCLUSION_MINT_CONTEXT = object()
_StrictString = Annotated[str, Field(strict=True)]
_FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]


class Inclusion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["inclusion"] = "inclusion"
    evidence_id: str
    source_id: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1)
    mention_id: str | None = None
    alignment: Literal["exact", "fuzzy"]
    alignment_score: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def require_verified_minter(self, info: ValidationInfo) -> Inclusion:
        context = info.context or {}
        if context.get("inclusion_minter") is not _INCLUSION_MINT_CONTEXT:
            raise ValueError("Inclusion may only be minted by a verified grounding path")
        if self.end <= self.start:
            raise ValueError("inclusion end must be greater than start")
        expected_id = make_inclusion_id(
            source_id=self.source_id,
            start=self.start,
            end=self.end,
        )
        if self.evidence_id != expected_id:
            raise ValueError("evidence_id does not match the inclusion identity fields")
        return self


def _mint_inclusion(data: dict[str, Any]) -> Inclusion:
    """Internal constructor used only after source bounds and quote equality are verified."""
    return Inclusion.model_validate(
        data,
        context={"inclusion_minter": _INCLUSION_MINT_CONTEXT},
    )


class GroundingFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str
    candidate: str
    reason: Literal[
        "source_not_text",
        "empty_candidate",
        "not_found",
        "ambiguous",
        "hint_out_of_bounds",
        "hint_mismatch",
    ]
    message: str
    match_starts: tuple[int, ...] = ()


class InferenceEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["inference"] = "inference"
    evidence_id: _StrictString
    rationale: _StrictString
    inputs: list[_StrictString]
    trace_id: _StrictString | None
    span_id: _StrictString | None
    provenance: Provenance

    @field_validator("evidence_id")
    @classmethod
    def require_canonical_evidence_id(cls, value: str) -> str:
        return validate_ulid(value)

    @field_validator("rationale", "trace_id", "span_id")
    @classmethod
    def require_nonblank_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("value must not be blank")
        return value

    @field_validator("inputs")
    @classmethod
    def validate_and_freeze_inputs(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("inference inputs must not be empty")
        if any(not item.strip() for item in value):
            raise ValueError("inference input identifiers must not be blank")
        if len(value) != len(set(value)):
            raise ValueError("inference input identifiers must be distinct")
        return freeze_json(value)

    @field_validator("provenance", mode="before")
    @classmethod
    def revalidate_provenance(cls, value: Any) -> Provenance:
        if isinstance(value, Provenance):
            value = value.model_dump()
        return Provenance.model_validate(value)


class EvidenceEdge(BaseModel):
    """A claim-to-evidence relationship with an optional field role."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: _StrictString
    evidence_id: _StrictString
    role: _StrictString | None = None
    weight: _FiniteFloat | None = None

    @field_validator("claim_id", "evidence_id", "role")
    @classmethod
    def require_nonblank_identifier(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("identifiers must not be blank")
        return value
