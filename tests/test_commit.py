"""End-to-end tests for atomic proposed-claim commits."""

from __future__ import annotations

import warnings
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import Field, ValidationError

import amber.graph as graph_module
import amber.tools.commit as commit_module
from amber.graph import EvidenceGraph
from amber.graph_errors import GraphValidationError
from amber.grounding import exact_quote
from amber.ids import new_ulid, schema_ref
from amber.schemas import (
    AnswerModel,
    Claim,
    Inclusion,
    InferenceEvidence,
    Sensitivity,
    Source,
    SourceKind,
    Task,
)

from ._claim_helpers import make_provenance, make_source, make_task

FIELD = "progression_or_recurrence"
RATIONALE = "Synthetic interpretation; no clinical assertion."


def _graph_with_leaf(
    text: str = "An invented current cancer statement.",
) -> tuple[EvidenceGraph, Task, Inclusion]:
    source = make_source(text)
    task = make_task()
    graph = EvidenceGraph(source=source, task=task, sensitivity=Sensitivity.synthetic)
    leaf = exact_quote(source, text)
    assert isinstance(leaf, Inclusion)
    graph.register_evidence(leaf)
    return graph, task, leaf


def _commit(
    graph: EvidenceGraph,
    task: Task,
    leaf: Inclusion,
    *,
    value: dict[str, Any] | None = None,
    evidence_ids: Any = None,
    rationale: Any = RATIONALE,
    field_roles: Any = None,
    provenance: Any = None,
    effective_datetime: Any = None,
    confidence: Any = None,
) -> Claim:
    selected_ids = [leaf.evidence_id] if evidence_ids is None else evidence_ids
    selected_roles = {FIELD: [leaf.evidence_id]} if field_roles is None else field_roles
    return commit_module.commit_claim(
        task,
        {FIELD: True} if value is None else value,
        selected_ids,
        rationale,
        selected_roles,
        graph=graph,
        provenance=make_provenance() if provenance is None else provenance,
        effective_datetime=effective_datetime,
        confidence=confidence,
    )


def _assert_atomic_error(
    code: str,
    graph: EvidenceGraph,
    operation: Callable[[], Any],
) -> None:
    before = deepcopy(graph.to_payload())
    with pytest.raises(GraphValidationError) as failure:
        operation()
    assert failure.value.code == code
    assert str(failure.value) == code
    assert graph.to_payload() == before


@pytest.mark.parametrize("value", [True, False])
def test_commit_retains_field_support_and_only_proposed_status(value: bool) -> None:
    source = make_source("An invented current cancer statement.")
    task = make_task()
    graph = EvidenceGraph(source=source, task=task, sensitivity=Sensitivity.synthetic)
    leaf = exact_quote(source, "An invented current cancer statement.")
    assert isinstance(leaf, Inclusion)
    graph.register_evidence(leaf)
    provenance = make_provenance().model_copy(update={"trace_id": "trace-synthetic-1"})
    claim = commit_module.commit_claim(
        task,
        {"progression_or_recurrence": value},
        [leaf.evidence_id],
        "Synthetic interpretation; no clinical assertion.",
        {"progression_or_recurrence": [leaf.evidence_id]},
        graph=graph,
        provenance=provenance,
    )
    assert claim.status == "proposed"
    assert claim.value == {"progression_or_recurrence": value}
    assert claim.source_id == source.source_id
    assert claim.patient_id == source.patient_id
    assert claim.task == task.name
    assert claim.schema_ref == schema_ref(task.answer_model)
    assert claim.effective_datetime == source.datetime
    assert claim.confidence is None
    assert claim.provenance == provenance
    assert [item.evidence_id for item in graph.source_leaves(claim.claim_id)] == [leaf.evidence_id]
    assert len(graph.claims) == 1
    assert len(graph.evidence) == 2

    inference = next(
        item for item in graph.evidence.values() if isinstance(item, InferenceEvidence)
    )
    assert inference.inputs == [leaf.evidence_id]
    assert inference.rationale == RATIONALE
    assert inference.trace_id == "trace-synthetic-1"
    assert inference.span_id is None
    assert inference.provenance == claim.provenance
    assert {(edge.claim_id, edge.evidence_id, edge.role, edge.weight) for edge in graph.edges} == {
        (claim.claim_id, inference.evidence_id, None, None),
        (claim.claim_id, leaf.evidence_id, FIELD, None),
    }


def test_commit_ids_are_distinct_for_each_record_and_repeated_commit() -> None:
    graph, task, leaf = _graph_with_leaf()

    first = _commit(graph, task, leaf)
    second = _commit(graph, task, leaf)
    inference_ids = {
        item.evidence_id for item in graph.evidence.values() if isinstance(item, InferenceEvidence)
    }

    assert first.claim_id != second.claim_id
    assert len(inference_ids) == 2
    assert first.claim_id not in inference_ids
    assert second.claim_id not in inference_ids
    assert len(graph.claims) == 2
    assert len(graph.evidence) == 3


def test_effective_datetime_defaults_to_source_and_accepts_override() -> None:
    graph, task, leaf = _graph_with_leaf()
    override = datetime(2025, 12, 31, 23, 0, tzinfo=UTC)

    defaulted = _commit(graph, task, leaf)
    overridden = _commit(graph, task, leaf, effective_datetime=override, confidence=0.25)

    assert defaulted.effective_datetime == graph.source.datetime
    assert overridden.effective_datetime == override
    assert overridden.confidence == 0.25

    undated = Source.create(
        patient_id="synthetic-patient-undated",
        kind=SourceKind.note,
        external_id="synthetic-note-undated",
        datetime=None,
        text="Invented undated statement.",
        record=None,
    )
    undated_task = make_task()
    undated_graph = EvidenceGraph(
        source=undated,
        task=undated_task,
        sensitivity=Sensitivity.synthetic,
    )
    undated_leaf = exact_quote(undated, "Invented undated statement.")
    assert isinstance(undated_leaf, Inclusion)
    undated_graph.register_evidence(undated_leaf)
    assert _commit(undated_graph, undated_task, undated_leaf).effective_datetime is None


@pytest.mark.parametrize(
    "value",
    [
        {FIELD: None},
        {FIELD: 1},
        {FIELD: "true"},
        {FIELD: True, "extra": False},
        {},
    ],
)
def test_invalid_answers_are_rejected_atomically(value: dict[str, Any]) -> None:
    graph, task, leaf = _graph_with_leaf()

    _assert_atomic_error("invalid_schema", graph, lambda: _commit(graph, task, leaf, value=value))


def test_non_mapping_answer_is_rejected_atomically() -> None:
    graph, task, leaf = _graph_with_leaf()

    _assert_atomic_error(
        "invalid_schema",
        graph,
        lambda: _commit(graph, task, leaf, value=[True]),  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    "evidence_ids",
    [[], [""], [" "], ["missing-evidence"], [1]],
)
def test_invalid_or_unknown_primary_evidence_is_rejected_atomically(
    evidence_ids: Any,
) -> None:
    graph, task, leaf = _graph_with_leaf()
    expected = "unknown_reference" if evidence_ids == ["missing-evidence"] else "invalid_schema"

    _assert_atomic_error(
        expected,
        graph,
        lambda: _commit(graph, task, leaf, evidence_ids=evidence_ids),
    )


def test_duplicate_primary_evidence_is_rejected_atomically() -> None:
    graph, task, leaf = _graph_with_leaf()

    _assert_atomic_error(
        "invalid_schema",
        graph,
        lambda: _commit(
            graph,
            task,
            leaf,
            evidence_ids=[leaf.evidence_id, leaf.evidence_id],
        ),
    )


@pytest.mark.parametrize("evidence_ids", [None, "not-a-sequence-of-ids", {"id"}])
def test_primary_evidence_requires_an_explicit_sequence(evidence_ids: Any) -> None:
    graph, task, leaf = _graph_with_leaf()

    _assert_atomic_error(
        "invalid_schema",
        graph,
        lambda: commit_module.commit_claim(
            task,
            {FIELD: True},
            evidence_ids,
            RATIONALE,
            {FIELD: [leaf.evidence_id]},
            graph=graph,
            provenance=make_provenance(),
        ),
    )


@pytest.mark.parametrize("rationale", ["", " ", "\t\n", 1])
def test_invalid_rationale_is_rejected_atomically(rationale: Any) -> None:
    graph, task, leaf = _graph_with_leaf()

    _assert_atomic_error(
        "invalid_schema",
        graph,
        lambda: _commit(graph, task, leaf, rationale=rationale),
    )


@pytest.mark.parametrize(
    ("field_roles", "code"),
    [
        ({"unknown_field": []}, "invalid_context"),
        ({FIELD: []}, "invalid_schema"),
        ({FIELD: [""]}, "invalid_schema"),
        ({FIELD: ["missing-evidence"]}, "invalid_context"),
    ],
)
def test_invalid_field_roles_are_rejected_atomically(
    field_roles: Any,
    code: str,
) -> None:
    graph, task, leaf = _graph_with_leaf()

    _assert_atomic_error(
        code,
        graph,
        lambda: _commit(graph, task, leaf, field_roles=field_roles),
    )


def test_duplicate_field_role_ids_are_rejected_atomically() -> None:
    graph, task, leaf = _graph_with_leaf()

    _assert_atomic_error(
        "invalid_schema",
        graph,
        lambda: _commit(
            graph,
            task,
            leaf,
            field_roles={FIELD: [leaf.evidence_id, leaf.evidence_id]},
        ),
    )


@pytest.mark.parametrize(
    ("field_roles", "code"),
    [
        ([], "invalid_schema"),
        ({1: ["identifier"]}, "invalid_context"),
        ({FIELD: "not-a-sequence-of-ids"}, "invalid_schema"),
    ],
)
def test_field_roles_require_a_mapping_to_identifier_sequences(
    field_roles: Any,
    code: str,
) -> None:
    graph, task, leaf = _graph_with_leaf()

    _assert_atomic_error(
        code,
        graph,
        lambda: _commit(graph, task, leaf, field_roles=field_roles),
    )


def test_field_evidence_must_be_a_subset_of_primary_evidence() -> None:
    source = make_source("First invented sentence. Second invented sentence.")
    task = make_task()
    graph = EvidenceGraph(source=source, task=task, sensitivity=Sensitivity.synthetic)
    first = exact_quote(source, "First invented sentence.")
    second = exact_quote(source, "Second invented sentence.")
    assert isinstance(first, Inclusion)
    assert isinstance(second, Inclusion)
    graph.register_evidence(first)
    graph.register_evidence(second)

    _assert_atomic_error(
        "invalid_context",
        graph,
        lambda: _commit(
            graph,
            task,
            first,
            evidence_ids=[first.evidence_id],
            field_roles={FIELD: [second.evidence_id]},
        ),
    )


@pytest.mark.parametrize("field_roles", [None, {}])
def test_field_policy_never_infers_an_omitted_role(field_roles: Any) -> None:
    graph, task, leaf = _graph_with_leaf()

    _assert_atomic_error(
        "missing_field_evidence",
        graph,
        lambda: commit_module.commit_claim(
            task,
            {FIELD: True},
            [leaf.evidence_id],
            RATIONALE,
            field_roles,
            graph=graph,
            provenance=make_provenance(),
        ),
    )


def test_claim_policy_accepts_omitted_field_roles_without_inference() -> None:
    graph, _, leaf = _graph_with_leaf()
    task = make_task(evidence_policy="claim")
    graph = EvidenceGraph(source=graph.source, task=task, sensitivity=Sensitivity.synthetic)
    graph.register_evidence(leaf)

    claim = commit_module.commit_claim(
        task,
        {FIELD: False},
        [leaf.evidence_id],
        RATIONALE,
        graph=graph,
        provenance=make_provenance(),
    )

    assert claim.status == "proposed"
    assert {edge.role for edge in graph.edges} == {None}


def test_wrong_or_forged_tasks_are_rejected_without_warnings_or_partial_state() -> None:
    graph, task, leaf = _graph_with_leaf()
    wrong = Task(
        **{
            **task.model_dump(),
            "instructions": "Different synthetic instructions.",
        }
    )
    _assert_atomic_error("invalid_context", graph, lambda: _commit(graph, wrong, leaf))

    secret = "source-bearing forged task"
    forged = task.model_copy(update={"instructions": [secret]})
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        _assert_atomic_error("invalid_context", graph, lambda: _commit(graph, forged, leaf))
    assert captured == []

    _assert_atomic_error(
        "invalid_context",
        graph,
        lambda: _commit(graph, None, leaf),  # type: ignore[arg-type]
    )


def test_invalid_or_wrong_sensitivity_provenance_is_atomic_and_warning_free() -> None:
    graph, task, leaf = _graph_with_leaf()
    secret = "source-bearing forged provenance"
    forged = make_provenance().model_copy(update={"producer": [secret]})
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        _assert_atomic_error(
            "invalid_schema",
            graph,
            lambda: _commit(graph, task, leaf, provenance=forged),
        )
    assert captured == []

    _assert_atomic_error(
        "invalid_schema",
        graph,
        lambda: commit_module.commit_claim(
            task,
            {FIELD: True},
            [leaf.evidence_id],
            RATIONALE,
            {FIELD: [leaf.evidence_id]},
            graph=graph,
            provenance=None,  # type: ignore[arg-type]
        ),
    )

    wrong_sensitivity = make_provenance().model_copy(
        update={"sensitivity": Sensitivity.deidentified}
    )
    _assert_atomic_error(
        "invalid_context",
        graph,
        lambda: _commit(graph, task, leaf, provenance=wrong_sensitivity),
    )


@pytest.mark.parametrize("confidence", [-0.01, 1.01, float("nan"), float("inf")])
def test_invalid_confidence_is_rejected_atomically(confidence: float) -> None:
    graph, task, leaf = _graph_with_leaf()

    _assert_atomic_error(
        "invalid_schema",
        graph,
        lambda: _commit(graph, task, leaf, confidence=confidence),
    )


def test_raw_claim_id_cannot_be_reused_as_primary_evidence() -> None:
    graph, task, leaf = _graph_with_leaf()
    supporting = _commit(graph, task, leaf)

    _assert_atomic_error(
        "unknown_reference",
        graph,
        lambda: _commit(graph, task, leaf, evidence_ids=[supporting.claim_id]),
    )


def test_registered_inference_can_reference_a_real_supporting_claim_chain() -> None:
    source = make_source("First invented finding. Second invented finding.")
    task = make_task()
    graph = EvidenceGraph(source=source, task=task, sensitivity=Sensitivity.synthetic)
    first = exact_quote(source, "First invented finding.")
    second = exact_quote(source, "Second invented finding.")
    assert isinstance(first, Inclusion)
    assert isinstance(second, Inclusion)
    graph.register_evidence(first)
    graph.register_evidence(second)
    supporting = _commit(graph, task, first, value={FIELD: False})
    bridge = InferenceEvidence(
        evidence_id=new_ulid(),
        rationale="Combine the synthetic support branches.",
        inputs=[supporting.claim_id, second.evidence_id],
        trace_id=None,
        span_id=None,
        provenance=make_provenance(),
    )
    graph.register_evidence(bridge)

    answer = commit_module.commit_claim(
        task,
        {FIELD: True},
        [bridge.evidence_id],
        RATIONALE,
        {FIELD: [bridge.evidence_id]},
        graph=graph,
        provenance=make_provenance(),
    )

    assert set(graph.support_closure(answer.claim_id)) == {
        answer.claim_id,
        supporting.claim_id,
        first.evidence_id,
        second.evidence_id,
        bridge.evidence_id,
        *(
            item.evidence_id
            for item in graph.evidence.values()
            if isinstance(item, InferenceEvidence) and item.evidence_id != bridge.evidence_id
        ),
    }
    assert {item.evidence_id for item in graph.source_leaves(answer.claim_id)} == {
        first.evidence_id,
        second.evidence_id,
    }


def test_defaulted_two_field_answer_requires_and_retains_both_field_roles() -> None:
    class TwoFieldAnswer(AnswerModel):
        documented: bool = Field(strict=True)
        reviewed: bool = Field(default=False, strict=True)

    source = make_source("Invented documentation reviewed.")
    task = Task(
        name="synthetic_two_field",
        answer_model=TwoFieldAnswer,
        instructions="Record two invented boolean fields.",
        evidence_policy="field",
    )
    graph = EvidenceGraph(source=source, task=task, sensitivity=Sensitivity.synthetic)
    leaf = exact_quote(source, "Invented documentation reviewed.")
    assert isinstance(leaf, Inclusion)
    graph.register_evidence(leaf)

    _assert_atomic_error(
        "missing_field_evidence",
        graph,
        lambda: commit_module.commit_claim(
            task,
            {"documented": True},
            [leaf.evidence_id],
            RATIONALE,
            {"documented": [leaf.evidence_id]},
            graph=graph,
            provenance=make_provenance(),
        ),
    )
    claim = commit_module.commit_claim(
        task,
        {"documented": True},
        [leaf.evidence_id],
        RATIONALE,
        {
            "documented": [leaf.evidence_id],
            "reviewed": [leaf.evidence_id],
        },
        graph=graph,
        provenance=make_provenance(),
    )

    assert claim.value == {"documented": True, "reviewed": False}
    assert {(edge.role, edge.evidence_id) for edge in graph.edges if edge.role} == {
        ("documented", leaf.evidence_id),
        ("reviewed", leaf.evidence_id),
    }


def test_successful_commit_snapshot_round_trip_revalidates_complete_state() -> None:
    graph, task, leaf = _graph_with_leaf()
    claim = _commit(graph, task, leaf)
    payload = graph.to_payload()

    restored = EvidenceGraph.from_payload(
        payload,
        source=graph.source,
        task=task,
        sensitivity=Sensitivity.synthetic,
    )

    assert restored.to_payload() == payload
    assert restored.claims[claim.claim_id] == claim
    restored.validate()
    assert restored.to_payload() == payload


def test_commit_result_and_nested_provenance_are_read_only_and_detached() -> None:
    graph, task, leaf = _graph_with_leaf()
    value = {FIELD: True}
    prompt_versions = {"extractor": "prompts:/synthetic/1"}
    provenance = make_provenance().model_copy(update={"prompt_versions": prompt_versions})

    claim = _commit(graph, task, leaf, value=value, provenance=provenance)
    value[FIELD] = False
    prompt_versions["extractor"] = "changed"

    assert claim.value == {FIELD: True}
    assert claim.provenance.prompt_versions == {"extractor": "prompts:/synthetic/1"}
    with pytest.raises(ValidationError, match="frozen"):
        claim.status = "rejected"  # type: ignore[misc]
    with pytest.raises(TypeError, match="frozen JSON values cannot be mutated"):
        claim.value[FIELD] = False
    with pytest.raises(TypeError, match="frozen JSON values cannot be mutated"):
        claim.provenance.prompt_versions["extractor"] = "changed"
    assert graph.claims[claim.claim_id] is not claim


def test_late_claim_inference_id_collision_does_not_publish_partial_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph, task, leaf = _graph_with_leaf()
    collision = new_ulid()
    monkeypatch.setattr(graph_module, "new_ulid", lambda: collision)

    _assert_atomic_error("duplicate_reference", graph, lambda: _commit(graph, task, leaf))


def test_exactly_grounded_semantically_wrong_citation_remains_only_proposed() -> None:
    source = make_source("No fever is documented in this invented note.")
    task = make_task()
    graph = EvidenceGraph(source=source, task=task, sensitivity=Sensitivity.synthetic)
    leaf = exact_quote(source, "No fever is documented in this invented note.")
    assert isinstance(leaf, Inclusion)
    graph.register_evidence(leaf)

    claim = _commit(graph, task, leaf, value={FIELD: True})

    assert claim.status == "proposed"
    assert claim.value == {FIELD: True}
    assert graph.source_leaves(claim.claim_id) == (leaf,)
