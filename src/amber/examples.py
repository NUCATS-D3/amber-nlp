"""The annotated-data load boundary: Examples and LabelRecords (contract v0.1 section 4).

Every Example is revalidated against caller-supplied authoritative context: the ``Case`` (with
its split), the ``Source``, and the ``VariableSet``. No import, source lookup, or I/O happens
here. Building Examples from annotation events is a separate step; this module checks the
snapshot those events produce.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal, NoReturn, TypeAlias

from pydantic import ValidationError

from amber._graph_validation import (
    RETAINED_STATUSES,
    _ValidatedState,
    build_context,
    ordered_records,
    validate_state,
)
from amber.ids import example_id, validate_ulid
from amber.schemas._wire import check_schema_version
from amber.schemas.cases import Case, CaseOutcome
from amber.schemas.examples import (
    HUMAN_TIERS,
    Example,
    LabelRecord,
    VariableReference,
    _mint_example,
)
from amber.schemas.sources import Source
from amber.schemas.variables import VariableSet

ExampleValidationCode: TypeAlias = Literal[
    "invalid_schema",
    "invalid_context",
    "variable_set_mismatch",
    "split_mismatch",
    "example_id_mismatch",
    "failed_outcome",
    "invalid_reference",
    "invalid_final_claims",
    "incomplete_review",
    "unknown_reference",
    "claim_status_mismatch",
    "unreferenced_claim",
]
_EXAMPLE_KEYS = frozenset(Example.model_fields)
_LABEL_KEYS = frozenset(LabelRecord.model_fields)
_REFERENCE_KEYS = frozenset(VariableReference.model_fields)
_RECORD_KEYS = ("claims", "evidence", "edges", "mentions", "mention_readings")


class ExampleValidationError(ValueError):
    """Stable annotated-data diagnostic that does not render source-bearing inputs."""

    def __init__(self, code: ExampleValidationCode) -> None:
        self.code = code
        super().__init__(code)


def _raise(code: ExampleValidationCode) -> NoReturn:
    raise ExampleValidationError(code) from None


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _raise("invalid_schema")
    try:
        copied = dict(value)
    except (TypeError, ValueError):
        _raise("invalid_schema")
    if not all(type(key) is str for key in copied):
        _raise("invalid_schema")
    return copied


def _records(value: Any) -> list[Mapping[str, Any]]:
    if type(value) is not list or not all(isinstance(item, Mapping) for item in value):
        _raise("invalid_schema")
    return value


def _bound_case(case: Case) -> Case:
    try:
        if not isinstance(case, Case):
            _raise("invalid_context")
        return Case.model_validate(case.model_dump(warnings=False))
    except (AttributeError, TypeError, ValueError):
        _raise("invalid_context")


def _require_schema_version(payload: dict[str, Any]) -> None:
    try:
        payload["schema_version"] = check_schema_version(payload["schema_version"])
    except ValueError:
        _raise("invalid_schema")


def _require_set(payload: Mapping[str, Any], variables: VariableSet) -> None:
    if not isinstance(variables, VariableSet):
        _raise("invalid_context")
    if (
        payload["variable_set"] != variables.name
        or payload["variable_set_version"] != variables.version
        or payload["variable_set_ref"] != variables.schema_ref
    ):
        _raise("variable_set_mismatch")


def _require_case(payload: Mapping[str, Any], case: Case, variables: VariableSet) -> None:
    if type(payload["case_id"]) is not str or payload["case_id"] != case.case_id:
        _raise("invalid_context")
    if case.task != variables.name:
        _raise("invalid_context")
    if payload["split"] != case.split:
        _raise("split_mismatch")


def _reference(
    name: str,
    item: Any,
    *,
    case: Case,
    variables: VariableSet,
    state: _ValidatedState,
    as_of_event: str,
) -> VariableReference:
    if isinstance(item, VariableReference):
        item = item.model_dump(warnings=False)
    item = _mapping(item)
    if set(item) != _REFERENCE_KEYS:
        _raise("invalid_schema")
    if item["variable"] != name:
        _raise("invalid_context")
    try:
        outcome = (
            item["outcome"]
            if isinstance(item["outcome"], CaseOutcome)
            else CaseOutcome.model_validate(item["outcome"])
        )
    except (TypeError, ValueError):
        _raise("invalid_schema")
    if outcome.status == "failed":
        _raise("failed_outcome")
    final_ids = item["final_claim_ids"]
    if not isinstance(final_ids, Sequence) or isinstance(final_ids, (str, bytes, bytearray)):
        _raise("invalid_schema")
    if (outcome.status == "answered") != bool(final_ids):
        _raise("invalid_final_claims")
    try:
        reference = VariableReference.model_validate({**item, "outcome": outcome})
    except (TypeError, ValueError):
        _raise("invalid_reference")

    if outcome.provenance.sensitivity is not case.sensitivity:
        _raise("invalid_context")
    if not set(outcome.reviewed_source_ids).issubset(case.source_ids):
        _raise("invalid_context")
    if outcome.status == "not_mentioned" and set(outcome.reviewed_source_ids) != set(
        case.source_ids
    ):
        _raise("incomplete_review")
    if any(evidence_id not in state.evidence for evidence_id in outcome.evidence_ids):
        _raise("unknown_reference")
    if any(
        claim_id not in state.claims or state.claims[claim_id].variable != name
        for claim_id in reference.final_claim_ids
    ):
        _raise("invalid_final_claims")
    if variables.variables[name].level == "mention" and outcome.status == "conflicting_evidence":
        _raise("invalid_reference")
    if any(event_id > as_of_event for event_id in reference.derived_from_events):
        _raise("invalid_reference")
    return reference


def _claim_closure(state: _ValidatedState, roots: Sequence[str]) -> set[str]:
    visited: set[str] = set()
    pending = list(roots)
    while pending:
        node_id = pending.pop()
        if node_id in visited:
            continue
        visited.add(node_id)
        pending.extend(state.adjacency[node_id])
    return {node_id for node_id in visited if node_id in state.claims}


def _require_statuses_by_tier(
    state: _ValidatedState, references: Mapping[str, VariableReference]
) -> None:
    """Claim status records review and tier records quality; each record meets the
    strictest requirement among the references it serves."""
    serves: dict[str, set[bool]] = {}
    final_of: dict[str, set[bool]] = {}
    reading_serves: dict[str, set[bool]] = {}
    for reference in references.values():
        human = reference.tier in HUMAN_TIERS
        for claim_id in reference.final_claim_ids:
            final_of.setdefault(claim_id, set()).add(human)
        for claim_id in _claim_closure(state, reference.final_claim_ids):
            serves.setdefault(claim_id, set()).add(human)
            reading_id = state.claims[claim_id].reading_id
            if reading_id is not None:
                reading_serves.setdefault(reading_id, set()).add(human)

    for claim_id, claim in state.claims.items():
        if claim_id not in serves:
            _raise("unreferenced_claim")
        if True in serves[claim_id]:
            allowed = {"gold"} if True in final_of.get(claim_id, set()) else {"gold", "verified"}
        else:
            allowed = {"proposed"}
        if claim.status not in allowed:
            _raise("claim_status_mismatch")
    for reading_id, served in reading_serves.items():
        allowed = {"gold", "verified"} if True in served else {"proposed"}
        if state.readings[reading_id].status not in allowed:
            _raise("claim_status_mismatch")


def load_example(
    payload: Mapping[str, Any],
    *,
    case: Case,
    source: Source,
    variables: VariableSet,
    excluded_spans: Sequence[tuple[int, int]] = (),
) -> Example:
    """Revalidate an Example against authoritative context (contract v0.1 section 4.3).

    The ``example_id`` must be the hash of the validated content (``amber.ids.example_id``),
    so any edited value, status, tier, outcome, or attribution fails with
    ``example_id_mismatch``. Graph problems raise ``GraphValidationError``; annotated-data
    problems raise ``ExampleValidationError``. Neither renders payload values.
    """
    return _load_example(
        payload, case=case, source=source, variables=variables, excluded_spans=excluded_spans
    )


def mint_example(
    payload: Mapping[str, Any],
    *,
    case: Case,
    source: Source,
    variables: VariableSet,
    excluded_spans: Sequence[tuple[int, int]] = (),
) -> Example:
    """Validate a payload exactly as ``load_example`` does and give it its content ID,
    ignoring any supplied ``example_id``. Used to build Examples from projections and to
    re-key Examples stored before content IDs."""
    return _load_example(
        payload,
        case=case,
        source=source,
        variables=variables,
        excluded_spans=excluded_spans,
        assign_id=True,
    )


def _load_example(
    payload: Mapping[str, Any],
    *,
    case: Case,
    source: Source,
    variables: VariableSet,
    excluded_spans: Sequence[tuple[int, int]],
    assign_id: bool = False,
) -> Example:
    bound_case = _bound_case(case)
    copied = _mapping(payload)
    if set(copied) != _EXAMPLE_KEYS:
        _raise("invalid_schema")
    _require_schema_version(copied)
    _require_set(copied, variables)
    _require_case(copied, bound_case, variables)
    if (
        copied["source_ids"] != bound_case.source_ids
        or bound_case.source_ids != [source.source_id]
        or bound_case.patient_id != source.patient_id
    ):
        _raise("invalid_context")
    as_of_event = copied["as_of_event"]
    try:
        validate_ulid(as_of_event)
    except (TypeError, ValueError):
        _raise("invalid_schema")
    if assign_id:
        copied["example_id"] = "0" * 64
    elif not isinstance(copied["example_id"], str):
        _raise("invalid_schema")

    context = build_context(
        source=source,
        variables=variables,
        sensitivity=bound_case.sensitivity,
        excluded_spans=excluded_spans,
    )
    records = {key: _records(copied[key]) for key in _RECORD_KEYS}
    state = validate_state(
        context,
        claims=records["claims"],
        evidence=records["evidence"],
        edges=records["edges"],
        mentions=records["mentions"],
        readings=records["mention_readings"],
        statuses=RETAINED_STATUSES,
    )

    finals = sorted(name for name, variable in variables.variables.items() if variable.final)
    supplied = _mapping(copied["references"])
    if set(supplied) != set(finals):
        _raise("invalid_context")
    references = {
        name: _reference(
            name,
            supplied[name],
            case=bound_case,
            variables=variables,
            state=state,
            as_of_event=as_of_event,
        )
        for name in finals
    }
    _require_statuses_by_tier(state, references)

    try:
        example = _mint_example(
            {
                **copied,
                "references": references,
                **ordered_records(
                    claims=state.claims,
                    evidence=state.evidence,
                    edges=state.edges,
                    mentions=state.mentions,
                    readings=state.readings,
                ),
            }
        )
    except (AttributeError, TypeError, ValueError, ValidationError):
        _raise("invalid_schema")
    if example.created_at.utcoffset() is None:
        _raise("invalid_schema")
    content_id = example_id(example.model_dump(mode="json"))
    if assign_id:
        return example.model_copy(update={"example_id": content_id})
    if example.example_id != content_id:
        _raise("example_id_mismatch")
    return example


def load_label_record(
    payload: Mapping[str, Any], *, case: Case, variables: VariableSet
) -> LabelRecord:
    """Revalidate a silver label against its Case and VariableSet (contract v0.1 section 4.6).

    The value must validate against the final variable's answer model and is normalized to
    its JSON form. A LabelRecord never loads as an Example.
    """
    bound_case = _bound_case(case)
    copied = _mapping(payload)
    if set(copied) != _LABEL_KEYS:
        _raise("invalid_schema")
    _require_schema_version(copied)
    _require_set(copied, variables)
    _require_case(copied, bound_case, variables)
    variable = (
        variables.variables.get(copied["variable"]) if type(copied["variable"]) is str else None
    )
    if variable is None or not variable.final:
        _raise("invalid_context")
    try:
        record = LabelRecord.model_validate(copied)
        value = None
        if record.value is not None:
            answer = variable.answer_model.model_validate(dict(record.value))
            value = answer.model_dump(mode="json", warnings=False)
        return LabelRecord.model_validate({**record.model_dump(), "value": value})
    except (TypeError, ValueError):
        _raise("invalid_schema")
