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
    SerializerFunctionWrapHandler,
    ValidationInfo,
    field_validator,
    model_serializer,
    model_validator,
)

from amber.ids import inclusion_id as make_inclusion_id
from amber.ids import validate_ulid
from amber.schemas._immutable import FrozenMapping, freeze_json
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
        "invalid_fragments",
    ]
    message: str
    match_starts: tuple[int, ...] = ()


class Derivation(BaseModel):
    """A typed record of how an inference was derived (contract v0.1 section 3.4).

    ``inputs`` maps each role to every input ID it consumed (empty when the role was missing);
    ``parameters`` holds only rule configuration; ``defaults_used`` maps each defaulted role
    to the value assumed. Only ``rule`` derivations use defaults, and only for empty roles.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["rule", "model", "human"]
    name: _StrictString
    version: _StrictString
    inputs: dict[str, tuple[_StrictString, ...]]
    parameters: dict[str, Any] = Field(default_factory=dict)
    defaults_used: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name", "version")
    @classmethod
    def require_nonblank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be blank")
        return value

    @field_validator("inputs")
    @classmethod
    def freeze_inputs(cls, value: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
        if not value:
            raise ValueError("a derivation needs at least one role")
        for role, ids in value.items():
            if not role.strip():
                raise ValueError("role names must not be blank")
            if any(not item.strip() for item in ids) or len(set(ids)) != len(ids):
                raise ValueError("role input identifiers must be distinct and non-blank")
        return FrozenMapping({role: tuple(ids) for role, ids in value.items()})

    @field_validator("parameters", "defaults_used")
    @classmethod
    def freeze_values(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            return freeze_json(value)  # type: ignore[no-any-return]
        except TypeError:
            raise ValueError("values must be JSON") from None

    @model_validator(mode="after")
    def require_consistent_defaults(self) -> Derivation:
        if self.defaults_used and self.kind != "rule":
            raise ValueError("only rule derivations use defaults")
        for role in self.defaults_used:
            if role not in self.inputs or self.inputs[role]:
                raise ValueError("a defaulted role must be listed with no inputs")
        return self


class InferenceEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["inference"] = "inference"
    evidence_id: _StrictString
    rationale: _StrictString
    inputs: list[_StrictString]
    trace_id: _StrictString | None
    span_id: _StrictString | None
    provenance: Provenance
    # Omitted from dumps when None, so records without one serialize as before.
    derivation: Derivation | None = None

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
            value = value.model_dump(warnings=False)
        return Provenance.model_validate(value)

    @model_validator(mode="after")
    def require_derivation_inputs(self) -> InferenceEvidence:
        if self.derivation is not None:
            listed = {item for ids in self.derivation.inputs.values() for item in ids}
            if not listed <= set(self.inputs):
                raise ValueError("derivation inputs must be among the inference's inputs")
        return self

    @model_serializer(mode="wrap")
    def omit_absent_derivation(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if data.get("derivation", ...) is None:
            del data["derivation"]
        return data


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
