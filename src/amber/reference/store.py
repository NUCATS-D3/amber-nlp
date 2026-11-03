"""``InMemoryStore`` and ``InMemoryResolver``: synthetic references for ``Store`` and
``SourceResolver`` (contract v0.1 sections 5.6 and 6.2).

Records are held as JSON payloads and revalidated through amber's load boundaries on every
read, exactly as a persistent store must.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any, Literal, NoReturn, TypeAlias

from amber.annotations import (
    AnnotationValidationError,
    Projection,
    example_from_projection,
    replay,
    verify_example,
)
from amber.cases import load_case_result
from amber.examples import ExampleValidationError, load_example
from amber.graph_errors import GraphValidationError
from amber.ids import example_id
from amber.interfaces import SourceResolver, StoreCapability
from amber.schemas.annotations import AnnotationEvent
from amber.schemas.cases import Case, CaseResult
from amber.schemas.evidence import Inclusion
from amber.schemas.examples import Example, LabelRecord
from amber.schemas.mentions import Mention
from amber.schemas.sources import Source
from amber.schemas.variables import VariableSet

StoreErrorCode: TypeAlias = Literal[
    "invalid_record",
    "unknown_case",
    "case_conflict",
    "split_conflict",
    "split_mismatch",
    "variable_set_mismatch",
    "variable_set_version_conflict",
    "duplicate_result",
    "unknown_result",
    "duplicate_event",
    "event_order",
    "span_conflict",
    "events_required",
    "unsupported_tier",
    "example_id_mismatch",
    "unknown_example",
    "source_unavailable",
    "source_hash_mismatch",
]


class StoreError(ValueError):
    """Stable store diagnostic naming no record contents."""

    def __init__(self, code: StoreErrorCode) -> None:
        self.code = code
        super().__init__(code)


def _raise(code: StoreErrorCode) -> NoReturn:
    raise StoreError(code) from None


class InMemoryResolver:
    """Holds synthetic sources in memory and returns them by ID."""

    def __init__(self, sources: Iterable[Source] = ()) -> None:
        self._sources = {source.source_id: source for source in sources}

    def resolve(self, source_id: str) -> Source | None:
        source = self._sources.get(source_id)
        return None if source is None else Source.model_validate(source.model_dump())


class InMemoryStore:
    """The reference ``Store``: append-only events, runs keyed by ``(case_id, run_id)``."""

    capabilities: frozenset[StoreCapability] = frozenset({"events", "examples"})

    def __init__(self) -> None:
        self._sets: dict[str, VariableSet] = {}
        self._set_versions: dict[tuple[str, str], str] = {}
        self._cases: dict[str, tuple[Case, str]] = {}
        self._splits: dict[tuple[str | None, str], str | None] = {}
        self._results: dict[str, dict[str, dict[str, Any]]] = {}
        self._events: dict[str, list[AnnotationEvent]] = {}
        self._spans: dict[str, Inclusion | Mention] = {}
        self._examples: dict[str, dict[str, Any]] = {}
        self._labels: dict[str, list[LabelRecord]] = {}

    # Cases and variable sets ---------------------------------------------------------------

    def _put_variable_set(self, variables: VariableSet) -> None:
        key = (variables.name, variables.version)
        known = self._set_versions.get(key)
        if known is not None and known != variables.schema_ref:
            _raise("variable_set_version_conflict")
        self._set_versions[key] = variables.schema_ref
        self._sets[variables.schema_ref] = VariableSet.model_validate(variables.model_dump())

    def put_case(self, case: Case, *, variables: VariableSet) -> None:
        """Register a case bound to its variable set.

        A case never changes, and a ``(dataset, source)`` pair has one split across every
        case and variable set.
        """
        if not isinstance(case, Case) or not isinstance(variables, VariableSet):
            _raise("invalid_record")
        case = Case.model_validate(case.model_dump())
        if case.task != variables.name:
            _raise("variable_set_mismatch")
        pair = (case.dataset, case.source_ids[0])
        known_case = self._cases.get(case.case_id)
        if known_case is not None:
            if known_case[0].split != case.split:
                _raise("split_conflict")
            if known_case != (case, variables.schema_ref):
                _raise("case_conflict")
            return
        if pair in self._splits and self._splits[pair] != case.split:
            _raise("split_conflict")
        self._put_variable_set(variables)
        self._splits[pair] = case.split
        self._cases[case.case_id] = (case, variables.schema_ref)

    def get_case(self, case_id: str) -> Case:
        return self._case(case_id)[0]

    def _case(self, case_id: str) -> tuple[Case, VariableSet]:
        known = self._cases.get(case_id)
        if known is None:
            _raise("unknown_case")
        case, ref = known
        return Case.model_validate(case.model_dump()), self._sets[ref]

    @staticmethod
    def _source(case: Case, resolver: SourceResolver) -> Source:
        source_id = case.source_ids[0]
        source = resolver.resolve(source_id)
        if source is None:
            _raise("source_unavailable")
        if not isinstance(source, Source) or source.source_id != source_id:
            _raise("source_hash_mismatch")
        return source

    # Results -------------------------------------------------------------------------------

    def put_result(self, result: CaseResult) -> None:
        if not isinstance(result, CaseResult):
            _raise("invalid_record")
        case, variables = self._case(result.case_id)
        if result.variable_set_ref != variables.schema_ref:
            _raise("variable_set_mismatch")
        runs = self._results.setdefault(case.case_id, {})
        if result.run_id in runs:
            _raise("duplicate_result")
        runs[result.run_id] = result.model_dump(mode="json")

    def get_result(self, case_id: str, run_id: str, *, resolver: SourceResolver) -> CaseResult:
        case, variables = self._case(case_id)
        payload = self._results.get(case_id, {}).get(run_id)
        if payload is None:
            _raise("unknown_result")
        return load_case_result(
            payload, case=case, source=self._source(case, resolver), variables=variables
        )

    def runs(self, case_id: str) -> Sequence[str]:
        self._case(case_id)
        return tuple(self._results.get(case_id, {}))

    # Events --------------------------------------------------------------------------------

    def append_events(
        self, events: Sequence[AnnotationEvent], *, spans: Iterable[Inclusion | Mention] = ()
    ) -> None:
        """Append events atomically. Events are never updated or deleted, and each case's
        events arrive in ``event_id`` order."""
        validated: list[AnnotationEvent] = []
        for event in events:
            if not isinstance(event, AnnotationEvent):
                _raise("invalid_record")
            validated.append(AnnotationEvent.model_validate(event.model_dump()))
        latest = {case_id: log[-1].event_id for case_id, log in self._events.items() if log}
        known = {event.event_id for log in self._events.values() for event in log}
        for event in validated:
            self._case(event.case_id)
            if event.event_id in known:
                _raise("duplicate_event")
            previous = latest.get(event.case_id)
            if previous is not None and event.event_id <= previous:
                _raise("event_order")
            latest[event.case_id] = event.event_id
            known.add(event.event_id)
        new_spans: dict[str, Inclusion | Mention] = {}
        for span in spans:
            if isinstance(span, Inclusion):
                key = span.evidence_id
            elif isinstance(span, Mention):
                key = span.mention_id
            else:
                _raise("invalid_record")
            existing = self._spans.get(key, new_spans.get(key))
            if existing is not None and existing != span:
                _raise("span_conflict")
            new_spans[key] = span
        self._spans.update(new_spans)
        for event in validated:
            self._events.setdefault(event.case_id, []).append(event)

    def events(self, case_id: str, *, stream: str | None = None) -> Sequence[AnnotationEvent]:
        self._case(case_id)
        log = self._events.get(case_id, [])
        return tuple(event for event in log if stream is None or event.stream == stream)

    def project(
        self,
        case_id: str,
        *,
        actor: str | None,
        as_of_event: str | None = None,
        resolver: SourceResolver,
    ) -> Projection:
        case, variables = self._case(case_id)
        return replay(
            self._events.get(case_id, []),
            case=case,
            source=self._source(case, resolver),
            variables=variables,
            spans=self._spans.values(),
            actor=actor,
            as_of_event=as_of_event,
        )

    # Examples ------------------------------------------------------------------------------

    def put_example(self, example: Example, *, resolver: SourceResolver) -> None:
        if not isinstance(example, Example):
            _raise("invalid_record")
        case, variables = self._case(example.case_id)
        if example.variable_set_ref != variables.schema_ref:
            _raise("variable_set_mismatch")
        if example.split != case.split:
            _raise("split_mismatch")
        if example.example_id != example_id(example.model_dump(mode="json")):
            _raise("example_id_mismatch")
        log = self._events.get(case.case_id, [])
        if example.as_of_event not in {event.event_id for event in log}:
            _raise("events_required")
        try:
            verify_example(
                example,
                log,
                case=case,
                source=self._source(case, resolver),
                variables=variables,
                spans=self._spans.values(),
            )
        except (AnnotationValidationError, ExampleValidationError, GraphValidationError):
            _raise("unsupported_tier")
        self._examples[example.example_id] = example.model_dump(mode="json")

    def get_example(self, example_id: str, *, resolver: SourceResolver) -> Example:
        payload = self._examples.get(example_id)
        if payload is None:
            _raise("unknown_example")
        case, variables = self._case(payload["case_id"])
        return load_example(
            payload, case=case, source=self._source(case, resolver), variables=variables
        )

    def current_example(self, case_id: str, *, resolver: SourceResolver) -> Example | None:
        """The snapshot at the case's latest event, or None when that snapshot is not a valid
        Example (work in progress, an excluded case, or no events)."""
        case, variables = self._case(case_id)
        if not self._events.get(case_id):
            return None
        source = self._source(case, resolver)
        try:
            projection = replay(
                self._events[case_id],
                case=case,
                source=source,
                variables=variables,
                spans=self._spans.values(),
            )
            return example_from_projection(
                projection, case=case, source=source, variables=variables
            )
        except (AnnotationValidationError, ExampleValidationError, GraphValidationError):
            return None

    # Label records -------------------------------------------------------------------------

    def put_label_record(self, record: LabelRecord) -> None:
        if not isinstance(record, LabelRecord):
            _raise("invalid_record")
        case, variables = self._case(record.case_id)
        if record.variable_set_ref != variables.schema_ref:
            _raise("variable_set_mismatch")
        if record.split != case.split:
            _raise("split_mismatch")
        self._labels.setdefault(case.case_id, []).append(
            LabelRecord.model_validate(record.model_dump())
        )

    def label_records(self, case_id: str) -> Sequence[LabelRecord]:
        self._case(case_id)
        return tuple(self._labels.get(case_id, []))
