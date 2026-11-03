"""labels_only_view and schema closedness (contract v0.1 sections 1.4, 4.4, 5.2; catalog
POL-06, ANN-08)."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Literal

import pytest
from pydantic import Field

from amber.cases import create_case_result
from amber.conformance import fixtures as fx
from amber.examples import load_label_record
from amber.graph import EvidenceGraph
from amber.ids import new_ulid
from amber.labels import EventLabel, LabelsOnlyError, is_closed_schema, labels_only_view
from amber.schemas import (
    AnswerModel,
    CaseOutcome,
    CaseResult,
    InferenceEvidence,
    MentionReading,
    Sensitivity,
    Variable,
    VariableOutcome,
    VariableSet,
)

RUN = new_ulid()
LEAK = "mass \U0001f642 seen in the liver.\r\nNo e\u0301vidence"


def _windows(text: str, size: int = 8) -> set[str]:
    return {text[index : index + size] for index in range(len(text) - size + 1)}


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for key, item in value.items() for text in (*_strings(key), *_strings(item))]
    if isinstance(value, list | tuple):
        return [text for item in value for text in _strings(item)]
    return []


def _contains_source_text(value: Any) -> bool:
    windows = _windows(fx.TEXT)
    return any(window in text for text in _strings(value) for window in windows)


def _assert_no_source_text(serialized: str) -> None:
    assert not _contains_source_text(json.loads(serialized))


# Closedness --------------------------------------------------------------------------------


class _Closed(AnswerModel):
    present: bool
    count: int | None
    score: float
    grade: Literal["low", "high"]
    seen_on: date
    at: datetime
    flags: list[bool]
    nested: dict[str, Literal["a", "b"]]


class _OpenString(AnswerModel):
    present: bool
    note: str


class _OpenNested(AnswerModel):
    labels: list[str]


class _OpenMapping(AnswerModel):
    values: dict[str, str]


class _Inner(AnswerModel):
    value: Literal["x"]


class _Outer(AnswerModel):
    inner: _Inner
    optional: _Inner | None = None


def test_closed_schemas_admit_only_fixed_values() -> None:
    assert is_closed_schema(_Closed.model_json_schema())
    assert is_closed_schema(_Outer.model_json_schema())
    assert is_closed_schema(fx.FindingAnswer.model_json_schema())
    for model in (_OpenString, _OpenNested, _OpenMapping):
        assert not is_closed_schema(model.model_json_schema())
    assert not is_closed_schema({"type": "string", "format": "email"})
    assert not is_closed_schema({"type": "object"})
    assert not is_closed_schema({"$ref": "#/$defs/Missing"})
    assert not is_closed_schema({})


# Results (POL-06) --------------------------------------------------------------------------


def _result(
    *, variables: VariableSet = fx.VARIABLES, outcome_status: str = "answered", **values: Any
) -> CaseResult:
    source = fx.SOURCE
    graph = EvidenceGraph(source=source, variables=variables, sensitivity=Sensitivity.synthetic)
    stamped = fx.provenance("model:labels-test@1", RUN)
    reading = MentionReading(
        reading_id=new_ulid(),
        mention_id=fx.MENTION.mention_id,
        value=LEAK,
        provenance=stamped,
    )
    graph.register_mention(fx.MENTION, [reading])
    graph.register_evidence(fx.POSITION)
    graph.register_evidence(fx.SENTENCE)
    finding = graph.commit_claim(
        "finding",
        values.get("finding", {"present": True}),
        [fx.POSITION.evidence_id],
        f"The note says {LEAK}",
        provenance=stamped,
        reading_id=reading.reading_id,
    )
    bridge = InferenceEvidence(
        evidence_id=new_ulid(),
        rationale=f"Derived from {LEAK}",
        inputs=[finding.claim_id, fx.SENTENCE.evidence_id],
        trace_id=None,
        span_id=None,
        provenance=stamped,
    )
    graph.register_evidence(bridge)
    summary = graph.commit_claim(
        "summary",
        values.get("summary", {"present": True}),
        [bridge.evidence_id],
        f"Summary of {LEAK}",
        provenance=stamped,
    )
    answered = CaseOutcome(status="answered", provenance=stamped)
    other = CaseOutcome(
        status=outcome_status,  # type: ignore[arg-type]
        reason=f"Reviewer wrote {LEAK}",
        failure_kind="provider" if outcome_status == "failed" else None,
        reviewed_source_ids=[source.source_id] if outcome_status == "not_mentioned" else [],
        provenance=stamped,
    )
    summary_outcome = (
        VariableOutcome(variable="summary", outcome=answered, final_claim_ids=[summary.claim_id])
        if outcome_status == "answered"
        else VariableOutcome(variable="summary", outcome=other)
    )
    return create_case_result(
        fx.case(),
        graph=graph,
        run_id=RUN,
        variable_outcomes={
            "finding": VariableOutcome(
                variable="finding", outcome=answered, final_claim_ids=[finding.claim_id]
            ),
            "summary": summary_outcome,
        },
    )


def test_a_result_reduces_to_one_label_per_final_variable_without_source_text() -> None:
    result = _result()
    assert _contains_source_text(result.model_dump(mode="json"))

    labels = labels_only_view(result, case=fx.case(), variables=fx.VARIABLES)

    assert [(label.variable, label.outcome_status, label.value) for label in labels] == [
        ("finding", "answered", {"present": True}),
        ("summary", "answered", {"present": True}),
    ]
    assert {label.producer for label in labels} == {"model:labels-test@1"}
    assert {label.split for label in labels} == {"dev"}
    assert {label.tier for label in labels} == {"silver"}
    _assert_no_source_text(json.dumps([label.model_dump(mode="json") for label in labels]))
    for label in labels:
        assert (
            load_label_record(label.model_dump(mode="json"), case=fx.case(), variables=fx.VARIABLES)
            == label
        )


@pytest.mark.parametrize("status", ["not_mentioned", "insufficient_evidence"])
def test_non_answers_keep_status_and_producer_but_no_value(status: str) -> None:
    labels = labels_only_view(
        _result(outcome_status=status), case=fx.case(), variables=fx.VARIABLES
    )
    summary = next(label for label in labels if label.variable == "summary")
    assert (summary.outcome_status, summary.value) == (status, None)
    assert summary.producer == "model:labels-test@1"
    _assert_no_source_text(json.dumps([label.model_dump(mode="json") for label in labels]))


def test_failed_variables_yield_no_label() -> None:
    labels = labels_only_view(
        _result(outcome_status="failed"), case=fx.case(), variables=fx.VARIABLES
    )
    assert [label.variable for label in labels] == ["finding"]


class _FreeText(AnswerModel):
    present: bool
    note: str = Field(strict=True)


def test_open_answer_fields_cannot_be_reduced() -> None:
    variables = VariableSet.create(
        name=fx.VARIABLES.name,
        version="open",
        variables=(
            fx.VARIABLES.variables["finding"],
            Variable(
                name="summary",
                level="note",
                answer_model=_FreeText,
                instructions="Invented.",
                inputs=frozenset({"finding"}),
                final=True,
            ),
        ),
    )
    result = _result(variables=variables, summary={"present": True, "note": LEAK})
    with pytest.raises(LabelsOnlyError, match="open_answer_fields"):
        labels_only_view(result, case=fx.case(), variables=variables)


def test_labels_need_the_authoritative_case_and_set() -> None:
    result = _result()
    with pytest.raises(LabelsOnlyError, match="invalid_context"):
        labels_only_view(result, case=fx.case("other-case"), variables=fx.VARIABLES)
    other = VariableSet.model_validate({**fx.VARIABLES.model_dump(), "version": "9"})
    with pytest.raises(LabelsOnlyError, match="variable_set_mismatch"):
        labels_only_view(result, case=fx.case(), variables=other)
    with pytest.raises(LabelsOnlyError, match="invalid_context"):
        labels_only_view(result)  # type: ignore[call-overload]


def test_labels_record_the_case_split() -> None:
    case = fx.case(split=None)
    labels = labels_only_view(_result(), case=case, variables=fx.VARIABLES)
    assert {label.split for label in labels} == {None}


# Events (ANN-08) ---------------------------------------------------------------------------


def test_events_reduce_to_who_did_what_and_when() -> None:
    log, ids = fx.blind_streams()
    log.add(
        "reading.update",
        fx.REVIEWER_A,
        "reviewer",
        stream="a",
        target_id=ids["reading"],
        reason=f"Corrected: {LEAK}",
        after={
            "reading": {
                "reading_id": new_ulid(),
                "mention_id": fx.MENTION.mention_id,
                "value": LEAK,
                "provenance": fx.provenance(fx.REVIEWER_A).model_dump(mode="json"),
                "status": "proposed",
            }
        },
    )
    assert _contains_source_text([event.model_dump(mode="json") for event in log.events])

    labels = labels_only_view(log.events)

    assert len(labels) == len(log.events)
    assert set(EventLabel.model_fields) == {
        "event_id",
        "case_id",
        "variable",
        "actor",
        "role",
        "round",
        "stream",
        "action",
        "target_id",
        "at",
        "external_ref",
    }
    assert [label.event_id for label in labels] == [event.event_id for event in log.events]
    assert labels[-1].action == "reading.update" and labels[-1].stream == "a"
    serialized = json.dumps([label.model_dump(mode="json") for label in labels])
    for key in ("before", "after", "reason"):
        assert f'"{key}"' not in serialized
    _assert_no_source_text(serialized)


def test_event_views_reject_other_records_and_context() -> None:
    log, _ = fx.silver_import()
    with pytest.raises(LabelsOnlyError, match="invalid_schema"):
        labels_only_view([log.events[0].model_dump()])  # type: ignore[list-item]
    with pytest.raises(LabelsOnlyError, match="invalid_context"):
        labels_only_view(log.events, case=fx.case())  # type: ignore[call-overload]
