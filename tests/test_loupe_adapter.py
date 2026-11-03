"""The loupe claim-mode adapter: requests become validated CaseResults, rebuilt identically
(amber-1ah.7). loupe itself is not imported; these tests call the adapter as loupe does."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from amber.cases import load_case_result
from amber.conformance import fixtures as fx
from amber.loupe import SCHEMA_VERSION, AdapterError, AmberClaimsAdapter

TEXT = fx.TEXT
CONFIG_SHA = "c" * 64


def _configuration(**changes: Any) -> dict[str, Any]:
    project = {
        "variable_set": "amber.conformance.fixtures:NOTE_VARIABLES",
        "variable_set_ref": fx.NOTE_VARIABLES.schema_ref,
        "dataset": fx.DATASET,
        "dataset_manifest_hash": "synthetic-manifest",
        "sensitivity": "synthetic",
        "policy_version": "synthetic-policy@1",
        "destination": "loupe-institution",
        "zone": "institution",
        **changes,
    }
    return {
        "name": "amber-claims",
        "synthetic": True,
        "adapter": "amber.loupe:AmberClaimsAdapter",
        "concepts": [{"concept_id": "unused"}],
        "axes": {"assertion": {"values": ["affirmed"], "default": "affirmed"}},
        "project_configuration": project,
    }


ADAPTER = AmberClaimsAdapter(_configuration())


def _example(**metadata: Any) -> dict[str, Any]:
    fields = {"patient_id": fx.SOURCE.patient_id, "external_id": "conformance-note-1", **metadata}
    record = {"record_id": fx.SOURCE.source_id, "text": TEXT, "metadata": fields}
    return {"example_id": fx.SOURCE.source_id, "source_text": TEXT, "source_record": record}


def _span(quote: str, occurrence: int = 0) -> dict[str, Any]:
    start = -1
    for _ in range(occurrence + 1):
        start = TEXT.index(quote, start + 1)
    return {"start": start, "end": start + len(quote), "quote": quote}


def _answer(spans: list[dict[str, Any]] | None = None, **claim: Any) -> dict[str, Any]:
    spans = spans or [_span("mass")]
    body = {
        "value": {"present": True},
        "rationale": "Invented rationale.",
        "evidence": spans,
        "fields": {"present": [0]},
        **claim,
    }
    return _request({fx.NOTE_VARIABLES.name: {"status": "answered", "claims": [body]}})


def _request(variables: dict[str, Any]) -> dict[str, Any]:
    return {"reviewer": "Synthetic Reviewer", "whole_note_complete": True, "variables": variables}


def _status(status: str, **entry: Any) -> dict[str, Any]:
    return _request({fx.NOTE_VARIABLES.name: {"status": status, **entry}})


def _build(request: dict[str, Any], example: dict[str, Any] | None = None) -> dict[str, Any]:
    return ADAPTER.build_result(request, example or _example(), ADAPTER.inventory, CONFIG_SHA)


def _code(request: dict[str, Any], **kwargs: Any) -> str:
    with pytest.raises(AdapterError) as failure:
        _build(request, **kwargs)
    return failure.value.code


# Building ----------------------------------------------------------------------------------


def test_an_answer_becomes_a_validated_case_result() -> None:
    result = _build(_answer())
    assert result["schema_version"] == SCHEMA_VERSION
    assert result["record_id"] == fx.SOURCE.source_id
    assert (result["processing_state"], result["issues"]) == ("ok", [])
    payload = result["case_result"]
    loaded = load_case_result(
        payload,
        case=fx.note_case(fx.SOURCE.source_id),
        source=fx.SOURCE,
        variables=fx.NOTE_VARIABLES,
    )
    (claim,) = loaded.claims
    assert claim.provenance.producer == "human:Synthetic-Reviewer"
    assert claim.provenance.destination == "loupe-institution"
    assert loaded.run_id == claim.provenance.run_id
    assert loaded.variable_outcomes[fx.NOTE_VARIABLES.name].final_claim_ids == [claim.claim_id]
    assert json.loads(json.dumps(result)) == result


def test_the_same_request_always_builds_the_same_result() -> None:
    first, second = _build(_answer()), _build(_answer())
    assert first == second
    assert _build(_answer(value={"present": False})) != first
    other = ADAPTER.build_result(_answer(), _example(), ADAPTER.inventory, "d" * 64)
    assert other["case_result"]["run_id"] != first["case_result"]["run_id"]


def test_unicode_and_crlf_offsets_are_code_points() -> None:
    accent = _span("e\u0301vidence")
    assert "\r\n" in TEXT[: accent["start"]]
    result = _build(_answer([_span("\U0001f642 seen"), accent]))
    spans = {
        (e["start"], e["end"])
        for e in result["case_result"]["evidence"]
        if e["kind"] == "inclusion"
    }
    assert (accent["start"], accent["end"]) in spans
    assert TEXT[accent["start"] : accent["end"]] == "e\u0301vidence"


def test_repeated_quotes_are_resolved_by_their_offsets() -> None:
    second = _span("mass", occurrence=1)
    result = _build(_answer([second]))
    (inclusion,) = [e for e in result["case_result"]["evidence"] if e["kind"] == "inclusion"]
    assert inclusion["start"] == second["start"] != TEXT.index("mass")


@pytest.mark.parametrize(
    ("span", "code"),
    [
        ({"start": 6, "end": 10, "quote": "liver"}, "quote_mismatch"),
        ({"start": 7, "end": 11, "quote": "mass"}, "quote_mismatch"),
        ({"start": -1, "end": 3, "quote": "Exa"}, "invalid_span"),
        ({"start": 0, "end": len(TEXT) + 1, "quote": TEXT}, "invalid_span"),
        ({"start": 4, "end": 4, "quote": ""}, "invalid_span"),
        ({"start": "6", "end": 10, "quote": "mass"}, "invalid_span"),
        ({"start": 6, "end": 10}, "invalid_span"),
    ],
    ids=["other_text", "shifted", "negative", "past_end", "empty", "string_offset", "no_quote"],
)
def test_forged_offsets_are_rejected(span: dict[str, Any], code: str) -> None:
    assert _code(_answer([span])) == code


def test_missing_field_evidence_is_rejected() -> None:
    assert _code(_answer(fields={})) == "missing_field_evidence"
    assert _code(_answer(fields={"present": [3]})) == "invalid_field_evidence"
    assert _code(_answer(fields={"absent": [0]})) == "invalid_context"
    assert _code(_answer(value={"present": "yes"})) == "invalid_schema"
    assert _code(_answer(evidence=[])) == "claim_evidence_required"


_CLAIMS = _answer()["variables"][fx.NOTE_VARIABLES.name]["claims"]
_ABSENT = _status("not_mentioned", reason="r")


@pytest.mark.parametrize(
    ("request_", "code"),
    [
        (_status("failed", reason="x"), "invalid_outcome"),
        (_status("answered"), "invalid_outcome"),
        (_status("not_mentioned"), "reason_required"),
        (_status("not_mentioned", reason="r", claims=_CLAIMS), "invalid_outcome"),
        (_status("conflicting_evidence", reason="r"), "invalid_outcome"),
        (_status("insufficient_evidence", reason="r", evidence=[_span("mass")]), "invalid_outcome"),
        ({**_ABSENT, "whole_note_complete": False}, "whole_note_review_required"),
        ({**_ABSENT, "reviewer": " "}, "reviewer_required"),
        ({**_ABSENT, "variables": {}}, "every_final_variable_required"),
    ],
    ids=["failed", "answered_without_claims", "no_reason", "claims_on_non_answer",
         "conflict_without_evidence", "evidence_on_insufficient", "partial_review",
         "no_reviewer", "missing_variable"],
)  # fmt: skip
def test_invalid_outcomes_are_rejected(request_: dict[str, Any], code: str) -> None:
    assert _code(request_) == code


def test_unanswered_outcomes_record_whole_note_review() -> None:
    absent = _build(_status("not_mentioned", reason="Invented: nothing relevant."))
    outcome = absent["case_result"]["variable_outcomes"][fx.NOTE_VARIABLES.name]["outcome"]
    assert outcome["status"] == "not_mentioned"
    assert outcome["reviewed_source_ids"] == [fx.SOURCE.source_id]

    partial = _build(_status("insufficient_evidence", reason="Invented: unclear."))
    assert partial["processing_state"] == "partial"
    assert partial["issues"] == [{"code": "insufficient_evidence", "variable": "conformance_note"}]

    conflict = _build(
        _status(
            "conflicting_evidence",
            reason="Invented: two readings.",
            evidence=[_span("mass"), _span("mass", 1)],
        )
    )
    cited = conflict["case_result"]["variable_outcomes"]["conformance_note"]["outcome"]
    assert len(cited["evidence_ids"]) == 2


# Records and configuration -----------------------------------------------------------------


def test_the_record_must_be_the_amber_source() -> None:
    assert _code(_answer(), example=_example(external_id="another-note")) == (
        "source_identity_mismatch"
    )
    broken = _example()
    broken["source_record"]["metadata"] = "x"
    assert _code(_answer(), example=broken) == "invalid_record"
    result = _build(_answer(), _example(split="test", case_id="case-7"))
    assert result["case_result"]["case_id"] == "case-7"


def test_configuration_is_checked() -> None:
    with pytest.raises(AdapterError, match="variable_set_mismatch"):
        AmberClaimsAdapter(_configuration(variable_set_ref="other"))
    with pytest.raises(AdapterError, match="unsupported_variable_level"):
        AmberClaimsAdapter(
            _configuration(
                variable_set="amber.conformance.fixtures:VARIABLES",
                variable_set_ref=fx.VARIABLES.schema_ref,
            )
        )
    with pytest.raises(AdapterError, match="variable_set_unavailable"):
        AmberClaimsAdapter(_configuration(variable_set="amber.nowhere:SET"))
    with pytest.raises(AdapterError, match="invalid_configuration"):
        AmberClaimsAdapter(_configuration(zone="moon"))


def test_the_inventory_describes_the_variables() -> None:
    (variable,) = ADAPTER.inventory["variables"]
    assert variable["name"] == fx.NOTE_VARIABLES.name
    assert variable["fields"] == ["present"]
    assert variable["evidence_policy"] == "field"
    assert ADAPTER.inventory["mode"] == "claims"
    assert ADAPTER.binding()["variable_set_ref"] == fx.NOTE_VARIABLES.schema_ref
    assert ADAPTER.attribute_values == {"assertion": ["affirmed"]}


# Validation of stored results --------------------------------------------------------------


def test_stored_results_validate_and_reload_into_the_editor() -> None:
    result = _build(_answer([_span("mass", 1)]))
    assert ADAPTER.validate_result(copy.deepcopy(result), _example()) == result
    items = ADAPTER.editable(result["mentions"])
    variables = {
        item["variable"]: {k: v for k, v in item.items() if k != "variable"} for item in items
    }
    rebuilt = ADAPTER.build_result(_request(variables), _example(), ADAPTER.inventory, CONFIG_SHA)
    assert rebuilt == result


@pytest.mark.parametrize(
    "edit",
    [
        lambda r: r["case_result"]["claims"][0]["value"].update(present=False),
        lambda r: r["mentions"][0]["claims"][0]["value"].update(present=False),
        lambda r: r["producer"].update(reviewer="Someone Else"),
        lambda r: r.update(processing_state="partial"),
        lambda r: r.update(schema_version="loupe-annotation-v1"),
        lambda r: r["mentions"][0]["claims"][0]["evidence"][0].update(start=7),
    ],
    ids=["claim_value", "editor_value", "reviewer", "state", "schema", "offset"],
)
def test_tampered_results_are_rejected(edit: Any) -> None:
    result = copy.deepcopy(_build(_answer()))
    edit(result)
    with pytest.raises(AdapterError):
        ADAPTER.validate_result(result, _example())


def test_a_result_for_another_record_is_rejected() -> None:
    result = _build(_answer())
    other = _example()
    other["example_id"] = "another-record"
    with pytest.raises(AdapterError, match="source_identity_mismatch"):
        ADAPTER.validate_result(result, other)
