"""Append-only annotation events (contract v0.1 section 4.4).

Events are the record; current state is a projection rebuilt by replay (``amber.annotations``).
``before`` and ``after`` carry no quotes or offsets: spans are referenced by mention and
inclusion IDs that resolve against records the caller supplies. They may still carry captured
values and free text, so an event log is ``derived_text``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from amber.ids import parse_producer, validate_ulid
from amber.schemas._immutable import freeze_json
from amber.schemas._wire import SchemaVersion, Timestamp
from amber.schemas.provenance import Provenance

_Text = Annotated[str, Field(strict=True, min_length=1)]

EventRole: TypeAlias = Literal["reviewer", "adjudicator", "importer", "system"]
EventAction: TypeAlias = Literal[
    "mention.create",
    "reading.create",
    "reading.update",
    "reading.set_status",
    "claim.create",
    "claim.update",
    "claim.set_status",
    "evidence.cite",
    "edge.add",
    "edge.remove",
    "outcome.set",
    "review.open",
    "review.submit",
    "review.reopen",
    "adjudication.resolve",
    "import.result",
    "tier.set",
]
HUMAN_ROLES = frozenset({"reviewer", "adjudicator"})


def role_binding_error(actor: str, role: str, *, in_stream: bool) -> str | None:
    """Why ``actor`` cannot act as ``role`` (in a blind stream when ``in_stream``), or None.

    One rule for events and for blind-review viewers (``amber.annotations.Viewer``): reviewers
    and adjudicators are people, importers and system actors are not, and only reviewers work
    in blind streams. ``actor`` must already parse as a producer.
    """
    kind = parse_producer(actor).kind
    if role in HUMAN_ROLES and kind != "human":
        return "reviewers and adjudicators are human: producers"
    if role not in HUMAN_ROLES and kind == "human":
        return "importer and system events are not made by people"
    if in_stream and role != "reviewer":
        return "only reviewer events belong to a blind stream"
    return None


def _nonblank(value: str | None) -> str | None:
    if value is not None and not value.strip():
        raise ValueError("value must not be blank")
    return value


class AnnotationEvent(BaseModel):
    """One append-only change to a case's annotations; ULID IDs order events in time."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: Annotated[str, Field(strict=True)]
    schema_version: SchemaVersion = "amber/0.1"
    case_id: _Text
    variable: _Text | None = None
    actor: _Text
    role: EventRole
    round: int = Field(strict=True, ge=0)
    stream: _Text | None = None
    action: EventAction
    target_id: _Text | None = None
    before: Mapping[str, Any] | None = None
    after: Mapping[str, Any] | None = None
    reason: _Text | None = None
    at: Timestamp
    provenance: Provenance
    external_ref: _Text | None = None

    @field_validator("event_id")
    @classmethod
    def require_ulid(cls, value: str) -> str:
        return validate_ulid(value)

    @field_validator("case_id", "variable", "stream", "target_id", "reason", "external_ref")
    @classmethod
    def require_nonblank(cls, value: str | None) -> str | None:
        return _nonblank(value)

    @field_validator("before", "after")
    @classmethod
    def freeze_state(cls, value: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
        return None if value is None else freeze_json(dict(value))  # type: ignore[no-any-return]

    @field_validator("provenance", mode="before")
    @classmethod
    def revalidate_provenance(cls, value: Any) -> Provenance:
        if isinstance(value, Provenance):
            value = value.model_dump(warnings=False)
        return Provenance.model_validate(value)

    @model_validator(mode="after")
    def require_consistent_event(self) -> AnnotationEvent:
        problem = role_binding_error(self.actor, self.role, in_stream=self.stream is not None)
        if problem is not None:
            raise ValueError(problem)
        if self.at.utcoffset() is None:
            raise ValueError("event time must be timezone-aware")
        return self
