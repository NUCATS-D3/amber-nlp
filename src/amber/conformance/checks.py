"""Implementation-level conformance checks, run against a consumer's targets.

Each check raises ``ConformanceFailure`` with a stable, source-free code. Checks call only
public protocol methods and amber's load boundaries, and every input is a synthetic fixture.
A store "rejects" by raising an exception; the kit does not depend on its exception types.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from functools import partial
from typing import Any, NoReturn

from amber.annotations import example_from_projection, replay, verify_example
from amber.cases import load_case_result
from amber.conformance import fixtures as fx
from amber.conformance.catalog import CASES, Target
from amber.examples import load_example, load_label_record
from amber.graph_errors import GraphValidationError
from amber.interfaces import (
    Extractor,
    Labeler,
    Provider,
    ReviewBackend,
    Scorer,
    SourceResolver,
    Store,
)
from amber.labeling import LabelerFailure, LabelerRejected, admit_labeler, spans_of
from amber.policy import (
    DEFAULT_MATRIX,
    DataPolicy,
    Destination,
    PolicyMatrix,
    PolicyViolation,
    ProviderDestination,
    StaticGate,
)
from amber.providers import ProviderFailure, ProviderRequest, ProviderResponse
from amber.review import ReviewBatchReceipt
from amber.rules import Registry, Rule, RuleSpec, replay_derivations
from amber.schemas import (
    Budget,
    Case,
    CaseResult,
    Example,
    Inclusion,
    LabelRecord,
    ReferenceTier,
    Sensitivity,
    Source,
    VariableSet,
    Zone,
)

StoreFactory = Callable[[], Store]
ResolverFactory = Callable[[Iterable[Source]], SourceResolver]


class ConformanceFailure(AssertionError):
    """A failed case, with a stable code that renders no record contents."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> NoReturn:
    raise ConformanceFailure(code) from None


def _rejects(call: Callable[[], Any]) -> bool:
    try:
        call()
    except Exception:
        return True
    return False


def _must_reject(call: Callable[[], Any], code: str) -> None:
    if not _rejects(call):
        _fail(code)


def _must_accept(call: Callable[[], Any], code: str) -> Any:
    try:
        return call()
    except ConformanceFailure:
        raise
    except Exception:
        _fail(code)


@dataclass(frozen=True)
class BundleUnderTest:
    """What bundle conformance needs besides the bundle directory: its variable set and,
    for the source cases (WIR-04, WIR-13), a resolver."""

    variables: VariableSet
    resolver: SourceResolver | None = None


@dataclass(frozen=True)
class Subject:
    """What a consumer declared: its name, version, and a factory per implemented target."""

    name: str
    version: str
    targets: Mapping[str, Callable[..., Any]]

    def store(self) -> Store:
        factory = self.targets["Store"]
        store = factory()
        if not isinstance(store, Store):
            _fail("not_a_store")
        return store

    def resolver(self) -> SourceResolver:
        factory = self.targets.get("SourceResolver")
        if factory is None:
            from amber.reference import InMemoryResolver

            return InMemoryResolver([fx.SOURCE])
        resolver = factory([fx.SOURCE])
        if not isinstance(resolver, SourceResolver):
            _fail("not_a_resolver")
        return resolver

    def provider(self) -> Provider:
        provider = self.targets["Provider"]()
        if not isinstance(provider, Provider) or not isinstance(
            getattr(provider, "destination", None), ProviderDestination
        ):
            _fail("not_a_provider")
        return provider

    def extractor(self, provider: Provider) -> Extractor:
        extractor = self.targets["Extractor"](provider)
        if not isinstance(extractor, Extractor):
            _fail("not_an_extractor")
        return extractor

    def scorer(self) -> Scorer:
        scorer = self.targets["Scorer"]()
        if not isinstance(scorer, Scorer):
            _fail("not_a_scorer")
        return scorer

    def labeler(self) -> Labeler:
        labeler = self.targets["Labeler"]()
        if not isinstance(labeler, Labeler):
            _fail("not_a_labeler")
        return labeler

    def review_backend(self) -> tuple[ReviewBackend, Callable[[ReviewBatchReceipt], None]]:
        made = self.targets["ReviewBackend"]()
        if not isinstance(made, tuple) or len(made) != 2:
            _fail("not_a_review_backend")
        backend, review = made
        if not isinstance(backend, ReviewBackend) or not callable(review):
            _fail("not_a_review_backend")
        destination = getattr(backend, "destination", None)
        if not isinstance(destination, Destination) or destination.kind != "review":
            _fail("not_a_review_destination")
        return backend, review

    def rule(self) -> tuple[Rule, CaseResult, VariableSet]:
        made = self.targets["Rule"]()
        if not isinstance(made, tuple) or len(made) != 3:
            _fail("not_a_rule")
        rule, result, variables = made
        if not isinstance(rule, Rule) or not isinstance(getattr(rule, "spec", None), RuleSpec):
            _fail("not_a_rule")
        if not isinstance(result, CaseResult) or not isinstance(variables, VariableSet):
            _fail("not_a_rule")
        return rule, result, variables


def _store_with_case(subject: Subject, case: Case | None = None) -> Store:
    store = subject.store()
    _must_accept(
        lambda: store.put_case(case or fx.case(), variables=fx.VARIABLES), "valid_case_rejected"
    )
    return store


def _example(events: list[Any], case: Case | None = None, **kwargs: Any) -> Example:
    bound = case or fx.case()
    projection = replay(
        events,
        case=bound,
        source=fx.SOURCE,
        variables=fx.VARIABLES,
        spans=fx.SPANS,
        **kwargs,
    )
    return example_from_projection(projection, case=bound, source=fx.SOURCE, variables=fx.VARIABLES)


def _append(store: Store, events: list[Any]) -> None:
    _must_accept(lambda: store.append_events(events, spans=fx.SPANS), "valid_events_rejected")


# Store -------------------------------------------------------------------------------------


def check_ann_05(subject: Subject) -> None:
    """Split belongs to the (dataset, source) pair and never changes."""
    store = _store_with_case(subject)
    _must_reject(
        lambda: store.put_case(fx.case("conformance-case-2", split="test"), variables=fx.VARIABLES),
        "second_split_accepted",
    )
    _must_reject(
        lambda: store.put_case(fx.case(split="train"), variables=fx.VARIABLES),
        "split_change_accepted",
    )
    _must_accept(
        lambda: store.put_case(
            fx.case("conformance-case-3", dataset="another-synthetic-set", split="test"),
            variables=fx.VARIABLES,
        ),
        "valid_case_rejected",
    )

    log, _ = fx.silver_import()
    _append(store, log.events)
    resolver = subject.resolver()
    other_split = _example(log.events, case=fx.case(split="test"))
    _must_reject(
        lambda: store.put_example(other_split, resolver=resolver), "example_split_accepted"
    )
    label = load_label_record(
        {
            "schema_version": "amber/0.1",
            "case_id": "conformance-case-1",
            "variable": "summary",
            "variable_set": fx.VARIABLES.name,
            "variable_set_version": fx.VARIABLES.version,
            "variable_set_ref": fx.VARIABLES.schema_ref,
            "tier": "silver",
            "outcome_status": "answered",
            "value": {"present": True},
            "producer": fx.TEACHER,
            "split": "test",
        },
        case=fx.case(split="test"),
        variables=fx.VARIABLES,
    )
    _must_reject(lambda: store.put_label_record(label), "label_split_accepted")


_ACTORS = (None, fx.REVIEWER_A, fx.REVIEWER_B, fx.ADJUDICATOR, fx.IMPORTER, "human:outsider")


def check_ann_16(subject: Subject) -> None:
    """A store's projection equals amber's replay for every actor and every as_of_event."""
    store = _store_with_case(subject)
    log, _ = fx.adjudicated()
    _append(store, log.events)
    resolver = subject.resolver()
    for actor in _ACTORS:
        for event in log.events:
            expected = replay(
                log.events,
                case=fx.case(),
                source=fx.SOURCE,
                variables=fx.VARIABLES,
                spans=fx.SPANS,
                actor=actor,
                as_of_event=event.event_id,
            )
            actual = _must_accept(
                partial(
                    store.project,
                    "conformance-case-1",
                    actor=actor,
                    as_of_event=event.event_id,
                    resolver=resolver,
                ),
                "projection_failed",
            )
            if actual != expected:
                _fail("projection_mismatch")


def check_ann_17(subject: Subject) -> None:
    """Example identity is checked on put, and current_example follows the latest event."""
    store = _store_with_case(subject)
    log, ids = fx.silver_import()
    _append(store, log.events)
    resolver = subject.resolver()
    silver = _example(log.events)
    forged = silver.model_copy(update={"example_id": "0" * 64})
    _must_reject(lambda: store.put_example(forged, resolver=resolver), "example_id_accepted")
    _must_accept(lambda: store.put_example(silver, resolver=resolver), "valid_example_rejected")
    if store.current_example("conformance-case-1", resolver=resolver) != silver:
        _fail("current_example_mismatch")
    start = len(log.events)
    fx.raise_to_single_review(log, ids)
    _append(store, log.events[start:])
    current = store.current_example("conformance-case-1", resolver=resolver)
    if current is None or current != _example(log.events):
        _fail("current_example_stale")
    if store.get_example(silver.example_id, resolver=resolver) != silver:
        _fail("snapshot_changed")


def check_ifc_07(subject: Subject) -> None:
    """Append-only events, stream isolation, Example admission, and projection behaviour."""
    store = _store_with_case(subject)
    log, ids = fx.blind_streams()
    _append(store, log.events[:-1])
    resolver = subject.resolver()
    altered = log.events[-2].model_copy(update={"external_ref": "altered"})
    _must_reject(lambda: store.append_events([altered]), "event_update_accepted")
    _append(store, log.events[-1:])

    stream_view = _must_accept(
        lambda: store.project("conformance-case-1", actor=fx.REVIEWER_A, resolver=resolver),
        "projection_failed",
    )
    if set(stream_view.claims) != set(ids["a"].values()):
        _fail("stream_leak")
    in_progress = _must_accept(
        lambda: store.project("conformance-case-1", actor=None, resolver=resolver),
        "projection_failed",
    )
    if not _rejects(
        lambda: example_from_projection(
            in_progress, case=fx.case(), source=fx.SOURCE, variables=fx.VARIABLES
        )
    ):
        _fail("unfinished_projection_is_example")

    start = len(log.events)
    fx.adjudicate(log, ids)
    finished = _example(log.events)
    _must_reject(
        lambda: store.put_example(finished, resolver=resolver), "example_before_events_accepted"
    )
    _append(store, log.events[start:])
    _must_accept(lambda: store.put_example(finished, resolver=resolver), "valid_example_rejected")
    projected = store.project("conformance-case-1", actor=None, resolver=resolver)
    stored = store.get_example(finished.example_id, resolver=resolver)
    rebuilt = example_from_projection(
        projected, case=fx.case(), source=fx.SOURCE, variables=fx.VARIABLES
    )
    if stored != finished or rebuilt != stored:
        _fail("stored_example_mismatch")

    silver_store = _store_with_case(subject, fx.case("conformance-case-4"))
    silver_log, _ = fx.silver_import("conformance-case-4")
    _append(silver_store, silver_log.events)
    asserted = _asserted_single_review(silver_log.events)
    _must_reject(
        lambda: silver_store.put_example(asserted, resolver=resolver), "unsupported_tier_accepted"
    )


def _asserted_single_review(events: list[Any]) -> Example:
    bound = fx.case("conformance-case-4")
    silver = _example(events, case=bound)
    payload = silver.model_dump(mode="json")
    for reference in payload["references"].values():
        reference.update(tier="single_review", annotators=[fx.REVIEWER_A])
    for claim in payload["claims"]:
        claim["status"] = "gold"
    for reading in payload["mention_readings"]:
        reading["status"] = "verified"
    return load_example(payload, case=bound, source=fx.SOURCE, variables=fx.VARIABLES)


def check_ifc_11(subject: Subject) -> None:
    """Runs of one case coexist; a second result with one (case_id, run_id) is rejected."""
    store = _store_with_case(subject)
    resolver = subject.resolver()
    results = [fx.result(run_id) for run_id in fx.RUN_IDS]
    for item in results:
        _must_accept(partial(store.put_result, item), "valid_result_rejected")
    if set(store.runs("conformance-case-1")) != set(fx.RUN_IDS):
        _fail("runs_incomplete")
    for item in results:
        loaded = _must_accept(
            partial(store.get_result, "conformance-case-1", item.run_id, resolver=resolver),
            "result_unavailable",
        )
        if loaded != item:
            _fail("result_mismatch")
    _must_reject(lambda: store.put_result(results[0]), "duplicate_result_accepted")


def check_ifc_12_store(subject: Subject) -> None:
    """Every declared capability works; an undeclared one is refused, never partly served."""
    store = _store_with_case(subject)
    capabilities = frozenset(getattr(store, "capabilities", frozenset()))
    if not capabilities <= {"events", "examples", "query", "transactions"}:
        _fail("unknown_capability")
    resolver = subject.resolver()
    log, _ = fx.silver_import()
    if "events" in capabilities:
        _append(store, log.events)
        if tuple(store.events("conformance-case-1")) != tuple(log.events):
            _fail("declared_capability_failed")
    else:
        _must_reject(lambda: store.append_events(log.events), "undeclared_capability_served")
    example = _example(log.events)
    if "examples" in capabilities and "events" in capabilities:
        _must_accept(
            lambda: store.put_example(example, resolver=resolver), "declared_capability_failed"
        )
        if store.get_example(example.example_id, resolver=resolver) != example:
            _fail("declared_capability_failed")
    elif "examples" not in capabilities:
        _must_reject(
            lambda: store.put_example(example, resolver=resolver), "undeclared_capability_served"
        )


# Provider ----------------------------------------------------------------------------------

_PROMPT = "Invented note: is the invented finding present? Answer briefly."
_SCHEMA = fx.FindingAnswer.model_json_schema()
_CAPABILITY_REQUESTS: dict[str, ProviderRequest] = {
    "schema_constrained": ProviderRequest(prompt=_PROMPT, json_schema=_SCHEMA),
    "tools": ProviderRequest(
        prompt=_PROMPT,
        tools=({"name": "lookup", "description": "Invented tool.", "parameters": {}},),
    ),
    "logprobs": ProviderRequest(prompt=_PROMPT, logprobs=True),
    "adapters": ProviderRequest(prompt=_PROMPT, adapter="conformance-adapter"),
}


def _complete(subject: Subject, request: ProviderRequest) -> Any:
    provider = subject.provider()
    return _must_accept(lambda: provider.complete(request), "provider_raised")


def check_ifc_04(subject: Subject) -> None:
    """Without schema_constrained, a schema request is refused, never answered with text."""
    if "schema_constrained" in subject.provider().destination.capabilities:
        return
    outcome = _complete(subject, _CAPABILITY_REQUESTS["schema_constrained"])
    if not isinstance(outcome, ProviderFailure):
        _fail("schema_request_served")


def check_ifc_12_provider(subject: Subject) -> None:
    """A plain request and every declared capability work; undeclared ones are refused."""
    if not isinstance(_complete(subject, ProviderRequest(prompt=_PROMPT)), ProviderResponse):
        _fail("plain_request_failed")
    declared = subject.provider().destination.capabilities
    for capability, request in _CAPABILITY_REQUESTS.items():
        outcome = _complete(subject, request)
        if capability not in declared:
            if not isinstance(outcome, ProviderFailure):
                _fail("undeclared_capability_served")
        elif not isinstance(outcome, ProviderResponse) or (
            capability == "schema_constrained" and not isinstance(outcome.parsed, Mapping)
        ):
            _fail("declared_capability_failed")


# Extractor ---------------------------------------------------------------------------------

_ALL_CAPABILITIES = frozenset({"schema_constrained", "tools", "logprobs", "batch", "adapters"})
_MODEL = ProviderDestination(
    name="local:conformance-model",
    kind="model",
    zone=Zone.local,
    accepts=frozenset({"source_text"}),
    model="conformance-model",
    capabilities=_ALL_CAPABILITIES,  # type: ignore[arg-type]
)
_REFUSING = StaticGate(
    PolicyMatrix(
        version=DEFAULT_MATRIX.version, allowed={level: frozenset() for level in Sensitivity}
    )
)


class _RecordingGate:
    def __init__(self, gate: StaticGate) -> None:
        self._gate = gate
        self.matrix = gate.matrix
        self.destinations: list[Destination] = []

    def check(self, data: Any, destination: Destination, payload: Any, **kwargs: Any) -> Any:
        self.destinations.append(destination)
        return self._gate.check(data, destination, payload, **kwargs)

    def require(self, data: Any, destination: Destination, payload: Any, **kwargs: Any) -> Any:
        self.destinations.append(destination)
        return self._gate.require(data, destination, payload, **kwargs)


def _scripted(outcome: Any) -> Any:
    from amber.reference import ScriptedProvider

    return ScriptedProvider(_MODEL, [outcome] * 50)


_FAILURES: dict[str, tuple[Any, StaticGate, Budget | None]] = {
    "provider": (
        ProviderFailure(kind="provider", reason="unavailable"),
        StaticGate(DEFAULT_MATRIX),
        None,
    ),
    "parse": (ProviderResponse(text="\u0001 not an answer"), StaticGate(DEFAULT_MATRIX), None),
    "policy": (ProviderResponse(text="unused"), _REFUSING, None),
    "budget": (ProviderResponse(text="unused"), StaticGate(DEFAULT_MATRIX), Budget(max_calls=0)),
}


def _run_extractor(
    subject: Subject, outcome: Any, gate: StaticGate, budget: Budget | None
) -> tuple[Extractor, CaseResult, _RecordingGate]:
    extractor = subject.extractor(_scripted(outcome))
    recording = _RecordingGate(gate)
    result = _must_accept(
        lambda: extractor.run(
            fx.note_case(),
            fx.SOURCE,
            fx.NOTE_VARIABLES,
            gate=recording,
            budget=budget,
            context=fx.run_context(),
        ),
        "extractor_raised",
    )
    if not isinstance(result, CaseResult):
        _fail("not_a_case_result")
    return extractor, result, recording


def check_ifc_02(subject: Subject) -> None:
    """Expected failures are failed results of the right kind, built from the run context."""
    context = fx.run_context()
    for kind, (outcome, gate, budget) in _FAILURES.items():
        extractor, result, recording = _run_extractor(subject, outcome, gate, budget)
        statuses = {
            (item.outcome.status, item.outcome.failure_kind)
            for item in result.variable_outcomes.values()
        }
        if statuses != {("failed", kind)}:
            _fail(f"{kind}_failure_not_reported")
        if result.run_id != context.run_id or any(
            item.outcome.provenance.run_id != context.run_id
            or item.outcome.provenance.policy_version != context.policy_version
            for item in result.variable_outcomes.values()
        ):
            _fail("provenance_not_from_context")
        if result.budget != budget:
            _fail("budget_not_recorded")
        if any(destination not in extractor.destinations for destination in recording.destinations):
            _fail("undeclared_destination")
        _must_accept(
            partial(
                load_case_result,
                result.model_dump(mode="json"),
                case=fx.note_case(),
                source=fx.SOURCE,
                variables=fx.NOTE_VARIABLES,
            ),
            "result_invalid",
        )


def check_ifc_13(subject: Subject) -> None:
    """With batch, run_many returns one result per case in input order, as run does."""
    probe = subject.extractor(_scripted(_FAILURES["provider"][0]))
    if "batch" not in probe.capabilities:
        return
    cases = [fx.note_case(f"conformance-note-case-{index}") for index in range(1, 4)]
    gate = StaticGate(DEFAULT_MATRIX)

    def summary(result: CaseResult) -> tuple[Any, ...]:
        return (
            result.case_id,
            tuple(
                (name, item.outcome.status, item.outcome.failure_kind)
                for name, item in sorted(result.variable_outcomes.items())
            ),
        )

    run_many = getattr(probe, "run_many", None)
    if run_many is None:
        _fail("batch_without_run_many")
    batch = _must_accept(
        lambda: list(
            run_many(
                [(case, fx.SOURCE) for case in cases],
                fx.NOTE_VARIABLES,
                gate=gate,
                budget=None,
                context=fx.run_context(),
            )
        ),
        "extractor_raised",
    )
    if [result.case_id for result in batch] != [case.case_id for case in cases]:
        _fail("batch_order")
    for case, result in zip(cases, batch, strict=True):
        single = subject.extractor(_scripted(_FAILURES["provider"][0])).run(
            case, fx.SOURCE, fx.NOTE_VARIABLES, gate=gate, budget=None, context=fx.run_context()
        )
        if summary(single) != summary(result):
            _fail("batch_differs_from_run")


# Scorer ------------------------------------------------------------------------------------

_CANDIDATE = "model:conformance-candidate@1"


def _label_record(producer: str) -> LabelRecord:
    return LabelRecord(
        schema_version="amber/0.1",
        case_id="conformance-note-case-1",
        variable=fx.NOTE_VARIABLES.name,
        variable_set=fx.NOTE_VARIABLES.name,
        variable_set_version=fx.NOTE_VARIABLES.version,
        variable_set_ref=fx.NOTE_VARIABLES.schema_ref,
        tier="silver",
        outcome_status="answered",
        value={"present": True},
        producer=producer,
        split="dev",
    )


def _score(subject: Subject, predicted: list[CaseResult], reference: list[Any]) -> Any:
    scorer = subject.scorer()
    return scorer.score(predicted, reference, variables=fx.NOTE_VARIABLES)


def _predicted(producer: str = _CANDIDATE) -> list[CaseResult]:
    return [fx.note_result(fx.RUN_IDS[0], present=True, producer=producer)]


def check_ifc_09(subject: Subject) -> None:
    """Provisional exactly below adjudicated; evidence inconclusive on labels; self_reference."""
    adjudicated = fx.note_example("adjudicated", present=True, producer=fx.TEACHER)
    silver = fx.note_example("silver", present=True, producer=fx.TEACHER)
    report = _must_accept(lambda: _score(subject, _predicted(), [adjudicated]), "scorer_raised")
    if report.provisional:
        _fail("provisional_wrong")
    report = _must_accept(lambda: _score(subject, _predicted(), [silver]), "scorer_raised")
    if not report.provisional:
        _fail("provisional_wrong")
    check_ann_11_scorer(subject)
    check_ann_12_scorer(subject)
    hashed = _predicted("rule:conformance-rules@1+aaaa")
    if not _rejects(
        lambda: _score(subject, hashed, [_label_record("rule:conformance-rules@1+bbbb")])
    ):
        _fail("self_reference_not_refused")
    shared_tool = _predicted("tool:conformance-tool@1")
    _must_accept(
        lambda: _score(subject, shared_tool, [_label_record("tool:conformance-tool@1")]),
        "self_reference_false_positive",
    )


def check_ann_11_scorer(subject: Subject) -> None:
    """Evidence metrics against label records are inconclusive, never reported."""
    report = _must_accept(
        lambda: _score(subject, _predicted(), [_label_record(fx.TEACHER)]), "scorer_raised"
    )
    if any(metric.kind == "evidence" for metric in report.metrics):
        _fail("evidence_metric_on_labels")


def check_ann_12_scorer(subject: Subject) -> None:
    """Scoring against silver references from the same configuration is refused."""
    silver = fx.note_example("silver", present=True, producer=_CANDIDATE)
    if not _rejects(lambda: _score(subject, _predicted(), [silver])):
        _fail("self_reference_not_refused")
    if not _rejects(lambda: _score(subject, _predicted(), [_label_record(_CANDIDATE)])):
        _fail("self_reference_not_refused")


def check_ann_17_scorer(subject: Subject) -> None:
    """A report names the example_ids it scored against."""
    examples = [
        fx.note_example("adjudicated", present=True, producer=fx.TEACHER),
        fx.note_example(
            "adjudicated", present=False, producer=fx.TEACHER, case_id="conformance-note-case-2"
        ),
    ]
    report = _must_accept(lambda: _score(subject, _predicted(), examples), "scorer_raised")
    if set(report.example_ids) != {example.example_id for example in examples}:
        _fail("example_ids_missing")


# Labeler -----------------------------------------------------------------------------------


def check_ifc_06(subject: Subject) -> None:
    """Labelers are silver; Examples come with events that begin at import.result and replay
    to them; label records validate."""
    labeler = subject.labeler()
    try:
        admit_labeler(labeler)
    except LabelerRejected:
        _fail("human_tier_declared")
    context = fx.run_context()
    case = fx.note_case()
    output = _must_accept(
        lambda: labeler.label(
            case, fx.SOURCE, fx.NOTE_VARIABLES, gate=StaticGate(DEFAULT_MATRIX), context=context
        ),
        "labeler_raised",
    )
    if isinstance(output, LabelerFailure):
        _fail("no_reference")
    if isinstance(output, LabelRecord):
        _must_accept(
            partial(
                load_label_record,
                output.model_dump(mode="json"),
                case=case,
                variables=fx.NOTE_VARIABLES,
            ),
            "label_invalid",
        )
        return
    events, example = output
    if not isinstance(example, Example) or not events:
        _fail("not_a_reference")
    if events[0].action != "import.result":
        _fail("missing_import_event")
    if any(event.case_id != case.case_id for event in events):
        _fail("events_for_another_case")
    if any(event.provenance.run_id != context.run_id for event in events):
        _fail("provenance_not_from_context")
    if any(reference.tier is not ReferenceTier.silver for reference in example.references.values()):
        _fail("tier_mismatch")
    _must_accept(
        partial(
            verify_example,
            example,
            events,
            case=case,
            source=fx.SOURCE,
            variables=fx.NOTE_VARIABLES,
            spans=spans_of(example),
        ),
        "events_do_not_replay",
    )


# ReviewBackend -----------------------------------------------------------------------------


class _PayloadGate:
    """Records each (payload, case_id) asked for; refuses everything when ``refuse`` is set."""

    def __init__(self, *, refuse: bool) -> None:
        self._gate = StaticGate(DEFAULT_MATRIX)
        self.matrix = self._gate.matrix
        self.refuse = refuse
        self.asked: list[tuple[str, str | None]] = []

    def _target(self, destination: Destination) -> Destination:
        return (
            destination.model_copy(update={"accepts": frozenset()}) if self.refuse else destination
        )

    def check(self, data: Any, destination: Destination, payload: Any, **kwargs: Any) -> Any:
        self.asked.append((payload, kwargs.get("case_id")))
        return self._gate.check(data, self._target(destination), payload, **kwargs)

    def require(self, data: Any, destination: Destination, payload: Any, **kwargs: Any) -> Any:
        self.asked.append((payload, kwargs.get("case_id")))
        return self._gate.require(data, self._target(destination), payload, **kwargs)


def check_ifc_10(subject: Subject) -> None:
    """Round trip: exported cases, simulated reviews, imported Examples whose events replay."""
    proposal = fx.note_result(fx.RUN_IDS[0], present=True, producer=fx.TEACHER)
    blank = fx.note_case("conformance-note-case-2")
    batch = [(fx.note_case(), fx.SOURCE, proposal), (blank, fx.SOURCE, None)]
    data = DataPolicy(sensitivity=Sensitivity.synthetic)

    def export(backend: ReviewBackend, gate: _PayloadGate) -> Any:
        return backend.export_cases(
            batch, variables=fx.NOTE_VARIABLES, streams=("a", "b"), gate=gate, data=data
        )

    backend, review = subject.review_backend()
    refusing = _PayloadGate(refuse=True)
    try:
        export(backend, refusing)
    except PolicyViolation:
        pass
    except Exception:
        _fail("refusal_not_policy_violation")
    else:
        _fail("export_not_gated")

    backend, review = subject.review_backend()
    gate = _PayloadGate(refuse=False)
    receipt = _must_accept(lambda: export(backend, gate), "export_rejected")
    asked = set(gate.asked)
    if not {("source_text", case.case_id) for case, _, _ in batch} <= asked:
        _fail("source_not_gated")
    if ("derived_text", fx.note_case().case_id) not in asked:
        _fail("proposal_not_gated")
    if not isinstance(receipt, ReviewBatchReceipt) or receipt.case_ids != tuple(
        case.case_id for case, _, _ in batch
    ):
        _fail("receipt_mismatch")
    if not receipt.decisions or not all(decision.permitted for decision in receipt.decisions):
        _fail("receipt_mismatch")

    _must_accept(lambda: review(receipt), "review_failed")
    examples, events = _must_accept(
        lambda: backend.import_references(receipt, resolver=subject.resolver()), "import_rejected"
    )
    by_case = {example.case_id: example for example in examples}
    if set(by_case) != set(receipt.case_ids) or len(by_case) != len(examples):
        _fail("references_missing")
    proposal_spans = tuple(item for item in proposal.evidence if isinstance(item, Inclusion))
    for case, _, _ in batch:
        example = by_case[case.case_id]
        if any(reference.tier is ReferenceTier.silver for reference in example.references.values()):
            _fail("tier_not_human")
        mine = [event for event in events if event.case_id == case.case_id]
        _must_accept(
            partial(
                verify_example,
                example,
                mine,
                case=case,
                source=fx.SOURCE,
                variables=fx.NOTE_VARIABLES,
                spans=(*spans_of(example), *proposal_spans),
            ),
            "events_do_not_replay",
        )


# Rule --------------------------------------------------------------------------------------


def check_var_09(subject: Subject) -> None:
    """Replay recomputes the rule's derivations; a tampered derived value fails
    ``derivation_mismatch``; an unregistered rule is not replayed."""
    rule, result, variables = subject.rule()
    try:
        rule.spec.check(variables)
    except GraphValidationError:
        _fail("spec_does_not_fit_variables")
    registry = Registry([rule])
    replayed = _must_accept(
        lambda: replay_derivations(result, registry=registry, variables=variables),
        "replay_failed",
    )
    if not replayed:
        _fail("no_derivation_replayed")
    if replay_derivations(result, registry=Registry(), variables=variables):
        _fail("unregistered_rule_replayed")
    derived = {edge.claim_id for edge in result.edges if edge.evidence_id in set(replayed)}
    tampered = result.model_copy(
        update={
            "claims": tuple(
                claim.model_copy(update={"value": {"amber_conformance_tampered": True}})
                if claim.claim_id in derived
                else claim
                for claim in result.claims
            )
        }
    )
    try:
        replay_derivations(tampered, registry=registry, variables=variables)
    except GraphValidationError as error:
        if error.code != "derivation_mismatch":
            _fail("tamper_wrong_code")
    else:
        _fail("tamper_not_detected")


@dataclass(frozen=True)
class Check:
    """One check of one case for one target. ``also_covers`` names other targets whose part of
    the case this check exercises too (IFC-02 is about extractors, through their providers)."""

    case_id: str
    target: Target
    run: Callable[[Subject], None]
    also_covers: tuple[Target, ...] = ()


CHECKS: tuple[Check, ...] = (
    Check("ANN-05", "Store", check_ann_05),
    Check("ANN-16", "Store", check_ann_16),
    Check("ANN-17", "Store", check_ann_17),
    Check("IFC-07", "Store", check_ifc_07),
    Check("IFC-11", "Store", check_ifc_11),
    Check("IFC-12", "Store", check_ifc_12_store),
    Check("IFC-04", "Provider", check_ifc_04),
    Check("IFC-12", "Provider", check_ifc_12_provider),
    Check("IFC-02", "Extractor", check_ifc_02, also_covers=("Provider",)),
    Check("IFC-13", "Extractor", check_ifc_13),
    Check("IFC-09", "Scorer", check_ifc_09),
    Check("ANN-11", "Scorer", check_ann_11_scorer),
    Check("ANN-12", "Scorer", check_ann_12_scorer),
    Check("ANN-17", "Scorer", check_ann_17_scorer),
    Check("IFC-06", "Labeler", check_ifc_06),
    Check("IFC-10", "ReviewBackend", check_ifc_10),
    Check("VAR-09", "Rule", check_var_09),
)
assert all(
    check.case_id in CASES and check.target in CASES[check.case_id].targets for check in CHECKS
)
