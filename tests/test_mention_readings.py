"""Adversarial tests for mention readings, attributes, and mentions in graphs and results
(contract v0.1 sections 2.2, 2.3, and 2.9)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import ValidationError

from amber.cases import create_case_result, load_case_result
from amber.conformance.forgery import forge
from amber.graph import EvidenceGraph
from amber.graph_errors import GraphValidationError
from amber.ids import new_ulid
from amber.schemas import (
    Attribute,
    Case,
    CaseOutcome,
    Inclusion,
    Mention,
    MentionReading,
    Provenance,
    Section,
    Sensitivity,
    Source,
    SourceKind,
)
from amber.tools.mention import mention
from amber.tools.quote import quote

from ._claim_helpers import SYNTHETIC_RUN_ID, make_provenance, make_task

TEXT = "Findings: no evidence of e\u0301 distant metastasis.\r\nNodes \U0001f642 positive."
RULE = "rule:synthetic_negation@1+abc123"
MODEL = "model:synthetic-tagger@2"


def _source() -> Source:
    return Source.create(
        patient_id="synthetic-patient-1",
        kind=SourceKind.note,
        external_id="synthetic-note-1",
        datetime=None,
        text=TEXT,
        record=None,
        sections=(Section(start=0, end=TEXT.index("\r\n"), label="Findings", category="findings"),),
    )


SOURCE = _source()
SOURCES = {SOURCE.source_id: SOURCE}


def _assert_error(code: str, function: Callable[..., Any], /, *args: Any, **kwargs: Any) -> None:
    with pytest.raises(GraphValidationError) as failure:
        function(*args, **kwargs)
    assert failure.value.code == code
    assert str(failure.value) == code


def _provenance(producer: str = RULE, **changes: Any) -> Provenance:
    return Provenance.model_validate(
        {**make_provenance().model_dump(), "producer": producer, **changes}
    )


def _inclusion(text: str, hint: int | None = None) -> Inclusion:
    result = quote(SOURCE.source_id, text, hint, sources=SOURCES)
    assert isinstance(result, Inclusion)
    return result


def _mention(text: str, mention_type: str = "finding", **kwargs: Any) -> Mention:
    result = mention(SOURCE.source_id, text, None, mention_type, sources=SOURCES, **kwargs)
    assert isinstance(result, Mention)
    return result


def _reading(span: Mention, producer: str = RULE, **changes: Any) -> MentionReading:
    payload: dict[str, Any] = {
        "reading_id": new_ulid(),
        "mention_id": span.mention_id,
        "label": "metastasis",
        "value": "distant metastasis",
        "provenance": _provenance(producer),
        **changes,
    }
    return MentionReading.model_validate(payload)


def _graph(excluded_spans: tuple[tuple[int, int], ...] = ()) -> EvidenceGraph:
    return EvidenceGraph(
        source=SOURCE,
        task=make_task(),
        sensitivity=Sensitivity.synthetic,
        excluded_spans=excluded_spans,
    )


def _negated_graph() -> tuple[EvidenceGraph, Mention, MentionReading, Inclusion, Inclusion]:
    graph = _graph()
    cue = _inclusion("no evidence of")
    scope = _inclusion(TEXT[TEXT.index("no evidence") : TEXT.index(".")])
    graph.register_evidence(cue)
    graph.register_evidence(scope)
    span = _mention("distant metastasis")
    reading = _reading(
        span,
        attributes={
            "negation": Attribute(
                value=True, evidence_id=cue.evidence_id, scope_evidence_id=scope.evidence_id
            ),
            "temporality": Attribute(value="current"),
        },
    )
    graph.register_mention(span, [reading])
    return graph, span, reading, cue, scope


# Schema ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, 1.5, 1.0, [True], {"negated": True}, "", "  "])
def test_attribute_values_are_strict_nonblank_scalars(value: Any) -> None:
    with pytest.raises(ValidationError):
        Attribute(value=value)


@pytest.mark.parametrize("value", [True, False, 0, 3, "historical"])
def test_attribute_values_keep_their_exact_type(value: Any) -> None:
    attribute = Attribute.model_validate(json.loads(Attribute(value=value).model_dump_json()))
    assert attribute.value == value
    assert type(attribute.value) is type(value)


def test_attribute_assigned_by_must_be_a_producer_reference() -> None:
    with pytest.raises(ValidationError):
        Attribute(value=True, assigned_by="copal negation")
    assert Attribute(value=True, assigned_by=RULE).assigned_by == RULE


def test_attribute_assigned_by_equal_to_the_reading_producer_is_rejected() -> None:
    span = _mention("distant metastasis")
    with pytest.raises(ValidationError, match="assigned_by"):
        _reading(span, attributes={"negation": Attribute(value=True, assigned_by=RULE)})


def test_human_correction_keeps_assigned_by_on_carried_over_attributes() -> None:
    graph, span, original, cue, _ = _negated_graph()
    carried = original.attributes["negation"].model_copy(update={"assigned_by": RULE})
    correction = _reading(
        span,
        producer="human:synthetic-reviewer",
        label="metastasis",
        value="metastasis",
        attributes={"negation": carried, "temporality": Attribute(value="historical")},
    )

    graph.register_reading(correction)

    retained = graph.mention_readings[correction.reading_id]
    assert retained.attributes["negation"].assigned_by == RULE
    assert retained.attributes["temporality"].assigned_by is None
    assert graph.mention_readings[original.reading_id] == original


@pytest.mark.parametrize(
    "changes",
    [
        {"reading_id": "not-a-ulid"},
        {"reading_id": new_ulid().lower()},
        {"mention_id": " "},
        {"label": ""},
        {"value": True},
        {"value": float("nan")},
        {"value": [1]},
        {"unit": " "},
        {"inputs": ("copal:1", "copal:1")},
        {"inputs": ("",)},
        {"attributes": {"": {"value": True}}},
        {"attributes": {" negation": {"value": True}}},
        {"attributes": {"negation": {"value": None}}},
        {"concept": {"system": "SYNTH", "code": "", "label": "x"}},
        {"concept": {"system": "SYNTH", "code": "1", "label": "x", "score": float("inf")}},
        {"status": "accepted"},
        {"extra": "field"},
    ],
)
def test_reading_schema_rejects_invalid_fields(changes: dict[str, Any]) -> None:
    span = _mention("distant metastasis")
    payload = {**_reading(span).model_dump(), **changes}
    with pytest.raises(ValidationError):
        MentionReading.model_validate(payload)


def test_reading_is_frozen_including_attributes() -> None:
    _, _, reading, _, _ = _negated_graph()
    with pytest.raises(ValidationError):
        reading.label = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        reading.attributes["negation"] = Attribute(value=False)  # type: ignore[index]


# Registration ----------------------------------------------------------------------------------


def test_two_producers_reading_one_span_share_one_mention() -> None:
    graph, span, rule_reading, _, _ = _negated_graph()
    model_reading = _reading(
        span, producer=MODEL, value="metastasis", attributes={"negation": Attribute(value=False)}
    )
    again = _mention("distant metastasis")

    registered, readings = graph.register_mention(again, [model_reading])

    assert registered == span
    assert readings == (model_reading,)
    assert list(graph.mentions) == [span.mention_id]
    assert set(graph.mention_readings) == {rule_reading.reading_id, model_reading.reading_id}
    assert graph.readings_of(span.mention_id) == tuple(
        sorted((rule_reading, model_reading), key=lambda item: item.reading_id)
    )
    assert graph.mention_readings[rule_reading.reading_id].attributes["negation"].value is True
    assert graph.mention_readings[model_reading.reading_id].attributes["negation"].value is False


def test_overlapping_and_nested_mentions_of_different_types_coexist() -> None:
    graph = _graph()
    outer = _mention("distant metastasis", "finding")
    inner = _mention("metastasis", "anatomy_free_finding")
    overlapping = _mention("e\u0301 distant", "modifier")
    for span in (outer, inner, overlapping):
        graph.register_mention(span, [_reading(span)])

    assert set(graph.mentions) == {outer.mention_id, inner.mention_id, overlapping.mention_id}


def test_a_mention_needs_a_reading() -> None:
    graph = _graph()
    span = _mention("distant metastasis")
    _assert_error("missing_reading", graph.register_mention, span, [])
    payload = graph.to_payload()
    payload["mentions"] = [span.model_dump(mode="json")]
    _assert_error(
        "missing_reading",
        EvidenceGraph.from_payload,
        payload,
        source=SOURCE,
        task=graph.task,
        sensitivity=Sensitivity.synthetic,
    )


def test_a_reading_needs_a_registered_mention() -> None:
    graph = _graph()
    span = _mention("distant metastasis")
    _assert_error("unknown_reference", graph.register_reading, _reading(span))
    payload = graph.to_payload()
    payload["mention_readings"] = [_reading(span).model_dump(mode="json")]
    _assert_error(
        "unknown_reference",
        EvidenceGraph.from_payload,
        payload,
        source=SOURCE,
        task=graph.task,
        sensitivity=Sensitivity.synthetic,
    )


def test_readings_must_name_the_registered_span() -> None:
    graph = _graph()
    span = _mention("distant metastasis")
    other = _mention("metastasis")
    _assert_error("invalid_context", graph.register_mention, span, [_reading(other)])


@pytest.mark.parametrize("field", ["evidence_id", "scope_evidence_id"])
def test_attribute_evidence_must_be_registered_inclusions(field: str) -> None:
    graph = _graph()
    cue = _inclusion("no evidence of")
    span = _mention("distant metastasis")
    unregistered = _reading(
        span, attributes={"negation": Attribute(value=True, **{field: cue.evidence_id})}
    )
    _assert_error("unknown_reference", graph.register_mention, span, [unregistered])
    assert graph.mentions == {}

    graph.register_evidence(cue)
    claim = graph.commit_claim(
        graph.task,
        {"finding_present": False},
        [cue.evidence_id],
        "Invented rationale.",
        {"finding_present": [cue.evidence_id]},
        provenance=make_provenance(),
    )
    inference_id = next(
        evidence_id for evidence_id in graph.evidence if evidence_id != cue.evidence_id
    )
    assert claim.claim_id not in graph.evidence
    rationale = _reading(
        span, attributes={"negation": Attribute(value=True, **{field: inference_id})}
    )
    _assert_error("unsupported_evidence", graph.register_mention, span, [rationale])


def test_attribute_scope_must_contain_the_mention_hull() -> None:
    graph = _graph()
    cue = _inclusion("no evidence of")
    short_scope = _inclusion("no evidence of e\u0301 distant")
    graph.register_evidence(cue)
    graph.register_evidence(short_scope)
    span = _mention("distant metastasis")
    reading = _reading(
        span,
        attributes={
            "negation": Attribute(
                value=True, evidence_id=cue.evidence_id, scope_evidence_id=short_scope.evidence_id
            )
        },
    )
    _assert_error("attribute_outside_scope", graph.register_mention, span, [reading])


def test_scope_need_not_contain_the_cue_and_absent_scope_is_not_checked() -> None:
    graph = _graph()
    span = _mention("distant metastasis")
    cue = _inclusion("positive")
    scope = _inclusion("distant metastasis.")
    graph.register_evidence(cue)
    graph.register_evidence(scope)
    post_negation = _reading(
        span,
        attributes={
            "negation": Attribute(
                value=True, evidence_id=cue.evidence_id, scope_evidence_id=scope.evidence_id
            ),
            "experiencer": Attribute(value="patient", evidence_id=cue.evidence_id),
        },
    )

    graph.register_mention(span, [post_negation])

    assert graph.mention_readings[post_negation.reading_id] == post_negation


@pytest.mark.parametrize("status", ["verified", "gold"])
def test_graphs_hold_only_proposed_or_rejected_readings(status: str) -> None:
    graph = _graph()
    span = _mention("distant metastasis")
    _assert_error(
        "unsupported_reading_status", graph.register_mention, span, [_reading(span, status=status)]
    )
    rejected = _reading(span, status="rejected")
    graph.register_mention(span, [rejected])
    assert graph.mention_readings[rejected.reading_id].status == "rejected"


def test_reading_sensitivity_must_match_the_graph() -> None:
    graph = _graph()
    span = _mention("distant metastasis")
    reading = _reading(span, provenance=_provenance(sensitivity=Sensitivity.phi))
    _assert_error("invalid_context", graph.register_mention, span, [reading])


def test_mentions_in_excluded_spans_are_rejected() -> None:
    start = TEXT.index("distant")
    graph = _graph(excluded_spans=((start, start + 3),))
    span = _mention("distant metastasis")
    _assert_error("invalid_context", graph.register_mention, span, [_reading(span)])


def test_mentions_from_another_source_are_rejected() -> None:
    other = Source.create(
        patient_id="synthetic-patient-1",
        kind=SourceKind.note,
        external_id="synthetic-note-2",
        datetime=None,
        text=TEXT,
        record=None,
    )
    foreign = mention(
        other.source_id, "distant metastasis", None, "finding", sources={other.source_id: other}
    )
    assert isinstance(foreign, Mention)
    graph = _graph()
    _assert_error("invalid_context", graph.register_mention, foreign, [_reading(foreign)])


def test_reregistration_is_idempotent_and_conflicts_are_atomic() -> None:
    graph, span, reading, _, _ = _negated_graph()
    before = graph.to_payload()

    assert graph.register_mention(span, [reading]) == (span, (reading,))
    assert graph.to_payload() == before

    conflicting = reading.model_copy(update={"label": "other"})
    _assert_error("duplicate_reference", graph.register_reading, conflicting)
    _assert_error("duplicate_reference", graph.register_mention, span, [reading, reading])
    fresh = _reading(span)
    _assert_error("duplicate_reference", graph.register_mention, span, [fresh, conflicting])
    tampered = forge(span, section_category=None)
    _assert_error("duplicate_reference", graph.register_mention, tampered, [reading])
    assert graph.to_payload() == before


def test_mentions_at_reaches_spans_and_readings_from_a_source_leaf() -> None:
    graph, span, reading, cue, _ = _negated_graph()
    position = _inclusion("distant metastasis")
    graph.register_evidence(position)
    other_type = _mention("distant metastasis", "anatomy")
    other_reading = _reading(other_type, producer=MODEL)
    graph.register_mention(other_type, [other_reading])

    found = graph.mentions_at(position.evidence_id)

    assert {item.mention_id for item in found} == {span.mention_id, other_type.mention_id}
    assert graph.mentions_at(cue.evidence_id) == ()
    assert graph.readings_of(other_type.mention_id) == (other_reading,)
    _assert_error("unknown_reference", graph.mentions_at, "unknown")
    _assert_error("unknown_reference", graph.readings_of, "unknown")


def test_discontinuous_mentions_are_found_at_each_fragment() -> None:
    graph = _graph()
    start = TEXT.index("no evidence")
    end = TEXT.index("metastasis") + len("metastasis")
    fragments = ((start, start + 2), (TEXT.index("metastasis"), end))
    span = _mention(TEXT[start:end], fragments=fragments)
    graph.register_mention(span, [_reading(span)])
    pieces = [_inclusion(TEXT[lo:hi], lo) for lo, hi in fragments]
    hull = _inclusion(TEXT[start:end])
    for piece in (*pieces, hull):
        graph.register_evidence(piece)

    assert [graph.mentions_at(piece.evidence_id) for piece in pieces] == [(span,), (span,)]
    assert graph.mentions_at(hull.evidence_id) == ()


# Payload revalidation --------------------------------------------------------------------------


def _mention_payload_error(mutation: Callable[[dict[str, Any]], None], code: str) -> None:
    graph, _, _, _, _ = _negated_graph()
    payload = json.loads(json.dumps(graph.to_payload()))
    mutation(payload["mentions"][0])
    _assert_error(
        code,
        EvidenceGraph.from_payload,
        payload,
        source=SOURCE,
        task=graph.task,
        sensitivity=Sensitivity.synthetic,
    )


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda m: m.update(section_category="impression"), "invalid_context"),
        (lambda m: m.update(section_category=None), "invalid_context"),
        (lambda m: m.update(mention_type="other"), "invalid_context"),
        (lambda m: m.update(mention_id="0" * 64), "invalid_context"),
        (lambda m: m.update(source_id="other"), "invalid_context"),
        (lambda m: m.update(quote="SECRET_SOURCE_VALUE"), "quote_mismatch"),
        (lambda m: m.update(start=m["start"] + 1), "quote_mismatch"),
        (lambda m: m.update(end=10_000), "invalid_span"),
        (lambda m: m.update(start=True), "invalid_span"),
        (lambda m: m.update(fragments=[[m["start"], m["end"]]]), "invalid_span"),
        (lambda m: m.update(fragments=[["0", 1]]), "invalid_span"),
        (lambda m: m.update(fragments="none"), "invalid_schema"),
        (lambda m: m.update(mention_type=" "), "invalid_schema"),
        (lambda m: m.update(extra="field"), "invalid_schema"),
        (lambda m: m.pop("section_category"), "invalid_schema"),
    ],
)
def test_mention_payloads_are_reminted_against_the_source(
    mutation: Callable[[dict[str, Any]], None], code: str
) -> None:
    _mention_payload_error(mutation, code)


def test_graph_snapshot_round_trips_mentions_and_readings() -> None:
    graph, span, reading, _, _ = _negated_graph()
    restored = EvidenceGraph.from_payload(
        json.loads(json.dumps(graph.to_payload())),
        source=SOURCE,
        task=graph.task,
        sensitivity=Sensitivity.synthetic,
    )
    assert restored.mentions == {span.mention_id: span}
    assert restored.mention_readings == {reading.reading_id: reading}
    assert restored.to_payload() == graph.to_payload()


# Case results ----------------------------------------------------------------------------------


def _case() -> Case:
    return Case(
        case_id="synthetic-case-1",
        task=make_task().name,
        patient_id=SOURCE.patient_id,
        source_ids=[SOURCE.source_id],
        sensitivity=Sensitivity.synthetic,
    )


def _result_payload() -> tuple[dict[str, Any], Case, EvidenceGraph]:
    graph, _, _, _, _ = _negated_graph()
    outcome = CaseOutcome(
        status="not_mentioned",
        reason="Synthetic complete review.",
        reviewed_source_ids=[SOURCE.source_id],
        provenance=make_provenance(),
    )
    result = create_case_result(
        _case(), graph=graph, run_id=SYNTHETIC_RUN_ID, outcome=outcome, final_claim_ids=[]
    )
    return json.loads(result.model_dump_json()), _case(), graph


def test_uncited_mentions_and_readings_round_trip_through_case_results() -> None:
    payload, case, graph = _result_payload()
    loaded = load_case_result(payload, case=case, source=SOURCE, task=graph.task)

    assert loaded.mentions == list(graph.mentions.values())
    assert loaded.mention_readings == list(graph.mention_readings.values())
    with pytest.raises(TypeError):
        loaded.mentions.clear()
    with pytest.raises(TypeError):
        loaded.mention_readings.clear()


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda p: p.update(mentions=[]), "unknown_reference"),
        (lambda p: p.update(mention_readings=[]), "missing_reading"),
        (lambda p: p.pop("mention_readings"), None),
        (lambda p: p.update(evidence=[]), "unknown_reference"),
        (lambda p: p["mentions"][0].update(section_category=None), "invalid_context"),
    ],
)
def test_case_results_revalidate_mentions_on_load(
    mutation: Callable[[dict[str, Any]], None], code: str | None
) -> None:
    payload, case, graph = _result_payload()
    mutation(payload)
    with pytest.raises((GraphValidationError, ValueError)) as failure:
        load_case_result(payload, case=case, source=SOURCE, task=graph.task)
    if code is not None:
        assert getattr(failure.value, "code", None) == code
