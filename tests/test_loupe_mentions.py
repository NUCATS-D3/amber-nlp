"""Mention-level variables in loupe claim mode (amber-sm8.3): request mentions become grounded
Mentions with the reviewer's readings and attribute cues, mention-level claims rely on them,
publications import with mention and reading events, and results become proposals again."""

from __future__ import annotations

import copy
import hashlib
import uuid
from typing import Any

import pytest

from amber.cases import load_case_result
from amber.conformance import fixtures as fx
from amber.loupe import AdapterError, AmberClaimsAdapter, import_reference, source_manifest
from amber.loupe.reference import adapter_sha256, loupe_digest, proposal_request
from amber.schemas import AttributeRequirement, ReferenceTier, Variable, VariableSet

from .test_loupe_reference import DATA, GATE, NOW, OTHER, RESOLVER, REVIEW, _span

FINDINGS = VariableSet.create(
    name="loupe_mention_findings",
    version="1.0.0",
    variables=(
        Variable(
            name="finding",
            level="mention",
            answer_model=fx.FindingAnswer,
            instructions="Invented: is the invented finding present at this mention?",
            mention_types=frozenset({"finding"}),
            requires_attributes=(AttributeRequirement(category="negation", values=(True, False)),),
            final=True,
        ),
        Variable(
            name="summary",
            level="note",
            answer_model=fx.FindingAnswer,
            instructions="Invented: is the invented finding present in the note?",
            final=True,
        ),
    ),
)
PROJECT = {
    "variable_set": "tests.test_loupe_mentions:FINDINGS",
    "variable_set_ref": FINDINGS.schema_ref,
    "dataset": fx.DATASET,
    "dataset_manifest_hash": "synthetic-manifest",
    "sensitivity": "synthetic",
    "policy_version": "synthetic-policy@1",
    "destination": "loupe-institution",
    "zone": "institution",
}
TEXT = fx.TEXT


def _case(source: Any, case_id: str) -> Any:
    return fx.case(
        case_id, task=FINDINGS.name, patient_id=source.patient_id, source_ids=[source.source_id]
    )


CASES = [(_case(fx.SOURCE, "mention-case-1"), fx.SOURCE), (_case(OTHER, "mention-case-2"), OTHER)]


def _manifest(streams: tuple[str, ...]) -> dict[str, Any]:
    manifest, _ = source_manifest(
        CASES,
        name="amber-mentions",
        variables=FINDINGS,
        project_configuration=PROJECT,
        streams=streams,
        synthetic=True,
        gate=GATE,
        data=DATA,
        destination=REVIEW,
        now=NOW,
    )
    return manifest


ADAPTER = AmberClaimsAdapter(_manifest(("a",)))


def _example(source: Any = fx.SOURCE) -> dict[str, Any]:
    manifest = _manifest(("a",))
    record = next(r for r in manifest["records"] if r["record_id"] == source.source_id)
    return {
        "example_id": record["record_id"],
        "source_text": record["text"],
        "source_record": record,
    }


def _mention(key: str, occurrence: int, negated: bool, cue: str) -> dict[str, Any]:
    return {
        "key": key,
        **_span(TEXT, "mass", occurrence),
        "mention_type": "finding",
        "label": "mass",
        "attributes": {"negation": {"value": negated, "cue": _span(TEXT, cue)}},
    }


def _request(reviewer: str = "Reviewer A", *, second_present: bool = False) -> dict[str, Any]:
    return {
        "reviewer": reviewer,
        "whole_note_complete": True,
        "mentions": [
            _mention("m1", 0, False, "Exam"),
            _mention("m2", 1, True, "No e\u0301vidence of"),
        ],
        "variables": {
            "finding": {
                "status": "answered",
                "claims": [
                    {
                        "value": {"present": True},
                        "rationale": "Invented: the mass is seen.",
                        "mention": "m1",
                        "evidence": [],
                    },
                    {
                        "value": {"present": second_present},
                        "rationale": "Invented: the other mass is negated.",
                        "mention": "m2",
                        "evidence": [],
                    },
                ],
            },
            "summary": {
                "status": "answered",
                "claims": [
                    {
                        "value": {"present": True},
                        "rationale": "Invented: one mass is present.",
                        "evidence": [_span(TEXT, "mass \U0001f642 seen in the liver.")],
                        "fields": {"present": [0]},
                    }
                ],
            },
        },
    }


def _absent(reviewer: str) -> dict[str, Any]:
    reason = "Invented: nothing relevant."
    return {
        "reviewer": reviewer,
        "whole_note_complete": True,
        "variables": {
            "finding": {"status": "not_mentioned", "reason": reason},
            "summary": {"status": "not_mentioned", "reason": reason},
        },
    }


def _build(request: dict[str, Any]) -> dict[str, Any]:
    return ADAPTER.build_result(request, _example(), ADAPTER.inventory, "c" * 64)


# The adapter -------------------------------------------------------------------------------


def test_request_mentions_become_readings_that_mention_claims_rely_on() -> None:
    result = _build(_request())
    loaded = load_case_result(
        result["case_result"], case=CASES[0][0], source=fx.SOURCE, variables=FINDINGS
    )
    assert [(m.quote, m.mention_type) for m in loaded.mentions] == [("mass", "finding")] * 2
    readings = {reading.reading_id: reading for reading in loaded.mention_readings}
    assert sorted(r.attributes["negation"].value for r in readings.values()) == [False, True]
    findings = [claim for claim in loaded.claims if claim.variable == "finding"]
    assert {readings[claim.reading_id].mention_id for claim in findings} == {  # type: ignore[index]
        mention.mention_id for mention in loaded.mentions
    }
    roles = {edge.role for edge in loaded.edges if edge.claim_id == findings[0].claim_id}
    assert "attribute:negation" in roles
    assert result["amber_mentions"][0]["key"] == "m1"


def test_mention_results_rebuild_identically_and_reload() -> None:
    result = _build(_request())
    assert ADAPTER.validate_result(copy.deepcopy(result), _example()) == result
    assert ADAPTER.request_from(result)["mentions"] == result["amber_mentions"]
    tampered = copy.deepcopy(result)
    tampered["amber_mentions"][1]["attributes"]["negation"]["value"] = False
    with pytest.raises(AdapterError, match="result_mismatch"):
        ADAPTER.validate_result(tampered, _example())


def test_note_only_results_carry_no_mention_state() -> None:
    result = ADAPTER.build_result(_absent("Reviewer A"), _example(OTHER), ADAPTER.inventory, "c")
    assert "amber_mentions" not in result
    assert "mentions" not in ADAPTER.request_from(result)


def test_the_inventory_describes_mention_variables() -> None:
    finding = next(v for v in ADAPTER.inventory["variables"] if v["name"] == "finding")
    assert finding["level"] == "mention" and finding["mention_types"] == ["finding"]
    assert finding["requires_attributes"] == [
        {"category": "negation", "values": [False, True], "evidence": "required"}
    ]


def _without_mention(request: dict[str, Any]) -> None:
    del request["variables"]["finding"]["claims"][0]["mention"]


def _unknown_type(request: dict[str, Any]) -> None:
    request["mentions"][0]["mention_type"] = "organ"


def _wrong_quote(request: dict[str, Any]) -> None:
    request["mentions"][0]["quote"] = "Mass"


def _duplicate_key(request: dict[str, Any]) -> None:
    request["mentions"][1]["key"] = "m1"


def _conflict(request: dict[str, Any]) -> None:
    request["variables"]["finding"] = {
        "status": "conflicting_evidence",
        "reason": "Invented.",
        "evidence": [_span(TEXT, "mass")],
    }


def _no_attribute(request: dict[str, Any]) -> None:
    request["mentions"][0]["attributes"] = {}


def _no_cue(request: dict[str, Any]) -> None:
    del request["mentions"][0]["attributes"]["negation"]["cue"]


def _mention_on_note_claim(request: dict[str, Any]) -> None:
    request["variables"]["summary"]["claims"][0]["mention"] = "m1"


def _bad_reading_value(request: dict[str, Any]) -> None:
    request["mentions"][0]["value"] = True


@pytest.mark.parametrize(
    ("edit", "code"),
    [
        (_without_mention, "claim_mention_required"),
        (_unknown_type, "invalid_mention_type"),
        (_wrong_quote, "quote_mismatch"),
        (_duplicate_key, "duplicate_mention_key"),
        (_conflict, "invalid_outcome"),
        (_no_attribute, "attribute_value_not_permitted"),
        (_no_cue, "missing_attribute_evidence"),
        (_mention_on_note_claim, "invalid_claim"),
        (_bad_reading_value, "invalid_mention"),
    ],
)
def test_invalid_mention_requests_are_refused(edit: Any, code: str) -> None:
    request = _request()
    edit(request)
    with pytest.raises(AdapterError, match=code):
        _build(request)


# Import ------------------------------------------------------------------------------------


def _publication(streams: tuple[str, ...], requests: dict[str, list[Any]]) -> dict[str, Any]:
    configuration = _manifest(streams)
    for record in configuration["records"]:
        record["source_sha256"] = hashlib.sha256(record["text"].encode()).hexdigest()
    adapter = AmberClaimsAdapter(copy.deepcopy(configuration))
    records = []
    for record in configuration["records"]:
        example = {
            "example_id": record["record_id"],
            "source_text": record["text"],
            "source_record": record,
        }
        reviews = [
            {
                "submission_id": str(uuid.UUID(int=len(records) * 10 + index + 1)),
                "stream": stream,
                "reviewer": request["reviewer"],
                "revision_sha256": "r" * 64,
                "native_assessments": [],
                "result": adapter.build_result(request, example, adapter.inventory, "c" * 64),
            }
            for index, (stream, request) in enumerate(requests[record["record_id"]])
        ]
        records.append(
            {
                "source": record,
                "status": "accepted",
                "result": reviews[0]["result"],
                "adjudication": {
                    "status": "accepted",
                    "selected_submission_id": reviews[0]["submission_id"],
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


def _imported(streams: tuple[str, ...]) -> Any:
    requests = {
        fx.SOURCE.source_id: [
            (stream, _request(f"Reviewer {stream.upper()}", second_present=stream != "a"))
            for stream in streams
        ],
        OTHER.source_id: [(stream, _absent(f"Reviewer {stream.upper()}")) for stream in streams],
    }
    return import_reference(_publication(streams, requests), resolver=RESOLVER, variables=FINDINGS)


def test_a_single_review_imports_with_verified_readings() -> None:
    imported = _imported(("a",))
    assert imported.diagnostics == ()
    example = next(e for e in imported.examples if e.case_id == "mention-case-1")
    assert example.references["finding"].tier is ReferenceTier.single_review
    assert len(example.mentions) == 2
    assert {reading.status for reading in example.mention_readings} == {"verified"}
    assert {claim.status for claim in example.claims} == {"gold"}
    actions = [event.action for event in imported.events]
    assert actions.count("mention.create") == 2 and actions.count("reading.set_status") == 2


def test_adjudication_makes_the_selected_readings_gold() -> None:
    imported = _imported(("a", "b"))
    example = next(e for e in imported.examples if e.case_id == "mention-case-1")
    finding = example.references["finding"]
    assert finding.tier is ReferenceTier.adjudicated
    assert finding.annotators == ("human:Reviewer-A", "human:Reviewer-B")
    gold = [claim for claim in example.claims if claim.status == "gold"]
    assert sorted(claim.value["present"] for claim in gold if claim.variable == "finding") == [
        False,
        True,
    ]
    statuses = sorted(reading.status for reading in example.mention_readings)
    assert statuses.count("gold") == 2
    streams = {event.stream for event in imported.events if event.action == "reading.create"}
    assert streams == {"a", "b"}


# Proposals ---------------------------------------------------------------------------------


def test_a_mention_result_becomes_a_request_that_rebuilds_it() -> None:
    result = _build(_request())
    loaded = load_case_result(
        result["case_result"], case=CASES[0][0], source=fx.SOURCE, variables=FINDINGS
    )
    request = proposal_request(loaded, reviewer="model:teacher@1")
    assert request is not None
    assert sorted(
        (m["start"], m["attributes"]["negation"]["value"]) for m in request["mentions"]
    ) == [(m["start"], m["attributes"]["negation"]["value"]) for m in _request()["mentions"]]
    rebuilt = _build(request)
    assert ADAPTER.validate_result(copy.deepcopy(rebuilt), _example()) == rebuilt
    again = load_case_result(
        rebuilt["case_result"], case=CASES[0][0], source=fx.SOURCE, variables=FINDINGS
    )
    assert sorted((c.variable, c.value["present"]) for c in again.claims) == sorted(
        (c.variable, c.value["present"]) for c in loaded.claims
    )


def test_the_inventory_names_the_editor_state_loupe_keeps() -> None:
    assert ADAPTER.inventory["state_keys"] == ["amber_mentions"]
