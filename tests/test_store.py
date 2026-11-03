"""The Store protocol and its in-memory reference (contract v0.1 sections 5.6 and 6.2;
catalog ANN-05, ANN-16, ANN-17, IFC-07, IFC-11, VAR-17)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from amber.annotations import AnnotationValidationError, example_from_projection, replay
from amber.cases import create_case_result
from amber.examples import load_example, load_label_record
from amber.graph import EvidenceGraph
from amber.ids import new_ulid
from amber.interfaces import SourceResolver, Store
from amber.reference import InMemoryResolver, InMemoryStore, StoreError
from amber.schemas import (
    Case,
    CaseOutcome,
    CaseResult,
    Example,
    Provenance,
    Source,
    VariableOutcome,
    VariableSet,
)

from ._annotation_helpers import (
    ADJ,
    CASE,
    IMPORTER,
    SOURCE,
    SPANS,
    VARIABLES,
    A,
    B,
    Log,
    _adjudicate,
    _adjudicated,
    _import,
    _raise_to_single_review,
    _silver,
    _stream,
)
from ._claim_helpers import make_provenance

RESOLVER = InMemoryResolver([SOURCE])


def _assert_code(code: str, function: Callable[..., Any], /, *args: Any, **kwargs: Any) -> None:
    with pytest.raises(StoreError) as failure:
        function(*args, **kwargs)
    assert failure.value.code == code
    assert str(failure.value) == code


def _store(case: Case = CASE) -> InMemoryStore:
    store = InMemoryStore()
    store.put_case(case, variables=VARIABLES)
    return store


def _example(log: Log, **kwargs: Any) -> Example:
    projection = replay(
        log.events, case=CASE, source=SOURCE, variables=VARIABLES, spans=SPANS, **kwargs
    )
    return example_from_projection(projection, case=CASE, source=SOURCE, variables=VARIABLES)


def _result(run_id: str) -> CaseResult:
    provenance = Provenance.model_validate({**make_provenance().model_dump(), "run_id": run_id})
    graph = EvidenceGraph(source=SOURCE, variables=VARIABLES, sensitivity=CASE.sensitivity)
    outcome = CaseOutcome(
        status="insufficient_evidence", reason="Synthetic.", provenance=provenance
    )
    return create_case_result(
        CASE,
        graph=graph,
        run_id=run_id,
        variable_outcomes={
            name: VariableOutcome(variable=name, outcome=outcome) for name in ("finding", "summary")
        },
    )


# Protocols ---------------------------------------------------------------------------------


def test_references_satisfy_their_protocols() -> None:
    assert isinstance(InMemoryStore(), Store)
    assert isinstance(RESOLVER, SourceResolver)
    assert InMemoryStore.capabilities == frozenset({"events", "examples"})
    assert RESOLVER.resolve(SOURCE.source_id) == SOURCE
    assert RESOLVER.resolve("unknown") is None


# Cases, splits, and variable sets (ANN-05, VAR-17) -----------------------------------------


def test_a_dataset_source_pair_has_one_split() -> None:
    store = _store(CASE.model_copy(update={"dataset": "synthetic-set"}))
    other = CASE.model_copy(update={"case_id": "synthetic-case-2", "dataset": "synthetic-set"})
    _assert_code(
        "split_conflict",
        store.put_case,
        other.model_copy(update={"split": "test"}),
        variables=VARIABLES,
    )
    store.put_case(other, variables=VARIABLES)
    store.put_case(
        other.model_copy(
            update={"case_id": "synthetic-case-3", "dataset": "other-set", "split": "test"}
        ),
        variables=VARIABLES,
    )


def test_a_case_never_changes() -> None:
    store = _store()
    store.put_case(CASE, variables=VARIABLES)
    _assert_code(
        "split_conflict",
        store.put_case,
        CASE.model_copy(update={"split": "train"}),
        variables=VARIABLES,
    )
    _assert_code(
        "case_conflict",
        store.put_case,
        CASE.model_copy(update={"patient_id": "synthetic-patient-2"}),
        variables=VARIABLES,
    )
    _assert_code("unknown_case", store.get_case, "missing")
    assert store.get_case(CASE.case_id) == CASE


def test_one_name_and_version_has_one_schema_ref() -> None:
    store = _store()
    edited = VariableSet.create(
        name=VARIABLES.name,
        version=VARIABLES.version,
        variables=[
            VARIABLES.variables["finding"],
            VARIABLES.variables["summary"].model_copy(update={"instructions": "Edited."}),
        ],
    )
    other = CASE.model_copy(update={"case_id": "synthetic-case-2"})
    _assert_code("variable_set_version_conflict", store.put_case, other, variables=edited)
    _assert_code(
        "variable_set_mismatch",
        store.put_case,
        other,
        variables=VariableSet.create(
            name="other", version="1", variables=VARIABLES.variables.values()
        ),
    )


# Results (IFC-11) --------------------------------------------------------------------------


def test_runs_of_one_case_coexist_and_duplicates_are_rejected() -> None:
    store = _store()
    first, second = new_ulid(), new_ulid()
    store.put_result(_result(first))
    store.put_result(_result(second))

    assert store.runs(CASE.case_id) == (first, second)
    assert store.get_result(CASE.case_id, second, resolver=RESOLVER) == _result(second)
    _assert_code("duplicate_result", store.put_result, _result(first))
    _assert_code("unknown_result", store.get_result, CASE.case_id, new_ulid(), resolver=RESOLVER)


def test_results_need_a_registered_case_and_its_set() -> None:
    _assert_code("unknown_case", InMemoryStore().put_result, _result(new_ulid()))
    store = InMemoryStore()
    other_set = VariableSet.create(
        name=VARIABLES.name,
        version="2",
        variables=[
            VARIABLES.variables["finding"],
            VARIABLES.variables["summary"].model_copy(update={"instructions": "Other."}),
        ],
    )
    store.put_case(CASE, variables=other_set)
    _assert_code("variable_set_mismatch", store.put_result, _result(new_ulid()))


class _ForgingResolver:
    def resolve(self, source_id: str) -> Source | None:
        return Source.model_validate(
            {**SOURCE.model_dump(), "external_id": "other", "source_id": _OTHER.source_id}
        )


_OTHER = Source.create(
    patient_id=SOURCE.patient_id,
    kind=SOURCE.kind,
    external_id="other",
    datetime=None,
    text=SOURCE.text,
    record=None,
)


def test_reads_revalidate_against_the_resolver() -> None:
    store = _store()
    run = new_ulid()
    store.put_result(_result(run))
    _assert_code(
        "source_unavailable", store.get_result, CASE.case_id, run, resolver=InMemoryResolver()
    )
    _assert_code(
        "source_hash_mismatch", store.get_result, CASE.case_id, run, resolver=_ForgingResolver()
    )


# Events and projections (ANN-16, IFC-07) ---------------------------------------------------


def test_events_are_append_only_and_ordered() -> None:
    log, _ = _silver()
    events = log.events
    store = _store()
    store.append_events([events[0], events[2]], spans=SPANS)
    _assert_code("duplicate_event", store.append_events, [events[2], events[3]])
    _assert_code("event_order", store.append_events, [events[1]])
    stranger = events[3].model_copy(update={"case_id": "missing"})
    _assert_code("unknown_case", store.append_events, [events[3], stranger])
    assert store.events(CASE.case_id) == (events[0], events[2])
    store.append_events(events[3:])
    assert store.events(CASE.case_id) == (events[0], *events[2:])


def test_events_filter_by_stream() -> None:
    log = Log()
    ids: dict[str, Any] = _import(log)
    _stream(log, A, "a", ids["reading"])
    _stream(log, B, "b", ids["reading"])
    store = _store()
    store.append_events(log.events, spans=SPANS)
    assert {event.stream for event in store.events(CASE.case_id, stream="a")} == {"a"}
    assert len(store.events(CASE.case_id)) == len(log.events)


def test_store_projections_equal_amber_replay_for_every_actor_and_prefix() -> None:
    log, _ = _adjudicated()
    store = _store()
    store.append_events(log.events, spans=SPANS)
    for actor in (None, A, B, ADJ, IMPORTER, "human:someone-else"):
        for event in log.events:
            assert store.project(
                CASE.case_id, actor=actor, as_of_event=event.event_id, resolver=RESOLVER
            ) == replay(
                log.events,
                case=CASE,
                source=SOURCE,
                variables=VARIABLES,
                spans=SPANS,
                actor=actor,
                as_of_event=event.event_id,
            )


def test_a_stream_actor_projection_excludes_other_streams() -> None:
    log = Log()
    ids: dict[str, Any] = _import(log)
    mine = _stream(log, A, "a", ids["reading"])
    _stream(log, B, "b", ids["reading"])
    store = _store()
    store.append_events(log.events, spans=SPANS)
    assert set(store.project(CASE.case_id, actor=A, resolver=RESOLVER).claims) == set(mine.values())


def test_unsubmitted_work_projects_but_is_not_an_example() -> None:
    log = Log()
    ids: dict[str, Any] = _import(log)
    _stream(log, A, "a", ids["reading"])
    store = _store()
    store.append_events(log.events, spans=SPANS)
    projection = store.project(CASE.case_id, actor=None, resolver=RESOLVER)
    with pytest.raises(AnnotationValidationError, match="incomplete_projection"):
        example_from_projection(projection, case=CASE, source=SOURCE, variables=VARIABLES)
    assert store.current_example(CASE.case_id, resolver=RESOLVER) is None


# Examples (ANN-05, ANN-17, IFC-07) ---------------------------------------------------------


def test_a_finished_case_stores_the_example_its_projection_gives() -> None:
    log, _ = _adjudicated()
    store = _store()
    store.append_events(log.events, spans=SPANS)
    example = _example(log)
    store.put_example(example, resolver=RESOLVER)

    stored = store.get_example(example.example_id, resolver=RESOLVER)
    projected = store.project(CASE.case_id, actor=None, resolver=RESOLVER)
    assert stored == example
    assert (
        example_from_projection(projected, case=CASE, source=SOURCE, variables=VARIABLES) == stored
    )
    assert store.current_example(CASE.case_id, resolver=RESOLVER) == stored
    _assert_code("unknown_example", store.get_example, "0" * 64, resolver=RESOLVER)


def test_examples_need_their_events_first() -> None:
    log, _ = _silver()
    store = _store()
    store.append_events(log.events[:-1], spans=SPANS)
    _assert_code("events_required", store.put_example, _example(log), resolver=RESOLVER)


def test_examples_asserting_an_unsupported_tier_are_rejected() -> None:
    log, _ = _silver()
    store = _store()
    store.append_events(log.events, spans=SPANS)
    payload = _example(log).model_dump(mode="json")
    for reference in payload["references"].values():
        reference.update(tier="single_review", annotators=[A])
    for claim in payload["claims"]:
        claim["status"] = "gold"
    for reading in payload["mention_readings"]:
        reading["status"] = "verified"
    asserted = load_example(payload, case=CASE, source=SOURCE, variables=VARIABLES)
    _assert_code("unsupported_tier", store.put_example, asserted, resolver=RESOLVER)


def test_examples_match_their_case_split_and_identity() -> None:
    log, _ = _silver()
    example = _example(log)
    store = _store(CASE.model_copy(update={"split": "test"}))
    store.append_events(log.events, spans=SPANS)
    _assert_code("split_mismatch", store.put_example, example, resolver=RESOLVER)

    store = _store()
    store.append_events(log.events, spans=SPANS)
    forged = example.model_copy(update={"example_id": "0" * 64})
    _assert_code("example_id_mismatch", store.put_example, forged, resolver=RESOLVER)


def test_current_example_is_the_snapshot_at_the_latest_event() -> None:
    log, ids = _silver()
    store = _store()
    store.append_events(log.events, spans=SPANS)
    silver = _example(log)
    store.put_example(silver, resolver=RESOLVER)
    assert store.current_example(CASE.case_id, resolver=RESOLVER) == silver

    start = len(log.events)
    _raise_to_single_review(log, ids)
    store.append_events(log.events[start:])
    current = store.current_example(CASE.case_id, resolver=RESOLVER)
    assert current is not None and current.as_of_event == log.events[-1].event_id
    assert current.example_id != silver.example_id
    assert store.get_example(silver.example_id, resolver=RESOLVER) == silver


def test_current_example_is_none_without_events() -> None:
    assert _store().current_example(CASE.case_id, resolver=RESOLVER) is None


# Label records (ANN-05) --------------------------------------------------------------------


def _label(split: str | None) -> Any:
    payload = {
        "schema_version": "amber/0.1",
        "case_id": CASE.case_id,
        "variable": "summary",
        "variable_set": VARIABLES.name,
        "variable_set_version": VARIABLES.version,
        "variable_set_ref": VARIABLES.schema_ref,
        "tier": "silver",
        "outcome_status": "answered",
        "value": {"present": True},
        "producer": "rule:synthetic@1",
        "split": split,
    }
    return load_label_record(
        payload, case=CASE.model_copy(update={"split": split}), variables=VARIABLES
    )


def test_label_records_match_their_case_split() -> None:
    store = _store()
    store.put_label_record(_label("dev"))
    assert len(store.label_records(CASE.case_id)) == 1
    _assert_code("split_mismatch", store.put_label_record, _label("test"))


def test_adjudication_flow_round_trips_through_the_store() -> None:
    log = Log()
    ids: dict[str, Any] = _import(log)
    ids["a"] = _stream(log, A, "a", ids["reading"])
    ids["b"] = _stream(log, B, "b", ids["reading"])
    store = _store()
    store.append_events(log.events, spans=SPANS)
    start = len(log.events)
    _adjudicate(log, ids)
    store.append_events(log.events[start:])
    example = store.current_example(CASE.case_id, resolver=RESOLVER)
    assert example is not None
    store.put_example(example, resolver=RESOLVER)
    assert {ref.tier for ref in example.references.values()} == {"adjudicated"}
