"""Explicit no-claim outcomes never fabricate negative or null-valued claims."""

from typing import Any

import pytest

from amber.cases import CaseValidationError, create_case_result
from amber.schemas import Sensitivity
from amber.tools.outcomes import no_claim

from ._claim_helpers import make_provenance
from .test_cases import _commit, _setup


@pytest.mark.parametrize(
    "reason", ["not_mentioned", "conflicting_evidence", "insufficient_evidence"]
)
def test_no_claim_validates_citations_and_leaves_graph_unchanged(reason: str) -> None:
    case, graph, inclusion = _setup()
    before = graph.to_payload()
    outcome = no_claim(
        reason,
        "Invented explanation only.",
        case.source_ids if reason == "not_mentioned" else [],
        [inclusion.evidence_id] if reason == "conflicting_evidence" else [],
        case=case,
        graph=graph,
        provenance=make_provenance(),
    )

    assert outcome.status == reason
    assert outcome.failure_kind is None
    assert graph.to_payload() == before
    result = create_case_result(case, graph=graph, outcome=outcome, final_claim_ids=[])
    assert result.claims == []
    assert result.final_claim_ids == []


@pytest.mark.parametrize("reason", ["answered", "failed", "no_claim", "", 1])
def test_no_claim_cannot_declare_answers_or_runtime_failures(reason: Any) -> None:
    case, graph, _ = _setup()
    before = graph.to_payload()
    with pytest.raises(CaseValidationError, match="invalid_schema"):
        no_claim(
            reason,
            "Synthetic reason.",
            [],
            [],
            case=case,
            graph=graph,
            provenance=make_provenance(),
        )
    assert graph.to_payload() == before


@pytest.mark.parametrize("reason", [None, "", " \r\n", 1])
def test_no_claim_requires_a_nonblank_explanation(reason: Any) -> None:
    case, graph, _ = _setup()
    with pytest.raises(CaseValidationError, match="invalid_schema"):
        no_claim(
            "insufficient_evidence",
            reason,
            [],
            [],
            case=case,
            graph=graph,
            provenance=make_provenance(),
        )


def test_no_claim_partial_review_cannot_be_not_mentioned() -> None:
    case, graph, _ = _setup()
    with pytest.raises(CaseValidationError, match="incomplete_review"):
        no_claim(
            "not_mentioned",
            "Only part reviewed.",
            [],
            [],
            case=case,
            graph=graph,
            provenance=make_provenance(),
        )


def test_no_claim_rejects_unknown_citations_and_claim_ids() -> None:
    case, graph, inclusion = _setup()
    claim_id = _commit(graph, inclusion)
    for identifier in ("unknown", claim_id):
        with pytest.raises(CaseValidationError, match="unknown_reference"):
            no_claim(
                "conflicting_evidence",
                "Invented conflict.",
                [],
                [identifier],
                case=case,
                graph=graph,
                provenance=make_provenance(),
            )


def test_no_claim_rejects_empty_conflict_evidence_and_sensitivity_mismatch() -> None:
    case, graph, _ = _setup()
    with pytest.raises(CaseValidationError, match="invalid_schema"):
        no_claim(
            "conflicting_evidence",
            "Invented conflict.",
            [],
            [],
            case=case,
            graph=graph,
            provenance=make_provenance(),
        )
    provenance = make_provenance().model_copy(update={"sensitivity": Sensitivity.phi})
    with pytest.raises(CaseValidationError, match="invalid_context"):
        no_claim(
            "insufficient_evidence",
            "Invented uncertainty.",
            [],
            [],
            case=case,
            graph=graph,
            provenance=provenance,
        )


@pytest.mark.parametrize("invalid_ids", ["identifier", {"identifier"}, None, 1])
def test_no_claim_requires_ordered_identifier_sequences(invalid_ids: Any) -> None:
    case, graph, _ = _setup()
    for reviewed, evidence in ((invalid_ids, []), ([], invalid_ids)):
        with pytest.raises(CaseValidationError, match="invalid_schema"):
            no_claim(
                "insufficient_evidence",
                "Invented explanation.",
                reviewed,
                evidence,
                case=case,
                graph=graph,
                provenance=make_provenance(),
            )
