"""Public lifecycle tests for owned, validated evidence graphs."""

from __future__ import annotations

import json
import random
import warnings
from collections import Counter
from collections.abc import Callable
from copy import deepcopy
from typing import Any

import pytest

from amber import _graph_validation
from amber.graph import EvidenceGraph
from amber.graph_errors import GraphValidationError
from amber.grounding import exact_mention, exact_quote
from amber.ids import new_ulid
from amber.schemas import (
    Claim,
    Inclusion,
    InferenceEvidence,
    Mention,
    MentionReading,
    Sensitivity,
)
from amber.schemas.variables import VariableSet

from ._claim_helpers import (
    make_claim_record,
    make_inference_record,
    make_provenance,
    make_source,
    make_task,
)

FIELD = "finding_present"


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
        "format_version": 3,
        "context": {
            "variable_set": task.name,
            "variable_set_version": "task",
            "variable_set_ref": VariableSet.from_task(task).schema_ref,
            "source_id": source.source_id,
            "patient_id": source.patient_id,
            "sensitivity": sensitivity.value,
            "excluded_spans": [list(span) for span in excluded_spans],
        },
        "claims": [] if claims is None else claims,
        "evidence": [] if evidence is None else evidence,
        "edges": [] if edges is None else edges,
        "mentions": [],
        "mention_readings": [],
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
        "format_version": 3,
        "context": {
            "variable_set": "synthetic_finding",
            "variable_set_version": "task",
            "variable_set_ref": VariableSet.from_task(task).schema_ref,
            "source_id": source.source_id,
            "patient_id": "synthetic-patient-1",
            "sensitivity": "synthetic",
            "excluded_spans": [[0, 1]],
        },
        "claims": [],
        "evidence": [],
        "edges": [],
        "mentions": [],
        "mention_readings": [],
    }


@pytest.mark.parametrize("version", [True, 3.0, "3", None, 1, 2, 4])
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
        target.pop("edges" if level == "payload" else "variable_set_ref")
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
        ("variable_set", "other-task"),
        ("variable_set_version", "1.0.0"),
        ("variable_set_ref", "0" * 64),
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


# Incremental validation (amber-2ad) -----------------------------------------------------------


def _reading(span: Mention, reading_id: str | None = None) -> MentionReading:
    return MentionReading.model_validate(
        {
            "reading_id": new_ulid() if reading_id is None else reading_id,
            "mention_id": span.mention_id,
            "label": "invented",
            "provenance": make_provenance(),
        }
    )


def _register_word(graph: EvidenceGraph, word: str) -> tuple[Inclusion, MentionReading, Claim]:
    source = graph.source
    leaf = exact_quote(source, word)
    span = exact_mention(source, word, None, "finding")
    assert isinstance(leaf, Inclusion)
    assert isinstance(span, Mention)
    graph.register_evidence(leaf)
    _, (reading,) = graph.register_mention(span, [_reading(span)])
    claim = graph.commit_claim(
        make_task(),
        {FIELD: True},
        [leaf.evidence_id],
        "Invented.",
        {FIELD: [leaf.evidence_id]},
        provenance=make_provenance(),
    )
    return leaf, reading, claim


def test_each_mutation_checks_only_its_new_records(monkeypatch: pytest.MonkeyPatch) -> None:
    words = [f"word{index}" for index in range(6)]
    graph = EvidenceGraph(
        source=make_source(" ".join(words)), task=make_task(), sensitivity=Sensitivity.synthetic
    )
    calls: Counter[str] = Counter()
    for name in (
        "_validate_claim",
        "_validate_inclusion",
        "_validate_inference",
        "_validate_mention",
        "_validate_reading",
    ):
        original = getattr(_graph_validation, name)

        def counted(*args: Any, _name: str = name, _original: Any = original, **kwargs: Any) -> Any:
            calls[_name] += 1
            return _original(*args, **kwargs)

        monkeypatch.setattr(_graph_validation, name, counted)

    for word in words:
        _register_word(graph, word)

    assert calls == {
        "_validate_claim": 6,
        "_validate_inclusion": 6,
        "_validate_inference": 6,
        "_validate_mention": 6,
        "_validate_reading": 6,
    }
    graph.validate()


_NEXT_IDS: list[str] = []


def _graph_with_every_kind() -> tuple[EvidenceGraph, dict[str, str]]:
    _NEXT_IDS.clear()
    graph = EvidenceGraph(
        source=make_source("Alpha beta"),
        task=make_task(),
        sensitivity=Sensitivity.synthetic,
        ids=lambda: _NEXT_IDS.pop(0) if _NEXT_IDS else new_ulid(),
    )
    leaf, reading, claim = _register_word(graph, "Alpha")
    inference_id = next(
        evidence_id
        for evidence_id, record in graph.evidence.items()
        if isinstance(record, InferenceEvidence)
    )
    return graph, {
        "claim": claim.claim_id,
        "inference": inference_id,
        "inclusion": leaf.evidence_id,
        "reading": reading.reading_id,
    }


def _new_reading(graph: EvidenceGraph, existing: str) -> None:
    span = next(iter(graph.mentions.values()))
    graph.register_reading(_reading(span, reading_id=existing))


def _new_mention(graph: EvidenceGraph, existing: str) -> None:
    span = exact_mention(graph.source, "beta", None, "finding")
    assert isinstance(span, Mention)
    graph.register_mention(span, [_reading(span, reading_id=existing)])


def _new_inference(graph: EvidenceGraph, existing: str) -> None:
    leaf = next(record for record in graph.evidence.values() if isinstance(record, Inclusion))
    graph.register_evidence(
        InferenceEvidence.model_validate(
            {**make_inference_record(inputs=[leaf.evidence_id]), "evidence_id": existing}
        )
    )


def _new_claim(graph: EvidenceGraph, existing: str, *, collide_inference: bool) -> None:
    _NEXT_IDS[:] = [new_ulid(), existing] if collide_inference else [existing, new_ulid()]
    leaf = next(record for record in graph.evidence.values() if isinstance(record, Inclusion))
    graph.commit_claim(
        make_task(),
        {FIELD: True},
        [leaf.evidence_id],
        "Invented.",
        {FIELD: [leaf.evidence_id]},
        provenance=make_provenance(),
    )


@pytest.mark.parametrize(
    ("new_record", "existing_kind"),
    [
        (_new_reading, "claim"),
        (_new_reading, "inference"),
        (_new_mention, "claim"),
        (_new_mention, "inference"),
        (_new_inference, "claim"),
        (_new_inference, "reading"),
        (lambda graph, existing: _new_claim(graph, existing, collide_inference=False), "reading"),
        (lambda graph, existing: _new_claim(graph, existing, collide_inference=True), "reading"),
        (lambda graph, existing: _new_claim(graph, existing, collide_inference=False), "inference"),
        (lambda graph, existing: _new_claim(graph, existing, collide_inference=True), "claim"),
    ],
)
def test_new_records_cannot_reuse_an_existing_id_of_another_kind(
    new_record: Callable[[EvidenceGraph, str], None], existing_kind: str
) -> None:
    graph, ids = _graph_with_every_kind()
    before = graph.to_payload()

    _assert_error("duplicate_reference", new_record, graph, ids[existing_kind])

    assert graph.to_payload() == before
    graph.validate()
