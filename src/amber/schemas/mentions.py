"""Typed source spans shared by every producer that finds them.

A ``Mention`` is minted only by the grounding path (``amber.tools.mention.mention``), under the
same private-context guard as ``Inclusion``. It is not evidence: claims cite the inclusions at
its position.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationInfo,
    field_validator,
    model_validator,
)

from amber.ids import mention_id as make_mention_id
from amber.ids import parse_producer, validate_ulid
from amber.schemas._immutable import FrozenMapping
from amber.schemas._minted import MintedRecord
from amber.schemas.provenance import Provenance

_MENTION_MINT_CONTEXT = object()
_StrictInt = Annotated[int, Field(strict=True)]
_Fragment = tuple[_StrictInt, _StrictInt]
_Text = Annotated[str, Field(strict=True, min_length=1)]
_FiniteFloat = Annotated[float, Field(strict=True, allow_inf_nan=False)]


def _nonblank(value: str | None) -> str | None:
    if value is not None and not value.strip():
        raise ValueError("value must not be blank")
    return value


def fragments_error(start: int, end: int, fragments: tuple[tuple[int, int], ...]) -> str | None:
    """Return why ``fragments`` are not a canonical discontinuous span over ``(start, end)``.

    Fragments are ordered, separated by gaps, and their hull is exactly ``(start, end)``. A
    single fragment or touching fragments would be a second spelling of a contiguous span with a
    different ``mention_id``, so both are rejected.
    """
    if not fragments:
        return None
    if len(fragments) == 1:
        return "a discontinuous mention needs at least two fragments"
    previous_end: int | None = None
    for fragment_start, fragment_end in fragments:
        if fragment_end <= fragment_start:
            return "fragment end must be greater than start"
        if previous_end is not None and fragment_start <= previous_end:
            return "fragments must be ordered and separated by a gap"
        previous_end = fragment_end
    if fragments[0][0] != start or fragments[-1][1] != end:
        return "mention start and end must be the hull of its fragments"
    return None


class Mention(MintedRecord):
    """A content-addressed typed span; readings of it are separate records."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mention_id: Annotated[str, Field(strict=True)]
    source_id: Annotated[str, Field(strict=True, min_length=1)]
    start: _StrictInt = Field(ge=0)
    end: _StrictInt = Field(gt=0)
    quote: Annotated[str, Field(strict=True, min_length=1)]
    mention_type: Annotated[str, Field(strict=True, min_length=1)]
    fragments: tuple[_Fragment, ...] = ()
    section_category: Annotated[str, Field(strict=True, min_length=1)] | None = None

    @field_validator("mention_type")
    @classmethod
    def require_nonblank_type(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("mention_type must not be blank")
        return value

    @model_validator(mode="after")
    def require_verified_minter(self, info: ValidationInfo) -> Mention:
        context = info.context or {}
        if context.get("mention_minter") is not _MENTION_MINT_CONTEXT:
            raise ValueError("Mention may only be minted by the verified mention() path")
        if self.end <= self.start:
            raise ValueError("mention end must be greater than start")
        if (error := fragments_error(self.start, self.end, self.fragments)) is not None:
            raise ValueError(error)
        expected_id = make_mention_id(
            source_id=self.source_id,
            start=self.start,
            end=self.end,
            mention_type=self.mention_type,
            fragments=self.fragments,
        )
        if self.mention_id != expected_id:
            raise ValueError("mention_id does not match the mention identity fields")
        return self

    @classmethod
    def model_construct(  # type: ignore[override]
        cls, _fields_set: set[str] | None = None, **values: Any
    ) -> Self:
        raise TypeError("Mention may only be minted by the verified mention() path")


def _mint_mention(data: dict[str, Any]) -> Mention:
    """Internal constructor used only after source bounds and quotes are verified."""
    return Mention.model_validate(data, context={"mention_minter": _MENTION_MINT_CONTEXT})


class Concept(BaseModel):
    """An optional terminology concept for a reading (system, code, label, linker score)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    system: _Text
    code: _Text
    label: _Text
    score: _FiniteFloat | None = None

    @field_validator("system", "code", "label")
    @classmethod
    def require_nonblank(cls, value: str) -> str | None:
        return _nonblank(value)


class Attribute(BaseModel):
    """One assessed attribute category of a reading, with its cue and scope evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    value: StrictBool | StrictInt | StrictStr
    evidence_id: _Text | None = None
    scope_evidence_id: _Text | None = None
    assigned_by: _Text | None = None

    @field_validator("value")
    @classmethod
    def require_nonblank_value(cls, value: bool | int | str) -> bool | int | str:
        if isinstance(value, str):
            _nonblank(value)
        return value

    @field_validator("evidence_id", "scope_evidence_id")
    @classmethod
    def require_nonblank_reference(cls, value: str | None) -> str | None:
        return _nonblank(value)

    @field_validator("assigned_by")
    @classmethod
    def require_producer_reference(cls, value: str | None) -> str | None:
        if value is not None:
            parse_producer(value)
        return value


class MentionReading(BaseModel):
    """One producer's reading of a span; corrections are new readings, never edits."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reading_id: Annotated[str, Field(strict=True)]
    mention_id: _Text
    label: _Text | None = None
    value: StrictStr | StrictInt | _FiniteFloat | None = None
    unit: _Text | None = None
    concept: Concept | None = None
    attributes: Mapping[str, Attribute] = Field(default_factory=dict)
    inputs: tuple[_Text, ...] = ()
    status: Literal["proposed", "verified", "rejected", "gold"] = "proposed"
    provenance: Provenance

    @field_validator("reading_id")
    @classmethod
    def require_canonical_reading_id(cls, value: str) -> str:
        return validate_ulid(value)

    @field_validator("mention_id", "label", "unit")
    @classmethod
    def require_nonblank_text(cls, value: str | None) -> str | None:
        return _nonblank(value)

    @field_validator("attributes")
    @classmethod
    def freeze_attributes(cls, value: Mapping[str, Attribute]) -> Mapping[str, Attribute]:
        for category in value:
            if not category.strip() or category != category.strip():
                raise ValueError("attribute categories must be nonblank and unpadded")
        return FrozenMapping(value)

    @field_validator("inputs")
    @classmethod
    def require_distinct_inputs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value) or len(value) != len(set(value)):
            raise ValueError("reading inputs must be distinct and nonblank")
        return value

    @field_validator("provenance", mode="before")
    @classmethod
    def revalidate_provenance(cls, value: Any) -> Provenance:
        if isinstance(value, Provenance):
            value = value.model_dump(warnings=False)
        return Provenance.model_validate(value)

    @model_validator(mode="after")
    def require_real_assigners(self) -> MentionReading:
        if any(
            attribute.assigned_by == self.provenance.producer
            for attribute in self.attributes.values()
        ):
            raise ValueError("assigned_by equal to the reading's producer must be omitted")
        return self
