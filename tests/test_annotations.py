"""Adversarial tests for annotation events, replay, visibility, transitions, and Examples from
projections (contract v0.1 sections 4.4, 4.5, 4.10; catalog ANN-04, ANN-06, ANN-07, ANN-09,
ANN-10, ANN-15, ANN-18)."""

from __future__ import annotations

import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import Field, ValidationError
from ulid import ULID

from amber.annotations import (
    AnnotationValidationError,
    Projection,
    example_from_projection,
    replay,
    verify_example,
)
from amber.examples import ExampleValidationError, load_example
from amber.graph_errors import GraphValidationError
from amber.ids import new_ulid, schema_ref
from amber.schemas import (
    AnnotationEvent,
    AnswerModel,
    Case,
    Example,
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


def _replay(events: list[AnnotationEvent], **kwargs: Any) -> Projection:
    return replay(events, case=CASE, source=SOURCE, variables=VARIABLES, spans=SPANS, **kwargs)


def _example(events: list[AnnotationEvent], **kwargs: Any) -> Example:
    return example_from_projection(
        _replay(events, **kwargs), case=CASE, source=SOURCE, variables=VARIABLES
    )


def _assert_code(code: str, function: Callable[..., Any], /, *args: Any, **kwargs: Any) -> None:
    with pytest.raises(
        (AnnotationValidationError, ExampleValidationError, GraphValidationError)
    ) as failure:
        function(*args, **kwargs)
    assert failure.value.code == code  # type: ignore[union-attr]
    assert str(failure.value) == code


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


def _adjudicated() -> tuple[Log, dict[str, Any]]:
    """Two blind streams and an adjudicator who keeps stream a."""
    log = Log()
    ids: dict[str, Any] = _import(log)
    for actor, stream in [(A, "a"), (B, "b")]:
        ids[stream] = _stream(log, actor, stream, ids["reading"])
    _adjudicate(log, ids)
    return log, ids


def _adjudicate(log: Log, ids: dict[str, Any]) -> None:
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
    ids["resolve"] = log.add(
        "adjudication.resolve", ADJ, "adjudicator", after={"decision": "accepted"}
    )
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


# Examples from events ----------------------------------------------------------------------


def test_an_adjudicated_example_is_replayed_from_blind_streams() -> None:
    log, ids = _adjudicated()
    example = _example(log.events)

    references = example.references
    assert {name: ref.tier for name, ref in references.items()} == {
        "finding": "adjudicated",
        "summary": "adjudicated",
    }
    assert references["summary"].annotators == (A, B)
    assert references["summary"].adjudicator == ADJ
    assert ids["resolve"].event_id in references["summary"].derived_from_events
    assert {claim.claim_id: claim.status for claim in example.claims} == {
        ids["a"]["finding"]: "gold",
        ids["a"]["summary"]: "gold",
    }
    assert [reading.status for reading in example.mention_readings] == ["verified"]
    assert example.split == "dev"
    assert example.as_of_event == log.events[-1].event_id
    assert example.created_at == log.events[-1].at
    assert (
        verify_example(
            example, log.events, case=CASE, source=SOURCE, variables=VARIABLES, spans=SPANS
        )
        == example
    )


def test_a_silver_import_is_an_example_with_proposed_claims() -> None:
    log, _ = _silver()
    example = _example(log.events)
    assert {ref.tier for ref in example.references.values()} == {"silver"}
    assert example.references["finding"].annotators == (TEACHER,)
    assert {claim.status for claim in example.claims} == {"proposed"}


def test_projections_name_the_missing_tier_or_outcome() -> None:
    log, _ = _silver()
    _assert_code("incomplete_projection", _example, log.events[:-1])
    _assert_code("incomplete_projection", _example, log.events[:-4])


def test_examples_come_only_from_complete_views() -> None:
    log, _ = _adjudicated()
    projection = _replay(log.events, actor=A)
    _assert_code(
        "invalid_context",
        example_from_projection,
        projection,
        case=CASE,
        source=SOURCE,
        variables=VARIABLES,
    )


# ANN-06: determinism and prefixes ----------------------------------------------------------


@pytest.mark.parametrize("seed", range(5))
def test_replay_is_deterministic_and_prefixes_give_prefix_projections(seed: int) -> None:
    rng = random.Random(seed)
    log = Log()
    ids: dict[str, Any] = _import(log)
    streams: dict[str, list[Callable[[], None]]] = {}
    created: dict[str, dict[str, str]] = {"a": {}, "b": {}}

    def steps(actor: str, stream: str) -> list[Callable[[], None]]:
        def finding() -> None:
            created[stream]["finding"] = log.finding(
                actor, actor, "reviewer", ids["reading"], stream=stream
            )

        def summary() -> None:
            created[stream]["summary"] = log.summary(
                actor, actor, "reviewer", created[stream]["finding"], stream=stream
            )

        def submit() -> None:
            log.add("review.submit", actor, "reviewer", stream=stream)

        return [finding, summary, submit]

    streams = {"a": steps(A, "a"), "b": steps(B, "b")}
    while streams["a"] or streams["b"]:
        stream = rng.choice([key for key, pending in streams.items() if pending])
        streams[stream].pop(0)()
    ids.update(created)
    _adjudicate(log, ids)

    full = _replay(log.events)
    assert _replay(list(log.events)) == full
    assert _example(log.events) == _example(log.events)
    for index, event in enumerate(log.events):
        assert _replay(log.events, as_of_event=event.event_id) == _replay(log.events[: index + 1])


def test_events_must_be_ordered_and_belong_to_the_case() -> None:
    log, _ = _silver()
    events = list(log.events)
    _assert_code("event_order", _replay, [events[1], events[0], *events[2:]])
    _assert_code("event_order", _replay, [events[0], *events])
    other = events[0].model_copy(update={"case_id": "other"})
    _assert_code("invalid_context", _replay, [other, *events[1:]])
    phi = events[0].model_dump()
    phi["provenance"] = {**phi["provenance"], "sensitivity": "phi"}
    _assert_code("invalid_context", _replay, [phi, *events[1:]])
    _assert_code("unknown_reference", _replay, events, as_of_event=new_ulid())


def test_spans_resolve_only_from_supplied_records() -> None:
    log, _ = _silver()
    _assert_code(
        "unknown_reference",
        replay,
        log.events,
        case=CASE,
        source=SOURCE,
        variables=VARIABLES,
        spans=(MENTION,),
    )


# ANN-07: blind streams ---------------------------------------------------------------------


def test_reviewers_see_only_their_stream_until_resolve() -> None:
    log = Log()
    ids: dict[str, Any] = _import(log)
    ids["a"] = _stream(log, A, "a", ids["reading"])
    ids["b"] = _stream(log, B, "b", ids["reading"])

    as_a = _replay(log.events, actor=A)
    assert set(as_a.claims) == set(ids["a"].values())
    assert ids["reading"] in as_a.readings
    assert set(_replay(log.events, actor=B).claims) == set(ids["b"].values())
    assert set(_replay(log.events, actor="human:someone-else").claims) == set()
    everything = set(ids["a"].values()) | set(ids["b"].values())
    assert set(_replay(log.events, actor=IMPORTER).claims) == everything

    log.add(
        "reading.set_status",
        ADJ,
        "adjudicator",
        target_id=ids["reading"],
        after={"status": "verified"},
    )
    assert set(_replay(log.events, actor=ADJ).claims) == everything
    assert set(_replay(log.events, actor=A).claims) == set(ids["a"].values())

    log.add("adjudication.resolve", ADJ, "adjudicator")
    assert set(_replay(log.events, actor=A).claims) == everything
    assert set(_replay(log.events, actor=B).claims) == everything


def test_resolve_opens_only_its_own_round() -> None:
    log = Log()
    ids: dict[str, Any] = _import(log)
    _stream(log, A, "a", ids["reading"])
    log.add("adjudication.resolve", ADJ, "adjudicator", round=1)
    later = log.finding(B, B, "reviewer", ids["reading"], stream="b", round=1)
    early_b = log.finding(B, B, "reviewer", ids["reading"], stream="b2")
    visible = set(_replay(log.events, actor=A).claims)
    assert later in visible
    assert early_b not in visible


# Transitions (ANN-09, ANN-10) --------------------------------------------------------------


def _single_finding() -> tuple[Log, dict[str, str]]:
    log = Log()
    ids = _import(log)
    ids["finding"] = log.finding(A, A, "reviewer", ids["reading"])
    return log, ids


def test_a_correction_is_a_new_claim_and_supersedes_the_old() -> None:
    log, ids = _single_finding()
    corrected = _claim("finding", A, ids["reading"])
    corrected["value"] = {"present": False}
    log.add(
        "claim.update",
        A,
        "reviewer",
        target_id=ids["finding"],
        after={
            "claim": corrected,
            "inclusions": [POSITION.evidence_id],
            "edges": [{"claim_id": corrected["claim_id"], "evidence_id": POSITION.evidence_id}],
        },
    )
    claims = _replay(log.events).claims
    assert claims[ids["finding"]].status == "rejected"
    assert claims[corrected["claim_id"]].status == "proposed"
    assert claims[corrected["claim_id"]].value == {"present": False}


def test_a_claim_is_never_edited_in_place() -> None:
    log, ids = _single_finding()
    same = _claim("finding", A, ids["reading"])
    same["claim_id"] = ids["finding"]
    log.add(
        "claim.update",
        A,
        "reviewer",
        target_id=ids["finding"],
        after={
            "claim": same,
            "edges": [{"claim_id": ids["finding"], "evidence_id": POSITION.evidence_id}],
        },
    )
    _assert_code("invalid_transition", _replay, log.events)


def test_correcting_gold_keeps_the_gold_claim_until_adjudication() -> None:
    log, ids = _silver()
    _raise_to_single_review(log, ids)
    corrected = _claim("finding", A, ids["reading"])
    log.add(
        "claim.update",
        A,
        "reviewer",
        target_id=ids["finding"],
        after={
            "claim": corrected,
            "edges": [{"claim_id": corrected["claim_id"], "evidence_id": POSITION.evidence_id}],
        },
    )
    claims = _replay(log.events).claims
    assert claims[ids["finding"]].status == "gold"
    assert claims[corrected["claim_id"]].status == "proposed"
    log.add(
        "adjudication.resolve",
        ADJ,
        "adjudicator",
        target_id=ids["finding"],
        after={"status": "rejected"},
    )
    _assert_code("invalid_transition", _replay, log.events)
    # With a reason the transition is permitted, but the gold summary still relies on it.
    log.events[-1] = log.events[-1].model_copy(update={"reason": "Synthetic correction."})
    _assert_code("invalid_context", _replay, log.events)


@pytest.mark.parametrize(
    ("action", "role", "actor", "status", "code"),
    [
        ("claim.set_status", "importer", IMPORTER, "verified", "invalid_role"),
        ("claim.set_status", "reviewer", A, "gold", "invalid_transition"),
        ("claim.set_status", "reviewer", A, "proposed", "invalid_transition"),
        ("adjudication.resolve", "reviewer", A, "gold", "invalid_role"),
    ],
)
def test_status_changes_follow_the_transition_table(
    action: str, role: str, actor: str, status: str, code: str
) -> None:
    log, ids = _single_finding()
    log.add(action, actor, role, target_id=ids["finding"], after={"status": status})
    _assert_code(code, _replay, log.events)


def test_verified_then_rejected_is_not_a_reviewer_transition() -> None:
    log, ids = _single_finding()
    log.add(
        "claim.set_status", A, "reviewer", target_id=ids["finding"], after={"status": "verified"}
    )
    assert _replay(log.events).claims[ids["finding"]].status == "verified"
    log.add(
        "claim.set_status", A, "reviewer", target_id=ids["finding"], after={"status": "rejected"}
    )
    _assert_code("invalid_transition", _replay, log.events)


def test_gold_requires_reviewed_readings() -> None:
    log, ids = _single_finding()
    log.add(
        "adjudication.resolve",
        ADJ,
        "adjudicator",
        target_id=ids["finding"],
        after={"status": "gold"},
    )
    _assert_code("invalid_transition", _replay, log.events)

    log, ids = _silver()
    log.add("tier.set", A, "reviewer", variable="finding", after={"tier": "single_review"})
    _assert_code("invalid_transition", _replay, log.events)


def test_a_reading_correction_is_a_new_reading_of_the_same_span() -> None:
    log, ids = _single_finding()
    new_id = new_ulid()
    corrected = {
        "reading_id": new_id,
        "mention_id": MENTION.mention_id,
        "label": "corrected",
        "provenance": _provenance(A),
        "status": "proposed",
    }
    log.add("reading.update", A, "reviewer", target_id=ids["reading"], after={"reading": corrected})
    _assert_code("invalid_context", _replay, log.events)  # the claim still relies on the old one

    log = Log()
    ids = _import(log)
    log.add("reading.update", A, "reviewer", target_id=ids["reading"], after={"reading": corrected})
    readings = _replay(log.events).readings
    assert readings[ids["reading"]].status == "rejected"
    assert readings[new_id].label == "corrected"
    assert list(_replay(log.events).mentions) == [MENTION.mention_id]

    other = mention(SOURCE.source_id, "mass", TEXT.rindex("mass"), "finding", sources=SOURCES)
    assert isinstance(other, Mention)
    moved = {**corrected, "reading_id": new_ulid(), "mention_id": other.mention_id}
    log.add("mention.create", A, "reviewer", target_id=other.mention_id)
    log.add("reading.update", A, "reviewer", target_id=new_id, after={"reading": moved})
    _assert_code(
        "invalid_transition",
        replay,
        log.events,
        case=CASE,
        source=SOURCE,
        variables=VARIABLES,
        spans=(*SPANS, other),
    )


def test_human_records_carry_the_actor_and_start_proposed() -> None:
    log = Log()
    ids = _import(log)
    log.finding(B, A, "reviewer", ids["reading"])
    _assert_code("invalid_role", _replay, log.events)

    log = Log()
    ids = _import(log)
    claim = _claim("finding", A, ids["reading"])
    claim["status"] = "verified"
    log.add(
        "claim.create",
        A,
        "reviewer",
        after={
            "claim": claim,
            "edges": [{"claim_id": claim["claim_id"], "evidence_id": POSITION.evidence_id}],
        },
    )
    _assert_code("invalid_transition", _replay, log.events)


# ANN-04 and ANN-15: tiers and snapshots ----------------------------------------------------


def test_a_tier_rises_only_through_new_events_and_old_snapshots_stay_reproducible() -> None:
    log, ids = _silver()
    silver = _example(log.events)
    silver_events = list(log.events)
    _raise_to_single_review(log, ids)
    single = _example(log.events)

    assert {ref.tier for ref in single.references.values()} == {"single_review"}
    assert single.references["summary"].annotators == (A,)
    assert single.example_id != silver.example_id
    assert _example(log.events, as_of_event=silver.as_of_event) == silver
    assert _example(silver_events) == silver
    for example in (silver, single):
        verify_example(
            example, log.events, case=CASE, source=SOURCE, variables=VARIABLES, spans=SPANS
        )


def test_an_example_asserting_a_tier_its_events_do_not_support_is_rejected() -> None:
    log, ids = _silver()
    silver = _example(log.events)
    payload = silver.model_dump(mode="json")
    for reference in payload["references"].values():
        reference.update(tier="single_review", annotators=[A])
    for claim in payload["claims"]:
        claim["status"] = "gold"
    for reading in payload["mention_readings"]:
        reading["status"] = "verified"
    asserted = load_example(payload, case=CASE, source=SOURCE, variables=VARIABLES)
    _assert_code(
        "events_mismatch",
        verify_example,
        asserted,
        log.events,
        case=CASE,
        source=SOURCE,
        variables=VARIABLES,
        spans=SPANS,
    )


def test_a_lower_tier_than_the_events_support_is_rejected() -> None:
    log, ids = _silver()
    silver = _example(log.events)
    _raise_to_single_review(log, ids)
    payload = silver.model_dump(mode="json")
    latest = log.events[-1].event_id
    payload["as_of_event"] = latest
    payload["example_id"] = _example(log.events).example_id
    stale = load_example(payload, case=CASE, source=SOURCE, variables=VARIABLES)
    _assert_code(
        "events_mismatch",
        verify_example,
        stale,
        log.events,
        case=CASE,
        source=SOURCE,
        variables=VARIABLES,
        spans=SPANS,
    )


@pytest.mark.parametrize("tier", ["silver", "single_review"])
def test_tiers_never_fall_or_repeat(tier: str) -> None:
    log, ids = _silver()
    _raise_to_single_review(log, ids)
    role, actor = ("importer", IMPORTER) if tier == "silver" else ("reviewer", A)
    log.add("tier.set", actor, role, variable="finding", after={"tier": tier})
    _assert_code("invalid_transition", _replay, log.events)


@pytest.mark.parametrize(
    ("tier", "role", "actor"),
    [
        ("silver", "reviewer", A),
        ("single_review", "adjudicator", ADJ),
        ("single_review", "importer", IMPORTER),
        ("adjudicated", "reviewer", A),
    ],
)
def test_tiers_are_set_by_their_roles(tier: str, role: str, actor: str) -> None:
    log, _ = _silver()
    log.events = log.events[:-2]
    log.add("tier.set", actor, role, variable="finding", after={"tier": tier})
    _assert_code("invalid_role", _replay, log.events)


# ANN-18: move-on decisions -----------------------------------------------------------------


def test_a_move_on_decision_is_a_system_event_with_a_decision_reference() -> None:
    log, ids = _silver()
    _raise_to_single_review(log, ids)
    decision = log.add(
        "tier.set", SYSTEM, "system", after={"decision_ref": "synthetic-protocol/move-on/1"}
    )
    projection = _replay(log.events)
    assert projection.move_on_events == (decision.event_id,)
    example = _example(log.events)
    assert decision.event_id in example.references["finding"].derived_from_events
    assert example.as_of_event >= decision.event_id


@pytest.mark.parametrize(
    ("actor", "role", "after"),
    [
        (A, "reviewer", {"decision_ref": "synthetic/1"}),
        (SYSTEM, "system", {}),
        (SYSTEM, "system", {"decision_ref": " "}),
    ],
)
def test_move_on_without_system_role_or_decision_reference_is_rejected(
    actor: str, role: str, after: dict[str, Any]
) -> None:
    log, _ = _silver()
    log.add("tier.set", actor, role, after=after)
    _assert_code("invalid_role", _replay, log.events)


# Adjudication decisions --------------------------------------------------------------------


def test_excluded_cases_yield_no_example_and_keep_their_events() -> None:
    log, _ = _adjudicated()
    log.add(
        "adjudication.resolve",
        ADJ,
        "adjudicator",
        round=1,
        after={"decision": "excluded"},
        reason="Synthetic.",
    )
    assert _replay(log.events).excluded
    _assert_code("excluded_case", _example, log.events)


def test_unresolved_and_excluded_decisions_need_a_reason() -> None:
    for decision in ("unresolved", "excluded"):
        log, _ = _silver()
        log.add("adjudication.resolve", ADJ, "adjudicator", after={"decision": decision})
        _assert_code("invalid_schema", _replay, log.events)
    log, _ = _silver()
    log.add("adjudication.resolve", ADJ, "adjudicator", after={"decision": "maybe"}, reason="x")
    _assert_code("invalid_schema", _replay, log.events)


# Event schema ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "changes",
    [
        {"event_id": "not-a-ulid"},
        {"actor": "reviewer a"},
        {"role": "reviewer", "actor": IMPORTER},
        {"role": "importer", "actor": A},
        {"role": "adjudicator", "actor": ADJ, "stream": "a"},
        {"round": -1},
        {"round": True},
        {"at": datetime(2026, 11, 2)},
        {"action": "claim.delete"},
        {"reason": " "},
        {"extra": "field"},
    ],
)
def test_invalid_events_are_rejected(changes: dict[str, Any]) -> None:
    payload = {
        "event_id": new_ulid(),
        "case_id": CASE.case_id,
        "actor": A,
        "role": "reviewer",
        "round": 0,
        "action": "review.submit",
        "at": BASE,
        "provenance": make_provenance(),
        **changes,
    }
    with pytest.raises(ValidationError):
        AnnotationEvent.model_validate(payload)


def test_events_are_frozen() -> None:
    log, _ = _silver()
    event = log.events[0]
    with pytest.raises(ValidationError):
        event.actor = A  # type: ignore[misc]
    with pytest.raises(TypeError):
        log.events[-1].after["tier"] = "gold"  # type: ignore[index]
