"""Labelers: silver-only admission, import events, and the ImportLabeler reference (contract
v0.1 section 6.2; catalog IFC-06)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from amber.annotations import verify_example
from amber.conformance import fixtures as fx
from amber.interfaces import Labeler
from amber.labeling import LabelerFailure, LabelerRejected, admit_labeler, import_events, spans_of
from amber.policy import DEFAULT_MATRIX, StaticGate
from amber.reference import ImportLabeler, InMemoryStore, StoreError
from amber.reference.store import InMemoryResolver
from amber.schemas import Example, LabelRecord, ReferenceTier

from .test_labels import _result as _mention_result

CLOCK = datetime(2026, 1, 3, tzinfo=UTC)
GATE = StaticGate(DEFAULT_MATRIX)
TEACHER = "model:teacher@1"


def _labeler(tmp_path: Path, entries: dict[str, Any]) -> ImportLabeler:
    path = tmp_path / "predictions.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    return ImportLabeler(path, clock=lambda: CLOCK)


def _note_result(**kwargs: Any) -> Any:
    return fx.note_result(fx.RUN_IDS[0], present=True, producer=TEACHER, **kwargs)


def _label(labeler: ImportLabeler, case: Any = None, variables: Any = None) -> Any:
    return labeler.label(
        case or fx.note_case(),
        fx.SOURCE,
        variables or fx.NOTE_VARIABLES,
        gate=GATE,
        context=fx.run_context(),
    )


# Admission ---------------------------------------------------------------------------------


class _Labeler:
    def __init__(self, tier: str) -> None:
        self.name, self.version, self.tier = "x", "1", tier

    def label(self, *args: Any, **kwargs: Any) -> Any:
        return LabelerFailure(reason="missing")


def test_only_silver_labelers_are_admitted(tmp_path: Path) -> None:
    labeler = _labeler(tmp_path, {})
    assert isinstance(labeler, Labeler)
    assert admit_labeler(labeler) is labeler
    assert admit_labeler(_Labeler("silver")) is not None
    for tier in ("single_review", "adjudicated"):
        with pytest.raises(LabelerRejected, match="human_tier"):
            admit_labeler(_Labeler(tier))
    with pytest.raises(LabelerRejected, match="not_a_labeler"):
        admit_labeler(object())


# Import events -----------------------------------------------------------------------------


def test_a_stored_result_becomes_a_silver_example_with_its_import_events(tmp_path: Path) -> None:
    result = _note_result()
    labeler = _labeler(tmp_path, {result.case_id: {"result": json.loads(result.model_dump_json())}})
    events, example = _label(labeler)

    assert [event.action for event in events] == [
        "import.result",
        "claim.create",
        "outcome.set",
        "tier.set",
    ]
    assert {event.actor for event in events} == {"tool:amber-import@0.1"}
    assert {event.role for event in events} == {"importer"}
    assert all(event.provenance.run_id == fx.RUN_IDS[0] for event in events)
    assert events[0].external_ref == result.run_id
    reference = example.references[fx.NOTE_VARIABLES.name]
    assert (reference.tier, reference.annotators) == (ReferenceTier.silver, (TEACHER,))
    assert example.as_of_event == events[-1].event_id
    assert [claim.status for claim in example.claims] == ["proposed"]
    assert (
        verify_example(
            example,
            events,
            case=fx.note_case(),
            source=fx.SOURCE,
            variables=fx.NOTE_VARIABLES,
            spans=spans_of(example),
        )
        == example
    )


def test_mention_level_results_import_in_dependency_order() -> None:
    result = _mention_result()
    case = fx.case()
    events, spans, example = import_events(
        result,
        case=case,
        source=fx.SOURCE,
        variables=fx.VARIABLES,
        actor="tool:importer@1",
        provenance=fx.provenance("tool:importer@1", fx.RUN_IDS[0]),
        now=CLOCK,
    )
    actions = [event.action for event in events]
    assert actions[:3] == ["import.result", "mention.create", "reading.create"]
    claims = [
        event.after["claim"]["variable"] for event in events if event.action == "claim.create"
    ]
    assert claims == ["finding", "summary"]
    assert [event.event_id for event in events] == sorted(event.event_id for event in events)
    assert {ref.tier for ref in example.references.values()} == {ReferenceTier.silver}
    assert len(spans) == 3


def test_failed_results_never_become_references(tmp_path: Path) -> None:
    result = fx.result(fx.RUN_IDS[0])
    failed = result.model_copy(
        update={
            "variable_outcomes": {
                name: item.model_copy(
                    update={
                        "outcome": item.outcome.model_copy(
                            update={"status": "failed", "failure_kind": "provider"}
                        )
                    }
                )
                for name, item in result.variable_outcomes.items()
            }
        }
    )
    with pytest.raises(ValueError, match="failed"):
        import_events(
            failed,
            case=fx.case(),
            source=fx.SOURCE,
            variables=fx.VARIABLES,
            actor="tool:importer@1",
            provenance=fx.provenance("tool:importer@1", fx.RUN_IDS[0]),
            now=CLOCK,
        )


def test_events_must_be_stored_before_their_example(tmp_path: Path) -> None:
    result = _note_result()
    labeler = _labeler(tmp_path, {result.case_id: {"result": json.loads(result.model_dump_json())}})
    events, example = _label(labeler)
    store = InMemoryStore()
    store.put_case(fx.note_case(), variables=fx.NOTE_VARIABLES)
    resolver = InMemoryResolver([fx.SOURCE])
    with pytest.raises(StoreError, match="events_required"):
        store.put_example(example, resolver=resolver)
    store.append_events(events, spans=spans_of(example))
    store.put_example(example, resolver=resolver)
    assert store.current_example(fx.note_case().case_id, resolver=resolver) == example


# ImportLabeler outcomes --------------------------------------------------------------------


def test_stored_labels_become_label_records(tmp_path: Path) -> None:
    record = {
        "schema_version": "amber/0.1",
        "case_id": fx.note_case().case_id,
        "variable": fx.NOTE_VARIABLES.name,
        "variable_set": fx.NOTE_VARIABLES.name,
        "variable_set_version": fx.NOTE_VARIABLES.version,
        "variable_set_ref": fx.NOTE_VARIABLES.schema_ref,
        "tier": "silver",
        "outcome_status": "answered",
        "value": {"present": False},
        "producer": "rule:candidate@1+abc",
        "split": "dev",
    }
    output = _label(_labeler(tmp_path, {record["case_id"]: {"label": record}}))
    assert isinstance(output, LabelRecord)
    assert output.value == {"present": False}


@pytest.mark.parametrize(
    ("entries", "reason"),
    [
        ({}, "missing"),
        ({"conformance-note-case-1": {"other": {}}}, "unsupported"),
        ({"conformance-note-case-1": {"result": {"case_id": "x"}}}, "invalid"),
        ({"conformance-note-case-1": {"label": {"case_id": "x"}}}, "invalid"),
    ],
)
def test_unusable_entries_are_labeler_failures(
    tmp_path: Path, entries: dict[str, Any], reason: str
) -> None:
    assert _label(_labeler(tmp_path, entries)) == LabelerFailure(reason=reason)  # type: ignore[arg-type]


def test_a_stored_failed_result_is_a_labeler_failure(tmp_path: Path) -> None:
    result = fx.result(fx.RUN_IDS[0])
    failed = json.loads(result.model_dump_json())
    for item in failed["variable_outcomes"].values():
        item["outcome"].update(status="failed", failure_kind="provider", reason="Synthetic.")
    labeler = _labeler(tmp_path, {result.case_id: {"result": failed}})
    output = _label(labeler, fx.case(), fx.VARIABLES)
    assert output == LabelerFailure(reason="failed_result")


def test_an_unreadable_file_is_missing(tmp_path: Path) -> None:
    labeler = ImportLabeler(tmp_path / "absent.json")
    assert _label(labeler) == LabelerFailure(reason="missing")
    assert not isinstance(_label(labeler), Example)
