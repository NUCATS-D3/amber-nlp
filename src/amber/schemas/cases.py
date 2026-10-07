"""Note-scoped case identity and explicit clinical/execution outcomes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal, Self, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SkipValidation,
    ValidationInfo,
    field_validator,
    model_validator,
)

from amber.schemas._immutable import _FrozenList, freeze_json
from amber.schemas.claims import Claim
from amber.schemas.evidence import EvidenceEdge, Inclusion, InferenceEvidence
from amber.schemas.provenance import Provenance, Sensitivity

OutcomeStatus: TypeAlias = Literal[
    "answered", "not_mentioned", "conflicting_evidence", "insufficient_evidence", "failed"
]
UnansweredStatus: TypeAlias = Literal[
    "not_mentioned", "conflicting_evidence", "insufficient_evidence"
]
FailureKind: TypeAlias = Literal[
    "policy", "provider", "parse", "grounding", "validation", "budget", "internal"
]
_Identifier = Annotated[str, Field(strict=True, min_length=1)]
_RESULT_MINT_CONTEXT = object()
_RESULT_GUARD_MESSAGE = "CaseResult requires the validated case result boundary"


class _FrozenRecords(_FrozenList):
    """Protect a list of already validated, frozen domain records."""

    def __init__(self, value: list[Any]) -> None:
        if getattr(self, "_initialized", False):
            raise TypeError("frozen record lists cannot be mutated")
        list.__init__(self, value)
        self._initialized = True


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("value must not be blank")
    return value


def _freeze_identifiers(value: list[str]) -> list[str]:
    for identifier in value:
        _nonblank(identifier)
    if len(value) != len(set(value)):
        raise ValueError("identifiers must be distinct")
    return freeze_json(value)


class Case(BaseModel):
    """Immutable membership for exactly one note and one named task."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: _Identifier
    task: _Identifier
    patient_id: _Identifier
    source_ids: list[_Identifier] = Field(min_length=1, max_length=1)
    sensitivity: Sensitivity

    @field_validator("case_id", "task", "patient_id")
    @classmethod
    def require_nonblank_identity(cls, value: str) -> str:
        return _nonblank(value)

    @field_validator("source_ids")
    @classmethod
    def freeze_membership(cls, value: list[str]) -> list[str]:
        return _freeze_identifiers(value)


class CaseOutcome(BaseModel):
    """Locally consistent outcome; source scope is checked at the result boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: OutcomeStatus
    reason: Annotated[str, Field(strict=True)] | None = None
    failure_kind: FailureKind | None = None
    reviewed_source_ids: list[_Identifier] = Field(default_factory=list, validate_default=True)
    evidence_ids: list[_Identifier] = Field(default_factory=list, validate_default=True)
    provenance: Provenance

    @field_validator("reason")
    @classmethod
    def require_nonblank_reason(cls, value: str | None) -> str | None:
        return _nonblank(value) if value is not None else None

    @field_validator("reviewed_source_ids", "evidence_ids")
    @classmethod
    def freeze_references(cls, value: list[str]) -> list[str]:
        return _freeze_identifiers(value)

    @field_validator("provenance", mode="before")
    @classmethod
    def revalidate_provenance(cls, value: Any) -> Provenance:
        if isinstance(value, Provenance):
            value = value.model_dump(warnings=False)
        return Provenance.model_validate(value)

    @model_validator(mode="after")
    def require_consistent_outcome(self) -> CaseOutcome:
        if self.status != "answered" and self.reason is None:
            raise ValueError("non-answered outcomes require a reason")
        if (self.status == "failed") != (self.failure_kind is not None):
            raise ValueError("failure_kind is required exactly for failed outcomes")
        if self.status == "conflicting_evidence" and not self.evidence_ids:
            raise ValueError("conflicting evidence requires citations")
        return self


class CaseResult(BaseModel):
    """Detached run result issued only after complete source and outcome validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: _Identifier
    claims: list[Claim]
    # The guarded result boundary supplies records from a fully revalidated graph.
    # Re-running Inclusion's minter validator here would require a third minting path.
    evidence: list[SkipValidation[Inclusion | InferenceEvidence]]
    edges: list[EvidenceEdge]
    final_claim_ids: list[_Identifier]
    outcome: CaseOutcome
    mentions: list[Any] = Field(max_length=0)
    grounding_failures: int = Field(strict=True, ge=0)
    tool_calls: int = Field(strict=True, ge=0)
    escalated: bool = Field(strict=True)
    trace_id: Annotated[str, Field(strict=True)] | None

    @model_validator(mode="before")
    @classmethod
    def require_validated_result(cls, data: Any, info: ValidationInfo) -> Any:
        if (info.context or {}).get("case_result_minter") is not _RESULT_MINT_CONTEXT:
            raise ValueError(_RESULT_GUARD_MESSAGE)
        return data

    @field_validator("case_id", "trace_id")
    @classmethod
    def require_nonblank_identifiers(cls, value: str | None) -> str | None:
        return _nonblank(value) if value is not None else None

    @field_validator("final_claim_ids")
    @classmethod
    def freeze_final_ids(cls, value: list[str]) -> list[str]:
        return _freeze_identifiers(value)

    @field_validator("claims", "evidence", "edges")
    @classmethod
    def freeze_nodes(cls, value: list[Any]) -> list[Any]:
        return _FrozenRecords(value)

    @field_validator("mentions")
    @classmethod
    def freeze_empty_mentions(cls, value: list[Any]) -> list[Any]:
        return freeze_json(value)

    @classmethod
    def model_construct(  # type: ignore[override]
        cls, _fields_set: set[str] | None = None, **values: Any
    ) -> Self:
        raise TypeError(_RESULT_GUARD_MESSAGE)


def _mint_case_result(data: Mapping[str, Any]) -> CaseResult:
    return CaseResult.model_validate(
        dict(data), context={"case_result_minter": _RESULT_MINT_CONTEXT}
    )
