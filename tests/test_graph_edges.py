"""Graph validation at its edges (amber-u4c.7, from mutation testing): section and exclusion
boundaries, mention bounds, attribute requirements without listed values, rejected claims,
ID collisions across record kinds, and payload field types."""

from __future__ import annotations

import json
from typing import Any

import pytest

from amber._graph_validation import ordered_records, validate_state
from amber.graph import EvidenceGraph
from amber.graph_errors import GraphValidationError
from amber.ids import inclusion_id
from amber.schemas import (
    AnswerModel,
    Attribute,
    EvidenceEdge,
    Mention,
    MentionReading,
    Sensitivity,
    Variable,
    VariableSet,
)
from amber.tools.mention import cite, mention

from ._claim_helpers import make_provenance
from .test_graph_validation import _assert_error, _valid_graph
from .test_mention_claims import (
    LINE_1,
    SOURCE,
    SOURCES,
    TEXT,
    _commit,
    _commit_m,
    _m_setup,
    _variables,
)


def _graph(**kwargs: Any) -> EvidenceGraph:
    return EvidenceGraph(
        source=SOURCE, variables=_variables(), sensitivity=Sensitivity.synthetic, **kwargs
    )


def _register(graph: EvidenceGraph, text: str, kind: str, hint: int) -> tuple[Mention, str]:
    span = mention(SOURCE.source_id, text, hint, kind, sources=SOURCES)
    assert isinstance(span, Mention), span
    reading = MentionReading(
        reading_id=_ulid(), mention_id=span.mention_id, value=text, provenance=make_provenance()
    )
    graph.register_mention(span, [reading])
    return span, reading.reading_id


def _ulid() -> str:
    from amber.ids import new_ulid

    return new_ulid()


def _restore(graph: EvidenceGraph, payload: dict[str, Any], **kwargs: Any) -> EvidenceGraph:
    return EvidenceGraph.from_payload(
        payload, source=SOURCE, variables=_variables(), sensitivity=Sensitivity.synthetic, **kwargs
    )


# Sections and bounds ----------------------------------------------------------------------


def test_citations_may_touch_both_ends_of_their_section() -> None:
    graph = _graph()
    for text, hint in (("Final", 0), ("metastasis.\r\n", LINE_1.index("metastasis"))):
        span, reading_id = _register(graph, text, "tnm_t", hint)
        (position,) = cite(span, sources=SOURCES)
        graph.register_evidence(position)
        claim = _commit(graph, "pathologic_T", {"t": "T2"}, [position.evidence_id], reading_id)
        assert claim.reading_id == reading_id


def test_a_mention_may_end_at_the_end_of_the_text() -> None:
    graph = _graph()
    tail = "grade 2.\r\n"
    span, _ = _register(graph, tail, "tnm_t", len(TEXT) - len(tail))
    assert span.end == len(TEXT)


def test_mentions_may_touch_but_not_overlap_exclusions() -> None:
    start = len(LINE_1)
    graph = _graph(excluded_spans=[(start, start + len("History: "))])
    _register(graph, "pT2", "tnm_t", TEXT.index("pT2"))  # wholly before
    _register(graph, "metastasis.\r\n", "tnm_t", LINE_1.index("metastasis"))  # ends at start
    _register(graph, "pT3", "tnm_t", TEXT.index("pT3"))  # starts at the exclusion's end
    with pytest.raises(GraphValidationError, match="^invalid_context$"):
        _register(graph, "History", "tnm_t", start)


def test_an_exclusion_may_end_at_the_end_of_the_text() -> None:
    graph = _graph(excluded_spans=[(len(TEXT) - 2, len(TEXT))])
    assert graph.excluded_spans == ((len(TEXT) - 2, len(TEXT)),)


# Attributes and rejected claims ------------------------------------------------------------


def test_a_requirement_without_values_permits_any_value() -> None:
    graph, reading, cue, position = _m_setup(negation_values=None)
    cue_id = cue.evidence_id
    graph, reading, cue, position = _m_setup(
        Attribute(value="possibly", evidence_id=cue_id), negation_values=None
    )
    claim = _commit_m(graph, reading, position, cue)
    assert claim.reading_id == reading.reading_id


def test_rejected_claims_are_not_held_to_variable_rules() -> None:
    graph, reading, cue, position = _m_setup()
    claim = _commit_m(graph, reading, position, cue)
    payload = json.loads(json.dumps(graph.to_payload()))
    for record in payload["claims"]:
        if record["claim_id"] == claim.claim_id:
            record["status"] = "rejected"
    payload["edges"] = [
        edge for edge in payload["edges"] if edge["role"] != "attribute:negation"
    ]  # the required attribute evidence is gone
    restored = _restore(graph, payload)
    assert restored.claims[claim.claim_id].status == "rejected"


# Identity and ordering ---------------------------------------------------------------------


def test_a_reading_cannot_reuse_a_claim_id() -> None:
    graph, reading, cue, position = _m_setup()
    claim = _commit_m(graph, reading, position, cue)
    payload = json.loads(json.dumps(graph.to_payload()))
    for record in payload["mention_readings"]:
        record["reading_id"] = claim.claim_id
    for record in payload["claims"]:
        record["reading_id"] = claim.claim_id
    with pytest.raises(GraphValidationError, match="^duplicate_reference$"):
        _restore(graph, payload)


def test_edges_without_a_role_order_first() -> None:
    claim_id, evidence_id = _ulid(), "e" * 64
    named = EvidenceEdge(claim_id=claim_id, evidence_id=evidence_id, role="x")
    bare = EvidenceEdge(claim_id=claim_id, evidence_id=evidence_id, role=None)
    ordered = ordered_records(claims={}, evidence={}, edges=[named, bare], mentions={}, readings={})
    assert [edge.role for edge in ordered["edges"]] == [None, "x"]


# Payload field types -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ({"end": True}, "invalid_span"),
        ({"quote": 5}, "invalid_span"),
        ({"source_id": 5}, "invalid_context"),
        ({"evidence_id": 5}, "invalid_context"),
        ({"alignment_score": True}, "unsupported_evidence"),
        ({"source_id": "another-source"}, "invalid_context"),
    ],
    ids=["end_bool", "quote_type", "source_id_type", "evidence_id_type", "score_bool", "foreign"],
)
def test_inclusion_fields_are_typed(mutation: dict[str, Any], code: str) -> None:
    context, record, leaf, edge = _valid_graph()
    payload = {**leaf.model_dump(), **mutation}
    if mutation.get("source_id") == "another-source":
        # Keep the ID consistent with the forged source, so only the source check can fail.
        payload["evidence_id"] = inclusion_id(
            source_id="another-source", start=payload["start"], end=payload["end"]
        )
    _assert_error(code, validate_state, context, claims=[record], evidence=[payload], edges=[edge])


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ({"quote": 5}, "invalid_span"),
        ({"fragments": [[1, 2, 3]]}, "invalid_span"),
        ({"fragments": [["a", "b"]]}, "invalid_span"),
    ],
    ids=["quote_type", "fragment_arity", "fragment_types"],
)
def test_mention_fields_are_typed(mutation: dict[str, Any], code: str) -> None:
    graph = _graph()
    _register(graph, "pT2", "tnm_t", TEXT.index("pT2"))
    payload = json.loads(json.dumps(graph.to_payload()))
    payload["mentions"][0].update(mutation)
    with pytest.raises(GraphValidationError, match=f"^{code}$"):
        _restore(graph, payload)


def test_claim_values_are_stored_in_json_form() -> None:
    from datetime import date

    class Dated(AnswerModel):
        on: date

    variables = VariableSet.create(
        name="dated", version="1.0.0",
        variables=(Variable(name="when", level="note", answer_model=Dated,
                            instructions="Invented.", final=True),),
    )  # fmt: skip
    graph = EvidenceGraph(source=SOURCE, variables=variables, sensitivity=Sensitivity.synthetic)
    from amber.tools.quote import quote

    leaf = quote(SOURCE.source_id, "pT2", None, sources=SOURCES)
    graph.register_evidence(leaf)  # type: ignore[arg-type]
    claim = graph.commit_claim(
        "when", {"on": "2026-01-02"}, [leaf.evidence_id], "Invented.",  # type: ignore[union-attr]
        provenance=make_provenance(),
    )  # fmt: skip
    assert claim.value == {"on": "2026-01-02"}
