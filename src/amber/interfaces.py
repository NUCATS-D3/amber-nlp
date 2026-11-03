"""Protocols that implementations satisfy structurally (contract v0.1 section 6).

amber never imports an implementation. Every protocol here is experimental until a real
consumer uses it and the conformance kit covers it (charter, rules for growth). Synthetic
reference implementations live in ``amber.reference``, which ``import amber`` does not load.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Literal, Protocol, TypeAlias, runtime_checkable

from pydantic import BaseModel, ConfigDict

from amber.annotations import Projection
from amber.policy import (
    DataPolicy,
    Decision,
    Destination,
    PayloadKind,
    PolicyMatrix,
    ProviderDestination,
    RunContext,
)
from amber.providers import ProviderFailure, ProviderRequest, ProviderResponse
from amber.schemas.annotations import AnnotationEvent
from amber.schemas.cases import Case, CaseResult
from amber.schemas.evidence import Inclusion
from amber.schemas.examples import Example, LabelRecord
from amber.schemas.mentions import Mention
from amber.schemas.sources import Source
from amber.schemas.variables import VariableSet

StoreCapability: TypeAlias = Literal["events", "examples", "query", "transactions"]


@runtime_checkable
class PolicyGate(Protocol):
    """Decides every transfer (contract v0.1 section 1). Reference: ``amber.policy.StaticGate``."""

    matrix: PolicyMatrix

    def check(
        self,
        data: DataPolicy,
        destination: Destination,
        payload: PayloadKind,
        *,
        now: datetime,
        context: RunContext | None = None,
        case_id: str | None = None,
    ) -> Decision: ...
    def require(
        self,
        data: DataPolicy,
        destination: Destination,
        payload: PayloadKind,
        *,
        now: datetime,
        context: RunContext | None = None,
        case_id: str | None = None,
    ) -> Decision: ...


@runtime_checkable
class Provider(Protocol):
    """A model endpoint (contract v0.1 section 6.2). It never sees a ``Source``.

    ``complete`` returns a ``ProviderFailure`` rather than raising for expected failures, and
    refuses a request needing a capability its destination does not declare (for example a
    JSON schema without ``schema_constrained``) instead of returning unconstrained text.
    Callers go through ``amber.providers.call_provider``. Reference:
    ``amber.reference.ScriptedProvider``.
    """

    destination: ProviderDestination

    def complete(self, request: ProviderRequest) -> ProviderResponse | ProviderFailure: ...


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


class ExportedFile(BaseModel):
    """One written file: its payload kind, the decision that let it go, and its content hash."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    file: str
    payload_kind: PayloadKind
    decision: Decision
    content_sha256: str


class ExportReceipt(BaseModel):
    """What an export wrote and why it was allowed; ``labels_only``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    format: str
    destination: str
    location: str
    files: tuple[ExportedFile, ...]


ExportItem: TypeAlias = CaseResult | Example | LabelRecord


@runtime_checkable
class Exporter(Protocol):
    """Writes records in one format, gating every output file by its schema-derived payload kind.

    ``export`` calls the gate once per file before writing anything. If any file is refused,
    nothing is written and it raises ``PolicyViolation`` carrying every decision.
    """

    format: str

    def export(
        self,
        items: Sequence[ExportItem],
        target: Destination,
        *,
        gate: PolicyGate,
        data: DataPolicy,
        now: datetime,
    ) -> ExportReceipt: ...
