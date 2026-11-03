"""Score reports and the rules every scorer follows (contract v0.1 sections 4.2, 4.6, 6.2).

- A report is ``provisional`` when any reference is below ``adjudicated``.
- Metrics that need evidence (spans, citations) are ``inconclusive`` against ``LabelRecord``s,
  which carry labels only.
- A scorer refuses (``self_reference``) when the predicted results' configuration intersects a
  silver reference's: the ``model:``/``rule:`` producer references on claims, readings, and
  attribute ``assigned_by`` values, rule hashes removed.
- A report names the ``example_id``s it scored against, so it always pins its gold.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

from amber.ids import producer_configuration
from amber.schemas import CaseResult, Claim, Example, LabelRecord, MentionReading, ReferenceTier

MetricKind: TypeAlias = Literal["label", "evidence", "coverage", "cost", "structure"]
_FROZEN = ConfigDict(extra="forbid", frozen=True)
_Count = Annotated[int, Field(strict=True, ge=0)]


class ScoringRefused(ValueError):
    """A scoring request amber's rules forbid; names a code, never record contents."""

    def __init__(self, code: Literal["self_reference", "move_on_not_included"]) -> None:
        self.code = code
        super().__init__(code)


class Metric(BaseModel):
    """One metric as a ratio, kept as counts so reports can be pooled."""

    model_config = _FROZEN

    name: str = Field(min_length=1)
    kind: MetricKind
    numerator: float = Field(ge=0, allow_inf_nan=False)
    denominator: _Count
    variable: str | None = None

    @property
    def value(self) -> float | None:
        return None if self.denominator == 0 else self.numerator / self.denominator


class ScoreReport(BaseModel):
    """What a scorer measured, against which references, and how far to trust it."""

    model_config = _FROZEN

    scorer: str = Field(min_length=1)
    metrics: tuple[Metric, ...]
    example_ids: tuple[str, ...] = ()
    tier_mix: Mapping[ReferenceTier, _Count] = Field(default_factory=dict)
    provisional: bool
    inconclusive: tuple[str, ...] = ()

    @model_validator(mode="after")
    def require_consistent_report(self) -> ScoreReport:
        silver_or_single = any(
            tier is not ReferenceTier.adjudicated and count for tier, count in self.tier_mix.items()
        )
        if self.provisional != silver_or_single:
            raise ValueError("provisional exactly when a reference is below adjudicated")
        if set(self.inconclusive) & {metric.name for metric in self.metrics}:
            raise ValueError("an inconclusive metric has no value")
        return self


def tier_mix(references: Iterable[Example | LabelRecord]) -> dict[ReferenceTier, int]:
    """References counted per tier, one per (record, final variable)."""
    mix: dict[ReferenceTier, int] = {}
    for record in references:
        tiers = (
            [reference.tier for reference in record.references.values()]
            if isinstance(record, Example)
            else [ReferenceTier.silver]
        )
        for tier in tiers:
            mix[tier] = mix.get(tier, 0) + 1
    return mix


def _producers(claims: Iterable[Claim], readings: Iterable[MentionReading]) -> list[str]:
    producers = [claim.provenance.producer for claim in claims]
    for reading in readings:
        producers.append(reading.provenance.producer)
        producers.extend(
            attribute.assigned_by
            for attribute in reading.attributes.values()
            if attribute.assigned_by is not None
        )
    return producers


def configuration(record: CaseResult | Example | LabelRecord) -> frozenset[str]:
    """The ``model:``/``rule:`` references a record's labels came from, rule hash removed."""
    if isinstance(record, LabelRecord):
        return producer_configuration([record.producer])
    return producer_configuration(_producers(record.claims, record.mention_readings))


def refuse_self_reference(
    predicted: Sequence[CaseResult], references: Sequence[Example | LabelRecord]
) -> None:
    """Raise ``self_reference`` when a silver reference shares the predictions' configuration."""
    predicted_configuration = frozenset().union(*(configuration(result) for result in predicted))
    for record in references:
        silver = isinstance(record, LabelRecord) or any(
            reference.tier is ReferenceTier.silver for reference in record.references.values()
        )
        if silver and configuration(record) & predicted_configuration:
            raise ScoringRefused("self_reference")


def require_move_on(references: Sequence[Example | LabelRecord], move_on_event: str) -> None:
    """Under a move-on decision, every ``single_review`` snapshot includes that event."""
    for record in references:
        if not isinstance(record, Example):
            continue
        for reference in record.references.values():
            if reference.tier is ReferenceTier.single_review and (
                record.as_of_event < move_on_event
                or move_on_event not in reference.derived_from_events
            ):
                raise ScoringRefused("move_on_not_included")


def provisional(mix: Mapping[ReferenceTier, int]) -> bool:
    return any(tier is not ReferenceTier.adjudicated and count for tier, count in mix.items())
