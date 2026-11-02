"""Adversarial tests for per-variable outcomes and variable-set binding on case results
(contract v0.1 sections 3.5, 3.7, and 3.10)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import Field, ValidationError

from amber.cases import CaseValidationError, case_status, create_case_result, load_case_result
from amber.graph import EvidenceGraph
from amber.grounding import exact_quote
from amber.ids import new_ulid
from amber.schemas import (
    AnswerModel,
    Case,
    CaseOutcome,
    CaseResult,
    Inclusion,
    Mention,
    MentionReading,
    Sensitivity,
    Variable,
    VariableSet,
)
from amber.schemas.cases import VariableOutcome
from amber.tools.mention import cite, mention

from ._claim_helpers import make_provenance, make_source, make_task

TEXT = "Grade 2 noted.\r\nStage II \U0001f642 assigned; mass seen."


class GradeAnswer(AnswerModel):
    grade: int = Field(strict=True)


class StageAnswer(AnswerModel):
    stage: str = Field(strict=True)


class FindingAnswer(AnswerModel):
    present: bool = Field(strict=True)


def _variable(name: str, model: type[AnswerModel], **changes: Any) -> Variable:
    return Variable(
        name=name, level="note", answer_model=model, instructions="Invented.", **changes
    )


def _two_finals() -> VariableSet:
    return VariableSet.create(
        name="synthetic_pair",
        version="1.0.0",
        variables=(
            _variable("grade", GradeAnswer, final=True),
            _variable("stage", StageAnswer, final=True),
            _variable("note_finding", FindingAnswer),
            Variable(
                name="finding",
                level="mention",
                answer_model=FindingAnswer,
                instructions="Invented.",
                mention_types=frozenset({"finding"}),
                final=True,
            ),
        ),
    )


def _one_final_with_intermediate() -> VariableSet:
    return VariableSet.create(
        name="synthetic_layered",
        version="1.0.0",
        variables=(
            _variable("grade", GradeAnswer),
            _variable("stage", StageAnswer, inputs=frozenset({"grade"}), final=True),
        ),
    )


def _setup(variables: VariableSet) -> tuple[Case, EvidenceGraph, dict[str, Inclusion]]:
    source = make_source(TEXT)
    graph = EvidenceGraph(source=source, variables=variables, sensitivity=Sensitivity.synthetic)
    leaves: dict[str, Inclusion] = {}
    for key, text in (("grade", "Grade 2 noted."), ("stage", "Stage II \U0001f642 assigned")):
        leaf = exact_quote(source, text)
        assert isinstance(leaf, Inclusion)
        leaves[key] = graph.register_evidence(leaf)  # type: ignore[assignment]
    case = Case(
        case_id="synthetic-case-1",
        task=variables.name,
        patient_id=source.patient_id,
        source_ids=[source.source_id],
        sensitivity=Sensitivity.synthetic,
    )
    return case, graph, leaves


def _commit(graph: EvidenceGraph, variable: str, value: dict[str, Any], leaf: Inclusion) -> str:
    return graph.commit_claim(
        variable, value, [leaf.evidence_id], "Invented rationale.", provenance=make_provenance()
    ).claim_id


def _outcome(status: str, **changes: Any) -> CaseOutcome:
    return CaseOutcome.model_validate(
        {
            "status": status,
            "reason": None if status == "answered" else "Synthetic explanation.",
            "failure_kind": "provider" if status == "failed" else None,
            "provenance": make_provenance(),
            **changes,
        }
    )


def _vo(
    variable: str, status: str, ids: list[str] | None = None, **changes: Any
) -> VariableOutcome:
    return VariableOutcome(
        variable=variable, outcome=_outcome(status, **changes), final_claim_ids=ids or []
    )


def _pair_result() -> tuple[CaseResult, Case, EvidenceGraph, str]:
    case, graph, leaves = _setup(_two_finals())
    grade_id = _commit(graph, "grade", {"grade": 2}, leaves["grade"])
    reviewed = {"reviewed_source_ids": case.source_ids}
    result = create_case_result(
        case,
        graph=graph,
        variable_outcomes={
            "grade": _vo("grade", "answered", [grade_id]),
            "stage": _vo("stage", "not_mentioned", **reviewed),
            "finding": _vo("finding", "not_mentioned", **reviewed),
        },
    )
    return result, case, graph, grade_id


def _load(payload: Any, case: Case, graph: EvidenceGraph, **kwargs: Any) -> CaseResult:
    kwargs.setdefault("variables", graph.variables)
    return load_case_result(payload, case=case, source=graph.source, **kwargs)


def _assert_code(code: str, function: Any, /, *args: Any, **kwargs: Any) -> None:
    with pytest.raises(CaseValidationError) as failure:
        function(*args, **kwargs)
    assert failure.value.code == code


# Schema ------------------------------------------------------------------------------------


def test_variable_outcome_requires_final_ids_exactly_when_answered() -> None:
    with pytest.raises(ValidationError):
        _vo("grade", "answered")
    with pytest.raises(ValidationError):
        _vo("grade", "insufficient_evidence", [new_ulid()])
    with pytest.raises(ValidationError):
        VariableOutcome(variable=" ", outcome=_outcome("failed"))
    outcome = _vo("grade", "failed")
    with pytest.raises(ValidationError):
        outcome.variable = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (("answered", "not_mentioned"), "not_mentioned"),
        (("not_mentioned", "insufficient_evidence"), "insufficient_evidence"),
        (("insufficient_evidence", "conflicting_evidence"), "conflicting_evidence"),
        (("conflicting_evidence", "failed"), "failed"),
    ],
)
def test_case_status_is_the_most_severe_variable_status(
    statuses: tuple[str, str], expected: str
) -> None:
    case, graph, leaves = _setup(_two_finals())
    ids = {
        "grade": _commit(graph, "grade", {"grade": 2}, leaves["grade"]),
        "stage": _commit(graph, "stage", {"stage": "II"}, leaves["stage"]),
    }
    extra = {
        "not_mentioned": {"reviewed_source_ids": case.source_ids},
        "conflicting_evidence": {"evidence_ids": [leaves["grade"].evidence_id]},
    }
    outcomes = {
        name: _vo(
            name, status, [ids[name]] if status == "answered" else [], **extra.get(status, {})
        )
        for name, status in zip(("grade", "stage"), statuses, strict=True)
    }
    outcomes["finding"] = _vo("finding", "insufficient_evidence")
    if expected in ("answered", "not_mentioned"):
        outcomes["finding"] = _vo("finding", "not_mentioned", reviewed_source_ids=case.source_ids)
    result = create_case_result(case, graph=graph, variable_outcomes=outcomes)
    assert case_status(result) == expected


# Several final variables (VAR-13) ---------------------------------------------------------


def test_several_final_variables_each_carry_an_outcome_and_case_fields_are_null() -> None:
    result, case, graph, grade_id = _pair_result()

    assert set(result.variable_outcomes) == {"grade", "stage", "finding"}
    assert result.variable_outcomes["grade"].final_claim_ids == [grade_id]
    assert result.variable_outcomes["stage"].final_claim_ids == []
    assert result.outcome is None and result.final_claim_ids is None
    assert case_status(result) == "not_mentioned"
    assert (result.variable_set, result.variable_set_version) == ("synthetic_pair", "1.0.0")
    assert result.variable_set_ref == _two_finals().schema_ref
    assert _load(json.loads(result.model_dump_json()), case, graph) == result
    with pytest.raises(TypeError):
        result.variable_outcomes["stage"] = result.variable_outcomes["grade"]  # type: ignore[index]


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda p, o: p.update(outcome=o["grade"]["outcome"]), "invalid_outcome"),
        (lambda p, o: p.update(final_claim_ids=[]), "invalid_outcome"),
        (lambda p, o: o.pop("stage"), "invalid_context"),
        (
            lambda p, o: o.update(note_finding={**o["stage"], "variable": "note_finding"}),
            "invalid_context",
        ),
        (lambda p, o: o["stage"].update(variable="grade"), "invalid_context"),
        (lambda p, o: o["stage"].update(extra=True), "invalid_schema"),
        (
            lambda p, o: o["stage"].update(final_claim_ids=o["grade"]["final_claim_ids"]),
            "invalid_final_claims",
        ),
        (lambda p, o: o["stage"].update(outcome=o["grade"]["outcome"]), "invalid_final_claims"),
        (lambda p, o: o["grade"].update(final_claim_ids=[new_ulid()]), "invalid_final_claims"),
        (lambda p, o: p.update(variable_outcomes=[]), "invalid_schema"),
    ],
)
def test_load_revalidates_every_variable_outcome(mutation: Any, code: str) -> None:
    result, case, graph, _ = _pair_result()
    payload = json.loads(result.model_dump_json())
    mutation(payload, payload["variable_outcomes"])
    _assert_code(code, _load, payload, case, graph)


def test_final_claims_must_answer_their_own_variable() -> None:
    case, graph, leaves = _setup(_two_finals())
    stage_id = _commit(graph, "stage", {"stage": "II"}, leaves["stage"])
    _assert_code(
        "invalid_final_claims",
        create_case_result,
        case,
        graph=graph,
        variable_outcomes={
            "grade": _vo("grade", "answered", [stage_id]),
            "stage": _vo("stage", "answered", [stage_id]),
            "finding": _vo("finding", "insufficient_evidence"),
        },
    )


def test_several_final_variables_need_per_variable_outcomes() -> None:
    case, graph, _ = _setup(_two_finals())
    _assert_code(
        "invalid_outcome",
        create_case_result,
        case,
        graph=graph,
        outcome=_outcome("insufficient_evidence"),
        final_claim_ids=[],
    )


# Mention-level final variables (section 3.10 decision 3) -----------------------------------


def _finding_claim(graph: EvidenceGraph) -> str:
    source = graph.source
    span = mention(source.source_id, "mass", None, "finding", sources={source.source_id: source})
    assert isinstance(span, Mention)
    reading = MentionReading(
        reading_id=new_ulid(), mention_id=span.mention_id, provenance=make_provenance()
    )
    graph.register_mention(span, [reading])
    (position,) = cite(span, sources={source.source_id: source})
    graph.register_evidence(position)
    return graph.commit_claim(
        "finding",
        {"present": True},
        [position.evidence_id],
        "Invented rationale.",
        provenance=make_provenance(),
        reading_id=reading.reading_id,
    ).claim_id


def _with_finding(case: Case, finding: VariableOutcome) -> dict[str, VariableOutcome]:
    reviewed = {"reviewed_source_ids": case.source_ids}
    return {
        "grade": _vo("grade", "not_mentioned", **reviewed),
        "stage": _vo("stage", "not_mentioned", **reviewed),
        "finding": finding,
    }


def test_mention_level_variables_are_answered_by_mention_claims() -> None:
    case, graph, _ = _setup(_two_finals())
    claim_id = _finding_claim(graph)
    result = create_case_result(
        case,
        graph=graph,
        variable_outcomes=_with_finding(case, _vo("finding", "answered", [claim_id])),
    )
    assert result.variable_outcomes["finding"].final_claim_ids == [claim_id]
    assert case_status(result) == "not_mentioned"


def test_mention_level_variables_never_report_conflicting_evidence() -> None:
    case, graph, leaves = _setup(_two_finals())
    conflicting = _vo("finding", "conflicting_evidence", evidence_ids=[leaves["stage"].evidence_id])
    _assert_code(
        "invalid_outcome",
        create_case_result,
        case,
        graph=graph,
        variable_outcomes=_with_finding(case, conflicting),
    )


def test_mention_level_not_mentioned_requires_declared_full_review() -> None:
    case, graph, _ = _setup(_two_finals())
    _assert_code(
        "incomplete_review",
        create_case_result,
        case,
        graph=graph,
        variable_outcomes=_with_finding(case, _vo("finding", "not_mentioned")),
    )


# One final variable (VAR-12) ---------------------------------------------------------------


def test_one_final_variable_mirrors_its_outcome_at_case_level() -> None:
    case, graph, leaves = _setup(_one_final_with_intermediate())
    stage_id = _commit(graph, "stage", {"stage": "II"}, leaves["stage"])
    by_fields = create_case_result(
        case, graph=graph, outcome=_outcome("answered"), final_claim_ids=[stage_id]
    )
    by_outcomes = create_case_result(
        case, graph=graph, variable_outcomes={"stage": _vo("stage", "answered", [stage_id])}
    )

    assert by_fields == by_outcomes
    assert case_status(by_fields) == "answered"
    assert by_fields.final_claim_ids == [stage_id]
    assert by_fields.variable_outcomes["stage"].outcome == by_fields.outcome
    _assert_code(
        "invalid_outcome",
        create_case_result,
        case,
        graph=graph,
        variable_outcomes={"stage": _vo("stage", "answered", [stage_id])},
        outcome=_outcome("answered"),
        final_claim_ids=[],
    )


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"outcome": None}, "invalid_outcome"),
        ({"final_claim_ids": None}, "invalid_outcome"),
        ({"outcome": None, "final_claim_ids": None}, "invalid_outcome"),
        ({"final_claim_ids": [new_ulid()]}, "invalid_outcome"),
        ({"final_claim_ids": "bad"}, "invalid_schema"),
    ],
)
def test_one_variable_case_level_fields_must_equal_the_variable_outcome(
    changes: dict[str, Any], code: str
) -> None:
    case, graph, _ = _setup(VariableSet.from_task(make_task()))
    result = create_case_result(
        case,
        graph=graph,
        outcome=_outcome("not_mentioned", reviewed_source_ids=case.source_ids),
        final_claim_ids=[],
    )
    payload = {**json.loads(result.model_dump_json()), **changes}
    _assert_code(code, _load, payload, case, graph)


# Variable-set binding (VAR-17) -------------------------------------------------------------


def test_loading_against_a_different_set_is_a_variable_set_mismatch() -> None:
    result, case, graph, _ = _pair_result()
    payload = json.loads(result.model_dump_json())
    edited = VariableSet.create(
        name="synthetic_pair",
        version="1.0.0",
        variables=(*_two_finals().variables.values(), _variable("extra", GradeAnswer)),
    )
    _assert_code("variable_set_mismatch", _load, payload, case, graph, variables=edited)
    relabeled = VariableSet.model_validate({**_two_finals().model_dump(), "version": "2.0.0"})
    _assert_code("variable_set_mismatch", _load, payload, case, graph, variables=relabeled)
    for key, value in (("variable_set_ref", "0" * 64), ("variable_set", "other")):
        _assert_code("variable_set_mismatch", _load, {**payload, key: value}, case, graph)


def test_task_results_load_with_the_task_or_its_set() -> None:
    task = make_task()
    case, graph, _ = _setup(VariableSet.from_task(task))
    result = create_case_result(
        case, graph=graph, outcome=_outcome("insufficient_evidence"), final_claim_ids=[]
    )
    payload = json.loads(result.model_dump_json())
    assert payload["variable_set_version"] == "task"
    assert _load(payload, case, graph, variables=None, task=task) == result
    assert _load(payload, case, graph) == result
    _assert_code("variable_set_mismatch", _load, payload, case, graph, variables=_two_finals())
