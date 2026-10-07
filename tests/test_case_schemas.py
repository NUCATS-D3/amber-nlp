"""Synthetic tests for immutable case context and explicit outcome records."""

from typing import Any

import pytest
from pydantic import ValidationError

from amber.schemas import Case, CaseOutcome, Sensitivity

from ._claim_helpers import make_provenance, make_source


def _case_payload() -> dict[str, Any]:
    source = make_source()
    return {
        "case_id": "synthetic-case-1",
        "task": "oncology_current_progression",
        "patient_id": source.patient_id,
        "source_ids": [source.source_id],
        "sensitivity": "synthetic",
    }


def _outcome_payload(status: str = "insufficient_evidence") -> dict[str, Any]:
    return {
        "status": status,
        "reason": "Invented evidence does not establish a complete answer.",
        "failure_kind": None,
        "reviewed_source_ids": [],
        "evidence_ids": [],
        "provenance": make_provenance().model_dump(mode="json"),
    }


def test_case_preserves_identity_and_freezes_source_membership() -> None:
    payload = _case_payload()
    case = Case.model_validate(payload)
    payload["source_ids"].clear()

    assert case.sensitivity is Sensitivity.synthetic
    assert case.source_ids == [make_source().source_id]
    assert Case.model_validate_json(case.model_dump_json()) == case
    with pytest.raises(TypeError):
        case.source_ids.clear()
    with pytest.raises(ValidationError):
        case.case_id = "changed"


@pytest.mark.parametrize(
    "changes",
    [
        {"case_id": " "},
        {"case_id": 1},
        {"task": ""},
        {"patient_id": "\t"},
        {"source_ids": []},
        {"source_ids": ["one", "two"]},
        {"source_ids": ["one", "one"]},
        {"source_ids": [" "]},
        {"source_ids": [1]},
        {"sensitivity": "public"},
        {"unexpected": "field"},
    ],
)
def test_case_rejects_invalid_identity_or_non_note_scope(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Case.model_validate({**_case_payload(), **changes})


@pytest.mark.parametrize(
    "status",
    ["answered", "not_mentioned", "conflicting_evidence", "insufficient_evidence", "failed"],
)
def test_outcomes_round_trip_with_failure_kind_exactly_for_failure(status: str) -> None:
    payload = _outcome_payload(status)
    if status == "answered":
        payload["reason"] = None
    if status == "conflicting_evidence":
        payload["evidence_ids"] = ["synthetic-evidence"]
    if status == "failed":
        payload["failure_kind"] = "provider"
    outcome = CaseOutcome.model_validate(payload)

    assert outcome.status == status
    assert outcome.failure_kind == ("provider" if status == "failed" else None)
    assert CaseOutcome.model_validate_json(outcome.model_dump_json()) == outcome


@pytest.mark.parametrize("status", ["not_mentioned", "insufficient_evidence", "failed"])
@pytest.mark.parametrize("reason", [None, "", " \r\n", 1])
def test_non_answered_outcomes_require_nonblank_reason(status: str, reason: Any) -> None:
    payload = _outcome_payload(status)
    payload.update(reason=reason, failure_kind="internal" if status == "failed" else None)
    with pytest.raises(ValidationError):
        CaseOutcome.model_validate(payload)


@pytest.mark.parametrize("status", ["answered", "not_mentioned", "insufficient_evidence"])
def test_non_failed_outcome_cannot_carry_failure_kind(status: str) -> None:
    with pytest.raises(ValidationError):
        CaseOutcome.model_validate({**_outcome_payload(status), "failure_kind": "provider"})


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "failed"},
        {"status": "failed", "failure_kind": "unknown"},
        {"status": "conflicting_evidence"},
        {"status": "no_claim"},
        {"evidence_ids": ["same", "same"]},
        {"evidence_ids": [1]},
        {"evidence_ids": [" "]},
        {"reviewed_source_ids": ["same", "same"]},
        {"reviewed_source_ids": [" "]},
        {"reason": ""},
        {"unexpected": "field"},
    ],
)
def test_outcome_rejects_inconsistent_or_malformed_fields(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        CaseOutcome.model_validate({**_outcome_payload(), **changes})


def test_outcome_copies_and_freezes_ids_and_nested_provenance() -> None:
    payload = _outcome_payload()
    payload["reviewed_source_ids"] = ["synthetic-source"]
    payload["evidence_ids"] = ["synthetic-evidence"]
    outcome = CaseOutcome.model_validate(payload)
    payload["evidence_ids"].clear()
    payload["provenance"]["prompt_versions"].clear()

    assert outcome.evidence_ids == ["synthetic-evidence"]
    assert outcome.provenance.prompt_versions == {"extractor": "prompts:/synthetic/1"}
    with pytest.raises(TypeError):
        outcome.evidence_ids.append("other")
    with pytest.raises(TypeError):
        outcome.reviewed_source_ids.clear()
    with pytest.raises(TypeError):
        outcome.provenance.prompt_versions.clear()
    with pytest.raises(ValidationError):
        outcome.reason = "changed"


def test_outcome_revalidates_forged_provenance() -> None:
    provenance = make_provenance().model_copy(update={"producer": ""})
    with pytest.raises(ValidationError):
        CaseOutcome.model_validate({**_outcome_payload(), "provenance": provenance})


def test_outcome_default_reference_lists_are_immutable() -> None:
    outcome = CaseOutcome(status="answered", provenance=make_provenance())
    with pytest.raises(TypeError):
        outcome.evidence_ids.append("forged")
    with pytest.raises(TypeError):
        outcome.reviewed_source_ids.append("forged")
