"""Public lifecycle tests for owned, validated evidence graphs."""

from __future__ import annotations

import json
import random
import warnings
from collections.abc import Callable
from copy import deepcopy
from typing import Any

import pytest

from amber.graph import EvidenceGraph
from amber.graph_errors import GraphValidationError
from amber.grounding import exact_quote
from amber.ids import schema_ref
from amber.schemas import Inclusion, Sensitivity

from ._claim_helpers import make_claim_record, make_inference_record, make_source, make_task

FIELD = "progression_or_recurrence"


def _assert_error(code: str, function: Callable[..., Any], /, *args: Any, **kwargs: Any) -> None:
    with pytest.raises(GraphValidationError) as failure:
        function(*args, **kwargs)
    assert failure.value.code == code
    assert str(failure.value) == code


def _payload(
    *,
    source: Any,
    task: Any,
    claims: list[dict[str, Any]] | None = None,
    evidence: list[dict[str, Any]] | None = None,
    edges: list[dict[str, Any]] | None = None,
    sensitivity: Sensitivity = Sensitivity.synthetic,
    excluded_spans: tuple[tuple[int, int], ...] = (),
) -> dict[str, Any]:
    return {
        "format_version": 1,
        "context": {
            "task": task.name,
            "schema_ref": schema_ref(task.answer_model),
            "evidence_policy": task.evidence_policy,
            "scope": task.scope,
            "source_id": source.source_id,
            "patient_id": source.patient_id,
            "sensitivity": sensitivity.value,
            "excluded_spans": [list(span) for span in excluded_spans],
        },
        "claims": [] if claims is None else claims,
        "evidence": [] if evidence is None else evidence,
        "edges": [] if edges is None else edges,
    }


def _graph_with_support_tree() -> tuple[EvidenceGraph, dict[str, Any]]:
    source = make_source("Primary stable. Secondary stable.")
    task = make_task()
    first = exact_quote(source, "Primary stable.")
    second = exact_quote(source, "Secondary stable.")
    assert isinstance(first, Inclusion)
    assert isinstance(second, Inclusion)
    supporting = make_claim_record(source=source, task=task, value={FIELD: False})
    inference = make_inference_record(inputs=[supporting["claim_id"], second.evidence_id])
    answer = make_claim_record(source=source, task=task, value={FIELD: False})
    payload = _payload(
        source=source,
        task=task,
        claims=[answer, supporting],
        evidence=[inference, second.model_dump(), first.model_dump()],
        edges=[
            {
                "claim_id": supporting["claim_id"],
                "evidence_id": first.evidence_id,
                "role": FIELD,
            },
            {
                "claim_id": answer["claim_id"],
                "evidence_id": inference["evidence_id"],
                "role": FIELD,
            },
        ],
    )
    graph = EvidenceGraph.from_payload(
        payload,
        source=source,
        task=task,
        sensitivity=Sensitivity.synthetic,
    )
    return graph, {
        "answer_id": answer["claim_id"],
        "supporting_id": supporting["claim_id"],
        "inference_id": inference["evidence_id"],
        "leaf_ids": tuple(sorted((first.evidence_id, second.evidence_id))),
    }


def test_registration_and_payload_are_isolated() -> None:
    source = make_source("Stable.")
    graph = EvidenceGraph(source=source, task=make_task(), sensitivity=Sensitivity.synthetic)
    leaf = exact_quote(source, "Stable.")
    assert isinstance(leaf, Inclusion)

    first = graph.register_evidence(leaf)
    second = graph.register_evidence(leaf)

    assert first == second == leaf
    assert len(graph.evidence) == 1
    payload = graph.to_payload()
    payload["evidence"][0]["quote"] = "invented alteration"
    assert graph.evidence[leaf.evidence_id].quote == "Stable."


def test_registration_revalidates_existing_ids_before_idempotency() -> None:
    source = make_source("Stable.")
    graph = EvidenceGraph(source=source, task=make_task(), sensitivity=Sensitivity.synthetic)
    leaf = exact_quote(source, "Stable.")
    assert isinstance(leaf, Inclusion)
    graph.register_evidence(leaf)
    before = graph.to_payload()
    secret = "private clinical text"
    forged = leaf.model_copy(update={"start": secret})

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        _assert_error("invalid_span", graph.register_evidence, forged)

    assert graph.to_payload() == before
    assert captured == []


def test_inference_registration_requires_valid_inputs_and_conflicts_are_atomic() -> None:
    source = make_source("Stable.")
    graph = EvidenceGraph(source=source, task=make_task(), sensitivity=Sensitivity.synthetic)
    leaf = exact_quote(source, "Stable.")
    assert isinstance(leaf, Inclusion)
    inference_data = make_inference_record(inputs=[leaf.evidence_id])

    from amber.schemas import InferenceEvidence

    inference = InferenceEvidence.model_validate(inference_data)
    _assert_error("unknown_reference", graph.register_evidence, inference)
    assert graph.evidence == {}

    graph.register_evidence(leaf)
    registered = graph.register_evidence(inference)
    assert registered == inference
    assert graph.register_evidence(inference) == inference
    before = graph.to_payload()

    conflicting = inference.model_copy(update={"rationale": "Different but locally valid."})
    _assert_error("duplicate_reference", graph.register_evidence, conflicting)
    assert graph.to_payload() == before

    invalid_conflict = inference.model_copy(update={"inputs": ["missing-node"]})
    _assert_error("unknown_reference", graph.register_evidence, invalid_conflict)
    assert graph.to_payload() == before


def test_context_and_views_do_not_expose_graph_owned_state() -> None:
    source = make_source("Stable.")
    source.meta["nested"] = {"value": "original"}
    task = make_task()
    graph = EvidenceGraph(
        source=source,
        task=task,
        sensitivity=Sensitivity.synthetic,
        excluded_spans=((0, 1),),
    )
    source.meta["nested"]["value"] = "caller mutation"

    first_source = graph.source
    first_source.meta["nested"]["value"] = "view mutation"
    assert graph.source.meta == {"nested": {"value": "original"}}
    assert graph.source is not graph.source
    assert graph.task == task
    assert graph.task is not graph.task
    assert graph.sensitivity is Sensitivity.synthetic
    assert graph.excluded_spans == ((0, 1),)

    leaf = exact_quote(source, "table", hint_start=1)
    assert isinstance(leaf, Inclusion)
    graph.register_evidence(leaf)
    first_view = graph.evidence
    second_view = graph.evidence
    assert first_view is not second_view
    with pytest.raises(TypeError):
        first_view["new"] = leaf  # type: ignore[index]
    assert graph.claims == {}
    assert graph.edges == ()


def test_source_view_copies_arbitrary_mutable_metadata() -> None:
    source = make_source("Stable.")
    source.meta["bytes"] = bytearray(b"original")
    graph = EvidenceGraph(source=source, task=make_task(), sensitivity=Sensitivity.synthetic)

    view = graph.source
    view.meta["bytes"][0] = ord("x")

    assert graph.source.meta["bytes"] == bytearray(b"original")
    assert graph.source.meta["bytes"] is not view.meta["bytes"]


def test_support_traversal_visits_all_branches_and_returns_sorted_source_leaves() -> None:
    graph, ids = _graph_with_support_tree()

    assert graph.support_closure(ids["answer_id"]) == tuple(
        sorted(
            (
                ids["answer_id"],
                ids["supporting_id"],
                ids["inference_id"],
                *ids["leaf_ids"],
            )
        )
    )
    leaves = graph.source_leaves(ids["answer_id"])
    assert tuple(leaf.evidence_id for leaf in leaves) == ids["leaf_ids"]

    _assert_error("unknown_reference", graph.support_closure, "missing-node")
    _assert_error("unknown_reference", graph.source_leaves, "missing-node")


def test_support_traversal_is_iterative_for_deep_graphs() -> None:
    source = make_source("Stable.")
    task = make_task()
    leaf = exact_quote(source, "Stable.")
    assert isinstance(leaf, Inclusion)
    evidence = [leaf.model_dump()]
    child_id = leaf.evidence_id
    for _ in range(1_100):
        inference = make_inference_record(inputs=[child_id])
        evidence.append(inference)
        child_id = inference["evidence_id"]
    claim = make_claim_record(source=source, task=task, value={FIELD: False})
    graph = EvidenceGraph.from_payload(
        _payload(
            source=source,
            task=task,
            claims=[claim],
            evidence=evidence,
            edges=[
                {
                    "claim_id": claim["claim_id"],
                    "evidence_id": child_id,
                    "role": FIELD,
                }
            ],
        ),
        source=source,
        task=task,
        sensitivity=Sensitivity.synthetic,
    )

    assert len(graph.support_closure(claim["claim_id"])) == 1_102
    assert tuple(leaf.evidence_id for leaf in graph.source_leaves(claim["claim_id"])) == (
        leaf.evidence_id,
    )


def test_snapshot_round_trip_is_json_safe_deterministic_and_revalidated() -> None:
    graph, ids = _graph_with_support_tree()
    canonical = graph.to_payload()
    serialized = json.loads(json.dumps(canonical))
    for records in (serialized["claims"], serialized["evidence"], serialized["edges"]):
        random.Random(1).shuffle(records)
    assert serialized["claims"] != canonical["claims"]
    assert serialized["evidence"] != canonical["evidence"]
    assert serialized["edges"] != canonical["edges"]

    restored = EvidenceGraph.from_payload(
        serialized,
        source=graph.source,
        task=graph.task,
        sensitivity=graph.sensitivity,
    )

    assert restored.to_payload() == canonical
    assert restored.support_closure(ids["answer_id"]) == graph.support_closure(ids["answer_id"])
    restored.validate()
    assert restored.to_payload() == canonical


def test_snapshot_uses_the_exact_context_envelope() -> None:
    source = make_source("Stable.")
    task = make_task()
    graph = EvidenceGraph(
        source=source,
        task=task,
        sensitivity=Sensitivity.synthetic,
        excluded_spans=((0, 1),),
    )

    assert graph.to_payload() == {
        "format_version": 1,
        "context": {
            "task": "oncology_current_progression",
            "schema_ref": schema_ref(task.answer_model),
            "evidence_policy": "field",
            "scope": "note",
            "source_id": source.source_id,
            "patient_id": "synthetic-patient-1",
            "sensitivity": "synthetic",
            "excluded_spans": [[0, 1]],
        },
        "claims": [],
        "evidence": [],
        "edges": [],
    }


@pytest.mark.parametrize("version", [True, 1.0, "1", None, 2])
def test_snapshot_rejects_non_exact_format_versions(version: Any) -> None:
    source = make_source()
    task = make_task()
    payload = _payload(source=source, task=task)
    payload["format_version"] = version

    _assert_error(
        "invalid_schema",
        EvidenceGraph.from_payload,
        payload,
        source=source,
        task=task,
        sensitivity=Sensitivity.synthetic,
    )


@pytest.mark.parametrize("level", ["payload", "context"])
@pytest.mark.parametrize("operation", ["missing", "extra"])
def test_snapshot_requires_exact_envelope_keys(level: str, operation: str) -> None:
    source = make_source()
    task = make_task()
    payload = _payload(source=source, task=task)
    target = payload if level == "payload" else payload["context"]
    if operation == "missing":
        target.pop("edges" if level == "payload" else "scope")
    else:
        target["unexpected"] = "value"

    _assert_error(
        "invalid_schema",
        EvidenceGraph.from_payload,
        payload,
        source=source,
        task=task,
        sensitivity=Sensitivity.synthetic,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("task", "other-task"),
        ("schema_ref", "missing.module:Answer@unsafe"),
        ("evidence_policy", "claim"),
        ("scope", "patient"),
        ("source_id", "other-source"),
        ("patient_id", "other-patient"),
        ("sensitivity", "deidentified"),
        ("excluded_spans", []),
    ],
)
def test_snapshot_context_must_match_every_authoritative_field(field: str, value: Any) -> None:
    source = make_source()
    task = make_task()
    payload = _payload(source=source, task=task, excluded_spans=((0, 1),))
    payload["context"][field] = value

    _assert_error(
        "invalid_context",
        EvidenceGraph.from_payload,
        payload,
        source=source,
        task=task,
        sensitivity=Sensitivity.synthetic,
        excluded_spans=((0, 1),),
    )


@pytest.mark.parametrize(
    "excluded_spans",
    [
        ((0, 1),),
        [[True, 1]],
        [[0.0, 1]],
        [[0, 0]],
        [[2, 1]],
        [[0, 11]],
        [[4, 6], [2, 3]],
        [[0, 4], [3, 5]],
    ],
)
def test_snapshot_exclusions_require_strict_json_intervals(excluded_spans: Any) -> None:
    source = make_source()
    task = make_task()
    payload = _payload(source=source, task=task)
    payload["context"]["excluded_spans"] = excluded_spans

    _assert_error(
        "invalid_context",
        EvidenceGraph.from_payload,
        payload,
        source=source,
        task=task,
        sensitivity=Sensitivity.synthetic,
    )


@pytest.mark.parametrize("record_key", ["claims", "evidence", "edges"])
def test_snapshot_record_collections_must_be_json_lists(record_key: str) -> None:
    source = make_source()
    task = make_task()
    payload = _payload(source=source, task=task)
    payload[record_key] = {}

    _assert_error(
        "invalid_schema",
        EvidenceGraph.from_payload,
        payload,
        source=source,
        task=task,
        sensitivity=Sensitivity.synthetic,
    )


@pytest.mark.parametrize(
    ("record_kind", "field", "value", "code"),
    [
        ("claim", "unexpected", "invented source-bearing alteration", "invalid_schema"),
        ("inclusion", "quote", "invented source-bearing alteration", "quote_mismatch"),
        ("edge", "role", "invented source-bearing alteration", "invalid_context"),
    ],
)
def test_restore_rejects_forged_records_without_changing_an_existing_graph(
    record_kind: str, field: str, value: str, code: str
) -> None:
    graph, _ = _graph_with_support_tree()
    before = graph.to_payload()
    corrupt = deepcopy(before)
    if record_kind == "claim":
        record = corrupt["claims"][0]
    elif record_kind == "inclusion":
        record = next(item for item in corrupt["evidence"] if item["kind"] == "inclusion")
    else:
        record = corrupt["edges"][0]
    record[field] = value

    with pytest.raises(GraphValidationError) as failure:
        EvidenceGraph.from_payload(
            corrupt,
            source=graph.source,
            task=graph.task,
            sensitivity=graph.sensitivity,
        )

    assert failure.value.code == code
    assert value not in str(failure.value)
    assert graph.to_payload() == before


def test_payload_containers_are_detached_at_every_level() -> None:
    graph, _ = _graph_with_support_tree()
    payload = graph.to_payload()
    original = deepcopy(payload)

    payload["context"]["excluded_spans"].append([0, 1])
    payload["claims"][0]["value"][FIELD] = True
    payload["evidence"].clear()
    payload["edges"].clear()

    assert graph.to_payload() == original
