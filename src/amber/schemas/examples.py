"""Annotated data: reference tiers, Examples, and label-only records (contract v0.1 section 4).

``Example`` is the single annotated-data format. It is issued only by ``amber.examples`` after
complete validation against the authoritative Source, Case, and VariableSet.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SkipValidation,
    ValidationInfo,
    field_validator,
    model_validator,
)

from amber.ids import parse_producer, validate_ulid
from amber.schemas._immutable import FrozenMapping, freeze_json
from amber.schemas.cases import CaseOutcome, OutcomeStatus, Split, _FrozenRecords
from amber.schemas.claims import Claim
from amber.schemas.evidence import EvidenceEdge, Inclusion, InferenceEvidence
from amber.schemas.mentions import Mention, MentionReading

EXAMPLE_SCHEMA_VERSION = "0.1"
LABEL_RECORD_SCHEMA_VERSION = "0.1"
_Identifier = Annotated[str, Field(strict=True, min_length=1)]
_EXAMPLE_MINT_CONTEXT = object()
_EXAMPLE_GUARD_MESSAGE = "Example requires the validated annotated-data boundary"


class ReferenceTier(StrEnum):
    """How good a reference is; independent of whether a claim was reviewed."""

    adjudicated = "adjudicated"
    single_review = "single_review"
    silver = "silver"


HUMAN_TIERS = frozenset({ReferenceTier.adjudicated, ReferenceTier.single_review})


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("value must not be blank")
    return value


def _distinct(values: tuple[str, ...]) -> tuple[str, ...]:
    if len(values) != len(set(values)):
        raise ValueError("identifiers must be distinct")
    return values


class VariableReference(BaseModel):
    """The reference answer for one final variable, with its tier and attribution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    variable: _Identifier
    tier: ReferenceTier
    outcome: CaseOutcome
    final_claim_ids: tuple[_Identifier, ...] = ()
    annotators: tuple[_Identifier, ...]
    adjudicator: _Identifier | None = None
    derived_from_events: tuple[_Identifier, ...]

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

    @field_validator("final_claim_ids", "annotators")
    @classmethod
    def require_distinct(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _distinct(value)

    @field_validator("derived_from_events")
    @classmethod
    def require_event_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("a reference names the events whose replay yields it")
        for event_id in value:
            validate_ulid(event_id)
        return _distinct(value)

    @model_validator(mode="after")
    def require_consistent_reference(self) -> VariableReference:
        if self.outcome.status == "failed":
            raise ValueError("failed runs are diagnostics, never references")
        if (self.outcome.status == "answered") != bool(self.final_claim_ids):
            raise ValueError("final_claim_ids are required exactly for answered references")
        if not self.annotators:
            raise ValueError("a reference names its annotators")
        kinds = [parse_producer(annotator).kind for annotator in self.annotators]
        adjudicator = None if self.adjudicator is None else parse_producer(self.adjudicator)
        if self.tier is ReferenceTier.silver:
            if "human" in kinds or adjudicator is not None:
                raise ValueError("a silver reference has no human review")
        elif any(kind != "human" for kind in kinds):
            raise ValueError("human-tier annotators must be human: producers")
        if self.tier is ReferenceTier.single_review and (
            len(self.annotators) != 1 or adjudicator is not None
        ):
            raise ValueError("single_review has exactly one reviewer and no adjudicator")
        if self.tier is ReferenceTier.adjudicated:
            if len(self.annotators) < 2:
                raise ValueError("adjudicated references need at least two reviewers")
            if adjudicator is None or adjudicator.kind != "human":
                raise ValueError("adjudicated references need a human adjudicator")
        return self


class Example(BaseModel):
    """A validated snapshot of a case's annotations as of one event."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    example_id: _Identifier
    schema_version: Literal["0.1"]
    case_id: _Identifier
    variable_set: _Identifier
    variable_set_version: _Identifier
    variable_set_ref: _Identifier
    as_of_event: _Identifier
    source_ids: list[_Identifier]
    split: Split | None
    references: Mapping[str, VariableReference]
    claims: list[Claim]
    # Inclusions and mentions are re-minted by graph validation before reaching this model.
    mentions: list[SkipValidation[Mention]]
    mention_readings: list[MentionReading]
    evidence: list[SkipValidation[Inclusion | InferenceEvidence]]
    edges: list[EvidenceEdge]
    created_at: datetime

    @model_validator(mode="before")
    @classmethod
    def require_validated_example(cls, data: Any, info: ValidationInfo) -> Any:
        if (info.context or {}).get("example_minter") is not _EXAMPLE_MINT_CONTEXT:
            raise ValueError(_EXAMPLE_GUARD_MESSAGE)
        return data

    @field_validator("source_ids")
    @classmethod
    def freeze_source_ids(cls, value: list[str]) -> list[str]:
        return freeze_json(value)  # type: ignore[no-any-return]

    @field_validator("references")
    @classmethod
    def freeze_references(
        cls, value: Mapping[str, VariableReference]
    ) -> Mapping[str, VariableReference]:
        return FrozenMapping(value)

    @field_validator("claims", "mentions", "mention_readings", "evidence", "edges")
    @classmethod
    def freeze_records(cls, value: list[Any]) -> list[Any]:
        return _FrozenRecords(value)

    @classmethod
    def model_construct(  # type: ignore[override]
        cls, _fields_set: set[str] | None = None, **values: Any
    ) -> Self:
        raise TypeError(_EXAMPLE_GUARD_MESSAGE)


def _mint_example(data: Mapping[str, Any]) -> Example:
    return Example.model_validate(dict(data), context={"example_minter": _EXAMPLE_MINT_CONTEXT})


class LabelRecord(BaseModel):
    """A silver label without evidence: scoreable for labels, never an Example."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["0.1"]
    case_id: _Identifier
    variable: _Identifier
    variable_set: _Identifier
    variable_set_version: _Identifier
    variable_set_ref: _Identifier
    tier: Literal["silver"]
    outcome_status: OutcomeStatus
    value: Mapping[str, Any] | None
    producer: _Identifier
    split: Split | None

    @field_validator("value")
    @classmethod
    def freeze_value(cls, value: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
        return None if value is None else freeze_json(dict(value))  # type: ignore[no-any-return]

    @field_validator("producer")
    @classmethod
    def require_machine_producer(cls, value: str) -> str:
        if parse_producer(value).kind == "human":
            raise ValueError("a silver label has no human producer")
        return value

    @model_validator(mode="after")
    def require_consistent_label(self) -> LabelRecord:
        if self.outcome_status == "failed":
            raise ValueError("failed runs are diagnostics, never labels")
        if (self.outcome_status == "answered") != (self.value is not None):
            raise ValueError("value is required exactly for answered labels")
        return self
