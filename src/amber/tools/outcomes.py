"""Validated unanswered outcomes shared by fixed pipelines and optional agents."""

from collections.abc import Sequence

from amber.cases import CaseValidationError, validate_case_outcome
from amber.graph import EvidenceGraph
from amber.schemas.cases import Case, CaseOutcome, UnansweredStatus
from amber.schemas.provenance import Provenance


def no_claim(
    reason: UnansweredStatus,
    rationale: str,
    reviewed_source_ids: Sequence[str],
    evidence_ids: Sequence[str],
    *,
    case: Case,
    graph: EvidenceGraph,
    provenance: Provenance,
) -> CaseOutcome:
    """Record an unanswered task without minting or modifying any clinical Claim.

    Complete review is declared by the caller and checked for case scope, not
    clinically certified here. Failed outcomes are recorded by the runtime.
    """
    if reason not in ("not_mentioned", "conflicting_evidence", "insufficient_evidence"):
        raise CaseValidationError("invalid_schema")
    if any(
        not isinstance(ids, Sequence) or isinstance(ids, (str, bytes, bytearray))
        for ids in (reviewed_source_ids, evidence_ids)
    ):
        raise CaseValidationError("invalid_schema")
    try:
        outcome = CaseOutcome(
            status=reason,
            reason=rationale,
            reviewed_source_ids=list(reviewed_source_ids),
            evidence_ids=list(evidence_ids),
            provenance=provenance,
        )
    except (TypeError, ValueError):
        raise CaseValidationError("invalid_schema") from None
    return validate_case_outcome(case=case, graph=graph, outcome=outcome)
