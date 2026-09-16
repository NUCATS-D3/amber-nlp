"""Private deterministic validation for complete note-scoped evidence graphs."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, NoReturn

from pydantic import ValidationError

from amber.graph_errors import GraphValidationCode, GraphValidationError
from amber.grounding import exact_quote
from amber.ids import inclusion_id, schema_ref
from amber.schemas.claims import Claim, _ClaimRecord, _mint_claim
from amber.schemas.evidence import EvidenceEdge, Inclusion, InferenceEvidence
from amber.schemas.provenance import Sensitivity
from amber.schemas.sources import Source, SourceKind
from amber.schemas.tasks import Task

_INCLUSION_FIELDS = frozenset(Inclusion.model_fields)
_REQUIRED_INCLUSION_FIELDS = _INCLUSION_FIELDS - {"mention_id"}


@dataclass(frozen=True)
class _GraphContext:
    source: Source
    task: Task
    sensitivity: Sensitivity
    excluded_spans: tuple[tuple[int, int], ...]
    schema_ref: str


@dataclass(frozen=True)
class _ValidatedState:
    claims: Mapping[str, Claim]
    evidence: Mapping[str, Inclusion | InferenceEvidence]
    edges: tuple[EvidenceEdge, ...]
    adjacency: Mapping[str, tuple[str, ...]]


def _raise(code: GraphValidationCode) -> NoReturn:
    raise GraphValidationError(code) from None


def _copy_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return dict(value)
    except (TypeError, ValueError):
        _raise("invalid_schema")


def build_context(
    *,
    source: Source,
    task: Task,
    sensitivity: Sensitivity,
    excluded_spans: Sequence[tuple[int, int]] = (),
) -> _GraphContext:
    """Revalidate and defensively capture the authoritative graph context."""
    try:
        if not isinstance(source, Source) or not isinstance(task, Task):
            _raise("invalid_context")
        copied_source = Source.model_validate(source.model_dump(warnings=False))
        copied_task = Task.model_validate(task.model_dump(warnings=False))
    except GraphValidationError:
        raise
    except (AttributeError, TypeError, ValueError, ValidationError):
        _raise("invalid_context")

    source_text = copied_source.text
    if (
        copied_source.kind is not SourceKind.note
        or source_text is None
        or copied_task.scope != "note"
        or not isinstance(sensitivity, Sensitivity)
    ):
        _raise("invalid_context")

    spans: list[tuple[int, int]] = []
    try:
        for item in excluded_spans:
            if not isinstance(item, tuple) or len(item) != 2:
                _raise("invalid_context")
            start, end = item
            if (
                type(start) is not int
                or type(end) is not int
                or not 0 <= start < end <= len(source_text)
            ):
                _raise("invalid_context")
            if spans and start < spans[-1][1]:
                _raise("invalid_context")
            spans.append((start, end))
    except GraphValidationError:
        raise
    except (TypeError, ValueError):
        _raise("invalid_context")

    try:
        return _GraphContext(
            source=copied_source,
            task=copied_task,
            sensitivity=sensitivity,
            excluded_spans=tuple(spans),
            schema_ref=schema_ref(copied_task.answer_model),
        )
    except (TypeError, ValueError):
        _raise("invalid_schema")


def _validate_inclusion(
    context: _GraphContext,
    candidate: Mapping[str, Any],
) -> Inclusion:
    payload = _copy_mapping(candidate)
    supplied_fields = set(payload)
    if (
        not supplied_fields >= _REQUIRED_INCLUSION_FIELDS
        or not supplied_fields <= _INCLUSION_FIELDS
    ):
        _raise("invalid_schema")
    if payload.get("kind") != "inclusion":
        _raise("unsupported_evidence")

    start_value = payload.get("start")
    end_value = payload.get("end")
    quote_value = payload.get("quote")
    if (
        not isinstance(start_value, int)
        or isinstance(start_value, bool)
        or not isinstance(end_value, int)
        or isinstance(end_value, bool)
        or not isinstance(quote_value, str)
    ):
        _raise("invalid_span")
    start = start_value
    end = end_value
    quote = quote_value
    text = context.source.text
    if text is None:
        _raise("invalid_context")
    if not 0 <= start < end <= len(text):
        _raise("invalid_span")
    if quote != text[start:end]:
        _raise("quote_mismatch")
    if any(start < hi and lo < end for lo, hi in context.excluded_spans):
        _raise("invalid_context")

    evidence_id = payload.get("evidence_id")
    source_id = payload.get("source_id")
    if (
        type(evidence_id) is not str
        or type(source_id) is not str
        or source_id != context.source.source_id
        or evidence_id != inclusion_id(source_id=source_id, start=start, end=end)
    ):
        _raise("invalid_context")
    score = payload.get("alignment_score")
    if (
        payload.get("alignment") != "exact"
        or payload.get("mention_id") is not None
        or type(score) not in (int, float)
        or type(score) is bool
        or score != 1.0
    ):
        _raise("unsupported_evidence")

    verified = exact_quote(context.source, quote, hint_start=start)
    if not isinstance(verified, Inclusion):
        _raise("invalid_context")
    verified_payload = verified.model_dump()
    if any(verified_payload[field] != value for field, value in payload.items()):
        _raise("invalid_context")
    return verified


def _validate_claim(context: _GraphContext, candidate: Mapping[str, Any]) -> _ClaimRecord:
    try:
        record = _ClaimRecord.model_validate(_copy_mapping(candidate))
    except GraphValidationError:
        raise
    except (TypeError, ValueError, ValidationError):
        _raise("invalid_schema")
    if record.status not in ("proposed", "rejected"):
        _raise("unsupported_claim_status")
    if (
        record.source_id != context.source.source_id
        or record.patient_id != context.source.patient_id
        or record.task != context.task.name
        or record.schema_ref != context.schema_ref
        or record.provenance.sensitivity is not context.sensitivity
    ):
        _raise("invalid_context")
    try:
        answer = context.task.answer_model.model_validate(record.value)
        normalized = answer.model_dump(mode="json")
        return _ClaimRecord.model_validate({**record.model_dump(), "value": normalized})
    except (TypeError, ValueError, ValidationError):
        _raise("invalid_schema")


def _validate_inference(
    context: _GraphContext,
    candidate: Mapping[str, Any],
) -> InferenceEvidence:
    try:
        record = InferenceEvidence.model_validate(_copy_mapping(candidate))
    except GraphValidationError:
        raise
    except (TypeError, ValueError, ValidationError):
        _raise("invalid_schema")
    if record.provenance.sensitivity != context.sensitivity:
        _raise("invalid_context")
    return record


def validate_state(
    context: _GraphContext,
    *,
    claims: Sequence[Mapping[str, Any]],
    evidence: Sequence[Mapping[str, Any]],
    edges: Sequence[Mapping[str, Any]],
) -> _ValidatedState:
    """Validate every retained component before issuing any accepted Claim."""
    claim_records: dict[str, _ClaimRecord] = {}
    evidence_records: dict[str, Inclusion | InferenceEvidence] = {}

    for candidate in claims:
        claim_record = _validate_claim(context, candidate)
        if claim_record.claim_id in claim_records:
            _raise("duplicate_reference")
        claim_records[claim_record.claim_id] = claim_record

    for candidate in evidence:
        payload = _copy_mapping(candidate)
        kind = payload.get("kind")
        if kind == "inclusion":
            evidence_record: Inclusion | InferenceEvidence = _validate_inclusion(context, payload)
        elif kind == "inference":
            evidence_record = _validate_inference(context, payload)
        else:
            _raise("unsupported_evidence")
        if (
            evidence_record.evidence_id in evidence_records
            or evidence_record.evidence_id in claim_records
        ):
            _raise("duplicate_reference")
        evidence_records[evidence_record.evidence_id] = evidence_record

    validated_edges: list[EvidenceEdge] = []
    edge_keys: set[tuple[str, str, str | None]] = set()
    adjacency_lists: dict[str, set[str]] = {
        node_id: set() for node_id in (*claim_records, *evidence_records)
    }
    field_names = frozenset(context.task.answer_model.model_fields)
    field_roles: dict[str, set[str]] = {claim_id: set() for claim_id in claim_records}
    for candidate in edges:
        try:
            edge = EvidenceEdge.model_validate(_copy_mapping(candidate))
        except GraphValidationError:
            raise
        except (TypeError, ValueError, ValidationError):
            _raise("invalid_schema")
        key = (edge.claim_id, edge.evidence_id, edge.role)
        if key in edge_keys:
            _raise("duplicate_reference")
        edge_keys.add(key)
        if edge.claim_id not in claim_records or edge.evidence_id not in evidence_records:
            _raise("unknown_reference")
        if edge.role is not None and edge.role not in field_names:
            _raise("invalid_context")
        validated_edges.append(edge)
        adjacency_lists[edge.claim_id].add(edge.evidence_id)
        if edge.role is not None:
            field_roles[edge.claim_id].add(edge.role)

    for evidence_id, evidence_record in evidence_records.items():
        if not isinstance(evidence_record, InferenceEvidence):
            continue
        for input_id in evidence_record.inputs:
            if input_id in claim_records:
                if claim_records[input_id].status == "rejected":
                    _raise("invalid_context")
            elif input_id not in evidence_records:
                _raise("unknown_reference")
            adjacency_lists[evidence_id].add(input_id)

    for claim_id, claim_record in claim_records.items():
        if claim_record.status == "rejected":
            continue
        if not adjacency_lists[claim_id]:
            _raise("missing_evidence")
        if context.task.evidence_policy == "field" and field_roles[claim_id] != field_names:
            _raise("missing_field_evidence")

    adjacency = {node_id: tuple(sorted(children)) for node_id, children in adjacency_lists.items()}
    parents: dict[str, list[str]] = {node_id: [] for node_id in adjacency}
    remaining = {node_id: len(children) for node_id, children in adjacency.items()}
    for parent_id, children in adjacency.items():
        for child_id in children:
            parents[child_id].append(parent_id)

    ready = deque(node_id for node_id, count in remaining.items() if count == 0)
    backed: dict[str, bool] = {}
    processed = 0
    inclusion_ids = {
        evidence_id
        for evidence_id, evidence_record in evidence_records.items()
        if isinstance(evidence_record, Inclusion)
    }
    while ready:
        node_id = ready.popleft()
        processed += 1
        children = adjacency[node_id]
        backed[node_id] = node_id in inclusion_ids or (
            bool(children) and all(backed[child_id] for child_id in children)
        )
        for parent_id in parents[node_id]:
            remaining[parent_id] -= 1
            if remaining[parent_id] == 0:
                ready.append(parent_id)

    if processed != len(adjacency):
        _raise("cyclic_support")
    for evidence_id, evidence_record in evidence_records.items():
        if isinstance(evidence_record, InferenceEvidence) and not backed[evidence_id]:
            _raise("source_free_support")
    for claim_id, claim_record in claim_records.items():
        if claim_record.status != "rejected" and not backed[claim_id]:
            _raise("source_free_support")

    minted: dict[str, Claim] = {}
    try:
        for claim_id, claim_record in claim_records.items():
            minted[claim_id] = _mint_claim(claim_record.model_dump())
    except (TypeError, ValueError, ValidationError):
        _raise("invalid_schema")

    return _ValidatedState(
        claims=MappingProxyType(dict(minted)),
        evidence=MappingProxyType(dict(evidence_records)),
        edges=tuple(validated_edges),
        adjacency=MappingProxyType(dict(adjacency)),
    )
