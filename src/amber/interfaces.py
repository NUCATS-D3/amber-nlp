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
from amber.extraction import ExtractorCapability
from amber.labeling import LabelerOutput
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
from amber.review import ReviewBatchReceipt
from amber.schemas.annotations import AnnotationEvent
from amber.schemas.cases import Budget, Case, CaseResult
from amber.schemas.evidence import Inclusion
from amber.schemas.examples import Example, LabelRecord, ReferenceTier
from amber.schemas.mentions import Mention
from amber.schemas.sources import Source
from amber.schemas.variables import VariableSet
from amber.scoring import ScoreReport

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
class Extractor(Protocol):
    """Turns one case into exactly one ``CaseResult`` (contract v0.1 section 6.2).

    ``run`` never raises for expected failures: a policy refusal, provider or parse failure,
    or exhausted budget is a result whose final variables are ``failed`` with that
    ``failure_kind``. It contacts only ``destinations``, each through the gate; builds every
    node through amber's tools with provenance from ``context``; and records ``budget`` and
    ``usage``, saying ``failed(budget)`` itself when usage exceeds the budget. With the
    ``batch`` capability, ``run_many`` returns one result per case in input order, each equal
    to what ``run`` gives. Reference: ``amber.reference.DeterministicExtractor``.
    """

    name: str
    version: str
    destinations: frozenset[Destination]
    capabilities: frozenset[ExtractorCapability]

    def run(
        self,
        case: Case,
        source: Source,
        variables: VariableSet,
        *,
        gate: PolicyGate,
        budget: Budget | None,
        context: RunContext,
    ) -> CaseResult: ...


ScorerNeed: TypeAlias = Literal["examples", "label_records", "mentions", "traces"]


@runtime_checkable
class Scorer(Protocol):
    """Scores predicted results against references (contract v0.1 section 6.2).

    The report is ``provisional`` when any reference is below ``adjudicated``, lists the
    ``example_id``s it used, and marks evidence metrics ``inconclusive`` against label records.
    A scorer refuses (``self_reference``) when predictions and a silver reference share a
    configuration (``amber.scoring.refuse_self_reference``). It sees offsets but never source
    text unless ``needs`` includes ``mentions``. References: ``StructuralValidityScorer``,
    ``LabelCorrectScorer``.
    """

    name: str
    needs: frozenset[ScorerNeed]

    def score(
        self,
        predicted: Sequence[CaseResult],
        reference: Sequence[Example | LabelRecord],
        *,
        variables: VariableSet,
    ) -> ScoreReport: ...


@runtime_checkable
class Labeler(Protocol):
    """Produces tiered references for a case (contract v0.1 section 6.2).

    Models, rules, and importers declare ``tier = silver`` (``amber.labeling.admit_labeler``
    rejects anything else). ``label`` returns ``(events, Example)``, with events beginning at
    ``import.result`` that replay to the Example, or a ``LabelRecord``, or a
    ``LabelerFailure``. Reference: ``amber.reference.ImportLabeler``.
    """

    name: str
    version: str
    tier: ReferenceTier

    def label(
        self,
        case: Case,
        source: Source,
        variables: VariableSet,
        *,
        gate: PolicyGate,
        context: RunContext,
    ) -> LabelerOutput: ...


@runtime_checkable
class ReviewBackend(Protocol):
    """Sends cases to a review service and imports reviewed references (contract v0.1
    section 6.2).

    ``destination.kind`` is ``review``. ``export_cases`` sends each case's source after a
    ``source_text`` decision, and its proposal (a ``CaseResult``) after a ``derived_text``
    decision, deciding everything before sending anything. ``import_references`` returns the
    validated Examples and the full event log (``amber.review.reviewed_example`` is the rule),
    or raises ``ReviewImportError``; it never returns partially validated records. Reference:
    ``amber.reference.FileReviewBackend``.
    """

    destination: Destination

    def export_cases(
        self,
        cases: Sequence[tuple[Case, Source, CaseResult | None]],
        *,
        variables: VariableSet,
        streams: Sequence[str],
        gate: PolicyGate,
        data: DataPolicy,
    ) -> ReviewBatchReceipt: ...

    def import_references(
        self, receipt: ReviewBatchReceipt, *, resolver: SourceResolver
    ) -> tuple[Sequence[Example], Sequence[AnnotationEvent]]: ...


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
