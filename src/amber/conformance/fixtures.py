"""Deterministic synthetic fixtures for the conformance kit.

Every ID is fixed, so the fixture set has a stable ``fixtures_ref``. All text is invented and
exercises non-BMP characters, combining marks, and CRLF line endings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import cache
from typing import Any

from pydantic import Field
from ulid import ULID

from amber.cases import create_case_result
from amber.graph import EvidenceGraph
from amber.grounding import cite_mention, exact_mention, exact_quote
from amber.ids import canonical_sha256, schema_ref
from amber.schemas import (
    AnnotationEvent,
    AnswerModel,
    Case,
    CaseOutcome,
    CaseResult,
    Inclusion,
    Mention,
    Provenance,
    Sensitivity,
    Source,
    SourceKind,
    Variable,
    VariableOutcome,
    VariableSet,
    Zone,
)

TEXT = "Exam: mass \U0001f642 seen in the liver.\r\nNo e\u0301vidence of other mass."
DATASET = "amber-conformance-synthetic"
IMPORTER = "tool:amber-conformance-importer@1"
TEACHER = "model:amber-conformance-teacher@1"
REVIEWER_A = "human:conformance-reviewer-a"
REVIEWER_B = "human:conformance-reviewer-b"
ADJUDICATOR = "human:conformance-adjudicator"
_BASE = datetime(2026, 1, 1, tzinfo=UTC)


class FindingAnswer(AnswerModel):
    """Invented boolean answer for the conformance fixtures."""

    present: bool = Field(strict=True)


def fixed_ulid(tick: int, serial: int = 0) -> str:
    """A ULID at ``_BASE + tick`` milliseconds with a deterministic random part."""
    timestamp = int((_BASE + timedelta(milliseconds=tick)).timestamp() * 1000)
    return str(ULID.from_bytes(timestamp.to_bytes(6, "big") + serial.to_bytes(10, "big")))


RUN_IDS = (fixed_ulid(900_000), fixed_ulid(900_001))

SOURCE = Source.create(
    patient_id="conformance-patient-1",
    kind=SourceKind.note,
    external_id="conformance-note-1",
    datetime=None,
    text=TEXT,
    record=None,
)
VARIABLES = VariableSet.create(
    name="conformance_findings",
    version="1.0.0",
    variables=(
        Variable(
            name="finding",
            level="mention",
            answer_model=FindingAnswer,
            instructions="Invented: is the invented finding present at this mention?",
            mention_types=frozenset({"finding"}),
            final=True,
        ),
        Variable(
            name="summary",
            level="note",
            answer_model=FindingAnswer,
            instructions="Invented: is the invented finding present in the note?",
            inputs=frozenset({"finding"}),
            final=True,
        ),
    ),
)


def case(case_id: str = "conformance-case-1", **changes: Any) -> Case:
    fields: dict[str, Any] = {
        "case_id": case_id,
        "task": VARIABLES.name,
        "patient_id": SOURCE.patient_id,
        "source_ids": [SOURCE.source_id],
        "sensitivity": Sensitivity.synthetic,
        "dataset": DATASET,
        "split": "dev",
        **changes,
    }
    return Case(**fields)


def _mention() -> Mention:
    result = exact_mention(SOURCE, "mass", TEXT.index("mass"), "finding")
    assert isinstance(result, Mention)
    return result


def _sentence() -> Inclusion:
    result = exact_quote(SOURCE, "mass \U0001f642 seen in the liver.")
    assert isinstance(result, Inclusion)
    return result


MENTION = _mention()
(POSITION,) = cite_mention(SOURCE, MENTION)
SENTENCE = _sentence()
SPANS: tuple[Inclusion | Mention, ...] = (MENTION, POSITION, SENTENCE)


def provenance(producer: str, run_id: str | None = None) -> Provenance:
    return Provenance(
        producer=producer,
        run_id=run_id,
        destination="local:amber-conformance",
        policy_version="amber-conformance-policy@1",
        zone=Zone.local,
        sensitivity=Sensitivity.synthetic,
        created_at=_BASE,
        version="0.1",
    )


def result(run_id: str, case_id: str = "conformance-case-1") -> CaseResult:
    """A valid result with no claims: every final variable insufficient_evidence."""
    stamped = provenance("tool:amber-conformance-extractor@1", run_id)
    graph = EvidenceGraph(source=SOURCE, variables=VARIABLES, sensitivity=Sensitivity.synthetic)
    outcome = CaseOutcome(
        status="insufficient_evidence", reason="Synthetic run.", provenance=stamped
    )
    return create_case_result(
        case(case_id),
        graph=graph,
        run_id=run_id,
        variable_outcomes={
            name: VariableOutcome(variable=name, outcome=outcome) for name in ("finding", "summary")
        },
    )


@dataclass
class EventLog:
    """Builds an ordered event log with fixed IDs."""

    case_id: str = "conformance-case-1"
    events: list[AnnotationEvent] = field(default_factory=list)
    tick: int = 0
    serial: int = 0

    def _id(self) -> str:
        self.serial += 1
        return fixed_ulid(1_000_000 + self.serial, self.serial)

    def add(self, action: str, actor: str, role: str, **fields: Any) -> AnnotationEvent:
        self.tick += 1
        event = AnnotationEvent(
            event_id=fixed_ulid(self.tick),
            case_id=self.case_id,
            actor=actor,
            role=role,  # type: ignore[arg-type]
            round=fields.pop("round", 0),
            action=action,  # type: ignore[arg-type]
            at=_BASE + timedelta(milliseconds=self.tick),
            provenance=provenance(IMPORTER),
            **fields,
        )
        self.events.append(event)
        return event

    def reading(self) -> str:
        reading_id = self._id()
        self.add(
            "reading.create",
            IMPORTER,
            "importer",
            target_id=reading_id,
            after={
                "reading": {
                    "reading_id": reading_id,
                    "mention_id": MENTION.mention_id,
                    "provenance": provenance(TEACHER).model_dump(mode="json"),
                    "status": "proposed",
                }
            },
        )
        return reading_id

    def _claim(self, variable: str, producer: str, reading_id: str | None) -> dict[str, Any]:
        return {
            "claim_id": self._id(),
            "source_id": SOURCE.source_id,
            "patient_id": SOURCE.patient_id,
            "task": VARIABLES.name,
            "variable": variable,
            "reading_id": reading_id,
            "schema_ref": schema_ref(FindingAnswer),
            "value": {"present": True},
            "effective_datetime": None,
            "confidence": None,
            "status": "proposed",
            "provenance": provenance(producer).model_dump(mode="json"),
        }

    def finding(self, producer: str, actor: str, role: str, reading_id: str, **fields: Any) -> str:
        claim = self._claim("finding", producer, reading_id)
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
        claim = self._claim("summary", producer, None)
        inference = {
            "kind": "inference",
            "evidence_id": self._id(),
            "rationale": "Invented derivation.",
            "inputs": [finding_id, SENTENCE.evidence_id],
            "trace_id": None,
            "span_id": None,
            "provenance": provenance(producer).model_dump(mode="json"),
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
        producer = actor if role in ("reviewer", "adjudicator") else TEACHER
        self.add(
            "outcome.set",
            actor,
            role,
            variable=variable,
            after={
                "outcome": {
                    "status": "answered",
                    "provenance": provenance(producer).model_dump(mode="json"),
                },
                "final_claim_ids": final_ids,
            },
        )


def silver_import(case_id: str = "conformance-case-1") -> tuple[EventLog, dict[str, str]]:
    """A teacher model's result imported as silver references."""
    log = EventLog(case_id=case_id)
    log.add("import.result", IMPORTER, "importer", external_ref="conformance-run")
    log.add("mention.create", IMPORTER, "importer", target_id=MENTION.mention_id)
    ids = {"reading": log.reading()}
    ids["finding"] = log.finding(TEACHER, IMPORTER, "importer", ids["reading"])
    ids["summary"] = log.summary(TEACHER, IMPORTER, "importer", ids["finding"])
    log.outcome("finding", [ids["finding"]], IMPORTER, "importer")
    log.outcome("summary", [ids["summary"]], IMPORTER, "importer")
    for variable in ("finding", "summary"):
        log.add("tier.set", IMPORTER, "importer", variable=variable, after={"tier": "silver"})
    return log, ids


def raise_to_single_review(log: EventLog, ids: dict[str, str]) -> None:
    """One reviewer accepts the silver import unchanged."""
    log.add("review.submit", REVIEWER_A, "reviewer")
    log.add(
        "reading.set_status",
        REVIEWER_A,
        "reviewer",
        target_id=ids["reading"],
        after={"status": "verified"},
    )
    for variable in ("finding", "summary"):
        log.add(
            "tier.set", REVIEWER_A, "reviewer", variable=variable, after={"tier": "single_review"}
        )


def blind_streams(case_id: str = "conformance-case-1") -> tuple[EventLog, dict[str, Any]]:
    """Two blind reviewers annotate independently; nothing is adjudicated yet."""
    log = EventLog(case_id=case_id)
    log.add("import.result", IMPORTER, "importer")
    log.add("mention.create", IMPORTER, "importer", target_id=MENTION.mention_id)
    ids: dict[str, Any] = {"reading": log.reading()}
    for actor, stream in ((REVIEWER_A, "a"), (REVIEWER_B, "b")):
        finding = log.finding(actor, actor, "reviewer", ids["reading"], stream=stream)
        summary = log.summary(actor, actor, "reviewer", finding, stream=stream)
        log.add("review.submit", actor, "reviewer", stream=stream)
        ids[stream] = {"finding": finding, "summary": summary}
    return log, ids


def adjudicate(log: EventLog, ids: dict[str, Any]) -> None:
    """The adjudicator keeps stream a and rejects stream b."""
    log.add(
        "reading.set_status",
        ADJUDICATOR,
        "adjudicator",
        target_id=ids["reading"],
        after={"status": "verified"},
    )
    for key in ("summary", "finding"):
        log.add(
            "adjudication.resolve",
            ADJUDICATOR,
            "adjudicator",
            target_id=ids["b"][key],
            after={"status": "rejected"},
            reason="Synthetic: stream b not selected.",
        )
    log.outcome("finding", [ids["a"]["finding"]], ADJUDICATOR, "adjudicator")
    log.outcome("summary", [ids["a"]["summary"]], ADJUDICATOR, "adjudicator")
    log.add("adjudication.resolve", ADJUDICATOR, "adjudicator", after={"decision": "accepted"})
    for variable in ("finding", "summary"):
        log.add(
            "tier.set", ADJUDICATOR, "adjudicator", variable=variable, after={"tier": "adjudicated"}
        )


def adjudicated(case_id: str = "conformance-case-1") -> tuple[EventLog, dict[str, Any]]:
    log, ids = blind_streams(case_id)
    adjudicate(log, ids)
    return log, ids


@cache
def fixtures_ref() -> str:
    """SHA-256 of the fixture set: sources, variable set, cases, results, spans, and events."""
    silver, ids = silver_import()
    raise_to_single_review(silver, ids)
    full, _ = adjudicated()
    return canonical_sha256(
        {
            "source": SOURCE.model_dump(mode="json"),
            "variable_set_ref": VARIABLES.schema_ref,
            "case": case().model_dump(mode="json"),
            "results": [result(run_id).model_dump(mode="json") for run_id in RUN_IDS],
            "spans": [span.model_dump(mode="json") for span in SPANS],
            "events": {
                "single_review": [event.model_dump(mode="json") for event in silver.events],
                "adjudicated": [event.model_dump(mode="json") for event in full.events],
            },
        }
    )
