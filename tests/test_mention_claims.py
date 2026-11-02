"""Adversarial tests for mention-level claims, required attributes, and section rules
(contract v0.1 sections 2.3 and 3.3)."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import pytest
from pydantic import Field

from amber.graph import EvidenceGraph
from amber.graph_errors import GraphValidationError
from amber.ids import new_ulid
from amber.schemas import (
    AnswerModel,
    Attribute,
    AttributeRequirement,
    Claim,
    Inclusion,
    Mention,
    MentionReading,
    Section,
    Sensitivity,
    Source,
    SourceKind,
    Variable,
    VariableSet,
)
from amber.tools.mention import cite, mention
from amber.tools.quote import quote

from ._claim_helpers import make_provenance

LINE_1 = "Final diagnosis: pT2 tumor; no evidence of distant metastasis.\r\n"
LINE_2 = "History: pT3 \U0001f642 prior; grade 2.\r\n"
TEXT = LINE_1 + LINE_2


class TAnswer(AnswerModel):
    t: str = Field(strict=True)


class MAnswer(AnswerModel):
    m: str = Field(strict=True)


class GradeAnswer(AnswerModel):
    grade: int = Field(strict=True)


SOURCE = Source.create(
    patient_id="synthetic-patient-1",
    kind=SourceKind.note,
    external_id="synthetic-note-1",
    datetime=None,
    text=TEXT,
    record=None,
    sections=(
        Section(start=0, end=len(LINE_1), label="Final diagnosis", category="final_diagnosis"),
        Section(start=len(LINE_1), end=len(TEXT), label="History", category="history"),
    ),
)
SOURCES = {SOURCE.source_id: SOURCE}


def _variables(
    negation_values: tuple[bool | int | str, ...] | None = (True, False),
    negation_evidence: str = "required",
    m_policy: str = "claim",
) -> VariableSet:
    return VariableSet.create(
        name="synthetic_staging",
        version="1.0.0",
        variables=(
            Variable(
                name="pathologic_T",
                level="mention",
                answer_model=TAnswer,
                instructions="Invented.",
                mention_types=frozenset({"tnm_t"}),
                requires_section=frozenset({"final_diagnosis"}),
                final=True,
            ),
            Variable(
                name="pathologic_M",
                level="mention",
                answer_model=MAnswer,
                instructions="Invented.",
                evidence_policy=m_policy,  # type: ignore[arg-type]
                mention_types=frozenset({"tnm_m"}),
                requires_attributes=(
                    AttributeRequirement(
                        category="negation",
                        values=negation_values,
                        evidence=negation_evidence,  # type: ignore[arg-type]
                    ),
                ),
                final=True,
            ),
            Variable(
                name="grade",
                level="note",
                answer_model=GradeAnswer,
                instructions="Invented.",
                final=True,
            ),
        ),
    )


def _assert_error(code: str, function: Callable[..., Any], /, *args: Any, **kwargs: Any) -> None:
    with pytest.raises(GraphValidationError) as failure:
        function(*args, **kwargs)
    assert failure.value.code == code
    assert str(failure.value) == code


def _graph(**kwargs: Any) -> EvidenceGraph:
    return EvidenceGraph(
        source=SOURCE, variables=_variables(**kwargs), sensitivity=Sensitivity.synthetic
    )


def _inclusion(graph: EvidenceGraph, text: str, hint: int | None = None) -> Inclusion:
    result = quote(SOURCE.source_id, text, hint, sources=SOURCES)
    assert isinstance(result, Inclusion)
    return graph.register_evidence(result)  # type: ignore[return-value]


def _span(
    graph: EvidenceGraph,
    text: str,
    mention_type: str,
    hint: int | None = None,
    *,
    attributes: Mapping[str, Attribute] | None = None,
    status: str = "proposed",
    **kwargs: Any,
) -> tuple[Mention, MentionReading]:
    span = mention(SOURCE.source_id, text, hint, mention_type, sources=SOURCES, **kwargs)
    assert isinstance(span, Mention)
    reading = MentionReading(
        reading_id=new_ulid(),
        mention_id=span.mention_id,
        value=text,
        attributes=attributes or {},
        status=status,  # type: ignore[arg-type]
        provenance=make_provenance(),
    )
    graph.register_mention(span, [reading])
    return span, reading


def _commit(
    graph: EvidenceGraph,
    variable: str,
    value: dict[str, Any],
    evidence_ids: Sequence[str],
    reading_id: str | None,
    field_roles: Mapping[str, Sequence[str]] | None = None,
) -> Claim:
    return graph.commit_claim(
        variable,
        value,
        evidence_ids,
        "Invented rationale.",
        field_roles,
        provenance=make_provenance(),
        reading_id=reading_id,
    )


def _t_setup() -> tuple[EvidenceGraph, Mention, MentionReading, Inclusion]:
    graph = _graph()
    span, reading = _span(graph, "pT2", "tnm_t")
    (position,) = cite(span, sources=SOURCES)
    graph.register_evidence(position)
    return graph, span, reading, position


def _m_setup(
    attribute: Attribute | None = None, **kwargs: Any
) -> tuple[EvidenceGraph, MentionReading, Inclusion, Inclusion]:
    graph = _graph(**kwargs)
    cue = _inclusion(graph, "no evidence of")
    if attribute is None:
        attribute = Attribute(value=True, evidence_id=cue.evidence_id)
    _, reading = _span(graph, "distant metastasis", "tnm_m", attributes={"negation": attribute})
    position = _inclusion(graph, "distant metastasis")
    return graph, reading, cue, position


def _commit_m(
    graph: EvidenceGraph,
    reading: MentionReading,
    position: Inclusion,
    cue: Inclusion,
    roles: Mapping[str, Sequence[str]] | None = None,
) -> Claim:
    return _commit(
        graph,
        "pathologic_M",
        {"m": "M0"},
        [position.evidence_id, cue.evidence_id],
        reading.reading_id,
        {"attribute:negation": [cue.evidence_id]} if roles is None else roles,
    )


# Readings ----------------------------------------------------------------------------------


def test_a_mention_level_claim_relies_on_its_reading_and_cites_its_span() -> None:
    graph, _, reading, position = _t_setup()
    claim = _commit(graph, "pathologic_T", {"t": "T2"}, [position.evidence_id], reading.reading_id)

    assert claim.reading_id == reading.reading_id
    restored = EvidenceGraph.from_payload(
        json.loads(json.dumps(graph.to_payload())),
        source=SOURCE,
        variables=_variables(),
        sensitivity=Sensitivity.synthetic,
    )
    assert restored.claims[claim.claim_id] == claim


@pytest.mark.parametrize("reading_id", [None, "unknown", "new"])
def test_a_mention_level_claim_needs_a_registered_reading(reading_id: str | None) -> None:
    graph, _, _, position = _t_setup()
    code = "invalid_schema" if reading_id == "unknown" else "missing_reading"
    bound = new_ulid() if reading_id == "new" else reading_id
    _assert_error(code, _commit, graph, "pathologic_T", {"t": "T2"}, [position.evidence_id], bound)
    assert graph.claims == {}


def test_a_note_level_claim_must_not_name_a_reading() -> None:
    graph, _, reading, position = _t_setup()
    _assert_error(
        "invalid_context",
        _commit,
        graph,
        "grade",
        {"grade": 2},
        [position.evidence_id],
        reading.reading_id,
    )


def test_a_claim_never_relies_on_a_rejected_reading() -> None:
    graph = _graph()
    span, reading = _span(graph, "pT2", "tnm_t", status="rejected")
    (position,) = cite(span, sources=SOURCES)
    graph.register_evidence(position)
    _assert_error(
        "invalid_context",
        _commit,
        graph,
        "pathologic_T",
        {"t": "T2"},
        [position.evidence_id],
        reading.reading_id,
    )


def test_a_claim_relies_on_a_span_of_a_permitted_type() -> None:
    graph = _graph()
    span, reading = _span(graph, "pT2", "tnm_n")
    (position,) = cite(span, sources=SOURCES)
    graph.register_evidence(position)
    _assert_error(
        "invalid_context",
        _commit,
        graph,
        "pathologic_T",
        {"t": "T2"},
        [position.evidence_id],
        reading.reading_id,
    )


def test_a_claim_must_cite_its_reading_span() -> None:
    graph, _, reading, _ = _t_setup()
    elsewhere = _inclusion(graph, "tumor")
    _assert_error(
        "uncited_mention",
        _commit,
        graph,
        "pathologic_T",
        {"t": "T2"},
        [elsewhere.evidence_id],
        reading.reading_id,
    )


def test_a_discontinuous_span_is_cited_at_every_fragment() -> None:
    graph = _graph()
    start = TEXT.index("pT2")
    end = TEXT.index("tumor") + len("tumor")
    fragments = ((start, start + 3), (TEXT.index("tumor"), end))
    span, reading = _span(graph, TEXT[start:end], "tnm_t", fragments=fragments)
    hull = _inclusion(graph, TEXT[start:end])
    pieces = [graph.register_evidence(piece) for piece in cite(span, sources=SOURCES)]

    _assert_error(
        "uncited_mention",
        _commit,
        graph,
        "pathologic_T",
        {"t": "T2"},
        [hull.evidence_id, pieces[0].evidence_id],
        reading.reading_id,
    )
    claim = _commit(
        graph, "pathologic_T", {"t": "T2"}, [p.evidence_id for p in pieces], reading.reading_id
    )
    assert claim.reading_id == reading.reading_id


def test_a_claim_supported_only_by_its_reading_has_no_evidence() -> None:
    graph, _, reading, position = _t_setup()
    claim = _commit(graph, "pathologic_T", {"t": "T2"}, [position.evidence_id], reading.reading_id)
    payload = graph.to_payload()
    payload["edges"] = [edge for edge in payload["edges"] if edge["claim_id"] != claim.claim_id]
    _assert_error(
        "missing_evidence",
        EvidenceGraph.from_payload,
        payload,
        source=SOURCE,
        variables=_variables(),
        sensitivity=Sensitivity.synthetic,
    )


# Sections ----------------------------------------------------------------------------------


def test_citations_must_lie_in_a_required_section() -> None:
    graph = _graph()
    span, reading = _span(graph, "pT3", "tnm_t")
    (position,) = cite(span, sources=SOURCES)
    graph.register_evidence(position)
    _assert_error(
        "citation_outside_section",
        _commit,
        graph,
        "pathologic_T",
        {"t": "T3"},
        [position.evidence_id],
        reading.reading_id,
    )

    graph, _, reading, position = _t_setup()
    history = _inclusion(graph, "grade 2")
    _assert_error(
        "citation_outside_section",
        _commit,
        graph,
        "pathologic_T",
        {"t": "T2"},
        [position.evidence_id, history.evidence_id],
        reading.reading_id,
    )
    crossing = _inclusion(graph, "metastasis.\r\nHistory")
    _assert_error(
        "citation_outside_section",
        _commit,
        graph,
        "pathologic_T",
        {"t": "T2"},
        [position.evidence_id, crossing.evidence_id],
        reading.reading_id,
    )


# Attributes --------------------------------------------------------------------------------


def test_a_required_attribute_is_satisfied_by_its_cue_under_the_attribute_role() -> None:
    graph, reading, cue, position = _m_setup()
    claim = _commit_m(graph, reading, position, cue)
    assert claim.variable == "pathologic_M"


def test_a_required_attribute_without_the_attribute_edge_fails() -> None:
    graph, reading, cue, position = _m_setup()
    _assert_error("missing_attribute_evidence", _commit_m, graph, reading, position, cue, {})


def test_a_cue_cited_under_a_field_role_does_not_satisfy_an_attribute() -> None:
    graph, reading, cue, position = _m_setup()
    _assert_error(
        "missing_attribute_evidence",
        _commit_m,
        graph,
        reading,
        position,
        cue,
        {"m": [cue.evidence_id]},
    )


def test_a_different_inclusion_under_the_attribute_role_is_rejected() -> None:
    graph, reading, cue, position = _m_setup()
    _assert_error(
        "invalid_context",
        _commit_m,
        graph,
        reading,
        position,
        cue,
        {"attribute:negation": [position.evidence_id]},
    )
    _assert_error(
        "invalid_context",
        _commit_m,
        graph,
        reading,
        position,
        cue,
        {"attribute:temporality": [cue.evidence_id]},
    )


def test_attribute_edges_are_refused_on_note_level_claims() -> None:
    graph, _, cue, position = _m_setup()
    _assert_error(
        "invalid_context",
        _commit,
        graph,
        "grade",
        {"grade": 2},
        [position.evidence_id, cue.evidence_id],
        None,
        {"attribute:negation": [cue.evidence_id]},
    )


def test_a_cueless_attribute_cannot_satisfy_a_required_attribute() -> None:
    graph, reading, cue, position = _m_setup(Attribute(value=True))
    _assert_error("missing_attribute_evidence", _commit_m, graph, reading, position, cue, {})


def test_a_lenient_attribute_accepts_an_asserted_value() -> None:
    graph, reading, cue, position = _m_setup(Attribute(value=True), negation_evidence="lenient")
    assert _commit_m(graph, reading, position, cue, {}).reading_id == reading.reading_id


@pytest.mark.parametrize(
    ("values", "value"),
    [((True,), False), ((1,), True), ((True,), 1), (("true",), True), ((False, 0), "false")],
)
def test_attribute_values_must_be_permitted_with_strict_types(
    values: tuple[bool | int | str, ...], value: bool | int | str
) -> None:
    graph = _graph(negation_values=values)
    cue = _inclusion(graph, "no evidence of")
    _, reading = _span(
        graph,
        "distant metastasis",
        "tnm_m",
        attributes={"negation": Attribute(value=value, evidence_id=cue.evidence_id)},
    )
    position = _inclusion(graph, "distant metastasis")
    _assert_error("attribute_value_not_permitted", _commit_m, graph, reading, position, cue)


def test_an_absent_required_category_fails() -> None:
    graph = _graph()
    cue = _inclusion(graph, "no evidence of")
    _, reading = _span(
        graph,
        "distant metastasis",
        "tnm_m",
        attributes={"temporality": Attribute(value="current", evidence_id=cue.evidence_id)},
    )
    position = _inclusion(graph, "distant metastasis")
    _assert_error("attribute_value_not_permitted", _commit_m, graph, reading, position, cue, {})


def test_attribute_edges_do_not_count_toward_field_coverage() -> None:
    graph, reading, cue, position = _m_setup(m_policy="field")
    _assert_error("missing_field_evidence", _commit_m, graph, reading, position, cue)
    claim = _commit_m(
        graph,
        reading,
        position,
        cue,
        {"attribute:negation": [cue.evidence_id], "m": [position.evidence_id]},
    )
    assert claim.variable == "pathologic_M"


def test_two_readings_of_one_span_are_each_checked_against_their_own_attributes() -> None:
    graph, cued, cue, position = _m_setup()
    span = graph.mentions_at(position.evidence_id)[0]
    asserted = MentionReading(
        reading_id=new_ulid(),
        mention_id=span.mention_id,
        attributes={"negation": Attribute(value=True)},
        provenance=make_provenance().model_copy(update={"producer": "model:synthetic@1"}),
    )
    graph.register_reading(asserted)

    assert _commit_m(graph, cued, position, cue).reading_id == cued.reading_id
    _assert_error(
        "invalid_context",
        _commit_m,
        graph,
        asserted,
        position,
        cue,
    )
    _assert_error("missing_attribute_evidence", _commit_m, graph, asserted, position, cue, {})


def test_restored_attribute_edges_are_revalidated() -> None:
    graph, reading, cue, position = _m_setup()
    claim = _commit_m(graph, reading, position, cue)
    payload = graph.to_payload()
    payload["edges"] = [
        edge
        for edge in payload["edges"]
        if not (edge["claim_id"] == claim.claim_id and edge["role"] == "attribute:negation")
    ]
    _assert_error(
        "missing_attribute_evidence",
        EvidenceGraph.from_payload,
        payload,
        source=SOURCE,
        variables=_variables(),
        sensitivity=Sensitivity.synthetic,
    )
