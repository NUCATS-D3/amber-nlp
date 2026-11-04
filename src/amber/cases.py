"""Validated, in-memory note case results; no extraction runtime or persistence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal, NoReturn, TypeAlias, get_args

from amber._graph_validation import ordered_records
from amber.graph import EvidenceGraph
from amber.ids import validate_ulid
from amber.schemas._wire import SCHEMA_VERSION, check_schema_version
from amber.schemas.cases import (
    Budget,
    Case,
    CaseOutcome,
    CaseResult,
    Usage,
    VariableOutcome,
    _mint_case_result,
    case_status,
)
from amber.schemas.evidence import InferenceEvidence
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
    "run_mismatch",
    "budget_exceeded",
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
        # Codes are a closed, documented set; an unknown one is a programming error.
        if code not in get_args(CaseValidationCode):
            raise TypeError("unknown CaseValidationError code")
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


def _budget(value: Any) -> Budget | None:
    try:
        if value is None:
            return None
        if isinstance(value, Budget):
            value = value.model_dump()
        return Budget.model_validate(value)
    except (TypeError, ValueError):
        _raise("invalid_schema")


def _usage(value: Any) -> Usage | None:
    try:
        if value is None:
            return None
        if isinstance(value, Usage):
            value = value.model_dump()
        return Usage.model_validate(value)
    except (TypeError, ValueError):
        _raise("invalid_schema")


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
    run_id = payload["run_id"]
    try:
        validate_ulid(run_id)
    except (TypeError, ValueError):
        _raise("invalid_schema")
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
    provenances = [
        *(claim.provenance for claim in graph.claims.values()),
        *(
            record.provenance
            for record in graph.evidence.values()
            if isinstance(record, InferenceEvidence)
        ),
        *(reading.provenance for reading in graph.mention_readings.values()),
        *(item.outcome.provenance for item in outcomes.values()),
    ]
    # Every node of a result was produced by its run (contract v0.1 section 6.6 decision 4).
    if any(provenance.run_id != run_id for provenance in provenances):
        _raise("run_mismatch")
    budget, usage = _budget(payload["budget"]), _usage(payload["usage"])
    # Loading never rewrites an outcome: an over-budget run must already say so (6.6 decision 8).
    if (
        budget is not None
        and usage is not None
        and usage.exceeds(budget)
        and any(
            item.outcome.status != "failed" or item.outcome.failure_kind != "budget"
            for item in outcomes.values()
        )
    ):
        _raise("budget_exceeded")
    try:
        schema_version = check_schema_version(payload["schema_version"])
    except ValueError:
        _raise("invalid_schema")
    try:
        return _mint_case_result(
            {
                **payload,
                "schema_version": schema_version,
                "budget": budget,
                "usage": usage,
                "variable_set": variables.name,
                "variable_set_version": variables.version,
                "variable_set_ref": variables.schema_ref,
                **ordered_records(
                    claims=graph.claims,
                    evidence=graph.evidence,
                    edges=graph.edges,
                    mentions=graph.mentions,
                    readings=graph.mention_readings,
                ),
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
    run_id: str,
    outcome: CaseOutcome | None = None,
    final_claim_ids: Sequence[str] | None = None,
    variable_outcomes: Mapping[str, VariableOutcome] | None = None,
    budget: Budget | None = None,
    usage: Usage | None = None,
    grounding_failures: int = 0,
    tool_calls: int = 0,
    escalated: bool = False,
    trace_id: str | None = None,
) -> CaseResult:
    """Snapshot and validate the complete graph before issuing an immutable result.

    Give ``variable_outcomes`` (one per final variable), or, for a set with one final
    variable, ``outcome`` and ``final_claim_ids`` for it. ``run_id`` names the run; every
    claim, inference, reading, and outcome must carry it in its provenance.
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
        try:
            single = (
                supplied.get(finals[0])
                if len(finals) == 1 and isinstance(supplied, Mapping)
                else None
            )
        except (TypeError, ValueError):
            _raise("invalid_schema")
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
            "schema_version": SCHEMA_VERSION,
            "case_id": bound_case.case_id,
            "run_id": run_id,
            "budget": budget,
            "usage": usage,
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
