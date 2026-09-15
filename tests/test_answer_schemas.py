import json

import pytest
from pydantic import ValidationError

from amber.schemas import (
    ONCOLOGY_CURRENT_PROGRESSION_EVIDENCE_POLICY,
    ONCOLOGY_CURRENT_PROGRESSION_PROTOCOL_VERSION,
    ONCOLOGY_CURRENT_PROGRESSION_SCOPE,
    ONCOLOGY_CURRENT_PROGRESSION_TASK,
    AnswerModel,
    OncologyCurrentProgressionAnswer,
)


def test_current_progression_answer_accepts_only_a_required_strict_boolean() -> None:
    answer = OncologyCurrentProgressionAnswer(progression_or_recurrence=True)
    assert answer.model_dump() == {"progression_or_recurrence": True}

    for payload in ({}, {"progression_or_recurrence": 1}, {"progression_or_recurrence": "true"}):
        with pytest.raises(ValidationError):
            OncologyCurrentProgressionAnswer.model_validate(payload)


def test_current_progression_answer_is_frozen_and_forbids_extras() -> None:
    answer = OncologyCurrentProgressionAnswer(progression_or_recurrence=False)
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        OncologyCurrentProgressionAnswer(
            progression_or_recurrence=False,
            rationale="not part of the answer",  # type: ignore[call-arg]
        )
    with pytest.raises(ValidationError, match="frozen"):
        answer.progression_or_recurrence = True  # type: ignore[misc]


def test_current_progression_task_metadata_is_frozen_in_code() -> None:
    assert ONCOLOGY_CURRENT_PROGRESSION_TASK == "oncology_current_progression"
    assert ONCOLOGY_CURRENT_PROGRESSION_PROTOCOL_VERSION == "1.0.0"
    assert ONCOLOGY_CURRENT_PROGRESSION_SCOPE == "note"
    assert ONCOLOGY_CURRENT_PROGRESSION_EVIDENCE_POLICY == "field"


def test_current_progression_answer_round_trips_true_and_false_as_json() -> None:
    for value in (True, False):
        answer = OncologyCurrentProgressionAnswer(progression_or_recurrence=value)
        assert (
            OncologyCurrentProgressionAnswer.model_validate_json(answer.model_dump_json()) == answer
        )
        assert json.loads(answer.model_dump_json()) == {"progression_or_recurrence": value}


@pytest.mark.parametrize("value", [None, 0, 1, 0.0, 1.0, "true", "false", "1", "0"])
def test_current_progression_answer_rejects_non_boolean_values(value: object) -> None:
    with pytest.raises(ValidationError):
        OncologyCurrentProgressionAnswer.model_validate({"progression_or_recurrence": value})


@pytest.mark.parametrize(
    "payload",
    [
        '{"progression_or_recurrence": null}',
        '{"progression_or_recurrence": 1}',
        '{"progression_or_recurrence": 1.0}',
        '{"progression_or_recurrence": "true"}',
        '{"progression_or_recurrence": true, "extra": false}',
    ],
)
def test_current_progression_answer_rejects_invalid_json_payloads(payload: str) -> None:
    with pytest.raises(ValidationError):
        OncologyCurrentProgressionAnswer.model_validate_json(payload)


def test_answer_policy_is_inherited_by_scalar_field_subclass() -> None:
    class ScalarAnswer(AnswerModel):
        value: int

    scalar = ScalarAnswer(value=1)
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ScalarAnswer(value=1, extra=True)  # type: ignore[call-arg]
    with pytest.raises(ValidationError, match="frozen"):
        scalar.value = 2  # type: ignore[misc]
