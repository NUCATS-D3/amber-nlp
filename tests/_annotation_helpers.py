"""Invented event-log scenarios shared by annotation and store tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import Field
from ulid import ULID

from amber.ids import new_ulid, schema_ref
from amber.schemas import (
    AnnotationEvent,
    AnswerModel,
    Case,
    Inclusion,
    Mention,
    Provenance,
    Sensitivity,
    Source,
    SourceKind,
    Variable,
    VariableSet,
)
from amber.tools.mention import cite, mention
from amber.tools.quote import quote

from ._claim_helpers import make_provenance

TEXT = "Exam: mass \U0001f642 seen in the liver.\r\nNo other mass."
IMPORTER = "tool:synthetic-importer@1"
TEACHER = "model:synthetic-teacher@1"
SYSTEM = "tool:synthetic-protocol@1"
A = "human:synthetic-reviewer-a"
B = "human:synthetic-reviewer-b"
ADJ = "human:synthetic-adjudicator"
BASE = datetime(2026, 11, 2, 9, tzinfo=UTC)


class FindingAnswer(AnswerModel):
    present: bool = Field(strict=True)


SOURCE = Source.create(
    patient_id="synthetic-patient-1",
    kind=SourceKind.note,
    external_id="synthetic-note-1",
    datetime=None,
    text=TEXT,
    record=None,
)
SOURCES = {SOURCE.source_id: SOURCE}
VARIABLES = VariableSet.create(
    name="synthetic_findings",
    version="1.0.0",
    variables=(
        Variable(
            name="finding",
            level="mention",
            answer_model=FindingAnswer,
            instructions="Invented.",
            mention_types=frozenset({"finding"}),
            final=True,
        ),
        Variable(
            name="summary",
            level="note",
            answer_model=FindingAnswer,
            instructions="Invented.",
            inputs=frozenset({"finding"}),
            final=True,
        ),
    ),
)
CASE = Case(
    case_id="synthetic-case-1",
    task=VARIABLES.name,
    patient_id=SOURCE.patient_id,
    source_ids=[SOURCE.source_id],
    sensitivity=Sensitivity.synthetic,
    split="dev",
)
_MENTION = mention(SOURCE.source_id, "mass", TEXT.index("mass"), "finding", sources=SOURCES)
assert isinstance(_MENTION, Mention)
MENTION: Mention = _MENTION
(POSITION,) = cite(MENTION, sources=SOURCES)
_SENTENCE = quote(SOURCE.source_id, "mass \U0001f642 seen in the liver.", None, sources=SOURCES)
assert isinstance(_SENTENCE, Inclusion)
SENTENCE: Inclusion = _SENTENCE
SPANS = (MENTION, POSITION, SENTENCE)
SCHEMA_REF = schema_ref(FindingAnswer)


def _provenance(producer: str) -> dict[str, Any]:
    return Provenance.model_validate(
        {**make_provenance().model_dump(), "producer": producer}
    ).model_dump(mode="json")


class Log:
    """Builds a strictly ordered, invented event log."""

    def __init__(self) -> None:
        self.events: list[AnnotationEvent] = []
        self.tick = 0

    def add(self, action: str, actor: str, role: str, **fields: Any) -> AnnotationEvent:
        self.tick += 1
        at = BASE + timedelta(milliseconds=self.tick)
        event = AnnotationEvent(
            event_id=str(ULID.from_datetime(at)),
            case_id=CASE.case_id,
            actor=actor,
            role=role,  # type: ignore[arg-type]
            round=fields.pop("round", 0),
            action=action,  # type: ignore[arg-type]
            at=at,
            provenance=make_provenance(),
            **fields,
        )
        self.events.append(event)
        return event

    def reading(
        self, producer: str = TEACHER, actor: str = IMPORTER, role: str = "importer"
    ) -> str:
        reading_id = new_ulid()
        self.add(
            "reading.create",
            actor,
            role,
            target_id=reading_id,
            after={
                "reading": {
                    "reading_id": reading_id,
                    "mention_id": MENTION.mention_id,
                    "provenance": _provenance(producer),
                    "status": "proposed",
                }
            },
        )
        return reading_id

    def finding(self, producer: str, actor: str, role: str, reading_id: str, **fields: Any) -> str:
        claim = _claim("finding", producer, reading_id)
        self.add(
            "claim.create",
            actor,
            role,
            target_id=claim["claim_id"],
            after={
                "claim": claim,
                "inclusions": [POSITION.evidence_id],
                "edges": [{"claim_id": claim["claim_id"], "evidence_id": POSITION.evidence_id}],
            },
            **fields,
        )
        return str(claim["claim_id"])

    def summary(self, producer: str, actor: str, role: str, finding_id: str, **fields: Any) -> str:
        claim = _claim("summary", producer)
        inference = {
            "kind": "inference",
            "evidence_id": new_ulid(),
            "rationale": "Invented derivation.",
            "inputs": [finding_id, SENTENCE.evidence_id],
            "trace_id": None,
            "span_id": None,
            "provenance": _provenance(producer),
        }
        self.add(
            "claim.create",
            actor,
            role,
            target_id=claim["claim_id"],
            after={
                "claim": claim,
                "inclusions": [SENTENCE.evidence_id],
                "inferences": [inference],
                "edges": [{"claim_id": claim["claim_id"], "evidence_id": inference["evidence_id"]}],
            },
            **fields,
        )
        return str(claim["claim_id"])

    def outcome(self, variable: str, final_ids: list[str], actor: str, role: str) -> None:
        self.add(
            "outcome.set",
            actor,
            role,
            variable=variable,
            after={
                "outcome": {
                    "status": "answered",
                    "provenance": _provenance(actor if actor.startswith("human:") else TEACHER),
                },
                "final_claim_ids": final_ids,
            },
        )


def _claim(variable: str, producer: str, reading_id: str | None = None) -> dict[str, Any]:
    return {
        "claim_id": new_ulid(),
        "source_id": SOURCE.source_id,
        "patient_id": SOURCE.patient_id,
        "task": VARIABLES.name,
        "variable": variable,
        "reading_id": reading_id,
        "schema_ref": SCHEMA_REF,
        "value": {"present": True},
        "effective_datetime": None,
        "confidence": None,
        "status": "proposed",
        "provenance": _provenance(producer),
    }


# Scenarios ---------------------------------------------------------------------------------


def _import(log: Log) -> dict[str, str]:
    log.add("import.result", IMPORTER, "importer", external_ref="synthetic-run-1")
    log.add("mention.create", IMPORTER, "importer", target_id=MENTION.mention_id)
    reading = log.reading()
    return {"reading": reading}


def _stream(log: Log, actor: str, stream: str, reading: str) -> dict[str, str]:
    finding = log.finding(actor, actor, "reviewer", reading, stream=stream)
    summary = log.summary(actor, actor, "reviewer", finding, stream=stream)
    log.add("review.submit", actor, "reviewer", stream=stream)
    return {"finding": finding, "summary": summary}


def _adjudicated(decisions: tuple[str, ...] = ("accepted",)) -> tuple[Log, dict[str, Any]]:
    """Two blind streams and an adjudicator who keeps stream a (see ``_adjudicate``)."""
    log = Log()
    ids: dict[str, Any] = _import(log)
    for actor, stream in [(A, "a"), (B, "b")]:
        ids[stream] = _stream(log, actor, stream, ids["reading"])
    _adjudicate(log, ids, decisions)
    return log, ids


def _adjudicate(log: Log, ids: dict[str, Any], decisions: tuple[str, ...] = ("accepted",)) -> None:
    """The adjudicator keeps stream a; ``decisions`` are the untargeted resolves, in order,
    before the tiers are set."""
    log.add(
        "reading.set_status",
        ADJ,
        "adjudicator",
        target_id=ids["reading"],
        after={"status": "verified"},
    )
    for key in ("summary", "finding"):
        log.add(
            "adjudication.resolve",
            ADJ,
            "adjudicator",
            target_id=ids["b"][key],
            after={"status": "rejected"},
            reason="Synthetic: stream b not selected.",
        )
    log.outcome("finding", [ids["a"]["finding"]], ADJ, "adjudicator")
    log.outcome("summary", [ids["a"]["summary"]], ADJ, "adjudicator")
    for decision in decisions:
        ids["resolve"] = log.add(
            "adjudication.resolve", ADJ, "adjudicator", after={"decision": decision},
            reason=None if decision == "accepted" else "Synthetic: reviewers disagree.",
        )  # fmt: skip
    for variable in ("finding", "summary"):
        log.add("tier.set", ADJ, "adjudicator", variable=variable, after={"tier": "adjudicated"})


def _silver() -> tuple[Log, dict[str, str]]:
    log = Log()
    ids = _import(log)
    ids["finding"] = log.finding(TEACHER, IMPORTER, "importer", ids["reading"])
    ids["summary"] = log.summary(TEACHER, IMPORTER, "importer", ids["finding"])
    log.outcome("finding", [ids["finding"]], IMPORTER, "importer")
    log.outcome("summary", [ids["summary"]], IMPORTER, "importer")
    for variable in ("finding", "summary"):
        log.add("tier.set", IMPORTER, "importer", variable=variable, after={"tier": "silver"})
    return log, ids


def _raise_to_single_review(log: Log, ids: dict[str, str]) -> None:
    log.add("review.submit", A, "reviewer")
    log.add(
        "reading.set_status", A, "reviewer", target_id=ids["reading"], after={"status": "verified"}
    )
    for variable in ("finding", "summary"):
        log.add("tier.set", A, "reviewer", variable=variable, after={"tier": "single_review"})
