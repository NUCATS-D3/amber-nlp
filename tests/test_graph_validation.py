"""Adversarial tests for complete private graph validation."""

from __future__ import annotations

import warnings
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

import pytest
from pydantic import Field

from amber._graph_validation import build_context, validate_state
from amber.graph_errors import GraphValidationError
from amber.grounding import exact_quote
from amber.ids import inclusion_id, new_ulid, schema_ref
from amber.schemas import (
    AnswerModel,
    Claim,
    Inclusion,
    Sensitivity,
    SourceKind,
    Task,
)

from ._claim_helpers import (
    make_claim_record,
    make_inference_record,
    make_provenance,
    make_source,
    make_task,
)

FIELD = "progression_or_recurrence"


def _assert_error(code: str, function: Any, /, *args: Any, **kwargs: Any) -> None:
    with pytest.raises(GraphValidationError) as failure:
        function(*args, **kwargs)
    assert failure.value.code == code
    assert str(failure.value) == code


def _valid_graph() -> tuple[Any, dict[str, Any], Inclusion, dict[str, Any]]:
    source = make_source("Stable disease.")
    leaf = exact_quote(source, "Stable disease.")
    assert isinstance(leaf, Inclusion)
    context = build_context(
        source=source,
        task=make_task(),
        sensitivity=Sensitivity.synthetic,
    )
    record = make_claim_record(
        source=source,
        value={"progression_or_recurrence": False},
    )
    edge = {
        "claim_id": record["claim_id"],
        "evidence_id": leaf.evidence_id,
        "role": FIELD,
    }
    return context, record, leaf, edge


def test_valid_complete_graph_mints_a_real_claim_and_read_only_owned_state() -> None:
    context, record, leaf, edge = _valid_graph()

    state = validate_state(
        context,
        claims=[record],
        evidence=[leaf.model_dump()],
        edges=[edge],
    )
    record["value"][FIELD] = True
    edge["role"] = None

    claim = state.claims[record["claim_id"]]
    assert isinstance(claim, Claim)
    assert claim.value == {FIELD: False}
    assert state.evidence[leaf.evidence_id] == leaf
    assert state.adjacency[claim.claim_id] == (leaf.evidence_id,)
    assert state.edges[0].role == FIELD
    for mapping in (state.claims, state.evidence, state.adjacency):
        assert isinstance(mapping, Mapping)
        with pytest.raises(TypeError):
            mapping["changed"] = object()  # type: ignore[index]


def test_whole_claim_edge_does_not_satisfy_field_policy() -> None:
    context, record, leaf, edge = _valid_graph()
    edge.pop("role")

    with pytest.raises(GraphValidationError) as failure:
        validate_state(
            context,
            claims=[record],
            evidence=[leaf.model_dump()],
            edges=[edge],
        )

    assert failure.value.code == "missing_field_evidence"
    assert str(failure.value) == "missing_field_evidence"


def test_validation_errors_do_not_render_source_bearing_values() -> None:
    context, record, _, _ = _valid_graph()
    record["value"] = {FIELD: "invented sensitive value"}

    with pytest.raises(GraphValidationError) as failure:
        validate_state(context, claims=[record], evidence=[], edges=[])

    assert failure.value.code == "invalid_schema"
    assert str(failure.value) == "invalid_schema"
    assert "invented sensitive value" not in str(failure.value)


def test_context_revalidates_source_and_task_and_breaks_mutable_aliases() -> None:
    source = make_source()
    source.meta["nested"] = {"value": "original"}
    task = make_task()

    context = build_context(
        source=source,
        task=task,
        sensitivity=Sensitivity.synthetic,
        excluded_spans=((0, 2), (2, 4)),
    )
    source.meta["nested"]["value"] = "changed"

    assert context.source is not source
    assert context.task is not task
    assert context.source.meta == {"nested": {"value": "original"}}
    assert context.excluded_spans == ((0, 2), (2, 4))
    assert context.schema_ref == schema_ref(task.answer_model)


@pytest.mark.parametrize("target", ["source", "task"])
def test_context_revalidation_suppresses_source_bearing_serializer_warnings(target: str) -> None:
    source = make_source()
    task = make_task()
    secret = "private source-bearing value"
    if target == "source":
        source = source.model_copy(update={"text": [secret]})
    else:
        task = task.model_copy(update={"instructions": [secret]})

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        _assert_error(
            "invalid_context",
            build_context,
            source=source,
            task=task,
            sensitivity=Sensitivity.synthetic,
        )

    assert captured == []


@pytest.mark.parametrize(
    "source",
    [
        make_source().model_copy(update={"source_id": "wrong"}),
        make_source().model_copy(update={"kind": SourceKind.lab}),
        make_source().model_copy(update={"text": None}),
    ],
)
def test_context_rejects_forged_or_non_note_sources(source: Any) -> None:
    _assert_error(
        "invalid_context",
        build_context,
        source=source,
        task=make_task(),
        sensitivity=Sensitivity.synthetic,
    )


def test_context_rejects_patient_scope_and_non_explicit_sensitivity() -> None:
    _assert_error(
        "invalid_context",
        build_context,
        source=make_source(),
        task=make_task().model_copy(update={"scope": "patient"}),
        sensitivity=Sensitivity.synthetic,
    )
    _assert_error(
        "invalid_context",
        build_context,
        source=make_source(),
        task=make_task(),
        sensitivity="synthetic",  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    "excluded_spans",
    [
        ([0, 1],),
        ((True, 1),),
        ((0.0, 1),),
        ((0, 0),),
        ((2, 1),),
        ((0, 11),),
        ((4, 6), (2, 3)),
        ((0, 4), (3, 5)),
    ],
)
def test_context_rejects_malformed_unsorted_or_overlapping_exclusions(
    excluded_spans: Any,
) -> None:
    _assert_error(
        "invalid_context",
        build_context,
        source=make_source(),
        task=make_task(),
        sensitivity=Sensitivity.synthetic,
        excluded_spans=excluded_spans,
    )


def test_exact_unicode_crlf_and_repeated_occurrence_offsets_are_preserved() -> None:
    text = "é\r\nsame / same"
    source = make_source(text)
    start = text.rindex("same")
    leaf = exact_quote(source, "same", hint_start=start)
    assert isinstance(leaf, Inclusion)
    context = build_context(source=source, task=make_task(), sensitivity=Sensitivity.synthetic)
    record = make_claim_record(source=source, value={FIELD: False})

    state = validate_state(
        context,
        claims=[record],
        evidence=[leaf.model_dump()],
        edges=[
            {
                "claim_id": record["claim_id"],
                "evidence_id": leaf.evidence_id,
                "role": FIELD,
            }
        ],
    )

    retained = state.evidence[leaf.evidence_id]
    assert isinstance(retained, Inclusion)
    assert (retained.start, retained.end, retained.quote) == (start, start + 4, "same")


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ({"extra": "unknown"}, "invalid_schema"),
        ({"source_id": "wrong"}, "invalid_context"),
        ({"evidence_id": "wrong"}, "invalid_context"),
        ({"start": True}, "invalid_span"),
        ({"start": 0.0}, "invalid_span"),
        ({"start": -1}, "invalid_span"),
        ({"start": 6, "end": 6}, "invalid_span"),
        ({"start": 7, "end": 6}, "invalid_span"),
        ({"end": 100}, "invalid_span"),
        ({"quote": "invented"}, "quote_mismatch"),
        ({"alignment": "fuzzy"}, "unsupported_evidence"),
        ({"alignment_score": False}, "unsupported_evidence"),
        ({"alignment_score": 0.99}, "unsupported_evidence"),
        ({"mention_id": "mention-1"}, "unsupported_evidence"),
    ],
)
def test_inclusion_payloads_are_independently_revalidated(
    mutation: dict[str, Any], code: str
) -> None:
    context, record, leaf, edge = _valid_graph()
    payload = leaf.model_dump()
    payload.update(mutation)
    if "extra" not in mutation and ("start" in mutation or "end" in mutation):
        start = payload["start"]
        end = payload["end"]
        if type(start) is int and type(end) is int:
            payload["evidence_id"] = inclusion_id(
                source_id=payload["source_id"], start=start, end=end
            )

    _assert_error(
        code,
        validate_state,
        context,
        claims=[record],
        evidence=[payload],
        edges=[edge],
    )


def test_inclusion_requires_every_field() -> None:
    context, record, leaf, edge = _valid_graph()
    payload = leaf.model_dump()
    payload.pop("alignment")

    _assert_error(
        "invalid_schema",
        validate_state,
        context,
        claims=[record],
        evidence=[payload],
        edges=[edge],
    )


def test_inclusion_may_omit_its_defaulted_null_mention_id() -> None:
    context, record, leaf, edge = _valid_graph()
    payload = leaf.model_dump()
    payload.pop("mention_id")

    state = validate_state(
        context,
        claims=[record],
        evidence=[payload],
        edges=[edge],
    )

    assert state.evidence[leaf.evidence_id] == leaf


def test_exclusions_reject_overlap_but_allow_touching_boundaries_and_fragment_gaps() -> None:
    source = make_source("0123456789")
    task = make_task()
    record = make_claim_record(source=source, value={FIELD: False})

    touching_context = build_context(
        source=source,
        task=task,
        sensitivity=Sensitivity.synthetic,
        excluded_spans=((0, 2), (5, 7)),
    )
    touching = exact_quote(source, "234", hint_start=2)
    assert isinstance(touching, Inclusion)
    state = validate_state(
        touching_context,
        claims=[record],
        evidence=[touching.model_dump()],
        edges=[
            {
                "claim_id": record["claim_id"],
                "evidence_id": touching.evidence_id,
                "role": FIELD,
            }
        ],
    )
    assert state.evidence[touching.evidence_id] == touching

    overlapping = exact_quote(source, "456", hint_start=4)
    assert isinstance(overlapping, Inclusion)
    _assert_error(
        "invalid_context",
        validate_state,
        touching_context,
        claims=[record],
        evidence=[overlapping.model_dump()],
        edges=[
            {
                "claim_id": record["claim_id"],
                "evidence_id": overlapping.evidence_id,
                "role": FIELD,
            }
        ],
    )


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("claim_id", "not-a-ulid", "invalid_schema"),
        ("source_id", "", "invalid_schema"),
        ("source_id", "wrong", "invalid_context"),
        ("patient_id", "wrong", "invalid_context"),
        ("task", "wrong", "invalid_context"),
        ("schema_ref", "wrong", "invalid_context"),
        ("status", "verified", "unsupported_claim_status"),
        ("status", "gold", "unsupported_claim_status"),
        ("value", {FIELD: None}, "invalid_schema"),
        ("value", {FIELD: 1}, "invalid_schema"),
        ("value", {FIELD: False, "extra": True}, "invalid_schema"),
    ],
)
def test_claim_local_schema_status_and_context_are_revalidated(
    field: str, value: Any, code: str
) -> None:
    context, record, leaf, edge = _valid_graph()
    record[field] = value

    _assert_error(
        code,
        validate_state,
        context,
        claims=[record],
        evidence=[leaf.model_dump()],
        edges=[edge],
    )


def test_claim_and_inference_provenance_must_match_declared_sensitivity() -> None:
    context, record, leaf, edge = _valid_graph()
    record["provenance"] = {
        **record["provenance"],
        "sensitivity": Sensitivity.deidentified,
    }
    _assert_error(
        "invalid_context",
        validate_state,
        context,
        claims=[record],
        evidence=[leaf.model_dump()],
        edges=[edge],
    )

    context, record, leaf, edge = _valid_graph()
    inference = make_inference_record(inputs=[leaf.evidence_id])
    inference["provenance"] = {
        **inference["provenance"],
        "sensitivity": Sensitivity.deidentified,
    }
    edge["evidence_id"] = inference["evidence_id"]
    _assert_error(
        "invalid_context",
        validate_state,
        context,
        claims=[record],
        evidence=[leaf.model_dump(), inference],
        edges=[edge],
    )


def test_forged_nested_provenance_is_revalidated() -> None:
    context, record, leaf, edge = _valid_graph()
    secret = "private provenance value"
    provenance = make_provenance().model_copy(update={"producer": [secret]})
    record["provenance"] = provenance

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        _assert_error(
            "invalid_schema",
            validate_state,
            context,
            claims=[record],
            evidence=[leaf.model_dump()],
            edges=[edge],
        )

    assert captured == []


def test_forged_inference_provenance_suppresses_source_bearing_serializer_warnings() -> None:
    context, record, leaf, edge = _valid_graph()
    secret = "private inference provenance"
    provenance = make_provenance().model_copy(update={"producer": [secret]})
    inference = make_inference_record(inputs=[leaf.evidence_id])
    inference["provenance"] = provenance
    edge["evidence_id"] = inference["evidence_id"]

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        _assert_error(
            "invalid_schema",
            validate_state,
            context,
            claims=[record],
            evidence=[leaf.model_dump(), inference],
            edges=[edge],
        )

    assert captured == []


def test_two_field_answer_requires_evidence_for_a_defaulted_field() -> None:
    class TwoFieldAnswer(AnswerModel):
        documented: bool = Field(strict=True)
        reviewed: bool = Field(default=False, strict=True)

    source = make_source("Documented.")
    leaf = exact_quote(source, "Documented.")
    assert isinstance(leaf, Inclusion)
    task = Task(
        name="two_field",
        answer_model=TwoFieldAnswer,
        instructions="Synthetic two-field task.",
        evidence_policy="field",
    )
    context = build_context(source=source, task=task, sensitivity=Sensitivity.synthetic)
    record = make_claim_record(source=source, value={"documented": True}, task=task)
    first_edge = {
        "claim_id": record["claim_id"],
        "evidence_id": leaf.evidence_id,
        "role": "documented",
    }

    _assert_error(
        "missing_field_evidence",
        validate_state,
        context,
        claims=[record],
        evidence=[leaf.model_dump()],
        edges=[first_edge],
    )
    state = validate_state(
        context,
        claims=[record],
        evidence=[leaf.model_dump()],
        edges=[first_edge, {**first_edge, "role": "reviewed"}],
    )
    assert state.claims[record["claim_id"]].value == {
        "documented": True,
        "reviewed": False,
    }


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ({"rationale": ""}, "invalid_schema"),
        ({"inputs": []}, "invalid_schema"),
        ({"inputs": ["same", "same"]}, "invalid_schema"),
        ({"evidence_id": "not-a-ulid"}, "invalid_schema"),
        ({"extra": "unknown"}, "invalid_schema"),
    ],
)
def test_inference_local_fields_are_revalidated(mutation: dict[str, Any], code: str) -> None:
    context, record, leaf, edge = _valid_graph()
    inference = make_inference_record(inputs=[leaf.evidence_id])
    inference.update(mutation)
    edge["evidence_id"] = inference["evidence_id"]

    _assert_error(
        code,
        validate_state,
        context,
        claims=[record],
        evidence=[leaf.model_dump(), inference],
        edges=[edge],
    )


@pytest.mark.parametrize("kind", ["structured", "mention", None, 3])
def test_unsupported_evidence_kinds_are_rejected(kind: Any) -> None:
    context, record, leaf, edge = _valid_graph()
    unsupported = {"kind": kind, "evidence_id": new_ulid()}

    _assert_error(
        "unsupported_evidence",
        validate_state,
        context,
        claims=[record],
        evidence=[leaf.model_dump(), unsupported],
        edges=[edge],
    )


def test_valid_inference_and_supporting_claim_chain_reaches_the_source() -> None:
    source = make_source("Stable disease.")
    leaf = exact_quote(source, "Stable disease.")
    assert isinstance(leaf, Inclusion)
    context = build_context(source=source, task=make_task(), sensitivity=Sensitivity.synthetic)
    supporting = make_claim_record(source=source, value={FIELD: False})
    inference = make_inference_record(inputs=[supporting["claim_id"], leaf.evidence_id])
    answer = make_claim_record(source=source, value={FIELD: False})

    state = validate_state(
        context,
        claims=[answer, supporting],
        evidence=[inference, leaf.model_dump()],
        edges=[
            {
                "claim_id": supporting["claim_id"],
                "evidence_id": leaf.evidence_id,
                "role": FIELD,
            },
            {
                "claim_id": answer["claim_id"],
                "evidence_id": inference["evidence_id"],
                "role": FIELD,
            },
        ],
    )

    assert state.adjacency[inference["evidence_id"]] == (
        supporting["claim_id"],
        leaf.evidence_id,
    )


@pytest.mark.parametrize(
    ("target", "code"),
    [
        ("unknown", "unknown_reference"),
        ("rejected", "invalid_context"),
    ],
)
def test_inference_inputs_reject_unknown_and_rejected_claims(target: str, code: str) -> None:
    context, answer, leaf, edge = _valid_graph()
    rejected = make_claim_record(source=context.source, value={FIELD: False})
    rejected["status"] = "rejected"
    input_id = rejected["claim_id"] if target == "rejected" else "missing-node"
    inference = make_inference_record(inputs=[input_id])
    edge["evidence_id"] = inference["evidence_id"]

    _assert_error(
        code,
        validate_state,
        context,
        claims=[answer, rejected],
        evidence=[leaf.model_dump(), inference],
        edges=[edge],
    )


def test_rejected_claim_may_have_no_evidence_but_its_retained_edges_must_be_valid() -> None:
    source = make_source("Stable disease.")
    context = build_context(source=source, task=make_task(), sensitivity=Sensitivity.synthetic)
    rejected = make_claim_record(source=source, value={FIELD: False})
    rejected["status"] = "rejected"

    state = validate_state(context, claims=[rejected], evidence=[], edges=[])
    assert state.claims[rejected["claim_id"]].status == "rejected"

    _assert_error(
        "unknown_reference",
        validate_state,
        context,
        claims=[rejected],
        evidence=[],
        edges=[
            {
                "claim_id": rejected["claim_id"],
                "evidence_id": "missing-evidence",
            }
        ],
    )


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"claim_id": "missing-claim"}, "unknown_reference"),
        ({"evidence_id": "missing-evidence"}, "unknown_reference"),
        ({"role": "not_a_field"}, "invalid_context"),
        ({"weight": float("inf")}, "invalid_schema"),
        ({"extra": True}, "invalid_schema"),
    ],
)
def test_edges_reject_unknown_references_roles_and_malformed_fields(
    change: dict[str, Any], code: str
) -> None:
    context, record, leaf, edge = _valid_graph()
    edge.update(change)

    _assert_error(
        code,
        validate_state,
        context,
        claims=[record],
        evidence=[leaf.model_dump()],
        edges=[edge],
    )


def test_edge_targets_must_be_evidence_not_claims() -> None:
    context, record, leaf, edge = _valid_graph()
    other = make_claim_record(source=context.source, value={FIELD: False})
    edge["evidence_id"] = other["claim_id"]

    _assert_error(
        "unknown_reference",
        validate_state,
        context,
        claims=[record, other],
        evidence=[leaf.model_dump()],
        edges=[edge],
    )


def test_duplicate_claim_evidence_ids_and_edge_triples_are_rejected() -> None:
    context, record, leaf, edge = _valid_graph()
    duplicate_claim = deepcopy(record)
    _assert_error(
        "duplicate_reference",
        validate_state,
        context,
        claims=[record, duplicate_claim],
        evidence=[leaf.model_dump()],
        edges=[edge],
    )
    _assert_error(
        "duplicate_reference",
        validate_state,
        context,
        claims=[record],
        evidence=[leaf.model_dump(), leaf.model_dump()],
        edges=[edge],
    )
    _assert_error(
        "duplicate_reference",
        validate_state,
        context,
        claims=[record],
        evidence=[leaf.model_dump()],
        edges=[edge, {**edge, "weight": 0.5}],
    )


def test_claim_and_evidence_node_id_collision_is_rejected() -> None:
    context, record, leaf, edge = _valid_graph()
    inference = make_inference_record(inputs=[leaf.evidence_id])
    inference["evidence_id"] = record["claim_id"]

    _assert_error(
        "duplicate_reference",
        validate_state,
        context,
        claims=[record],
        evidence=[leaf.model_dump(), inference],
        edges=[edge],
    )


def test_non_rejected_claim_requires_at_least_one_edge_under_claim_policy() -> None:
    source = make_source()
    task = make_task(evidence_policy="claim")
    context = build_context(source=source, task=task, sensitivity=Sensitivity.synthetic)
    record = make_claim_record(source=source, value={FIELD: False}, task=task)

    _assert_error(
        "missing_evidence",
        validate_state,
        context,
        claims=[record],
        evidence=[],
        edges=[],
    )


def test_one_valid_branch_does_not_rescue_an_unknown_sibling_branch() -> None:
    context, record, leaf, edge = _valid_graph()
    inference = make_inference_record(inputs=[leaf.evidence_id, "missing-evidence"])
    edge["evidence_id"] = inference["evidence_id"]

    _assert_error(
        "unknown_reference",
        validate_state,
        context,
        claims=[record],
        evidence=[leaf.model_dump(), inference],
        edges=[edge],
    )


def test_self_mixed_and_disconnected_cycles_are_rejected() -> None:
    context, record, leaf, edge = _valid_graph()
    self_cycle = make_inference_record(inputs=[])
    self_cycle["inputs"] = [self_cycle["evidence_id"]]
    edge["evidence_id"] = self_cycle["evidence_id"]
    _assert_error(
        "cyclic_support",
        validate_state,
        context,
        claims=[record],
        evidence=[leaf.model_dump(), self_cycle],
        edges=[edge],
    )

    mixed = make_inference_record(inputs=[record["claim_id"]])
    edge["evidence_id"] = mixed["evidence_id"]
    _assert_error(
        "cyclic_support",
        validate_state,
        context,
        claims=[record],
        evidence=[leaf.model_dump(), mixed],
        edges=[edge],
    )

    first = make_inference_record(inputs=[])
    second = make_inference_record(inputs=[first["evidence_id"]])
    first["inputs"] = [second["evidence_id"]]
    direct_edge = {
        "claim_id": record["claim_id"],
        "evidence_id": leaf.evidence_id,
        "role": FIELD,
    }
    _assert_error(
        "cyclic_support",
        validate_state,
        context,
        claims=[record],
        evidence=[leaf.model_dump(), first, second],
        edges=[direct_edge],
    )


def test_every_disconnected_inference_component_is_checked() -> None:
    context, record, leaf, edge = _valid_graph()
    valid_disconnected = make_inference_record(inputs=[leaf.evidence_id])

    state = validate_state(
        context,
        claims=[record],
        evidence=[valid_disconnected, leaf.model_dump()],
        edges=[edge],
    )

    assert state.adjacency[valid_disconnected["evidence_id"]] == (leaf.evidence_id,)


def test_chain_deeper_than_python_recursion_limit_validates_iteratively() -> None:
    context, record, leaf, edge = _valid_graph()
    evidence: list[dict[str, Any]] = [leaf.model_dump()]
    child_id = leaf.evidence_id
    for _ in range(1_100):
        inference = make_inference_record(inputs=[child_id])
        evidence.append(inference)
        child_id = inference["evidence_id"]
    edge["evidence_id"] = child_id

    state = validate_state(
        context,
        claims=[record],
        evidence=list(reversed(evidence)),
        edges=[edge],
    )

    assert len(state.evidence) == 1_101
    assert state.adjacency[record["claim_id"]] == (child_id,)
