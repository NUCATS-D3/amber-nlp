"""Validated, in-memory note case results; no extraction runtime or persistence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal, NoReturn, TypeAlias

from amber.graph import EvidenceGraph
from amber.schemas.cases import (
    Case,
    CaseOutcome,
    CaseResult,
    VariableOutcome,
    _mint_case_result,
    case_status,
)
from amber.schemas.sources import Source
from amber.schemas.tasks import Task
from amber.schemas.variables import VariableSet

CaseValidationCode: TypeAlias = Literal[
    "invalid_schema",
    "invalid_context",
    "invalid_final_claims",
    "incomplete_review",
    "unknown_reference",
    "invalid_outcome",
    "variable_set_mismatch",
]
_RESULT_KEYS = frozenset(CaseResult.model_fields)
_VARIABLE_OUTCOME_KEYS = frozenset(VariableOutcome.model_fields)
__all__ = [
    "CaseValidationCode",
    "CaseValidationError",
    "case_status",
    "create_case_result",
    "load_case_result",
    "validate_case_outcome",
]
_GRAPH_KEYS = ("claims", "evidence", "edges", "mentions", "mention_readings")


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


def _task_set(task: Task | None) -> VariableSet | None:
    if not isinstance(task, Task):
        return None
    try:
        return VariableSet.from_task(task)
    except (TypeError, ValueError):
        return None


def _validate_context(case: Case, graph: EvidenceGraph) -> None:
    if not isinstance(graph, EvidenceGraph):
        _raise("invalid_context")
    source = graph.source
    variables = graph.variables
    if (
        case.task != variables.name
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
        variables=graph.variables,
        sensitivity=graph.sensitivity,
        excluded_spans=graph.excluded_spans,
    )
    _validate_outcome(bound_case, graph, bound_outcome)
    return bound_outcome


def _identifiers(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        _raise("invalid_schema")
    ids = list(value)
    if any(type(item) is not str or not item.strip() for item in ids) or len(ids) != len(set(ids)):
        _raise("invalid_schema")
    return ids


def _variable_outcome(
    case: Case, graph: EvidenceGraph, variables: VariableSet, name: str, item: Any
) -> VariableOutcome:
    if isinstance(item, VariableOutcome):
        item = item.model_dump(warnings=False)
    if not isinstance(item, Mapping) or set(item) != _VARIABLE_OUTCOME_KEYS:
        _raise("invalid_schema")
    if item["variable"] != name:
        _raise("invalid_context")
    outcome = _outcome(item["outcome"])
    _validate_outcome(case, graph, outcome)
    ids = _identifiers(item["final_claim_ids"])
    if (outcome.status == "answered") != bool(ids):
        _raise("invalid_final_claims")
    claims = graph.claims
    if any(
        identifier not in claims
        or claims[identifier].status == "rejected"
        or claims[identifier].variable != name
        for identifier in ids
    ):
        _raise("invalid_final_claims")
    # Conflicts surface where mentions are combined, never at mention level (section 3.10).
    if variables.variables[name].level == "mention" and outcome.status == "conflicting_evidence":
        _raise("invalid_outcome")
    try:
        return VariableOutcome(variable=name, outcome=outcome, final_claim_ids=ids)
    except (TypeError, ValueError):
        _raise("invalid_schema")


def _finish(case: Case, graph: EvidenceGraph, payload: Mapping[str, Any]) -> CaseResult:
    _validate_context(case, graph)
    if type(payload["case_id"]) is not str or payload["case_id"] != case.case_id:
        _raise("invalid_context")
    variables = graph.variables
    finals = sorted(name for name, variable in variables.variables.items() if variable.final)
    supplied = payload["variable_outcomes"]
    if not isinstance(supplied, Mapping):
        _raise("invalid_schema")
    try:
        supplied = dict(supplied)
    except (TypeError, ValueError):
        _raise("invalid_schema")
    if set(supplied) != set(finals):
        _raise("invalid_context")
    outcomes = {
        name: _variable_outcome(case, graph, variables, name, supplied[name]) for name in finals
    }

    outcome: CaseOutcome | None = None
    final_ids: list[str] | None = None
    if len(finals) == 1:
        if payload["outcome"] is None or payload["final_claim_ids"] is None:
            _raise("invalid_outcome")
        outcome = _outcome(payload["outcome"])
        final_ids = _identifiers(payload["final_claim_ids"])
        (single,) = outcomes.values()
        if outcome != single.outcome or final_ids != single.final_claim_ids:
            _raise("invalid_outcome")
    elif payload["outcome"] is not None or payload["final_claim_ids"] is not None:
        _raise("invalid_outcome")
    try:
        return _mint_case_result(
            {
                **payload,
                "variable_set": variables.name,
                "variable_set_version": variables.version,
                "variable_set_ref": variables.schema_ref,
                "claims": list(graph.claims.values()),
                "evidence": list(graph.evidence.values()),
                "edges": list(graph.edges),
                "mentions": list(graph.mentions.values()),
                "mention_readings": list(graph.mention_readings.values()),
                "variable_outcomes": outcomes,
                "final_claim_ids": final_ids,
                "outcome": outcome,
            }
        )
    except (AttributeError, TypeError, ValueError):
        _raise("invalid_schema")


def create_case_result(
    case: Case,
    *,
    graph: EvidenceGraph,
    outcome: CaseOutcome | None = None,
    final_claim_ids: Sequence[str] | None = None,
    variable_outcomes: Mapping[str, VariableOutcome] | None = None,
    grounding_failures: int = 0,
    tool_calls: int = 0,
    escalated: bool = False,
    trace_id: str | None = None,
) -> CaseResult:
    """Snapshot and validate the complete graph before issuing an immutable result.

    Give ``variable_outcomes`` (one per final variable), or, for a set with one final
    variable, ``outcome`` and ``final_claim_ids`` for it.
    """
    bound_case = _case(case)
    _validate_context(bound_case, graph)
    finals = [name for name, variable in graph.variables.variables.items() if variable.final]
    supplied: Mapping[str, Any]
    if variable_outcomes is None:
        if len(finals) != 1 or outcome is None or final_claim_ids is None:
            _raise("invalid_outcome")
        supplied = {
            finals[0]: {
                "variable": finals[0],
                "outcome": outcome,
                "final_claim_ids": final_claim_ids,
            }
        }
    else:
        supplied = variable_outcomes
        single = (
            supplied.get(finals[0]) if len(finals) == 1 and isinstance(supplied, Mapping) else None
        )
        if isinstance(single, VariableOutcome) and outcome is None and final_claim_ids is None:
            outcome, final_claim_ids = single.outcome, single.final_claim_ids
    validated_graph = EvidenceGraph.from_payload(
        graph.to_payload(),
        source=graph.source,
        variables=graph.variables,
        sensitivity=graph.sensitivity,
        excluded_spans=graph.excluded_spans,
    )
    return _finish(
        bound_case,
        validated_graph,
        {
            "case_id": bound_case.case_id,
            "variable_outcomes": supplied,
            "claims": [],
            "evidence": [],
            "edges": [],
            "final_claim_ids": final_claim_ids,
            "outcome": outcome,
            "mentions": [],
            "mention_readings": [],
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
    task: Task | None = None,
    variables: VariableSet | None = None,
    excluded_spans: Sequence[tuple[int, int]] = (),
) -> CaseResult:
    """Revalidate a wire result against caller-supplied authoritative context.

    Supply exactly one of ``task`` or ``variables``. The payload never supplies an answer
    model or authoritative source. No import, source lookup, provider access, or file I/O is
    performed.
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
    bound = variables if variables is not None else _task_set(task)
    if (
        not isinstance(bound, VariableSet)
        or copied["variable_set"] != bound.name
        or copied["variable_set_version"] != bound.version
        or copied["variable_set_ref"] != bound.schema_ref
    ):
        _raise("variable_set_mismatch")
    graph = EvidenceGraph(
        source=source,
        task=task,
        variables=variables,
        sensitivity=bound_case.sensitivity,
        excluded_spans=excluded_spans,
    )
    _validate_context(bound_case, graph)
    graph_payload = graph.to_payload()
    graph_payload.update({key: copied[key] for key in _GRAPH_KEYS})
    graph = EvidenceGraph.from_payload(
        graph_payload,
        source=source,
        task=task,
        variables=variables,
        sensitivity=bound_case.sensitivity,
        excluded_spans=excluded_spans,
    )
    return _finish(bound_case, graph, copied)
