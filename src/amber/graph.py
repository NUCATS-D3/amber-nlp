"""Owned, validated lifecycle for one note-scoped evidence graph."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any, NoReturn

from amber._graph_validation import _GraphContext, _ValidatedState, build_context, validate_state
from amber.graph_errors import GraphValidationCode, GraphValidationError
from amber.schemas.claims import Claim
from amber.schemas.evidence import EvidenceEdge, Inclusion, InferenceEvidence
from amber.schemas.provenance import Sensitivity
from amber.schemas.sources import Source
from amber.schemas.tasks import Task

_FORMAT_VERSION = 1
_PAYLOAD_KEYS = frozenset({"format_version", "context", "claims", "evidence", "edges"})
_CONTEXT_KEYS = frozenset(
    {
        "task",
        "schema_ref",
        "evidence_policy",
        "scope",
        "source_id",
        "patient_id",
        "sensitivity",
        "excluded_spans",
    }
)


def _raise(code: GraphValidationCode) -> NoReturn:
    raise GraphValidationError(code) from None


def _strict_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _raise("invalid_schema")
    try:
        copied = dict(value)
    except (TypeError, ValueError):
        _raise("invalid_schema")
    if not all(type(key) is str for key in copied):
        _raise("invalid_schema")
    return copied


def _strict_record_list(value: Any) -> list[Mapping[str, Any]]:
    if type(value) is not list:
        _raise("invalid_schema")
    records: list[Mapping[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            _raise("invalid_schema")
        records.append(item)
    return records


def _snapshot_exclusions(value: Any, *, source_length: int) -> tuple[tuple[int, int], ...]:
    if type(value) is not list:
        _raise("invalid_context")
    spans: list[tuple[int, int]] = []
    for item in value:
        if type(item) is not list or len(item) != 2:
            _raise("invalid_context")
        start, end = item
        if (
            type(start) is not int
            or type(end) is not int
            or not 0 <= start < end <= source_length
            or (spans and start < spans[-1][1])
        ):
            _raise("invalid_context")
        spans.append((start, end))
    return tuple(spans)


def _copy_source(source: Source) -> Source:
    try:
        return Source.model_validate(source.model_dump())
    except (TypeError, ValueError):
        _raise("invalid_context")


def _copy_task(task: Task) -> Task:
    try:
        return Task.model_validate(task.model_dump())
    except (TypeError, ValueError):
        _raise("invalid_context")


class EvidenceGraph:
    """An atomically published, validated evidence graph for one source and task."""

    __slots__ = ("_context", "_state")

    def __init__(
        self,
        *,
        source: Source,
        task: Task,
        sensitivity: Sensitivity,
        excluded_spans: Sequence[tuple[int, int]] = (),
    ) -> None:
        context = build_context(
            source=source,
            task=task,
            sensitivity=sensitivity,
            excluded_spans=excluded_spans,
        )
        state = validate_state(context, claims=[], evidence=[], edges=[])
        self._context: _GraphContext = context
        self._state: _ValidatedState = state

    @property
    def source(self) -> Source:
        """Return a detached, independently validated source copy."""
        return _copy_source(self._context.source)

    @property
    def task(self) -> Task:
        """Return a detached, independently validated task copy."""
        return _copy_task(self._context.task)

    @property
    def sensitivity(self) -> Sensitivity:
        return self._context.sensitivity

    @property
    def excluded_spans(self) -> tuple[tuple[int, int], ...]:
        return self._context.excluded_spans

    @property
    def claims(self) -> Mapping[str, Claim]:
        return MappingProxyType(
            {node_id: claim.model_copy(deep=True) for node_id, claim in self._state.claims.items()}
        )

    @property
    def evidence(self) -> Mapping[str, Inclusion | InferenceEvidence]:
        return MappingProxyType(
            {
                node_id: record.model_copy(deep=True)
                for node_id, record in self._state.evidence.items()
            }
        )

    @property
    def edges(self) -> tuple[EvidenceEdge, ...]:
        return tuple(edge.model_copy(deep=True) for edge in self._state.edges)

    def _records(
        self,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        claims = [claim.model_dump() for claim in self._state.claims.values()]
        evidence = [record.model_dump() for record in self._state.evidence.values()]
        edges = [edge.model_dump() for edge in self._state.edges]
        return claims, evidence, edges

    def register_evidence(
        self, record: Inclusion | InferenceEvidence
    ) -> Inclusion | InferenceEvidence:
        """Register evidence after validating the complete candidate graph."""
        if not isinstance(record, (Inclusion, InferenceEvidence)):
            _raise("unsupported_evidence")
        try:
            record_payload = record.model_dump(warnings=False)
            record_id = record.evidence_id
        except (AttributeError, TypeError, ValueError):
            _raise("invalid_schema")

        claims, evidence, edges = self._records()
        existing_index = next(
            (
                index
                for index, candidate in enumerate(evidence)
                if candidate.get("evidence_id") == record_id
            ),
            None,
        )
        if existing_index is None:
            evidence.append(record_payload)
        else:
            evidence[existing_index] = record_payload

        candidate_state = validate_state(
            self._context,
            claims=claims,
            evidence=evidence,
            edges=edges,
        )
        incoming = candidate_state.evidence[record_id]
        current = self._state.evidence.get(record_id)
        if current is not None and incoming != current:
            _raise("duplicate_reference")

        self._state = candidate_state
        return incoming.model_copy(deep=True)

    def validate(self) -> None:
        """Revalidate and atomically republish the current complete state."""
        claims, evidence, edges = self._records()
        candidate_state = validate_state(
            self._context,
            claims=claims,
            evidence=evidence,
            edges=edges,
        )
        self._state = candidate_state

    def support_closure(self, node_id: str) -> tuple[str, ...]:
        """Return the complete validated support closure, including ``node_id``."""
        if type(node_id) is not str or node_id not in self._state.adjacency:
            _raise("unknown_reference")
        visited: set[str] = set()
        pending = [node_id]
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            pending.extend(self._state.adjacency[current])
        return tuple(sorted(visited))

    def source_leaves(self, node_id: str) -> tuple[Inclusion, ...]:
        """Return all verified source leaves in a node's support closure."""
        closure = self.support_closure(node_id)
        leaves = (
            record
            for child_id in closure
            if isinstance((record := self._state.evidence.get(child_id)), Inclusion)
        )
        return tuple(
            leaf.model_copy(deep=True) for leaf in sorted(leaves, key=lambda item: item.evidence_id)
        )

    def to_payload(self) -> dict[str, Any]:
        """Return a deterministic, detached, JSON-compatible graph snapshot."""
        claims = sorted(self._state.claims.values(), key=lambda record: record.claim_id)
        evidence = sorted(self._state.evidence.values(), key=lambda record: record.evidence_id)
        edges = sorted(
            self._state.edges,
            key=lambda edge: (edge.claim_id, edge.evidence_id, edge.role or ""),
        )
        return {
            "format_version": _FORMAT_VERSION,
            "context": {
                "task": self._context.task.name,
                "schema_ref": self._context.schema_ref,
                "evidence_policy": self._context.task.evidence_policy,
                "scope": self._context.task.scope,
                "source_id": self._context.source.source_id,
                "patient_id": self._context.source.patient_id,
                "sensitivity": self._context.sensitivity.value,
                "excluded_spans": [list(span) for span in self._context.excluded_spans],
            },
            "claims": [record.model_dump(mode="json") for record in claims],
            "evidence": [record.model_dump(mode="json") for record in evidence],
            "edges": [edge.model_dump(mode="json") for edge in edges],
        }

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[str, Any],
        *,
        source: Source,
        task: Task,
        sensitivity: Sensitivity,
        excluded_spans: Sequence[tuple[int, int]] = (),
    ) -> EvidenceGraph:
        """Restore only after strict context and complete graph revalidation."""
        graph = cls(
            source=source,
            task=task,
            sensitivity=sensitivity,
            excluded_spans=excluded_spans,
        )
        envelope = _strict_mapping(payload)
        if set(envelope) != _PAYLOAD_KEYS:
            _raise("invalid_schema")
        version = envelope["format_version"]
        if type(version) is not int or version != _FORMAT_VERSION:
            _raise("invalid_schema")

        snapshot_context = _strict_mapping(envelope["context"])
        if set(snapshot_context) != _CONTEXT_KEYS:
            _raise("invalid_schema")
        expected = {
            "task": graph._context.task.name,
            "schema_ref": graph._context.schema_ref,
            "evidence_policy": graph._context.task.evidence_policy,
            "scope": graph._context.task.scope,
            "source_id": graph._context.source.source_id,
            "patient_id": graph._context.source.patient_id,
            "sensitivity": graph._context.sensitivity.value,
        }
        for key, value in expected.items():
            supplied = snapshot_context[key]
            if type(supplied) is not str or supplied != value:
                _raise("invalid_context")
        source_text = graph._context.source.text
        if source_text is None:
            _raise("invalid_context")
        snapshot_spans = _snapshot_exclusions(
            snapshot_context["excluded_spans"],
            source_length=len(source_text),
        )
        if snapshot_spans != graph._context.excluded_spans:
            _raise("invalid_context")

        claims = _strict_record_list(envelope["claims"])
        evidence = _strict_record_list(envelope["evidence"])
        edges = _strict_record_list(envelope["edges"])
        candidate_state = validate_state(
            graph._context,
            claims=claims,
            evidence=evidence,
            edges=edges,
        )
        graph._state = candidate_state
        return graph
