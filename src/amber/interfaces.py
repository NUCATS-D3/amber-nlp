"""Protocols that implementations satisfy structurally (contract v0.1 section 6).

amber never imports an implementation. Every protocol here is experimental until a real
consumer uses it and the conformance kit covers it (charter, rules for growth). Synthetic
reference implementations live in ``amber.reference``, which ``import amber`` does not load.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Literal, Protocol, TypeAlias, runtime_checkable

from amber.annotations import Projection
from amber.schemas.annotations import AnnotationEvent
from amber.schemas.cases import Case, CaseResult
from amber.schemas.evidence import Inclusion
from amber.schemas.examples import Example, LabelRecord
from amber.schemas.mentions import Mention
from amber.schemas.sources import Source
from amber.schemas.variables import VariableSet

StoreCapability: TypeAlias = Literal["events", "examples", "query", "transactions"]


@runtime_checkable
class SourceResolver(Protocol):
    """Returns the authoritative Source for an ID, or None (contract v0.1 section 5.6)."""

    def resolve(self, source_id: str) -> Source | None: ...


@runtime_checkable
class Store(Protocol):
    """Persistence for cases, results, events, and Examples, revalidated on every read.

    Every ``get_*`` and ``project`` revalidates through amber's load boundaries with the
    caller's resolver, so a store need not hold source text. Results are keyed by
    ``(case_id, run_id)``. Events are append-only. ``put_example`` requires the Example's
    events first and rejects a split, tier, or ``example_id`` the case and events do not
    support; it takes a resolver because checking the tier means replaying the events.
    ``current_example`` is the snapshot at the case's latest event.
    """

    capabilities: frozenset[StoreCapability]

    def put_case(self, case: Case, *, variables: VariableSet) -> None: ...
    def get_case(self, case_id: str) -> Case: ...
    def put_result(self, result: CaseResult) -> None: ...
    def get_result(self, case_id: str, run_id: str, *, resolver: SourceResolver) -> CaseResult: ...
    def runs(self, case_id: str) -> Sequence[str]: ...
    def append_events(
        self, events: Sequence[AnnotationEvent], *, spans: Iterable[Inclusion | Mention] = ()
    ) -> None: ...
    def events(self, case_id: str, *, stream: str | None = None) -> Sequence[AnnotationEvent]: ...
    def project(
        self,
        case_id: str,
        *,
        actor: str | None,
        as_of_event: str | None = None,
        resolver: SourceResolver,
    ) -> Projection: ...
    def put_example(self, example: Example, *, resolver: SourceResolver) -> None: ...
    def get_example(self, example_id: str, *, resolver: SourceResolver) -> Example: ...
    def current_example(self, case_id: str, *, resolver: SourceResolver) -> Example | None: ...
    def put_label_record(self, record: LabelRecord) -> None: ...
    def label_records(self, case_id: str) -> Sequence[LabelRecord]: ...
