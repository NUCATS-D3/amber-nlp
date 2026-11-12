"""Review imports: receipts and turning reviewed event logs into Examples (contract v0.1
sections 4.7 and 6.2).

A ``ReviewBackend`` sends cases to a review service and imports what reviewers did as
annotation events. ``reviewed_example`` is the shared import rule: an event log becomes an
Example only when it replays to a complete projection whose every reference has a human tier.
Unresolved, excluded, unsubmitted, or silver-only cases yield no Example; their events are
kept. Any other invalid event fails the whole import.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator

from amber.annotations import AnnotationValidationError, example_from_projection, replay
from amber.examples import ExampleValidationError
from amber.graph_errors import GraphValidationError
from amber.grounding import exact_mention, exact_quote
from amber.policy import Decision
from amber.schemas import (
    AnnotationEvent,
    Case,
    Example,
    Inclusion,
    Mention,
    ReferenceTier,
    Source,
    VariableSet,
)

ReviewImportCode: TypeAlias = Literal[
    "receipt_mismatch",
    "invalid_batch",
    "invalid_spans",
    "source_unavailable",
    "foreign_provenance",
    "invalid_events",
]
_NOT_YET = frozenset({"incomplete_projection", "excluded_case", "unresolved_case"})
_HUMAN = frozenset({ReferenceTier.single_review, ReferenceTier.adjudicated})


class ReviewImportError(ValueError):
    """A review import that cannot be accepted; renders no record contents.

    ``detail`` carries the annotation validation code for ``invalid_events``.
    """

    def __init__(self, code: ReviewImportCode, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if detail is None else f"{code}: {detail}")


class ReviewBatchReceipt(BaseModel):
    """What was sent for review and under which decisions; ``labels_only``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    batch_id: str = Field(min_length=1)
    destination: str = Field(min_length=1)
    location: str = Field(min_length=1)
    variable_set_ref: str = Field(min_length=1)
    case_ids: tuple[str, ...]
    streams: tuple[str, ...]
    decisions: tuple[Decision, ...]

    @field_validator("case_ids", "streams")
    @classmethod
    def require_distinct(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or len(set(value)) != len(value) or any(not item.strip() for item in value):
            raise ValueError("needs distinct, non-blank entries")
        return value


def check_review_provenance(
    events: Iterable[AnnotationEvent], *, destination: str, policy_version: str
) -> None:
    """Every reviewed event is stamped with the review service and the batch's policy."""
    for event in events:
        if (
            event.provenance.destination != destination
            or event.provenance.policy_version != policy_version
        ):
            raise ReviewImportError("foreign_provenance")


def load_spans(rows: Iterable[Any], source: Source) -> tuple[Inclusion | Mention, ...]:
    """Re-mint span rows (JSON dumps of mentions and inclusions) against the authoritative
    source; a row that does not reproduce exactly raises ``ReviewImportError("invalid_spans")``.
    """
    spans: list[Inclusion | Mention] = []
    for row in rows:
        if not isinstance(row, Mapping) or row.get("source_id") != source.source_id:
            raise ReviewImportError("invalid_spans")
        try:
            if "mention_id" in row:
                fragments = [tuple(item) for item in row.get("fragments") or []]
                minted: object = exact_mention(
                    source, row["quote"], row["start"], row["mention_type"], fragments=fragments
                )
            else:
                minted = exact_quote(source, row["quote"], row["start"])
        except (KeyError, TypeError, ValueError):
            raise ReviewImportError("invalid_spans") from None
        if not isinstance(minted, (Inclusion, Mention)) or minted.model_dump(mode="json") != dict(
            row
        ):
            raise ReviewImportError("invalid_spans")
        spans.append(minted)
    return tuple(spans)


def reviewed_example(
    events: Sequence[AnnotationEvent],
    *,
    case: Case,
    source: Source,
    variables: VariableSet,
    spans: Iterable[Inclusion | Mention],
) -> Example | None:
    """The Example a reviewed case's events replay to, or None while it is not a reviewed
    reference (unsubmitted, unresolved, excluded, or still silver).

    Raises ``ReviewImportError("invalid_events")`` for events that do not replay, such as an
    adjudicated tier set while the case is unresolved.
    """
    if not events:
        return None
    try:
        projection = replay(events, case=case, source=source, variables=variables, spans=spans)
        example = example_from_projection(projection, case=case, source=source, variables=variables)
    except AnnotationValidationError as error:
        if error.code in _NOT_YET:
            return None
        raise ReviewImportError("invalid_events", error.code) from None
    except (ExampleValidationError, GraphValidationError) as error:
        raise ReviewImportError("invalid_events", error.code) from None
    if any(reference.tier not in _HUMAN for reference in example.references.values()):
        return None
    return example


__all__ = [
    "ReviewBatchReceipt",
    "ReviewImportCode",
    "ReviewImportError",
    "check_review_provenance",
    "load_spans",
    "reviewed_example",
]
