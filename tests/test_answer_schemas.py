import json

import pytest
from pydantic import Field, ValidationError

from amber.schemas import AnswerModel


class StrictBooleanAnswer(AnswerModel):
    finding_present: bool = Field(strict=True)


def test_answer_subclass_accepts_only_a_required_strict_boolean() -> None:
    answer = StrictBooleanAnswer(finding_present=True)
    assert answer.model_dump() == {"finding_present": True}

    for payload in ({}, {"finding_present": 1}, {"finding_present": "true"}):
        with pytest.raises(ValidationError):
            StrictBooleanAnswer.model_validate(payload)


def test_answer_subclass_is_frozen_and_forbids_extras() -> None:
    answer = StrictBooleanAnswer(finding_present=False)
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        StrictBooleanAnswer(
            finding_present=False,
            rationale="not part of the answer",  # type: ignore[call-arg]
        )
    with pytest.raises(ValidationError, match="frozen"):
        answer.finding_present = True  # type: ignore[misc]


def test_answer_round_trips_true_and_false_as_json() -> None:
    for value in (True, False):
        answer = StrictBooleanAnswer(finding_present=value)
        assert StrictBooleanAnswer.model_validate_json(answer.model_dump_json()) == answer
        assert json.loads(answer.model_dump_json()) == {"finding_present": value}


@pytest.mark.parametrize(
    "payload",
    [
        '{"finding_present": null}',
        '{"finding_present": 1}',
        '{"finding_present": 1.0}',
        '{"finding_present": "true"}',
        '{"finding_present": true, "extra": false}',
    ],
)
def test_answer_rejects_invalid_json_payloads(payload: str) -> None:
    with pytest.raises(ValidationError):
        StrictBooleanAnswer.model_validate_json(payload)


def test_answer_policy_is_inherited_by_scalar_field_subclass() -> None:
    class ScalarAnswer(AnswerModel):
        value: int

    scalar = ScalarAnswer(value=1)
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ScalarAnswer(value=1, extra=True)  # type: ignore[call-arg]
    with pytest.raises(ValidationError, match="frozen"):
        scalar.value = 2  # type: ignore[misc]
