"""Review backends: the FileReviewBackend round trip, gating, and import rules (contract v0.1
sections 4.7 and 6.2; catalog IFC-10)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from amber.annotations import verify_example
from amber.conformance import fixtures as fx
from amber.interfaces import ReviewBackend
from amber.labeling import spans_of
from amber.policy import DEFAULT_MATRIX, DataPolicy, Destination, PolicyViolation, StaticGate
from amber.reference import FileReviewBackend, InMemoryResolver
from amber.reference.review import simulate_reviews
from amber.review import ReviewBatchReceipt, ReviewImportError, load_spans
from amber.schemas import ReferenceTier, Sensitivity, Zone

from .test_labels import _result as _mention_result

NOW = datetime(2026, 1, 4, tzinfo=UTC)
DATA = DataPolicy(sensitivity=Sensitivity.synthetic)
GATE = StaticGate(DEFAULT_MATRIX)
REVIEW = Destination(
    name="synthetic-review",
    kind="review",
    zone=Zone.local,
    accepts=frozenset({"source_text", "derived_text"}),
)
BLANK = fx.note_case("conformance-note-case-2")


def _backend(tmp_path: Path, destination: Destination = REVIEW) -> FileReviewBackend:
    return FileReviewBackend(
        tmp_path / "review", destination, answer_models=[fx.FindingAnswer], clock=lambda: NOW
    )


def _proposal() -> Any:
    return fx.note_result(fx.RUN_IDS[0], present=True, producer=fx.TEACHER)


def _export(backend: FileReviewBackend, streams: tuple[str, ...] = ("a", "b")) -> Any:
    return backend.export_cases(
        [(fx.note_case(), fx.SOURCE, _proposal()), (BLANK, fx.SOURCE, None)],
        variables=fx.NOTE_VARIABLES,
        streams=streams,
        gate=GATE,
        data=DATA,
    )


def _import(backend: FileReviewBackend, receipt: ReviewBatchReceipt) -> Any:
    return backend.import_references(receipt, resolver=InMemoryResolver([fx.SOURCE]))


def _round_trip(tmp_path: Path, streams: tuple[str, ...], **kwargs: Any) -> Any:
    backend = _backend(tmp_path)
    receipt = _export(backend, streams)
    simulate_reviews(receipt, variables=fx.NOTE_VARIABLES, now=NOW, **kwargs)
    # A fresh backend imports what another exported.
    return receipt, *_import(_backend(tmp_path), receipt)


def _review_file(receipt: ReviewBatchReceipt, index: int = 0) -> Path:
    return Path(receipt.location) / "reviews" / f"{index}.json"


# Round trips -------------------------------------------------------------------------------


def test_the_file_backend_is_a_review_backend(tmp_path: Path) -> None:
    assert isinstance(_backend(tmp_path), ReviewBackend)
    with pytest.raises(ValueError, match="review destination"):
        _backend(tmp_path, REVIEW.model_copy(update={"kind": "tracking"}))


def test_two_streams_and_an_adjudication_give_adjudicated_examples(tmp_path: Path) -> None:
    receipt, examples, events = _round_trip(tmp_path, ("a", "b"))
    assert [example.case_id for example in examples] == list(receipt.case_ids)
    proposed, blank = examples
    reference = proposed.references[fx.NOTE_VARIABLES.name]
    assert reference.tier is ReferenceTier.adjudicated
    assert reference.annotators == ("human:synthetic-reviewer-a", "human:synthetic-reviewer-b")
    assert reference.adjudicator == "human:synthetic-adjudicator"
    assert [claim.status for claim in proposed.claims] == ["gold"]
    assert blank.references[fx.NOTE_VARIABLES.name].outcome.status == "not_mentioned"
    proposal_spans = tuple(item for item in _proposal().evidence if hasattr(item, "quote"))
    for example, case in ((proposed, fx.note_case()), (blank, BLANK)):
        mine = [event for event in events if event.case_id == case.case_id]
        assert mine[0].action == ("import.result" if example is proposed else "review.open")
        assert (
            verify_example(
                example,
                mine,
                case=case,
                source=fx.SOURCE,
                variables=fx.NOTE_VARIABLES,
                spans=(*spans_of(example), *proposal_spans),
            )
            == example
        )


def test_one_stream_gives_single_review(tmp_path: Path) -> None:
    _, examples, _ = _round_trip(tmp_path, ("solo",))
    tiers = {example.references[fx.NOTE_VARIABLES.name].tier for example in examples}
    assert tiers == {ReferenceTier.single_review}
    with pytest.raises(ValueError, match="adjudication"):
        _round_trip(tmp_path, ("solo",), decision="excluded")


@pytest.mark.parametrize("decision", ["unresolved", "excluded"])
def test_unresolved_and_excluded_cases_keep_events_without_examples(
    tmp_path: Path, decision: Any
) -> None:
    receipt, examples, events = _round_trip(tmp_path, ("a", "b"), decision=decision)
    assert examples == ()
    assert {event.case_id for event in events} == set(receipt.case_ids)
    assert any(event.action == "adjudication.resolve" for event in events)


def test_unreviewed_cases_yield_nothing(tmp_path: Path) -> None:
    receipt, examples, events = _round_trip(tmp_path, ("a", "b"), skip=[BLANK.case_id])
    assert [example.case_id for example in examples] == [fx.note_case().case_id]
    assert BLANK.case_id not in {event.case_id for event in events}


def test_mention_level_proposals_round_trip(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    receipt = backend.export_cases(
        [(fx.case(), fx.SOURCE, _mention_result())],
        variables=fx.VARIABLES,
        streams=("a", "b"),
        gate=GATE,
        data=DATA,
    )
    simulate_reviews(receipt, variables=fx.VARIABLES, now=NOW)
    (example,), _ = _import(backend, receipt)
    assert {ref.tier for ref in example.references.values()} == {ReferenceTier.adjudicated}
    assert {reading.status for reading in example.mention_readings} == {"gold"}


# Export ------------------------------------------------------------------------------------


def test_export_gates_sources_and_proposals_per_case(tmp_path: Path) -> None:
    receipt = _export(_backend(tmp_path))
    asked = [(decision.payload, decision.case_id) for decision in receipt.decisions]
    assert asked == [
        ("source_text", fx.note_case().case_id),
        ("derived_text", fx.note_case().case_id),
        ("source_text", BLANK.case_id),
    ]
    assert all(decision.destination == REVIEW.name for decision in receipt.decisions)
    written = json.loads((Path(receipt.location) / "batch.json").read_text(encoding="utf-8"))
    assert written["receipt"] == receipt.model_dump(mode="json")


@pytest.mark.parametrize("accepts", [frozenset(), frozenset({"source_text"})])
def test_a_refused_payload_sends_nothing(tmp_path: Path, accepts: frozenset[str]) -> None:
    backend = _backend(tmp_path, REVIEW.model_copy(update={"accepts": accepts}))
    with pytest.raises(PolicyViolation):
        _export(backend)
    assert not (tmp_path / "review").exists() or not any((tmp_path / "review").iterdir())


def test_export_refuses_mismatched_inputs(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    with pytest.raises(ValueError, match="belong together"):
        backend.export_cases(
            [(fx.case(), fx.SOURCE, None)],
            variables=fx.NOTE_VARIABLES,
            streams=("a",),
            gate=GATE,
            data=DATA,
        )
    with pytest.raises(ValueError, match="sensitivity"):
        backend.export_cases(
            [(fx.note_case(), fx.SOURCE, None)],
            variables=fx.NOTE_VARIABLES,
            streams=("a",),
            gate=GATE,
            data=DataPolicy(sensitivity=Sensitivity.deidentified),
        )
    with pytest.raises(ValueError):
        _export(backend, streams=("a", "a"))


# Import ------------------------------------------------------------------------------------


def _reviewed(tmp_path: Path) -> tuple[FileReviewBackend, ReviewBatchReceipt]:
    backend = _backend(tmp_path)
    receipt = _export(backend)
    simulate_reviews(receipt, variables=fx.NOTE_VARIABLES, now=NOW)
    return backend, receipt


def _edit_review(receipt: ReviewBatchReceipt, edit: Any) -> None:
    path = _review_file(receipt)
    payload = json.loads(path.read_text(encoding="utf-8"))
    edit(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_a_tampered_receipt_is_refused(tmp_path: Path) -> None:
    backend, receipt = _reviewed(tmp_path)
    tampered = receipt.model_copy(update={"case_ids": (fx.note_case().case_id,)})
    with pytest.raises(ReviewImportError, match="receipt_mismatch"):
        _import(backend, tampered)


def test_an_unresolvable_source_is_refused(tmp_path: Path) -> None:
    backend, receipt = _reviewed(tmp_path)
    with pytest.raises(ReviewImportError, match="source_unavailable"):
        backend.import_references(receipt, resolver=InMemoryResolver())


def test_events_from_another_service_are_refused(tmp_path: Path) -> None:
    backend, receipt = _reviewed(tmp_path)

    def elsewhere(payload: dict[str, Any]) -> None:
        payload["events"][-1]["provenance"]["destination"] = "other-review"

    _edit_review(receipt, elsewhere)
    with pytest.raises(ReviewImportError, match="foreign_provenance"):
        _import(backend, receipt)


def test_events_that_do_not_replay_fail_the_whole_import(tmp_path: Path) -> None:
    backend, receipt = _reviewed(tmp_path)

    def reorder(payload: dict[str, Any]) -> None:
        payload["events"].reverse()

    _edit_review(receipt, reorder)
    with pytest.raises(ReviewImportError, match="invalid_events: event_order"):
        _import(backend, receipt)


def test_spans_must_reproduce_against_the_source(tmp_path: Path) -> None:
    backend, receipt = _reviewed(tmp_path)

    def shift(payload: dict[str, Any]) -> None:
        payload["spans"][0]["start"] += 1

    _edit_review(receipt, shift)
    with pytest.raises(ReviewImportError, match="invalid_spans"):
        _import(backend, receipt)
    assert load_spans([span.model_dump(mode="json") for span in fx.SPANS], fx.SOURCE) == fx.SPANS
    with pytest.raises(ReviewImportError, match="invalid_spans"):
        load_spans([{"source_id": "elsewhere"}], fx.SOURCE)


def test_a_malformed_review_file_is_refused(tmp_path: Path) -> None:
    backend, receipt = _reviewed(tmp_path)
    _review_file(receipt).write_text("[]", encoding="utf-8")
    with pytest.raises(ReviewImportError, match="invalid_batch"):
        _import(backend, receipt)


def test_receipts_need_distinct_cases_and_streams() -> None:
    fields: dict[str, Any] = {
        "batch_id": "b",
        "destination": "d",
        "location": "l",
        "variable_set_ref": "v",
        "case_ids": ("c",),
        "streams": ("a",),
        "decisions": (),
    }
    assert ReviewBatchReceipt(**fields).streams == ("a",)
    for bad in ({"case_ids": ()}, {"streams": ("a", "a")}, {"streams": (" ",)}):
        with pytest.raises(ValueError):
            ReviewBatchReceipt(**{**fields, **bad})
