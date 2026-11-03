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

from amber.annotations import example_from_projection, replay
from amber.conformance import fixtures as fx
from amber.conformance.catalog import CASES, Target
from amber.examples import load_example, load_label_record
from amber.interfaces import SourceResolver, Store
from amber.schemas import Case, Example, Source

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


@dataclass(frozen=True)
class Check:
    case_id: str
    target: Target
    run: Callable[[Subject], None]


CHECKS: tuple[Check, ...] = (
    Check("ANN-05", "Store", check_ann_05),
    Check("ANN-16", "Store", check_ann_16),
    Check("ANN-17", "Store", check_ann_17),
    Check("IFC-07", "Store", check_ifc_07),
    Check("IFC-11", "Store", check_ifc_11),
    Check("IFC-12", "Store", check_ifc_12_store),
)
assert all(
    check.case_id in CASES and check.target in CASES[check.case_id].targets for check in CHECKS
)
