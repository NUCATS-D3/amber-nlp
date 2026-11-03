"""Adversarial tests for Examples, reference tiers, and label records (contract v0.1 section 4.3
and 4.6; catalog ANN-01, ANN-02, ANN-03, ANN-11)."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import Field, ValidationError

from amber.examples import ExampleValidationError, load_example, load_label_record
from amber.graph import EvidenceGraph
from amber.graph_errors import GraphValidationError
from amber.ids import example_id, new_ulid
from amber.schemas import (
    AnswerModel,
    Case,
    Example,
    Inclusion,
    InferenceEvidence,
    Mention,
    MentionReading,
    Sensitivity,
    Source,
    SourceKind,
    Variable,
    VariableSet,
)
from amber.tools.mention import cite, mention

from ._claim_helpers import make_inference_record, make_provenance

TEXT = "Exam: mass \U0001f642 seen in the liver.\r\nNo other mass."
REVIEWER_A = "human:synthetic-reviewer-a"
REVIEWER_B = "human:synthetic-reviewer-b"
ADJUDICATOR = "human:synthetic-adjudicator"
TEACHER = "model:synthetic-teacher@1"


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
    split="train",
)


class Fixture:
    """A graph with a mention-level finding claim F and a note-level summary claim S that
    derives from F, plus an uncited second mention."""

    def __init__(self) -> None:
        graph = EvidenceGraph(source=SOURCE, variables=VARIABLES, sensitivity=Sensitivity.synthetic)
        span = mention(SOURCE.source_id, "mass", TEXT.index("mass"), "finding", sources=SOURCES)
        other = mention(SOURCE.source_id, "mass", TEXT.rindex("mass"), "finding", sources=SOURCES)
        assert isinstance(span, Mention) and isinstance(other, Mention)
        self.reading = MentionReading(
            reading_id=new_ulid(), mention_id=span.mention_id, provenance=make_provenance()
        )
        self.uncited = MentionReading(
            reading_id=new_ulid(), mention_id=other.mention_id, provenance=make_provenance()
        )
        graph.register_mention(span, [self.reading])
        graph.register_mention(other, [self.uncited])
        (position,) = cite(span, sources=SOURCES)
        graph.register_evidence(position)
        self.finding = graph.commit_claim(
            "finding",
            {"present": True},
            [position.evidence_id],
            "Invented rationale.",
            provenance=make_provenance(),
            reading_id=self.reading.reading_id,
        ).claim_id
        sentence = graph.register_evidence(_inclusion("mass \U0001f642 seen in the liver."))
        bridge = InferenceEvidence.model_validate(
            make_inference_record(inputs=[self.finding, sentence.evidence_id])
        )
        graph.register_evidence(bridge)
        self.summary = graph.commit_claim(
            "summary",
            {"present": True},
            [bridge.evidence_id],
            "Invented summary.",
            provenance=make_provenance(),
        ).claim_id
        self.position = position
        self.graph = graph
        self.events = sorted(new_ulid() for _ in range(3))
        self.as_of = self.events[-1]

    def reference(self, variable: str, tier: str = "adjudicated", **changes: Any) -> dict[str, Any]:
        annotators = {
            "adjudicated": [REVIEWER_A, REVIEWER_B],
            "single_review": [REVIEWER_A],
            "silver": [TEACHER],
        }[tier]
        final = self.finding if variable == "finding" else self.summary
        return {
            "variable": variable,
            "tier": tier,
            "outcome": {
                "status": "answered",
                "reason": None,
                "failure_kind": None,
                "reviewed_source_ids": [],
                "evidence_ids": [],
                "provenance": make_provenance().model_dump(mode="json"),
            },
            "final_claim_ids": [final],
            "annotators": annotators,
            "adjudicator": ADJUDICATOR if tier == "adjudicated" else None,
            "derived_from_events": self.events[:2],
            **changes,
        }

    def payload(
        self,
        tiers: tuple[str, str] = ("adjudicated", "adjudicated"),
        statuses: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        graph_payload = json.loads(json.dumps(self.graph.to_payload()))
        human = {tier != "silver" for tier in tiers}
        default = "gold" if tiers[1] != "silver" else "proposed"
        status = {
            self.summary: default,
            self.finding: "gold"
            if tiers[0] != "silver"
            else ("verified" if True in human else "proposed"),
            self.reading.reading_id: "verified" if True in human else "proposed",
            **(statuses or {}),
        }
        for record in graph_payload["claims"]:
            record["status"] = status.get(record["claim_id"], record["status"])
        for record in graph_payload["mention_readings"]:
            record["status"] = status.get(record["reading_id"], record["status"])
        return {
            "example_id": example_id(
                case_id=CASE.case_id,
                variable_set_ref=VARIABLES.schema_ref,
                as_of_event=self.as_of,
            ),
            "schema_version": "amber/0.1",
            "case_id": CASE.case_id,
            "variable_set": VARIABLES.name,
            "variable_set_version": VARIABLES.version,
            "variable_set_ref": VARIABLES.schema_ref,
            "as_of_event": self.as_of,
            "source_ids": [SOURCE.source_id],
            "split": "train",
            "references": {
                "finding": self.reference("finding", tiers[0]),
                "summary": self.reference("summary", tiers[1]),
            },
            "claims": graph_payload["claims"],
            "mentions": graph_payload["mentions"],
            "mention_readings": graph_payload["mention_readings"],
            "evidence": graph_payload["evidence"],
            "edges": graph_payload["edges"],
            "created_at": "2026-11-02T12:00:00Z",
        }


def _inclusion(text: str) -> Inclusion:
    from amber.tools.quote import quote

    result = quote(SOURCE.source_id, text, None, sources=SOURCES)
    assert isinstance(result, Inclusion)
    return result


def _load(payload: dict[str, Any], **changes: Any) -> Example:
    context: dict[str, Any] = {"case": CASE, "source": SOURCE, "variables": VARIABLES, **changes}
    return load_example(payload, **context)


def _assert_code(code: str, function: Callable[..., Any], /, *args: Any, **kwargs: Any) -> None:
    with pytest.raises((ExampleValidationError, GraphValidationError)) as failure:
        function(*args, **kwargs)
    assert failure.value.code == code  # type: ignore[union-attr]
    assert str(failure.value) == code


# Loading -----------------------------------------------------------------------------------


def test_an_adjudicated_example_loads_and_round_trips() -> None:
    fixture = Fixture()
    example = _load(fixture.payload())

    assert example.references["summary"].tier == "adjudicated"
    assert {claim.status for claim in example.claims} == {"gold"}
    assert example.split == "train"
    assert example.example_id == example_id(
        case_id=CASE.case_id, variable_set_ref=VARIABLES.schema_ref, as_of_event=fixture.as_of
    )
    assert _load(json.loads(example.model_dump_json())) == example


def test_examples_are_minted_only_by_the_load_boundary_and_frozen() -> None:
    fixture = Fixture()
    payload = fixture.payload()
    example = _load(payload)
    with pytest.raises(ValidationError, match="annotated-data boundary"):
        Example.model_validate(payload)
    with pytest.raises(TypeError, match="annotated-data boundary"):
        Example.model_construct(**payload)
    with pytest.raises(ValidationError):
        example.split = "test"  # type: ignore[misc]
    with pytest.raises(TypeError):
        example.claims.clear()
    with pytest.raises(TypeError):
        example.references["summary"] = example.references["finding"]  # type: ignore[index]


def test_silver_references_load_with_proposed_claims() -> None:
    fixture = Fixture()
    example = _load(fixture.payload(tiers=("silver", "silver")))
    assert {claim.status for claim in example.claims} == {"proposed"}
    assert example.references["finding"].annotators == (TEACHER,)


# Invariant 1: status by tier (ANN-01) ------------------------------------------------------


@pytest.mark.parametrize(
    ("tiers", "target", "status", "code"),
    [
        (("adjudicated", "adjudicated"), "summary", "proposed", "claim_status_mismatch"),
        (("adjudicated", "adjudicated"), "summary", "verified", "claim_status_mismatch"),
        (("single_review", "single_review"), "summary", "proposed", "claim_status_mismatch"),
        (("silver", "silver"), "summary", "verified", "claim_status_mismatch"),
        (("silver", "silver"), "summary", "gold", "claim_status_mismatch"),
        (("silver", "silver"), "reading", "verified", "claim_status_mismatch"),
        (("adjudicated", "adjudicated"), "reading", "proposed", "claim_status_mismatch"),
        (("silver", "adjudicated"), "finding", "proposed", "claim_status_mismatch"),
        (("adjudicated", "adjudicated"), "summary", "rejected", "unsupported_claim_status"),
        (("adjudicated", "adjudicated"), "reading", "rejected", "unsupported_reading_status"),
    ],
)
def test_claim_and_reading_status_follow_the_tier(
    tiers: tuple[str, str], target: str, status: str, code: str
) -> None:
    fixture = Fixture()
    key = {
        "summary": fixture.summary,
        "finding": fixture.finding,
        "reading": fixture.reading.reading_id,
    }[target]
    _assert_code(code, _load, fixture.payload(tiers=tiers, statuses={key: status}))


@pytest.mark.parametrize("status", ["verified", "gold"])
def test_a_record_serving_human_and_silver_references_meets_the_strictest(status: str) -> None:
    fixture = Fixture()
    example = _load(
        fixture.payload(tiers=("silver", "adjudicated"), statuses={fixture.finding: status})
    )
    assert example.references["finding"].tier == "silver"


@pytest.mark.parametrize("status", ["proposed", "verified", "gold"])
def test_uncited_readings_keep_their_own_status(status: str) -> None:
    fixture = Fixture()
    payload = fixture.payload(statuses={fixture.uncited.reading_id: status})
    example = _load(payload)
    readings = {reading.reading_id: reading for reading in example.mention_readings}
    assert readings[fixture.uncited.reading_id].status == status


def test_every_retained_claim_serves_a_reference() -> None:
    fixture = Fixture()
    payload = fixture.payload()
    payload["references"]["summary"] = fixture.reference(
        "summary",
        outcome={
            **payload["references"]["summary"]["outcome"],
            "status": "insufficient_evidence",
            "reason": "Synthetic.",
        },
        final_claim_ids=[],
    )
    _assert_code("unreferenced_claim", _load, payload)


# Invariants 2 and 3: outcomes and final claims (ANN-01, ANN-02) ----------------------------


def _with_outcome(fixture: Fixture, variable: str, **outcome: Any) -> dict[str, Any]:
    payload = fixture.payload()
    reference = payload["references"][variable]
    reference["outcome"] = {**reference["outcome"], **outcome}
    return payload


def test_failed_runs_never_become_examples() -> None:
    fixture = Fixture()
    payload = _with_outcome(
        fixture, "summary", status="failed", reason="Synthetic.", failure_kind="provider"
    )
    payload["references"]["summary"]["final_claim_ids"] = []
    _assert_code("failed_outcome", _load, payload)


def test_answered_needs_final_ids_and_non_answers_have_none() -> None:
    fixture = Fixture()
    payload = fixture.payload()
    payload["references"]["summary"]["final_claim_ids"] = []
    _assert_code("invalid_final_claims", _load, payload)

    payload = _with_outcome(
        fixture,
        "summary",
        status="not_mentioned",
        reason="Synthetic.",
        reviewed_source_ids=[SOURCE.source_id],
    )
    _assert_code("invalid_final_claims", _load, payload)


@pytest.mark.parametrize("final", ["finding", "unknown"])
def test_final_claims_answer_their_own_variable(final: str) -> None:
    fixture = Fixture()
    payload = fixture.payload()
    payload["references"]["summary"]["final_claim_ids"] = [
        fixture.finding if final == "finding" else new_ulid()
    ]
    _assert_code("invalid_final_claims", _load, payload)


def test_not_mentioned_references_require_full_review() -> None:
    fixture = Fixture()
    payload = _with_outcome(fixture, "summary", status="not_mentioned", reason="Synthetic.")
    payload["references"]["summary"]["final_claim_ids"] = []
    _assert_code("incomplete_review", _load, payload)


def test_mention_level_references_never_report_conflicting_evidence() -> None:
    fixture = Fixture()
    payload = _with_outcome(
        fixture,
        "finding",
        status="conflicting_evidence",
        reason="Synthetic.",
        evidence_ids=[fixture.position.evidence_id],
    )
    payload["references"]["finding"]["final_claim_ids"] = []
    payload["references"]["summary"]["final_claim_ids"] = [fixture.summary]
    _assert_code("invalid_reference", _load, payload)


def test_outcome_citations_must_resolve() -> None:
    fixture = Fixture()
    payload = _with_outcome(fixture, "summary", evidence_ids=["unknown"])
    _assert_code("unknown_reference", _load, payload)


# Invariant 6: mentions (ANN-03) ------------------------------------------------------------


def test_a_cited_mention_must_be_present_and_uncited_mentions_round_trip() -> None:
    fixture = Fixture()
    payload = fixture.payload()
    cited = next(
        item
        for item in payload["mention_readings"]
        if item["reading_id"] == fixture.reading.reading_id
    )
    payload["mentions"] = [
        item for item in payload["mentions"] if item["mention_id"] != cited["mention_id"]
    ]
    _assert_code("unknown_reference", _load, payload)

    payload = fixture.payload()
    payload["mention_readings"] = [
        item
        for item in payload["mention_readings"]
        if item["reading_id"] != fixture.reading.reading_id
    ]
    payload["mentions"] = [
        item for item in payload["mentions"] if item["mention_id"] != cited["mention_id"]
    ]
    _assert_code("missing_reading", _load, payload)

    example = _load(fixture.payload())
    assert fixture.uncited.reading_id in {item.reading_id for item in example.mention_readings}
    assert len(example.mentions) == 2


# References, identity, split, and context --------------------------------------------------


@pytest.mark.parametrize(
    "changes",
    [
        {"annotators": [REVIEWER_A]},
        {"annotators": [REVIEWER_A, REVIEWER_A]},
        {"annotators": [REVIEWER_A, TEACHER]},
        {"adjudicator": None},
        {"adjudicator": TEACHER},
        {"annotators": []},
        {"annotators": ["reviewer a", REVIEWER_B]},
        {"derived_from_events": []},
        {"derived_from_events": ["not-an-event"]},
        {"tier": "gold"},
    ],
)
def test_adjudicated_references_are_attributed(changes: dict[str, Any]) -> None:
    fixture = Fixture()
    payload = fixture.payload()
    payload["references"]["summary"].update(changes)
    _assert_code("invalid_reference", _load, payload)


@pytest.mark.parametrize(
    ("tier", "changes"),
    [
        ("single_review", {"annotators": [REVIEWER_A, REVIEWER_B]}),
        ("single_review", {"adjudicator": ADJUDICATOR}),
        ("silver", {"annotators": [REVIEWER_A]}),
        ("silver", {"adjudicator": ADJUDICATOR}),
    ],
)
def test_single_review_and_silver_references_are_attributed(
    tier: str, changes: dict[str, Any]
) -> None:
    fixture = Fixture()
    payload = fixture.payload(tiers=(tier, tier))
    payload["references"]["summary"].update(changes)
    _assert_code("invalid_reference", _load, payload)


def test_derived_events_precede_the_snapshot_event() -> None:
    fixture = Fixture()
    payload = fixture.payload()
    payload["references"]["summary"]["derived_from_events"] = [new_ulid()]
    _assert_code("invalid_reference", _load, payload)


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"example_id": "0" * 64}, "example_id_mismatch"),
        ({"as_of_event": "not-a-ulid"}, "invalid_schema"),
        ({"split": "test"}, "split_mismatch"),
        ({"split": None}, "split_mismatch"),
        ({"variable_set_ref": "0" * 64}, "variable_set_mismatch"),
        ({"variable_set_version": "2.0.0"}, "variable_set_mismatch"),
        ({"case_id": "other"}, "invalid_context"),
        ({"source_ids": ["other"]}, "invalid_context"),
        ({"schema_version": "0.1"}, "invalid_schema"),
        ({"schema_version": "amber/0.2"}, "invalid_schema"),
        ({"schema_version": "amber/1.0"}, "invalid_schema"),
        ({"created_at": "2026-11-02T12:00:00"}, "invalid_schema"),
        ({"extra": True}, "invalid_schema"),
        ({"claims": "none"}, "invalid_schema"),
        ({"references": {}}, "invalid_context"),
    ],
)
def test_example_envelopes_are_bound_to_their_context(changes: dict[str, Any], code: str) -> None:
    fixture = Fixture()
    _assert_code(code, _load, {**Fixture.payload(fixture), **changes})


def test_a_new_as_of_event_is_a_new_example_id() -> None:
    fixture = Fixture()
    first = _load(fixture.payload())
    fixture.as_of = new_ulid()
    second = _load(fixture.payload())
    assert first.example_id != second.example_id


def test_examples_load_only_against_their_case_split_and_set() -> None:
    fixture = Fixture()
    payload = fixture.payload()
    _assert_code("split_mismatch", _load, payload, case=CASE.model_copy(update={"split": "dev"}))
    other_set = VariableSet.model_validate({**VARIABLES.model_dump(), "version": "9.9.9"})
    _assert_code("variable_set_mismatch", _load, payload, variables=other_set)
    with pytest.raises(ValidationError):
        Case.model_validate({**CASE.model_dump(), "split": "validation"})


# Label records (section 4.6, ANN-11) -------------------------------------------------------


def _label(**changes: Any) -> dict[str, Any]:
    return {
        "schema_version": "amber/0.1",
        "case_id": CASE.case_id,
        "variable": "summary",
        "variable_set": VARIABLES.name,
        "variable_set_version": VARIABLES.version,
        "variable_set_ref": VARIABLES.schema_ref,
        "tier": "silver",
        "outcome_status": "answered",
        "value": {"present": True},
        "producer": "rule:synthetic_candidate@1+abc",
        "split": "train",
        **changes,
    }


def test_label_records_load_against_their_case_and_set() -> None:
    record = load_label_record(_label(), case=CASE, variables=VARIABLES)
    assert record.value == {"present": True}
    unanswered = load_label_record(
        _label(outcome_status="not_mentioned", value=None), case=CASE, variables=VARIABLES
    )
    assert unanswered.value is None


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"outcome_status": "failed", "value": None}, "invalid_schema"),
        ({"producer": REVIEWER_A}, "invalid_schema"),
        ({"tier": "adjudicated"}, "invalid_schema"),
        ({"value": None}, "invalid_schema"),
        ({"outcome_status": "not_mentioned"}, "invalid_schema"),
        ({"value": {"present": "yes"}}, "invalid_schema"),
        ({"variable": "missing"}, "invalid_context"),
        ({"split": "test"}, "split_mismatch"),
        ({"variable_set_ref": "0" * 64}, "variable_set_mismatch"),
        ({"extra": True}, "invalid_schema"),
    ],
)
def test_invalid_label_records_are_rejected(changes: dict[str, Any], code: str) -> None:
    _assert_code(code, load_label_record, _label(**changes), case=CASE, variables=VARIABLES)


def test_a_non_final_variable_has_no_label_record() -> None:
    variables = VariableSet.create(
        name=VARIABLES.name,
        version="2.0.0",
        variables=(
            VARIABLES.variables["finding"].model_copy(update={"final": False}),
            VARIABLES.variables["summary"],
        ),
    )
    payload = _label(
        variable="finding",
        variable_set_version="2.0.0",
        variable_set_ref=variables.schema_ref,
    )
    _assert_code("invalid_context", load_label_record, payload, case=CASE, variables=variables)


def test_a_label_record_cannot_load_as_an_example() -> None:
    _assert_code("invalid_schema", _load, _label())


def test_created_at_is_timezone_aware() -> None:
    fixture = Fixture()
    payload = fixture.payload()
    payload["created_at"] = datetime(2026, 11, 2, tzinfo=UTC).isoformat()
    assert _load(payload).created_at.utcoffset() is not None
