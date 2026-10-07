"""End-to-end case result validation with invented, source-backed graphs."""

from __future__ import annotations

import json
import warnings
from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from amber.cases import CaseValidationError, create_case_result, load_case_result
from amber.graph import EvidenceGraph
from amber.graph_errors import GraphValidationError
from amber.grounding import exact_quote
from amber.ids import new_ulid
from amber.schemas import Case, CaseOutcome, CaseResult, Inclusion, InferenceEvidence, Sensitivity

from ._claim_helpers import make_provenance, make_source, make_task


def _setup() -> tuple[Case, EvidenceGraph, Inclusion]:
    source = make_source("Invented α statement.\r\nInvented contrary statement.")
    task = make_task()
    graph = EvidenceGraph(source=source, task=task, sensitivity=Sensitivity.synthetic)
    inclusion = exact_quote(source, "Invented α statement.\r\n")
    assert isinstance(inclusion, Inclusion)
    graph.register_evidence(inclusion)
    case = Case(
        case_id="synthetic-case-1",
        task=task.name,
        patient_id=source.patient_id,
        source_ids=[source.source_id],
        sensitivity=Sensitivity.synthetic,
    )
    return case, graph, inclusion


def _commit(graph: EvidenceGraph, inclusion: Inclusion, value: bool = True) -> str:
    claim = graph.commit_claim(
        graph.task,
        {"progression_or_recurrence": value},
        [inclusion.evidence_id],
        "Invented interpretation for software testing only.",
        {"progression_or_recurrence": [inclusion.evidence_id]},
        provenance=make_provenance(),
    )
    return claim.claim_id


def _outcome(status: str, **changes: Any) -> CaseOutcome:
    payload = {
        "status": status,
        "reason": None if status == "answered" else "Synthetic outcome explanation.",
        "failure_kind": "provider" if status == "failed" else None,
        "provenance": make_provenance(),
    }
    return CaseOutcome.model_validate({**payload, **changes})


def _load(payload: dict[str, Any], case: Case, graph: EvidenceGraph) -> CaseResult:
    return load_case_result(payload, case=case, source=graph.source, task=graph.task)


@pytest.mark.parametrize("value", [True, False])
def test_answered_result_retains_exact_source_support_and_final_claims(value: bool) -> None:
    case, graph, inclusion = _setup()
    claim_id = _commit(graph, inclusion, value)
    result = create_case_result(
        case,
        graph=graph,
        outcome=_outcome("answered"),
        final_claim_ids=[claim_id],
        grounding_failures=1,
        tool_calls=3,
        escalated=True,
        trace_id="synthetic-trace",
    )

    assert result.outcome.status == "answered"
    assert result.final_claim_ids == [claim_id]
    assert result.claims[0].value == {"progression_or_recurrence": value}
    assert result.claims[0].effective_datetime == graph.source.datetime
    leaves = [item for item in result.evidence if isinstance(item, Inclusion)]
    assert [(item.start, item.end, item.quote) for item in leaves] == [
        (0, 23, "Invented α statement.\r\n")
    ]
    assert (result.grounding_failures, result.tool_calls, result.escalated) == (1, 3, True)
    assert result.trace_id == "synthetic-trace"
    assert result.mentions == []
    assert _load(json.loads(result.model_dump_json()), case, graph) == result


def test_empty_graph_can_record_complete_review_without_minting_claim() -> None:
    case, graph, _ = _setup()
    empty = EvidenceGraph(source=graph.source, task=graph.task, sensitivity=Sensitivity.synthetic)
    result = create_case_result(
        case,
        graph=empty,
        outcome=_outcome("not_mentioned", reviewed_source_ids=case.source_ids),
        final_claim_ids=[],
    )

    assert result.claims == []
    assert result.evidence == []
    assert result.final_claim_ids == []
    assert _load(result.model_dump(mode="json"), case, empty) == result


@pytest.mark.parametrize("status", ["insufficient_evidence", "failed"])
def test_partial_review_and_failure_retain_supporting_claims_without_final_answer(
    status: str,
) -> None:
    case, graph, inclusion = _setup()
    supporting_id = _commit(graph, inclusion)
    result = create_case_result(case, graph=graph, outcome=_outcome(status), final_claim_ids=[])

    assert result.outcome.reviewed_source_ids == []
    assert [claim.claim_id for claim in result.claims] == [supporting_id]
    assert result.final_claim_ids == []
    assert _load(result.model_dump(mode="json"), case, graph) == result


def test_conflict_retains_opposing_evidence_and_support_closure() -> None:
    case, graph, first = _setup()
    second = exact_quote(graph.source, "Invented contrary statement.")
    assert isinstance(second, Inclusion)
    graph.register_evidence(second)
    _commit(graph, first, True)
    _commit(graph, second, False)
    outcome = _outcome("conflicting_evidence", evidence_ids=[first.evidence_id, second.evidence_id])
    result = create_case_result(case, graph=graph, outcome=outcome, final_claim_ids=[])

    assert set(result.outcome.evidence_ids) == {first.evidence_id, second.evidence_id}
    assert len(result.claims) == 2
    assert len(result.edges) == 4
    assert _load(result.model_dump(mode="json"), case, graph) == result


@pytest.mark.parametrize(
    "status", ["not_mentioned", "conflicting_evidence", "insufficient_evidence", "failed"]
)
def test_non_answered_result_rejects_final_claims(status: str) -> None:
    case, graph, inclusion = _setup()
    claim_id = _commit(graph, inclusion)
    outcome = _outcome(
        status, reviewed_source_ids=case.source_ids, evidence_ids=[inclusion.evidence_id]
    )
    before = graph.to_payload()
    with pytest.raises(CaseValidationError, match="invalid_final_claims"):
        create_case_result(case, graph=graph, outcome=outcome, final_claim_ids=[claim_id])
    assert graph.to_payload() == before


def test_answered_result_rejects_empty_final_claims() -> None:
    case, graph, inclusion = _setup()
    _commit(graph, inclusion)
    with pytest.raises(CaseValidationError, match="invalid_final_claims"):
        create_case_result(case, graph=graph, outcome=_outcome("answered"), final_claim_ids=[])


@pytest.mark.parametrize("ids", [["unknown"], [" "], [1], "not-a-sequence", ["same", "same"]])
def test_result_rejects_unknown_or_malformed_final_ids(ids: Any) -> None:
    case, graph, _ = _setup()
    with pytest.raises(CaseValidationError):
        create_case_result(case, graph=graph, outcome=_outcome("answered"), final_claim_ids=ids)


def test_result_rejects_rejected_claim_as_final_answer() -> None:
    case, graph, inclusion = _setup()
    claim_id = _commit(graph, inclusion)
    snapshot = graph.to_payload()
    snapshot["claims"][0]["status"] = "rejected"
    rejected_graph = EvidenceGraph.from_payload(
        snapshot, source=graph.source, task=graph.task, sensitivity=graph.sensitivity
    )
    with pytest.raises(CaseValidationError, match="invalid_final_claims"):
        create_case_result(
            case, graph=rejected_graph, outcome=_outcome("answered"), final_claim_ids=[claim_id]
        )


def test_not_mentioned_requires_declared_complete_review() -> None:
    case, graph, _ = _setup()
    with pytest.raises(CaseValidationError, match="incomplete_review"):
        create_case_result(case, graph=graph, outcome=_outcome("not_mentioned"), final_claim_ids=[])


@pytest.mark.parametrize("reviewed", [["foreign-source"], ["foreign-source", "another"]])
def test_outcome_review_ids_must_belong_to_case(reviewed: list[str]) -> None:
    case, graph, _ = _setup()
    with pytest.raises(CaseValidationError, match="invalid_context"):
        create_case_result(
            case,
            graph=graph,
            outcome=_outcome("insufficient_evidence", reviewed_source_ids=reviewed),
            final_claim_ids=[],
        )


def test_outcome_citations_must_be_evidence_ids_not_claim_ids() -> None:
    case, graph, inclusion = _setup()
    claim_id = _commit(graph, inclusion)
    with pytest.raises(CaseValidationError, match="unknown_reference"):
        create_case_result(
            case,
            graph=graph,
            outcome=_outcome("conflicting_evidence", evidence_ids=[claim_id]),
            final_claim_ids=[],
        )


@pytest.mark.parametrize(
    "changes",
    [{"patient_id": "other"}, {"task": "other"}, {"source_ids": ["other"]}, {"sensitivity": "phi"}],
)
def test_case_must_match_authoritative_graph_context(changes: dict[str, Any]) -> None:
    case, graph, _ = _setup()
    other = Case.model_validate({**case.model_dump(), **changes})
    with pytest.raises(CaseValidationError, match="invalid_context"):
        create_case_result(other, graph=graph, outcome=_outcome("failed"), final_claim_ids=[])


def test_outcome_sensitivity_must_match_case() -> None:
    case, graph, _ = _setup()
    provenance = make_provenance().model_copy(update={"sensitivity": Sensitivity.phi})
    with pytest.raises(CaseValidationError, match="invalid_context"):
        create_case_result(
            case, graph=graph, outcome=_outcome("failed", provenance=provenance), final_claim_ids=[]
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"grounding_failures": -1},
        {"grounding_failures": True},
        {"grounding_failures": "1"},
        {"tool_calls": -1},
        {"tool_calls": False},
        {"tool_calls": 1.5},
        {"escalated": 1},
        {"trace_id": " "},
        {"trace_id": 1},
    ],
)
def test_result_counters_and_trace_are_strict(changes: dict[str, Any]) -> None:
    case, graph, _ = _setup()
    with pytest.raises(CaseValidationError, match="invalid_schema"):
        create_case_result(
            case, graph=graph, outcome=_outcome("failed"), final_claim_ids=[], **changes
        )


def test_result_is_detached_immutable_and_requires_validated_boundary() -> None:
    case, graph, inclusion = _setup()
    claim_id = _commit(graph, inclusion)
    ids = [claim_id]
    result = create_case_result(
        case, graph=graph, outcome=_outcome("answered"), final_claim_ids=ids
    )
    before = result.model_dump(mode="json")
    ids.clear()
    _commit(graph, inclusion, False)

    assert result.model_dump(mode="json") == before
    with pytest.raises(TypeError):
        result.claims.clear()
    with pytest.raises(TypeError):
        result.evidence.clear()
    with pytest.raises(TypeError):
        result.edges.clear()
    with pytest.raises(TypeError):
        result.final_claim_ids.clear()
    with pytest.raises(TypeError):
        result.mentions.append("unvalidated")
    with pytest.raises(ValidationError):
        result.case_id = "changed"
    with pytest.raises(ValidationError):
        CaseResult.model_validate(before)
    with pytest.raises(TypeError):
        CaseResult.model_construct(**before)


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda p: p["claims"][0].update(value=None), "invalid_schema"),
        (lambda p: p["claims"][0].update(patient_id="other"), "invalid_context"),
        (lambda p: p["claims"][0].update(status="gold"), "unsupported_claim_status"),
        (lambda p: p["claims"][0].update(schema_ref="foreign:Schema@hash"), "invalid_context"),
        (lambda p: p.update(edges=[]), "missing_evidence"),
        (lambda p: p["evidence"][0].update(quote="SECRET_SOURCE_VALUE"), "quote_mismatch"),
        (lambda p: p["evidence"][0].update(start=-1), "invalid_span"),
        (lambda p: p["evidence"][1].update(inputs=[]), "invalid_schema"),
        (lambda p: p["evidence"][1].update(inputs=["unknown"]), "unknown_reference"),
        (lambda p: p["evidence"][1].update(inputs=[p["claims"][0]["claim_id"]]), "cyclic_support"),
    ],
)
def test_load_revalidates_full_graph_without_leaking_source_data(mutation: Any, code: str) -> None:
    case, graph, inclusion = _setup()
    claim_id = _commit(graph, inclusion)
    result = create_case_result(
        case, graph=graph, outcome=_outcome("answered"), final_claim_ids=[claim_id]
    )
    payload = result.model_dump(mode="json")
    # Fixed order for these deliberately corrupt, invented records.
    payload["evidence"].sort(key=lambda item: item["kind"] != "inclusion")
    mutation(payload)
    with (
        warnings.catch_warnings(record=True) as caught,
        pytest.raises(GraphValidationError) as failure,
    ):
        _load(payload, case, graph)
    assert failure.value.code == code
    assert str(failure.value) == code
    assert caught == []


def test_load_rejects_missing_field_evidence_and_incomplete_retained_support() -> None:
    case, graph, inclusion = _setup()
    claim_id = _commit(graph, inclusion)
    result = create_case_result(
        case, graph=graph, outcome=_outcome("answered"), final_claim_ids=[claim_id]
    )
    payload = result.model_dump(mode="json")
    payload["edges"] = [edge for edge in payload["edges"] if edge["role"] is None]
    with pytest.raises(GraphValidationError, match="missing_field_evidence"):
        _load(payload, case, graph)
    payload = result.model_dump(mode="json")
    payload["evidence"] = [item for item in payload["evidence"] if item["kind"] == "inference"]
    with pytest.raises(GraphValidationError, match="unknown_reference"):
        _load(payload, case, graph)


def test_load_revalidates_components_not_cited_as_final_answers() -> None:
    case, graph, inclusion = _setup()
    final_id = _commit(graph, inclusion)
    _commit(graph, inclusion, False)
    result = create_case_result(
        case, graph=graph, outcome=_outcome("answered"), final_claim_ids=[final_id]
    )
    payload = result.model_dump(mode="json")
    support = next(claim for claim in payload["claims"] if claim["claim_id"] != final_id)
    payload["edges"] = [
        edge for edge in payload["edges"] if edge["claim_id"] != support["claim_id"]
    ]
    with pytest.raises(GraphValidationError, match="missing_evidence"):
        _load(payload, case, graph)


@pytest.mark.parametrize(
    "changes",
    [
        {"case_id": "other"},
        {"extra": "SECRET_SOURCE_VALUE"},
        {"mentions": ["unimplemented"]},
        {"claims": "bad"},
        {"outcome": {"reason": "SECRET_SOURCE_VALUE"}},
        {"final_claim_ids": ["unknown"]},
    ],
)
def test_load_rejects_invalid_envelope_without_rendering_payload(changes: dict[str, Any]) -> None:
    case, graph, inclusion = _setup()
    claim_id = _commit(graph, inclusion)
    result = create_case_result(
        case, graph=graph, outcome=_outcome("answered"), final_claim_ids=[claim_id]
    )
    payload = {**result.model_dump(mode="json"), **changes}
    with pytest.raises((CaseValidationError, GraphValidationError)) as failure:
        _load(payload, case, graph)
    assert "SECRET_SOURCE_VALUE" not in str(failure.value)


def test_load_rejects_missing_result_fields() -> None:
    case, graph, _ = _setup()
    result = create_case_result(case, graph=graph, outcome=_outcome("failed"), final_claim_ids=[])
    payload = result.model_dump(mode="json")
    del payload["final_claim_ids"]
    with pytest.raises(CaseValidationError, match="invalid_schema"):
        _load(payload, case, graph)


def test_builder_revalidates_forged_case_and_outcome_before_use() -> None:
    case, graph, _ = _setup()
    with pytest.raises(CaseValidationError, match="invalid_schema"):
        create_case_result(
            case.model_copy(update={"source_ids": []}),
            graph=graph,
            outcome=_outcome("failed"),
            final_claim_ids=[],
        )
    forged = _outcome("failed").model_copy(update={"reason": "", "failure_kind": None})
    with pytest.raises(CaseValidationError, match="invalid_schema"):
        create_case_result(case, graph=graph, outcome=forged, final_claim_ids=[])


def test_result_preserves_supporting_claim_chain_and_loaded_input_is_detached() -> None:
    case, graph, inclusion = _setup()
    supporting_id = _commit(graph, inclusion)
    # Add a source-backed inference through a known supporting claim.
    bridge = InferenceEvidence(
        evidence_id=new_ulid(),
        rationale="Invented supporting chain.",
        inputs=[supporting_id],
        trace_id=None,
        span_id=None,
        provenance=make_provenance(),
    )
    graph.register_evidence(bridge)
    final = graph.commit_claim(
        graph.task,
        {"progression_or_recurrence": True},
        [bridge.evidence_id],
        "Invented chain only.",
        {"progression_or_recurrence": [bridge.evidence_id]},
        provenance=make_provenance(),
    )
    result = create_case_result(
        case, graph=graph, outcome=_outcome("answered"), final_claim_ids=[final.claim_id]
    )
    payload = result.model_dump(mode="json")
    restored = _load(payload, case, graph)
    before = deepcopy(restored.model_dump(mode="json"))
    payload["claims"].clear()
    assert restored.model_dump(mode="json") == before
    assert len(restored.claims) == 2
    assert restored.final_claim_ids == [final.claim_id]
    assert _load(before, case, graph) == restored


def test_load_rechecks_outcome_final_consistency_and_review_scope() -> None:
    case, graph, inclusion = _setup()
    claim_id = _commit(graph, inclusion)
    result = create_case_result(
        case, graph=graph, outcome=_outcome("answered"), final_claim_ids=[claim_id]
    )
    for outcome, final_ids, code in (
        (_outcome("answered"), [], "invalid_final_claims"),
        (_outcome("failed"), [claim_id], "invalid_final_claims"),
        (_outcome("not_mentioned"), [], "incomplete_review"),
        (_outcome("conflicting_evidence", evidence_ids=["foreign"]), [], "unknown_reference"),
    ):
        payload = result.model_dump(mode="json")
        payload.update(outcome=outcome.model_dump(mode="json"), final_claim_ids=final_ids)
        with pytest.raises(CaseValidationError, match=code):
            _load(payload, case, graph)


def test_load_enforces_authoritative_exclusions_and_source() -> None:
    case, graph, inclusion = _setup()
    claim_id = _commit(graph, inclusion)
    result = create_case_result(
        case, graph=graph, outcome=_outcome("answered"), final_claim_ids=[claim_id]
    )
    payload = result.model_dump(mode="json")
    with pytest.raises(GraphValidationError, match="invalid_context"):
        load_case_result(
            payload, case=case, source=graph.source, task=graph.task, excluded_spans=[(0, 23)]
        )
    with pytest.raises(CaseValidationError, match="invalid_context"):
        load_case_result(payload, case=case, source=make_source("different"), task=graph.task)


def test_load_rejects_source_free_and_duplicate_retained_components() -> None:
    case, graph, inclusion = _setup()
    claim_id = _commit(graph, inclusion)
    result = create_case_result(
        case, graph=graph, outcome=_outcome("answered"), final_claim_ids=[claim_id]
    )
    payload = result.model_dump(mode="json")
    rejected_id = new_ulid()
    rejected = deepcopy(payload["claims"][0])
    rejected.update(claim_id=rejected_id, status="rejected")
    payload["claims"].append(rejected)
    inference = deepcopy(next(item for item in payload["evidence"] if item["kind"] == "inference"))
    inference.update(evidence_id=new_ulid(), inputs=[inclusion.evidence_id, rejected_id])
    payload["evidence"].append(inference)
    with pytest.raises(GraphValidationError, match="invalid_context"):
        _load(payload, case, graph)
    payload = result.model_dump(mode="json")
    payload["claims"].append(deepcopy(payload["claims"][0]))
    with pytest.raises(GraphValidationError, match="duplicate_reference"):
        _load(payload, case, graph)


def test_outcome_can_cite_a_source_backed_inference() -> None:
    case, graph, inclusion = _setup()
    _commit(graph, inclusion)
    inference_id = next(
        record.evidence_id
        for record in graph.evidence.values()
        if isinstance(record, InferenceEvidence)
    )
    result = create_case_result(
        case,
        graph=graph,
        outcome=_outcome("insufficient_evidence", evidence_ids=[inference_id]),
        final_claim_ids=[],
    )
    assert result.outcome.evidence_ids == [inference_id]
    assert _load(result.model_dump(mode="json"), case, graph) == result
