"""JSON record forms: schema versions, timestamps, budgets, and VariableSet JSON (contract
v0.1 sections 5.2, 5.3, 6.3, 6.6; catalog WIR-02 for JSON records, WIR-13 budget rules,
WIR-14)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import Field, ValidationError

from amber.cases import CaseValidationError, create_case_result, load_case_result
from amber.conformance import fixtures as fx
from amber.conformance.golden import RECORDS, REQUIRED_KEYS, golden, reserialize
from amber.examples import load_example
from amber.graph import EvidenceGraph
from amber.policy import Decision
from amber.schemas import (
    AnswerModel,
    AttributeRequirement,
    Budget,
    CaseOutcome,
    Provenance,
    Sensitivity,
    Usage,
    Variable,
    VariableOutcome,
    VariableSet,
)
from amber.schemas._wire import check_schema_version, json_timestamp
from amber.wire import WireFormatError, load_variable_set, variable_set_json

# WIR-14 ------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(RECORDS))
def test_records_match_their_golden_bytes_and_required_keys(name: str) -> None:
    text = RECORDS[name]()
    assert tuple(json.loads(text)) == REQUIRED_KEYS[name]
    assert text == golden(name), "a JSON record changed: that is a wire-format change"
    assert reserialize(name, text) == text


# Schema versions (WIR-02 for JSON records) -------------------------------------------------


@pytest.mark.parametrize("value", ["amber/0.0", "amber/0.1"])
def test_this_major_at_an_equal_or_lower_minor_is_accepted(value: str) -> None:
    assert check_schema_version(value) == "amber/0.1"


@pytest.mark.parametrize(
    "value", ["amber/0.2", "amber/1.0", "amber/1.1", "0.1", "amber/00.1", "amber/0", None, 1]
)
def test_other_versions_are_rejected(value: Any) -> None:
    with pytest.raises(ValueError):
        check_schema_version(value)


def test_older_minor_records_load_and_serialize_at_the_current_version() -> None:
    payload = json.loads(golden("example"))
    payload["schema_version"] = "amber/0.0"
    example = load_example(payload, case=fx.case(), source=fx.SOURCE, variables=fx.VARIABLES)
    assert example.model_dump_json() == golden("example")
    payload = json.loads(golden("case_result"))
    for version in ("amber/0.2", "amber/1.0", "0.1"):
        with pytest.raises(CaseValidationError, match="invalid_schema"):
            load_case_result(
                {**payload, "schema_version": version},
                case=fx.case(),
                source=fx.SOURCE,
                variables=fx.VARIABLES,
            )
    with pytest.raises(ValidationError):
        Decision.model_validate({**json.loads(golden("decision")), "schema_version": "amber/1.0"})


# Timestamps --------------------------------------------------------------------------------


def test_json_timestamps_are_utc_z_with_six_fractional_digits() -> None:
    offset = datetime(2026, 1, 1, 5, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    assert json_timestamp(offset) == "2026-01-01T00:00:00.000000Z"
    assert json_timestamp(datetime(2026, 1, 1, 0, 0, 0, 7, tzinfo=UTC)) == (
        "2026-01-01T00:00:00.000007Z"
    )
    assert json_timestamp(datetime(2026, 1, 1)) == "2026-01-01T00:00:00.000000"
    provenance = Provenance.model_validate(
        {**fx.provenance("tool:x@1").model_dump(), "created_at": offset}
    )
    assert json.loads(provenance.model_dump_json())["created_at"] == "2026-01-01T00:00:00.000000Z"
    assert provenance.model_dump()["created_at"] == offset


# Budgets (WIR-13 rules on CaseResult) ------------------------------------------------------


def _result(*, budget: Budget | None, usage: Usage | None, failure_kind: str | None = None) -> Any:
    run_id = fx.RUN_IDS[0]
    stamped = fx.provenance("tool:extractor@1", run_id)
    if failure_kind is None:
        outcome = CaseOutcome(
            status="insufficient_evidence", reason="Synthetic.", provenance=stamped
        )
    else:
        outcome = CaseOutcome(
            status="failed", reason="Synthetic.", failure_kind=failure_kind, provenance=stamped
        )
    graph = EvidenceGraph(
        source=fx.SOURCE, variables=fx.VARIABLES, sensitivity=Sensitivity.synthetic
    )
    return create_case_result(
        fx.case(),
        graph=graph,
        run_id=run_id,
        budget=budget,
        usage=usage,
        variable_outcomes={
            name: VariableOutcome(variable=name, outcome=outcome) for name in ("finding", "summary")
        },
    )


def test_budget_and_usage_round_trip() -> None:
    result = _result(
        budget=Budget(max_calls=3, max_tokens=1000, max_seconds=2.5),
        usage=Usage(calls=3, tokens=1000, retries=4, seconds=2.5),
    )
    payload = json.loads(result.model_dump_json())
    assert payload["budget"] == {
        "max_calls": 3,
        "max_tokens": 1000,
        "max_retries": None,
        "max_seconds": 2.5,
    }
    loaded = load_case_result(payload, case=fx.case(), source=fx.SOURCE, variables=fx.VARIABLES)
    assert loaded == result
    assert _result(budget=None, usage=Usage(calls=99)).usage == Usage(calls=99)


@pytest.mark.parametrize(
    "usage",
    [Usage(calls=4), Usage(tokens=1001), Usage(retries=1), Usage(seconds=2.6)],
)
def test_an_over_budget_result_must_record_failed_budget(usage: Usage) -> None:
    budget = Budget(max_calls=3, max_tokens=1000, max_retries=0, max_seconds=2.5)
    with pytest.raises(CaseValidationError, match="budget_exceeded"):
        _result(budget=budget, usage=usage)
    with pytest.raises(CaseValidationError, match="budget_exceeded"):
        _result(budget=budget, usage=usage, failure_kind="provider")
    recorded = _result(budget=budget, usage=usage, failure_kind="budget")
    payload = json.loads(recorded.model_dump_json())
    loaded = load_case_result(payload, case=fx.case(), source=fx.SOURCE, variables=fx.VARIABLES)
    assert {item.outcome.failure_kind for item in loaded.variable_outcomes.values()} == {"budget"}

    within = json.loads(_result(budget=budget, usage=Usage()).model_dump_json())
    within["usage"] = usage.model_dump(mode="json")
    with pytest.raises(CaseValidationError, match="budget_exceeded"):
        load_case_result(within, case=fx.case(), source=fx.SOURCE, variables=fx.VARIABLES)


@pytest.mark.parametrize(
    ("field", "value"),
    [("budget", {"max_calls": -1}), ("usage", {"calls": -1}), ("usage", {"seconds": "inf"}),
     ("budget", {"max_calls": 1.5}), ("usage", {"extra": 1})],
)  # fmt: skip
def test_invalid_budgets_and_usage_are_rejected(field: str, value: dict[str, Any]) -> None:
    payload = json.loads(golden("case_result"))
    payload[field] = value
    with pytest.raises(CaseValidationError, match="invalid_schema"):
        load_case_result(payload, case=fx.case(), source=fx.SOURCE, variables=fx.VARIABLES)


# VariableSet JSON --------------------------------------------------------------------------


class _Other(AnswerModel):
    present: bool = Field(strict=True)
    note: int


def _with_requirements() -> VariableSet:
    return VariableSet.create(
        name="wire-set",
        version="2",
        variables=(
            Variable(
                name="finding",
                level="mention",
                answer_model=fx.FindingAnswer,
                instructions="Invented.",
                mention_types=frozenset({"finding", "mass"}),
                requires_attributes=(
                    AttributeRequirement(category="negation", values=(True, 1, "yes")),
                ),
                requires_section=frozenset({"findings"}),
                final=True,
            ),
        ),
    )


def test_variable_sets_round_trip_through_json_with_their_answer_models() -> None:
    for variables in (fx.VARIABLES, _with_requirements()):
        payload = json.loads(json.dumps(variable_set_json(variables)))
        assert load_variable_set(payload, answer_models=[fx.FindingAnswer, _Other]) == variables
    entry = variable_set_json(fx.VARIABLES)["variables"]["finding"]["answer_model"]
    assert set(entry) == {"schema_ref", "json_schema"}
    assert entry["json_schema"] == fx.FindingAnswer.model_json_schema()


def _summary(payload: dict[str, Any]) -> dict[str, Any]:
    return payload["variables"]["summary"]  # type: ignore[no-any-return]


def _model(payload: dict[str, Any]) -> dict[str, Any]:
    return _summary(payload)["answer_model"]  # type: ignore[no-any-return]


def _payload() -> dict[str, Any]:
    return json.loads(json.dumps(variable_set_json(fx.VARIABLES)))


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda p: p.update(schema_ref="0" * 64), "variable_set_mismatch"),
        (lambda p: _summary(p).update(instructions="Edited."), "variable_set_mismatch"),
        (lambda p: _model(p).update(schema_ref="x:Y@0"), "unknown_answer_model"),
        (lambda p: _model(p)["json_schema"].update(title="Other"), "answer_model_mismatch"),
        (lambda p: p.update(schema_version="amber/1.0"), "invalid_schema"),
        (lambda p: p.update(extra=True), "invalid_schema"),
        (lambda p: _summary(p).update(name="other"), "invalid_schema"),
        (lambda p: _summary(p).update(level="case"), "invalid_schema"),
    ],
)  # fmt: skip
def test_variable_set_json_is_checked_against_the_answer_models(mutate: Any, code: str) -> None:
    payload = _payload()
    mutate(payload)
    with pytest.raises(WireFormatError) as failure:
        load_variable_set(payload, answer_models=[fx.FindingAnswer])
    assert failure.value.code == code
