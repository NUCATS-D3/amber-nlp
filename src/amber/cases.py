"""Validated, in-memory note case results; no extraction runtime or persistence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal, NoReturn, TypeAlias

from amber.graph import EvidenceGraph
from amber.schemas.cases import Case, CaseOutcome, CaseResult, _mint_case_result
from amber.schemas.sources import Source
from amber.schemas.tasks import Task

CaseValidationCode: TypeAlias = Literal[
    "invalid_schema",
    "invalid_context",
    "invalid_final_claims",
    "incomplete_review",
    "unknown_reference",
]
_RESULT_KEYS = frozenset(CaseResult.model_fields)


class CaseValidationError(ValueError):
    """Stable case diagnostic that does not render source-bearing inputs."""

    def __init__(self, code: CaseValidationCode) -> None:
        self.code = code
        super().__init__(code)


def _raise(code: CaseValidationCode) -> NoReturn:
    raise CaseValidationError(code) from None


def _case(value: Case) -> Case:
    try:
        if not isinstance(value, Case):
            _raise("invalid_schema")
        return Case.model_validate(value.model_dump(warnings=False))
    except (AttributeError, TypeError, ValueError):
        _raise("invalid_schema")


def _outcome(value: Any) -> CaseOutcome:
    try:
        if isinstance(value, CaseOutcome):
            value = value.model_dump(warnings=False)
        return CaseOutcome.model_validate(value)
    except (AttributeError, TypeError, ValueError):
        _raise("invalid_schema")


def _validate_context(case: Case, graph: EvidenceGraph) -> None:
    if not isinstance(graph, EvidenceGraph):
        _raise("invalid_context")
    source = graph.source
    if (
        case.task != graph.task.name
        or case.patient_id != source.patient_id
        or case.source_ids != [source.source_id]
        or case.sensitivity is not graph.sensitivity
    ):
        _raise("invalid_context")


def _validate_outcome(case: Case, graph: EvidenceGraph, outcome: CaseOutcome) -> None:
    if outcome.provenance.sensitivity is not case.sensitivity:
        _raise("invalid_context")
    if not set(outcome.reviewed_source_ids).issubset(case.source_ids):
        _raise("invalid_context")
    if outcome.status == "not_mentioned" and set(outcome.reviewed_source_ids) != set(
        case.source_ids
    ):
        _raise("incomplete_review")
    evidence = graph.evidence
    for evidence_id in outcome.evidence_ids:
        if evidence_id not in evidence:
            _raise("unknown_reference")
        if not graph.source_leaves(evidence_id):
            _raise("unknown_reference")


def validate_case_outcome(*, case: Case, graph: EvidenceGraph, outcome: CaseOutcome) -> CaseOutcome:
    """Validate declared review scope and citations without creating or editing claims."""
    bound_case = _case(case)
    bound_outcome = _outcome(outcome)
    _validate_context(bound_case, graph)
    graph = EvidenceGraph.from_payload(
        graph.to_payload(),
        source=graph.source,
        task=graph.task,
        sensitivity=graph.sensitivity,
        excluded_spans=graph.excluded_spans,
    )
    _validate_outcome(bound_case, graph, bound_outcome)
    return bound_outcome


def _finish(case: Case, graph: EvidenceGraph, payload: Mapping[str, Any]) -> CaseResult:
    _validate_context(case, graph)
    outcome = _outcome(payload["outcome"])
    _validate_outcome(case, graph, outcome)
    ids = payload["final_claim_ids"]
    if not isinstance(ids, Sequence) or isinstance(ids, (str, bytes, bytearray)):
        _raise("invalid_schema")
    ids = list(ids)
    if any(type(item) is not str or not item.strip() for item in ids) or len(ids) != len(set(ids)):
        _raise("invalid_schema")
    if (outcome.status == "answered") != bool(ids):
        _raise("invalid_final_claims")
    claims = graph.claims
    if any(
        identifier not in claims or claims[identifier].status == "rejected" for identifier in ids
    ):
        _raise("invalid_final_claims")
    if type(payload["case_id"]) is not str or payload["case_id"] != case.case_id:
        _raise("invalid_context")
    if payload["mentions"] != [] or type(payload["mentions"]) is not list:
        _raise("invalid_schema")
    try:
        return _mint_case_result(
            {
                **payload,
                "claims": list(graph.claims.values()),
                "evidence": list(graph.evidence.values()),
                "edges": list(graph.edges),
                "final_claim_ids": ids,
                "outcome": outcome,
            }
        )
    except (AttributeError, TypeError, ValueError):
        _raise("invalid_schema")


def create_case_result(
    case: Case,
    *,
    graph: EvidenceGraph,
    outcome: CaseOutcome,
    final_claim_ids: Sequence[str],
    grounding_failures: int = 0,
    tool_calls: int = 0,
    escalated: bool = False,
    trace_id: str | None = None,
) -> CaseResult:
    """Snapshot and validate the complete graph before issuing an immutable result."""
    bound_case = _case(case)
    _validate_context(bound_case, graph)
    validated_graph = EvidenceGraph.from_payload(
        graph.to_payload(),
        source=graph.source,
        task=graph.task,
        sensitivity=graph.sensitivity,
        excluded_spans=graph.excluded_spans,
    )
    return _finish(
        bound_case,
        validated_graph,
        {
            "case_id": bound_case.case_id,
            "claims": [],
            "evidence": [],
            "edges": [],
            "final_claim_ids": final_claim_ids,
            "outcome": outcome,
            "mentions": [],
            "grounding_failures": grounding_failures,
            "tool_calls": tool_calls,
            "escalated": escalated,
            "trace_id": trace_id,
        },
    )


def load_case_result(
    payload: Mapping[str, Any],
    *,
    case: Case,
    source: Source,
    task: Task,
    excluded_spans: Sequence[tuple[int, int]] = (),
) -> CaseResult:
    """Revalidate a wire result against caller-supplied authoritative context.

    The payload never supplies an answer model or authoritative source. No import,
    source lookup, provider access, or file I/O is performed.
    """
    bound_case = _case(case)
    if not isinstance(payload, Mapping):
        _raise("invalid_schema")
    try:
        copied = dict(payload)
    except (TypeError, ValueError):
        _raise("invalid_schema")
    if set(copied) != _RESULT_KEYS:
        _raise("invalid_schema")
    graph = EvidenceGraph(
        source=source, task=task, sensitivity=bound_case.sensitivity, excluded_spans=excluded_spans
    )
    _validate_context(bound_case, graph)
    graph_payload = graph.to_payload()
    graph_payload.update({key: copied[key] for key in ("claims", "evidence", "edges")})
    graph = EvidenceGraph.from_payload(
        graph_payload,
        source=source,
        task=task,
        sensitivity=bound_case.sensitivity,
        excluded_spans=excluded_spans,
    )
    return _finish(bound_case, graph, copied)
