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

from amber.ids import validate_ulid
from amber.schemas._immutable import FrozenMapping, _FrozenList, freeze_json
from amber.schemas.claims import Claim
from amber.schemas.evidence import EvidenceEdge, Inclusion, InferenceEvidence
from amber.schemas.mentions import Mention, MentionReading
from amber.schemas.provenance import Provenance, Sensitivity

OutcomeStatus: TypeAlias = Literal[
    "answered", "not_mentioned", "conflicting_evidence", "insufficient_evidence", "failed"
]
UnansweredStatus: TypeAlias = Literal[
    "not_mentioned", "conflicting_evidence", "insufficient_evidence"
]
Split: TypeAlias = Literal["train", "dev", "test"]
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
    # The dataset the source was drawn from; split belongs to the (dataset, source) pair.
    dataset: _Identifier | None = None
    # Examples and LabelRecords must match it.
    split: Split | None = None

    @field_validator("case_id", "task", "patient_id", "dataset")
    @classmethod
    def require_nonblank_identity(cls, value: str | None) -> str | None:
        return None if value is None else _nonblank(value)

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


class VariableOutcome(BaseModel):
    """The outcome and final claims of one final variable of a case."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    variable: _Identifier
    outcome: CaseOutcome
    final_claim_ids: list[_Identifier] = Field(default_factory=list, validate_default=True)

    @field_validator("variable")
    @classmethod
    def require_nonblank_variable(cls, value: str) -> str:
        return _nonblank(value)

    @field_validator("outcome", mode="before")
    @classmethod
    def revalidate_outcome(cls, value: Any) -> CaseOutcome:
        if isinstance(value, CaseOutcome):
            value = value.model_dump(warnings=False)
        return CaseOutcome.model_validate(value)

    @field_validator("final_claim_ids")
    @classmethod
    def freeze_final_ids(cls, value: list[str]) -> list[str]:
        return _freeze_identifiers(value)

    @model_validator(mode="after")
    def require_final_ids_iff_answered(self) -> VariableOutcome:
        if (self.outcome.status == "answered") != bool(self.final_claim_ids):
            raise ValueError("final_claim_ids are required exactly for answered outcomes")
        return self


_SEVERITY: tuple[OutcomeStatus, ...] = (
    "failed",
    "conflicting_evidence",
    "insufficient_evidence",
    "not_mentioned",
    "answered",
)


class CaseResult(BaseModel):
    """Detached run result issued only after complete source and outcome validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: _Identifier
    run_id: _Identifier
    variable_set: _Identifier
    variable_set_version: _Identifier
    variable_set_ref: _Identifier
    claims: list[Claim]
    # The guarded result boundary supplies records from a fully revalidated graph.
    # Re-running Inclusion's minter validator here would require a third minting path.
    evidence: list[SkipValidation[Inclusion | InferenceEvidence]]
    edges: list[EvidenceEdge]
    variable_outcomes: Mapping[str, VariableOutcome]
    # Present exactly when the set has one final variable, and equal to its VariableOutcome.
    final_claim_ids: list[_Identifier] | None
    outcome: CaseOutcome | None
    # Mentions, like inclusions, are re-minted by graph validation before reaching this model.
    mentions: list[SkipValidation[Mention]]
    mention_readings: list[MentionReading]
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

    @field_validator("run_id")
    @classmethod
    def require_ulid_run_id(cls, value: str) -> str:
        return validate_ulid(value)

    @field_validator("case_id", "trace_id")
    @classmethod
    def require_nonblank_identifiers(cls, value: str | None) -> str | None:
        return _nonblank(value) if value is not None else None

    @field_validator("final_claim_ids")
    @classmethod
    def freeze_final_ids(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _freeze_identifiers(value)

    @field_validator("variable_outcomes")
    @classmethod
    def freeze_variable_outcomes(
        cls, value: Mapping[str, VariableOutcome]
    ) -> Mapping[str, VariableOutcome]:
        if not value or any(key != item.variable for key, item in value.items()):
            raise ValueError("variable outcomes must be nonempty and keyed by variable")
        return FrozenMapping(value)

    @model_validator(mode="after")
    def require_consistent_case_level_fields(self) -> CaseResult:
        if (self.outcome is None) != (self.final_claim_ids is None):
            raise ValueError("outcome and final_claim_ids are both present or both null")
        if self.outcome is not None:
            if len(self.variable_outcomes) != 1:
                raise ValueError("case-level outcome requires exactly one final variable")
            (single,) = self.variable_outcomes.values()
            if single.outcome != self.outcome or single.final_claim_ids != self.final_claim_ids:
                raise ValueError("case-level outcome must equal the variable outcome")
        return self

    @field_validator("claims", "evidence", "edges", "mentions", "mention_readings")
    @classmethod
    def freeze_nodes(cls, value: list[Any]) -> list[Any]:
        return _FrozenRecords(value)

    @classmethod
    def model_construct(  # type: ignore[override]
        cls, _fields_set: set[str] | None = None, **values: Any
    ) -> Self:
        raise TypeError(_RESULT_GUARD_MESSAGE)


def case_status(result: CaseResult) -> OutcomeStatus:
    """The most severe variable status: failed, conflicting, insufficient, not_mentioned,
    answered. Computed on demand and never stored."""
    statuses = {item.outcome.status for item in result.variable_outcomes.values()}
    return next(status for status in _SEVERITY if status in statuses)


def _mint_case_result(data: Mapping[str, Any]) -> CaseResult:
    return CaseResult.model_validate(
        dict(data), context={"case_result_minter": _RESULT_MINT_CONTEXT}
    )
