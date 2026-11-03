"""Labelers produce tiered references (contract v0.1 sections 4.2 and 6.2).

A model, rule, or importer labeler may only produce ``silver``; human tiers come from review.
An Example always arrives with the events it replays from, beginning with ``import.result``;
``import_events`` builds that log from a teacher's validated ``CaseResult`` and returns the
Example the log replays to. A ``LabelRecord`` needs no events.
"""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict
from ulid import ULID

from amber.annotations import example_from_projection, replay
from amber.policy import RunContext
from amber.schemas import (
    AnnotationEvent,
    Case,
    CaseResult,
    Example,
    Inclusion,
    InferenceEvidence,
    LabelRecord,
    Mention,
    Provenance,
    ReferenceTier,
    Source,
    VariableSet,
)


class LabelerFailure(BaseModel):
    """A labeler that could not produce a reference for a case; names no record contents."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reason: Literal["missing", "invalid", "failed_result", "policy", "unsupported"]


LabelerOutput: TypeAlias = tuple[Sequence[AnnotationEvent], Example] | LabelRecord | LabelerFailure


class LabelerRejected(ValueError):
    """A labeler that may not be registered."""

    def __init__(self, code: Literal["human_tier", "not_a_labeler"]) -> None:
        self.code = code
        super().__init__(code)


def admit_labeler(labeler: object) -> object:
    """Accept a labeler for registration only when it declares ``silver``.

    Human tiers come from review imports, never from a model, rule, or importer.
    """
    tier = getattr(labeler, "tier", None)
    if not callable(getattr(labeler, "label", None)) or tier is None:
        raise LabelerRejected("not_a_labeler")
    if ReferenceTier(tier) is not ReferenceTier.silver:
        raise LabelerRejected("human_tier")
    return labeler


class _Ids:
    """Strictly increasing ULIDs from one millisecond base and a random starting serial."""

    def __init__(self, now: datetime) -> None:
        self._millis = int(now.timestamp() * 1000)
        self._serial = secrets.randbits(64)

    def next(self) -> str:
        self._serial += 1
        return str(
            ULID.from_bytes(self._millis.to_bytes(6, "big") + self._serial.to_bytes(10, "big"))
        )


def _claim_order(result: CaseResult) -> list[str]:
    """Live claims ordered so every claim comes after the claims its support consumes."""
    live = {claim.claim_id for claim in result.claims if claim.status != "rejected"}
    evidence = {item.evidence_id: item for item in result.evidence}
    children: dict[str, list[str]] = {}
    for edge in result.edges:
        children.setdefault(edge.claim_id, []).append(edge.evidence_id)
    ordered: list[str] = []
    done: set[str] = set()

    def consumed(claim_id: str) -> set[str]:
        found: set[str] = set()
        pending = list(children.get(claim_id, ()))
        seen: set[str] = set()
        while pending:
            node = pending.pop()
            if node in seen:
                continue
            seen.add(node)
            record = evidence.get(node)
            if isinstance(record, InferenceEvidence):
                for item in record.inputs:
                    if item in live:
                        found.add(item)
                    else:
                        pending.append(item)
        return found

    def visit(claim_id: str) -> None:
        if claim_id in done:
            return
        done.add(claim_id)
        for dependency in sorted(consumed(claim_id)):
            visit(dependency)
        ordered.append(claim_id)

    for claim_id in sorted(live):
        visit(claim_id)
    return ordered


def import_events(
    result: CaseResult,
    *,
    case: Case,
    source: Source,
    variables: VariableSet,
    context: RunContext,
    actor: str,
    provenance: Provenance,
    now: datetime,
    external_ref: str | None = None,
) -> tuple[tuple[AnnotationEvent, ...], tuple[Inclusion | Mention, ...], Example]:
    """Turn a teacher's validated result into a silver import log, its spans, and its Example.

    The log is ``import.result``; ``mention.create`` and ``reading.create`` for each live
    reading; one ``claim.create`` per live claim (with the inferences, inclusions, and edges it
    rests on, in dependency order); ``outcome.set`` and ``tier.set`` silver per final variable.
    ``actor`` is the importer's producer reference and ``provenance`` stamps every event. A
    failed variable has no reference, so a result with one raises ``ValueError``.
    """
    if any(item.outcome.status == "failed" for item in result.variable_outcomes.values()):
        raise ValueError("failed results are diagnostics, never references")
    ids = _Ids(now)
    events: list[AnnotationEvent] = []
    tick = 0

    def add(action: str, **fields: object) -> None:
        nonlocal tick
        tick += 1
        events.append(
            AnnotationEvent.model_validate(
                {
                    "event_id": ids.next(),
                    "case_id": case.case_id,
                    "actor": actor,
                    "role": "importer",
                    "round": 0,
                    "action": action,
                    "at": now + timedelta(microseconds=tick),
                    "provenance": provenance,
                    **fields,
                }
            )
        )

    add("import.result", external_ref=external_ref or result.run_id)
    readings = [reading for reading in result.mention_readings if reading.status != "rejected"]
    for mention in result.mentions:
        mine = [reading for reading in readings if reading.mention_id == mention.mention_id]
        if not mine:
            continue
        add("mention.create", target_id=mention.mention_id)
        for reading in mine:
            add(
                "reading.create",
                target_id=reading.reading_id,
                after={"reading": reading.model_dump(mode="json")},
            )

    claims = {claim.claim_id: claim for claim in result.claims}
    evidence = {item.evidence_id: item for item in result.evidence}
    emitted: set[str] = set()
    for claim_id in _claim_order(result):
        edges = [edge for edge in result.edges if edge.claim_id == claim_id]
        inclusions: list[str] = []
        inferences: list[dict[str, object]] = []
        pending = [edge.evidence_id for edge in edges]
        seen: set[str] = set()
        while pending:
            node = pending.pop()
            if node in seen or node in claims:
                continue
            seen.add(node)
            record = evidence[node]
            if isinstance(record, Inclusion):
                inclusions.append(node)
            elif node not in emitted:
                emitted.add(node)
                inferences.append(record.model_dump(mode="json"))
                pending.extend(record.inputs)
        add(
            "claim.create",
            target_id=claim_id,
            after={
                "claim": claims[claim_id].model_dump(mode="json"),
                "inclusions": sorted(set(inclusions)),
                "inferences": list(reversed(inferences)),
                "edges": [edge.model_dump(mode="json") for edge in edges],
            },
        )
    for name, item in sorted(result.variable_outcomes.items()):
        add(
            "outcome.set",
            variable=name,
            after={
                "outcome": item.outcome.model_dump(mode="json"),
                "final_claim_ids": list(item.final_claim_ids),
            },
        )
    for name in sorted(result.variable_outcomes):
        add("tier.set", variable=name, after={"tier": "silver"})

    spans: tuple[Inclusion | Mention, ...] = (
        *result.mentions,
        *(item for item in result.evidence if isinstance(item, Inclusion)),
    )
    projection = replay(events, case=case, source=source, variables=variables, spans=spans)
    example = example_from_projection(projection, case=case, source=source, variables=variables)
    return tuple(events), spans, example


def spans_of(example: Example) -> tuple[Inclusion | Mention, ...]:
    """The mentions and inclusions an Example's events may name."""
    return (*example.mentions, *(item for item in example.evidence if isinstance(item, Inclusion)))


__all__ = [
    "LabelerFailure",
    "LabelerOutput",
    "LabelerRejected",
    "admit_labeler",
    "import_events",
    "spans_of",
]
