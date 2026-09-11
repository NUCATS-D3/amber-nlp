"""Evidence value objects.

Inclusions require a private validation context so ordinary model construction cannot mint
verified evidence. Grounding and, later, the explicit human annotation path own that context.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator

from amber.ids import inclusion_id as make_inclusion_id

_INCLUSION_MINT_CONTEXT = object()


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
