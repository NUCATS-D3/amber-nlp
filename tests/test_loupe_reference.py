"""loupe export and publication import (amber-1ah.2; contract v0.1 section 4.7). The
publications here are synthetic fixtures in loupe's ``loupe-reference-v1`` shape."""

from __future__ import annotations

import copy
import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from amber.annotations import verify_example
from amber.conformance import fixtures as fx
from amber.loupe import AdapterError, AmberClaimsAdapter
from amber.loupe.reference import (
    ADAPTER_PATH,
    ReferenceError,
    adapter_sha256,
    import_reference,
    loupe_digest,
    source_manifest,
)
from amber.policy import DEFAULT_MATRIX, DataPolicy, Destination, PolicyViolation, StaticGate
from amber.reference import InMemoryResolver, InMemoryStore
from amber.schemas import ReferenceTier, Sensitivity, Source, SourceKind, Zone

NOW = datetime(2026, 1, 8, tzinfo=UTC)
GATE = StaticGate(DEFAULT_MATRIX)
DATA = DataPolicy(sensitivity=Sensitivity.synthetic)
REVIEW = Destination(
    name="loupe-institution",
    kind="review",
    zone=Zone.institution,
    accepts=frozenset({"source_text"}),
)
PROJECT = {
    "variable_set": "amber.conformance.fixtures:NOTE_VARIABLES",
    "variable_set_ref": fx.NOTE_VARIABLES.schema_ref,
    "dataset": fx.DATASET,
    "dataset_manifest_hash": "synthetic-manifest",
    "sensitivity": "synthetic",
    "policy_version": "synthetic-policy@1",
    "destination": "loupe-institution",
    "zone": "institution",
}
OTHER = Source.create(
    patient_id="conformance-patient-2",
    kind=SourceKind.note,
    external_id="conformance-note-2",
    datetime=None,
    text="Invented: no finding.\r\nCaf\u00e9 visit only.",
    record=None,
)
RESOLVER = InMemoryResolver([fx.SOURCE, OTHER])
VARIABLE = fx.NOTE_VARIABLES.name


def _case(source: Source, case_id: str) -> Any:
    return fx.case(
        case_id, task=VARIABLE, patient_id=source.patient_id, source_ids=[source.source_id]
    )


CASES = [(_case(fx.SOURCE, "loupe-case-1"), fx.SOURCE), (_case(OTHER, "loupe-case-2"), OTHER)]


def _manifest(streams: tuple[str, ...] = ("a", "b")) -> dict[str, Any]:
    manifest, _ = source_manifest(
        CASES,
        name="amber-claims",
        variables=fx.NOTE_VARIABLES,
        project_configuration=PROJECT,
        streams=streams,
        synthetic=True,
        gate=GATE,
        data=DATA,
        destination=REVIEW,
        now=NOW,
    )
    for record in manifest["records"]:
        record["source_sha256"] = hashlib.sha256(record["text"].encode()).hexdigest()
    return manifest


def _span(text: str, quote: str, occurrence: int = 0) -> dict[str, Any]:
    start = -1
    for _ in range(occurrence + 1):
        start = text.index(quote, start + 1)
    return {"start": start, "end": start + len(quote), "quote": quote}


def _answer(reviewer: str, present: bool, occurrence: int = 0) -> dict[str, Any]:
    claim = {
        "value": {"present": present},
        "rationale": "Invented rationale.",
        "evidence": [_span(fx.TEXT, "mass", occurrence)],
        "fields": {"present": [0]},
    }
    return {
        "reviewer": reviewer,
        "whole_note_complete": True,
        "variables": {VARIABLE: {"status": "answered", "claims": [claim]}},
    }


def _absent(reviewer: str) -> dict[str, Any]:
    return {
        "reviewer": reviewer,
        "whole_note_complete": True,
        "variables": {VARIABLE: {"status": "not_mentioned", "reason": "Invented: none."}},
    }


def _publication(
    decisions: dict[str, tuple[str, int | None]],
    requests: dict[str, list[tuple[str, dict[str, Any]]]],
    *,
    streams: tuple[str, ...] = ("a", "b"),
) -> dict[str, Any]:
    """A ``loupe-reference-v1`` publication: per record, the decision and selected review
    index, and the (stream, request) of each considered review."""
    configuration = _manifest(streams)
    adapter = AmberClaimsAdapter(copy.deepcopy(configuration))
    records = []
    for record in configuration["records"]:
        example = {
            "example_id": record["record_id"],
            "source_text": record["text"],
            "source_record": record,
        }
        status, selected = decisions[record["record_id"]]
        reviews = []
        for index, (stream, request) in enumerate(requests.get(record["record_id"], [])):
            result = adapter.build_result(request, example, adapter.inventory, "c" * 64)
            reviews.append(
                {
                    "submission_id": str(uuid.UUID(int=len(records) * 10 + index + 1)),
                    "stream": stream,
                    "reviewer": request["reviewer"],
                    "revision_sha256": "r" * 64,
                    "native_assessments": [],
                    "result": result,
                }
            )
        chosen = reviews[selected] if selected is not None else None
        records.append(
            {
                "source": record,
                "status": status,
                "result": chosen["result"] if chosen and status != "excluded" else None,
                "adjudication": {
                    "status": status,
                    "selected_submission_id": chosen["submission_id"] if chosen else None,
                    "considered_submission_ids": [review["submission_id"] for review in reviews],
                    "reason": "Invented adjudication reason.",
                    "reviewer": "Synthetic Adjudicator",
                    "reviews": reviews,
                },
            }
        )
    reference = {
        "format": "loupe-reference-v1",
        "synthetic": True,
        "clinical_reference": False,
        "source_sha256": "s" * 64,
        "source_dataset_id": "d-synthetic",
        "source_run_id": "run-synthetic",
        "adapter_sha256": adapter_sha256(adapter),
        "configuration": configuration,
        "adjudication": {"reviewer": "Synthetic Adjudicator", "whole_note_complete": True},
        "records": records,
    }
    reference["snapshot_sha256"] = loupe_digest(reference)
    return reference


def _rehash(reference: dict[str, Any]) -> dict[str, Any]:
    body = {key: value for key, value in reference.items() if key != "snapshot_sha256"}
    return {**body, "snapshot_sha256": loupe_digest(body)}


def _import(reference: dict[str, Any]) -> Any:
    return import_reference(reference, resolver=RESOLVER, variables=fx.NOTE_VARIABLES)


def _blind() -> dict[str, Any]:
    first, second = fx.SOURCE.source_id, OTHER.source_id
    return _publication(
        {first: ("accepted", 0), second: ("accepted", 1)},
        {
            first: [("a", _answer("Reviewer A", True)), ("b", _answer("Reviewer B", False, 1))],
            second: [("a", _absent("Reviewer A")), ("b", _absent("Reviewer B"))],
        },
    )


# Export ------------------------------------------------------------------------------------


def test_the_manifest_holds_sources_and_no_labels() -> None:
    manifest = _manifest()
    assert manifest["adapter"] == ADAPTER_PATH
    assert manifest["streams"] == ["a", "b"]
    (first,) = [r for r in manifest["records"] if r["record_id"] == fx.SOURCE.source_id]
    assert first["text"] == fx.TEXT
    assert first["metadata"]["case_id"] == "loupe-case-1"
    assert first["metadata"]["variable_set_ref"] == fx.NOTE_VARIABLES.schema_ref
    assert set(first) == {"record_id", "text", "metadata", "source_sha256"}
    AmberClaimsAdapter(copy.deepcopy(manifest))


def test_export_is_gated_and_checked() -> None:
    with pytest.raises(PolicyViolation):
        source_manifest(
            CASES,
            name="x",
            variables=fx.NOTE_VARIABLES,
            project_configuration=PROJECT,
            streams=["a"],
            synthetic=True,
            gate=GATE,
            data=DATA,
            destination=REVIEW.model_copy(update={"accepts": frozenset({"labels_only"})}),
            now=NOW,
        )
    with pytest.raises(ReferenceError, match="case_mismatch"):
        source_manifest(
            [(fx.case(), fx.SOURCE)],
            name="x",
            variables=fx.NOTE_VARIABLES,
            project_configuration=PROJECT,
            streams=["a"],
            synthetic=True,
            gate=GATE,
            data=DATA,
            destination=REVIEW,
            now=NOW,
        )


# Import ------------------------------------------------------------------------------------


def test_accepted_blind_reviews_become_adjudicated_examples() -> None:
    reference = _blind()
    imported = _import(reference)
    assert imported.diagnostics == ()
    by_case = {example.case_id: example for example in imported.examples}
    answered = by_case["loupe-case-1"].references[VARIABLE]
    assert answered.tier is ReferenceTier.adjudicated
    assert answered.annotators == ("human:Reviewer-A", "human:Reviewer-B")
    assert answered.adjudicator == "human:Synthetic-Adjudicator"
    (claim,) = by_case["loupe-case-1"].claims
    assert (claim.value, claim.status) == ({"present": True}, "gold")
    selected = reference["records"][0]["adjudication"]["reviews"][0]["result"]["case_result"]
    assert claim.claim_id == selected["claims"][0]["claim_id"]
    submits = [e for e in imported.events if e.action == "review.submit"]
    assert {e.external_ref for e in submits} == {
        r["submission_id"] for rec in reference["records"] for r in rec["adjudication"]["reviews"]
    }
    resolve = next(
        e for e in imported.events if e.action == "adjudication.resolve" and e.target_id is None
    )
    assert resolve.external_ref == reference["snapshot_sha256"]
    assert resolve.event_id in answered.derived_from_events
    absent = by_case["loupe-case-2"].references[VARIABLE]
    assert (absent.outcome.status, absent.final_claim_ids) == ("not_mentioned", ())


def test_imported_examples_replay_from_their_events_and_store() -> None:
    imported = _import(_blind())
    store = InMemoryStore()
    for case, _ in CASES:
        store.put_case(case, variables=fx.NOTE_VARIABLES)
    store.append_events(imported.events, spans=imported.spans)
    for example in imported.examples:
        case = next(c for c, _ in CASES if c.case_id == example.case_id)
        source = RESOLVER.resolve(case.source_ids[0])
        mine = [e for e in imported.events if e.case_id == case.case_id]
        assert (
            verify_example(
                example,
                mine,
                case=case,
                source=source,
                variables=fx.NOTE_VARIABLES,
                spans=imported.spans,
            )
            == example
        )
        store.put_example(example, resolver=RESOLVER)
    assert _import(_blind()) == imported


def test_one_stream_gives_single_review_without_adjudication_events() -> None:
    first, second = fx.SOURCE.source_id, OTHER.source_id
    reference = _publication(
        {first: ("accepted", 0), second: ("accepted", 0)},
        {first: [("solo", _answer("Solo", True))], second: [("solo", _absent("Solo"))]},
        streams=("solo",),
    )
    imported = _import(reference)
    assert {e.references[VARIABLE].tier for e in imported.examples} == {ReferenceTier.single_review}
    assert not any(e.action == "adjudication.resolve" for e in imported.events)
    assert all(e.stream is None for e in imported.events)


@pytest.mark.parametrize("status", ["unresolved", "excluded"])
def test_unresolved_and_excluded_records_keep_events_without_examples(status: str) -> None:
    first, second = fx.SOURCE.source_id, OTHER.source_id
    reference = _publication(
        {first: (status, 0 if status == "unresolved" else None), second: ("accepted", 0)},
        {
            first: [("a", _answer("Reviewer A", True)), ("b", _answer("Reviewer B", False))],
            second: [("a", _absent("Reviewer A")), ("b", _absent("Reviewer B"))],
        },
    )
    imported = _import(reference)
    assert [e.case_id for e in imported.examples] == ["loupe-case-2"]
    (diagnostic,) = imported.diagnostics
    assert (diagnostic.case_id, diagnostic.status) == ("loupe-case-1", status)
    kept = [e for e in imported.events if e.case_id == "loupe-case-1"]
    assert any(e.action == "review.submit" for e in kept)
    assert kept[-1].action == "adjudication.resolve" and kept[-1].after == {"decision": status}


# Tampering ---------------------------------------------------------------------------------


def test_a_changed_publication_fails_its_checksum() -> None:
    reference = _blind()
    reference["records"][0]["adjudication"]["reason"] = "Edited."
    with pytest.raises(ReferenceError, match="reference_checksum_mismatch"):
        _import(reference)


def _review(reference: dict[str, Any]) -> dict[str, Any]:
    return reference["records"][0]["adjudication"]["reviews"][0]["result"]


@pytest.mark.parametrize(
    "edit",
    [
        lambda r: r["case_result"]["claims"][0]["value"].update(present=False),
        lambda r: r["mentions"][0]["claims"][0]["evidence"][0].update(start=7, end=11),
        lambda r: r["case_result"]["variable_outcomes"][VARIABLE]["outcome"].update(
            status="failed", failure_kind="provider", reason="Invented."
        ),
    ],
    ids=["value", "offset", "failed_outcome"],
)
def test_tampered_reviews_are_refused_even_when_rehashed(edit: Any) -> None:
    reference = _blind()
    edit(_review(reference))
    with pytest.raises((AdapterError, ReferenceError)):
        _import(_rehash(reference))


def test_tampered_source_text_is_refused() -> None:
    reference = _blind()
    reference["records"][0]["source"]["text"] = fx.TEXT.replace("mass", "mask", 1)
    with pytest.raises(ReferenceError, match="source_text_mismatch"):
        _import(_rehash(reference))


def test_a_publication_from_another_adapter_build_is_refused() -> None:
    reference = _blind()
    reference["adapter_sha256"] = "0" * 64
    with pytest.raises(ReferenceError, match="adapter_mismatch"):
        _import(_rehash(reference))


def test_unknown_sources_and_variable_sets_are_refused() -> None:
    with pytest.raises(ReferenceError, match="source_unavailable"):
        import_reference(
            _blind(), resolver=InMemoryResolver([fx.SOURCE]), variables=fx.NOTE_VARIABLES
        )
    with pytest.raises(ReferenceError, match="variable_set_mismatch"):
        import_reference(_blind(), resolver=RESOLVER, variables=fx.VARIABLES)


# ReviewBackend -----------------------------------------------------------------------------


def _backend(tmp_path: Any) -> Any:
    from amber.loupe import LoupeReviewBackend

    return LoupeReviewBackend(
        tmp_path, REVIEW, name="amber-claims", project_configuration=PROJECT, synthetic=True,
        clock=lambda: NOW,
    )  # fmt: skip


def test_the_loupe_backend_round_trips_through_files(tmp_path: Any) -> None:
    import json

    from amber.interfaces import ReviewBackend

    backend = _backend(tmp_path)
    assert isinstance(backend, ReviewBackend)
    receipt = backend.export_cases(
        [(case, source, None) for case, source in CASES],
        variables=fx.NOTE_VARIABLES,
        streams=["a", "b"],
        gate=GATE,
        data=DATA,
    )
    written = json.loads((tmp_path / receipt.batch_id / "source.json").read_text())
    assert [r["record_id"] for r in written["records"]] == sorted(s.source_id for _, s in CASES)
    (tmp_path / receipt.batch_id / "reference.json").write_text(json.dumps(_blind()))
    examples, events = backend.import_references(receipt, resolver=RESOLVER)
    assert {example.case_id for example in examples} == set(receipt.case_ids)
    assert events


def test_the_loupe_backend_refuses_proposals_and_foreign_publications(tmp_path: Any) -> None:
    import json

    from amber.review import ReviewImportError

    backend = _backend(tmp_path)
    proposal = fx.note_result(fx.RUN_IDS[0], present=True, producer="model:x@1")
    with pytest.raises(ReferenceError, match="proposals_not_supported"):
        backend.export_cases(
            [(CASES[0][0], fx.SOURCE, proposal)],
            variables=fx.NOTE_VARIABLES, streams=["a"], gate=GATE, data=DATA,
        )  # fmt: skip
    receipt = backend.export_cases(
        [(case, source, None) for case, source in CASES],
        variables=fx.NOTE_VARIABLES, streams=["a"], gate=GATE, data=DATA,
    )  # fmt: skip
    with pytest.raises(ReviewImportError, match="invalid_batch"):
        backend.import_references(receipt, resolver=RESOLVER)
    (tmp_path / receipt.batch_id / "reference.json").write_text(json.dumps(_blind()))
    with pytest.raises(ReviewImportError, match="receipt_mismatch"):
        backend.import_references(receipt, resolver=RESOLVER)
